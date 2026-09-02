#!/usr/bin/env python3
"""
lilly_ops.py — Lilly's ops bridge.

Gives Lilly real "hands":

  • Coder jobs       — headless aider runs against the local Ollama backend,
                       sandboxed in their own git workspace (coder_workspace/).
  • Server shell     — guarded command execution on the host machine.
  • Phone shell      — command execution on the paired Termux phone
                       (pm / am / pkg / termux-*) via an injected async runner
                       (lilly_ai.termux_run).
  • Persona training — background LoRA training runs via trainer/run.py,
                       fed with REAL conversation history exported from
                       conversation_history.jsonl. No fake data.

lilly_ai.py calls configure() once at startup to inject the Ollama URL,
the default coder model, and the phone runner.
"""

import asyncio
import json
import logging
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Awaitable, Callable, Optional

logger = logging.getLogger("lilly_ops")

WORKSPACE = Path(os.environ.get("LILLY_WORKSPACE", str(Path(__file__).parent)))
CODER_WORKSPACE = Path(
    os.environ.get("LILLY_CODER_WORKSPACE", str(WORKSPACE / "coder_workspace"))
)
TRAINER_DIR = WORKSPACE / "trainer"
TRAIN_OUTPUT_DIR = WORKSPACE / "trained_persona_model"
CONVERSATION_HISTORY_FILE = WORKSPACE / "conversation_history.jsonl"
REAL_CONVERSATIONS_EXPORT = TRAINER_DIR / "dataset_cache" / "real_conversations.jsonl"

AIDER_BIN = os.environ.get("LILLY_AIDER_BIN", "aider")

# ─── Injected configuration (set by lilly_ai.configure) ─────────────────────
OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_CODER_MODEL = ""
# async callable(args: list[str], timeout: float) -> tuple[str, str]
_phone_runner: Optional[Callable[..., Awaitable[tuple[str, str]]]] = None


def configure(
    ollama_url: str = "",
    default_model: str = "",
    phone_runner: Optional[Callable[..., Awaitable[tuple[str, str]]]] = None,
) -> None:
    """Inject runtime dependencies from lilly_ai (avoids circular imports)."""
    global OLLAMA_URL, DEFAULT_CODER_MODEL, _phone_runner
    if ollama_url:
        OLLAMA_URL = ollama_url
    if default_model:
        DEFAULT_CODER_MODEL = default_model
    if phone_runner is not None:
        _phone_runner = phone_runner


# ─── Job registry ────────────────────────────────────────────────────────────
CODER_JOBS: dict[str, dict] = {}
TRAIN_JOBS: dict[str, dict] = {}
_MAX_JOBS = 50


def _new_job(registry: dict, kind: str, meta: dict) -> dict:
    job = {
        "id": uuid.uuid4().hex[:8],
        "kind": kind,
        "status": "running",
        "started": time.time(),
        "finished": None,
        **meta,
    }
    registry[job["id"]] = job
    # Bound the registry — drop oldest finished jobs first
    if len(registry) > _MAX_JOBS:
        finished = sorted(
            (j for j in registry.values() if j["status"] != "running"),
            key=lambda j: j.get("finished") or 0,
        )
        for old in finished[: len(registry) - _MAX_JOBS]:
            registry.pop(old["id"], None)
    return job


def get_job(registry: dict, job_id: str) -> Optional[dict]:
    return registry.get(job_id)


def list_jobs(registry: dict) -> list[dict]:
    return sorted(registry.values(), key=lambda j: j["started"], reverse=True)

# ─── Safety guards ───────────────────────────────────────────────────────────
# Catastrophic host commands that must never run, even after confirmation.
_SERVER_BLOCKLIST = re.compile(
    r"(rm\s+-\w*[rf]\w*\s+(?:--no-preserve-root\s+)?/(?:\s|\*|\.|$)"
    r"|mkfs|mkswap|fdisk|wipefs"
    r"|dd\s+.*of=/dev/"
    r"|:\(\)\s*\{"  # fork bomb
    r"|\bshutdown\b|\breboot\b|\bpoweroff\b|\bhalt\b|init\s+[06]\b"
    r"|>\s*/dev/sd[a-z]"
    r"|chmod\s+-R\s+777\s+/(?:\s|$)"
    r"|\buseradd\b|\buserdel\b|\bpasswd\b|\bvisudo\b"
    r"|\biptables\b|\bnft\s+flush"
    r"|systemctl\s+(?:stop|disable|mask)\s+(?:docker|ssh|sshd|networking|NetworkManager))",
    re.IGNORECASE,
)

# Phone commands that could brick / wipe the device.
_PHONE_BLOCKLIST = re.compile(
    r"(rm\s+-\w*[rf]\w*\s+/(?:\s|\*|\.|$)"
    r"|\breboot\b|\bshutdown\b"
    r"|pm\s+uninstall\s+(?:--user\s+\d+\s+)?(?:com\.android\.|android\b)"
    r"|pm\s+(?:disable|clear)\s+(?:com\.android\.|android\b)"
    r"|dd\s+.*of=/dev/|mkfs|fastboot|\badb\s+shell\s+reboot\b)",
    re.IGNORECASE,
)


def is_server_command_blocked(command: str) -> bool:
    return bool(_SERVER_BLOCKLIST.search(command))


def is_phone_command_blocked(command: str) -> bool:
    return bool(_PHONE_BLOCKLIST.search(command))


# ─── Server shell ────────────────────────────────────────────────────────────
async def run_server_shell(command: str, timeout: float = 30.0) -> tuple[bool, str]:
    """Run a shell command on the host. Guarded by _SERVER_BLOCKLIST.

    Returns (ok, combined_output). Real stdout/stderr only.
    """
    command = (command or "").strip()
    if not command:
        return False, "empty command"
    if is_server_command_blocked(command):
        return False, "blocked by safety policy"
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(WORKSPACE),
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        text = out.decode(errors="replace").strip()
        ok = proc.returncode == 0
        if not ok:
            text = f"(exit {proc.returncode}) {text}"
        return ok, text
    except asyncio.TimeoutError:
        return False, f"timed out after {timeout}s"
    except Exception as e:
        return False, str(e)


# ─── Phone shell ─────────────────────────────────────────────────────────────
async def run_phone_shell(command: str, timeout: float = 30.0) -> tuple[bool, str]:
    """Run a shell command on the paired Termux phone via the injected runner.

    pm/am/pkg/termux-* all work here. Returns (ok, output) — real device
    output only; an unreachable phone returns an honest error.
    """
    command = (command or "").strip()
    if not command:
        return False, "empty command"
    if is_phone_command_blocked(command):
        return False, "blocked by safety policy"
    if _phone_runner is None:
        return False, "phone bridge not configured (no phone runner injected)"
    try:
        stdout, stderr = await _phone_runner(["sh", "-lc", command], timeout=timeout)
    except Exception as e:
        return False, str(e)
    out = (stdout or "").strip()
    err = (stderr or "").strip()
    if err and not out:
        return False, err
    if err:
        out = f"{out}\n(stderr: {err})"
    return True, out or "(no output)"



# ─── Coder (headless aider) ──────────────────────────────────────────────────
def _ensure_coder_workspace() -> None:
    CODER_WORKSPACE.mkdir(parents=True, exist_ok=True)
    if not (CODER_WORKSPACE / ".git").exists():
        import subprocess

        subprocess.run(["git", "init", "-q"], cwd=str(CODER_WORKSPACE), check=False)
        subprocess.run(
            ["git", "config", "user.email", "lilly@coder.local"],
            cwd=str(CODER_WORKSPACE),
            check=False,
        )
        subprocess.run(
            ["git", "config", "user.name", "Lilly Coder"],
            cwd=str(CODER_WORKSPACE),
            check=False,
        )


async def start_aider_job(
    prompt: str,
    model: str = "",
    timeout: float = 900.0,
) -> dict:
    """Start a headless aider run in the sandboxed coder workspace.

    Returns the job dict immediately; the run continues in the background.
    """
    prompt = (prompt or "").strip()
    if len(prompt) < 3:
        return {"error": "Tell me what to build or fix — the request was too short."}
    model = model or DEFAULT_CODER_MODEL
    if not model:
        return {"error": "No coder model configured (set VIBECODE_MODEL or FAST_MODEL)."}

    _ensure_coder_workspace()
    log_file = CODER_WORKSPACE / f"aider_job_{uuid.uuid4().hex[:8]}.log"
    job = _new_job(
        CODER_JOBS,
        "coder",
        {"prompt": prompt, "model": model, "log": str(log_file), "tail": ""},
    )
    asyncio.create_task(_run_aider(job, prompt, model, log_file, timeout))
    return job


async def _run_aider(
    job: dict, prompt: str, model: str, log_file: Path, timeout: float
) -> None:
    env = os.environ.copy()
    env["OLLAMA_API_BASE"] = OLLAMA_URL
    cmd = [
        AIDER_BIN,
        "--model",
        f"ollama_chat/{model}",
        "--message",
        prompt,
        "--yes-always",
        "--no-stream",
        "--no-pretty",
        "--no-check-update",
        "--no-show-model-warnings",
        "--no-suggest-shell-commands",
        "--no-auto-lint",
        "--map-tokens",
        "512",
    ]
    logger.info("aider job %s: model=%s prompt=%r", job["id"], model, prompt[:80])
    try:
        with open(log_file, "w") as lf:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=lf,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(CODER_WORKSPACE),
                env=env,
            )
            try:
                await asyncio.wait_for(proc.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                job["status"] = "failed"
                job["tail"] = f"killed after {timeout}s timeout"
                return
        job["returncode"] = proc.returncode
        job["status"] = "done" if proc.returncode == 0 else "failed"
    except FileNotFoundError:
        job["status"] = "failed"
        job["tail"] = f"aider binary not found ({AIDER_BIN})"
        return
    except Exception as e:
        job["status"] = "failed"
        job["tail"] = str(e)
        return
    finally:
        job["finished"] = time.time()

    job["tail"] = _log_tail(log_file)
    # Real result summary: what actually changed in the workspace git repo
    job["git_summary"] = await _workspace_git_summary()


def _log_tail(log_file: Path, lines: int = 30) -> str:
    try:
        content = log_file.read_text(errors="replace").splitlines()
        return "\n".join(content[-lines:])
    except Exception:
        return ""


async def _workspace_git_summary() -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(CODER_WORKSPACE),
            "log",
            "--oneline",
            "-3",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        return out.decode(errors="replace").strip()
    except Exception:
        return ""


def coder_status_summary(job_id: str = "") -> str:
    if job_id:
        job = CODER_JOBS.get(job_id)
        if not job:
            return f"No coder job with id {job_id}."
        return _format_coder_job(job)
    running = [j for j in CODER_JOBS.values() if j["status"] == "running"]
    if running:
        j = running[-1]
        elapsed = int(time.time() - j["started"])
        return (
            f"The coder is on it — \"{j['prompt'][:60]}\" "
            f"(job {j['id']}, {elapsed}s in). I'll keep at it."
        )
    if not CODER_JOBS:
        return "The coder hasn't run anything yet. Say 'code <what you want>' to start."
    return _format_coder_job(list_jobs(CODER_JOBS)[0])


def _format_coder_job(job: dict) -> str:
    status = job["status"]
    base = f"Coder job {job['id']} ({status}): \"{job['prompt'][:60]}\""
    if status == "done" and job.get("git_summary"):
        return f"{base}. Latest commits: {job['git_summary'].splitlines()[0]}"
    if status == "failed" and job.get("tail"):
        last = [l for l in job["tail"].splitlines() if l.strip()]
        return f"{base}. Last output: {last[-1][:120] if last else 'none'}"
    return base



# ─── Persona training ────────────────────────────────────────────────────────
def export_real_conversations(
    persona_id: str = "",
    max_samples: int = 2000,
    out_path: Path = REAL_CONVERSATIONS_EXPORT,
) -> int:
    """Export real chat history into the trainer's persona-text format.

    Reads conversation_history.jsonl (real user↔Lilly turns only) and writes
    one {"text": ...} JSON object per line, formatted with the same special
    tokens the trainer's dataset preparers use. Returns the sample count.
    """
    if not CONVERSATION_HISTORY_FILE.exists():
        return 0
    try:
        from trainer.persona_backgrounds import PERSONA_BACKGROUNDS, list_personas

        personas = list_personas()
        if persona_id not in PERSONA_BACKGROUNDS:
            persona_id = personas[0] if personas else ""
        p_info = PERSONA_BACKGROUNDS.get(persona_id)
        if not p_info:
            return 0
        name = p_info["name"]
        header = [
            f"<|persona|>{persona_id}",
            f"<|background|>{p_info['background']}",
            f"<|style|>{p_info['voice_style']}",
            f"<|markers|>{', '.join(p_info['vocab_markers'])}",
        ]
    except Exception as e:
        logger.warning(f"persona backgrounds unavailable: {e}")
        return 0

    # Group flat turn list into (user, assistant) pairs per session
    pairs: list[tuple[str, str]] = []
    try:
        with open(CONVERSATION_HISTORY_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                user = (entry.get("user") or "").strip()
                assistant = (entry.get("assistant") or "").strip()
                if user and assistant:
                    pairs.append((user[:500], assistant[:500]))
    except Exception as e:
        logger.warning(f"could not read conversation history: {e}")
        return 0

    if not pairs:
        return 0

    pairs = pairs[-max_samples:]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with open(out_path, "w") as out:
        for user, assistant in pairs:
            text = "\n".join(
                header
                + ["<|conversation|>", f"<|user|>{user}", f"<|{name}|>{assistant}"]
            )
            out.write(json.dumps({"text": text}) + "\n")
            count += 1
    logger.info(f"exported {count} real conversation samples to {out_path}")
    return count


async def start_training_job(
    max_steps: int = 300,
    personas: Optional[list[str]] = None,
    save_gguf: bool = False,
    include_conversations: bool = True,
    timeout: float = 6 * 3600.0,
) -> dict:
    """Kick off a background LoRA training run via trainer/run.py.

    Exports real conversation history into the dataset cache first (unless
    disabled), so the persona evolves from actual interactions.
    """
    running = [j for j in TRAIN_JOBS.values() if j["status"] == "running"]
    if running:
        j = running[-1]
        elapsed = int(time.time() - j["started"])
        return {
            "error": f"Training job {j['id']} is already running ({elapsed}s in). "
            "Ask me 'training status' to check on it."
        }

    exported = 0
    if include_conversations:
        try:
            exported = export_real_conversations(
                persona_id=(personas[0] if personas else "")
            )
        except Exception as e:
            logger.warning(f"conversation export failed (training continues): {e}")

    log_file = TRAINER_DIR / "logs" / f"ops_train_{uuid.uuid4().hex[:8]}.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    job = _new_job(
        TRAIN_JOBS,
        "training",
        {
            "max_steps": max_steps,
            "personas": personas or "all",
            "real_samples": exported,
            "log": str(log_file),
            "tail": "",
        },
    )
    asyncio.create_task(
        _run_training(job, max_steps, personas, save_gguf, log_file, timeout)
    )
    return job


async def _run_training(
    job: dict,
    max_steps: int,
    personas: Optional[list[str]],
    save_gguf: bool,
    log_file: Path,
    timeout: float,
) -> None:
    cmd = [
        sys.executable,
        "-m",
        "trainer.run",
        "--output-dir",
        str(TRAIN_OUTPUT_DIR),
        "--max-steps",
        str(max_steps),
    ]
    if personas:
        cmd += ["--personas"] + list(personas)
    if save_gguf:
        cmd.append("--save-gguf")
    logger.info("training job %s: %s", job["id"], " ".join(cmd))
    try:
        with open(log_file, "w") as lf:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=lf,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(WORKSPACE),
            )
            try:
                await asyncio.wait_for(proc.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                job["status"] = "failed"
                job["tail"] = f"killed after {timeout}s timeout"
                return
        job["returncode"] = proc.returncode
        job["status"] = "done" if proc.returncode == 0 else "failed"
    except Exception as e:
        job["status"] = "failed"
        job["tail"] = str(e)
        return
    finally:
        job["finished"] = time.time()
    job["tail"] = _log_tail(log_file, lines=40)


def training_status_summary(job_id: str = "") -> str:
    if job_id:
        job = TRAIN_JOBS.get(job_id)
        if not job:
            return f"No training job with id {job_id}."
        return _format_train_job(job)
    running = [j for j in TRAIN_JOBS.values() if j["status"] == "running"]
    if running:
        j = running[-1]
        elapsed = int(time.time() - j["started"])
        tail = _log_tail(Path(j["log"]), lines=3)
        last = [l for l in tail.splitlines() if l.strip()]
        progress = last[-1][:100] if last else "warming up"
        return (
            f"Training job {j['id']} is running ({elapsed}s in, "
            f"{j.get('real_samples', 0)} real conversation samples). "
            f"Latest: {progress}"
        )
    if not TRAIN_JOBS:
        return "No training runs yet. Say 'train yourself' and I'll start one."
    return _format_train_job(list_jobs(TRAIN_JOBS)[0])


def _format_train_job(job: dict) -> str:
    status = job["status"]
    base = (
        f"Training job {job['id']} ({status}) — "
        f"{job.get('real_samples', 0)} real samples, "
        f"max_steps={job.get('max_steps')}"
    )
    if status == "done":
        return f"{base}. Model written to {TRAIN_OUTPUT_DIR.name}/."
    if status == "failed" and job.get("tail"):
        last = [l for l in job["tail"].splitlines() if l.strip()]
        return f"{base}. Last log line: {last[-1][:120] if last else 'none'}"
    return base
