"""
agent_core.py — Lilly Pup Agentic Orchestration Engine

This is the puzzle master. It wires all existing Lilly pieces into a
full autonomous agent loop: natural language → plan → tools → confirm
→ execute → reflect → repeat.

Inspired by the way Kiro/OpenCode operate — plan first, confirm with user,
then execute with full tool access and streaming feedback.

Architecture:
  User input
      │
      ▼
  intent_is_agentic()   ← decides if this is conversational or a task
      │
      ├─ Conversational ──► normal handle_intent() path (unchanged)
      │
      └─ Task/Agent ──────► AgentSession.run_stream()
                                │
                                ▼
                            PLAN phase
                            LLM (deepseek-coder) creates step list
                                │
                                ▼
                            CONFIRM phase
                            Stream plan to user, await approval
                            (auto-approve safe read-only tasks)
                                │
                                ▼
                            EXECUTE loop  (ReAct: Reason → Act → Observe)
                            ├─ read_file
                            ├─ write_file
                            ├─ run_shell
                            ├─ read_sensors
                            ├─ list_dir
                            ├─ search_code
                            ├─ create_automation
                            ├─ append_approval_log
                            └─ ask_user  (pause and wait for reply)
                                │
                                ▼
                            REFLECT phase
                            LLM (lilly-persona) narrates what happened
                            in Puppy's voice
                                │
                                ▼
                            Persona learning snapshot

Environment variables used (inherited from lilly_ai.py .env):
  OLLAMA_URL        — Ollama host
  FAST_MODEL        — quick responses (lilly-persona:latest)
  QUALITY_MODEL     — deepseek-coder-v2:16b for planning/coding tasks
  LILLY_WORKSPACE   — workspace root
  SENSOR_SERVER_URL — live phone sensor endpoint
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

import httpx

logger = logging.getLogger("AgentCore")

# ── Config — read from env, same values as lilly_ai.py ─────────────────────
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://100.73.249.14:11434").rstrip("/")
FAST_MODEL = os.environ.get("FAST_MODEL", "lilly-persona:latest")
QUALITY_MODEL = os.environ.get("QUALITY_MODEL", "deepseek-coder-v2:16b")
WORKSPACE = Path(os.environ.get("LILLY_WORKSPACE", "/home/labhrasd/Lilly_Workspace"))
SENSOR_URL = os.environ.get("SENSOR_SERVER_URL", "http://100.126.40.84:8099")
APPROVAL_LOG = (
    Path(os.environ.get("LILLY_DATA_DIR", "/home/labhrasd/.lilly/data"))
    / "approval_broadcast_log.jsonl"
)
AUTOMATIONS_DIR = WORKSPACE / "automations"
SHELL_TIMEOUT = int(os.environ.get("AGENT_SHELL_TIMEOUT", "30"))
MAX_ITERATIONS = int(os.environ.get("AGENT_MAX_ITERATIONS", "20"))

# Admin user — gets full autonomy, no extra confirmation gates
ADMIN_EMAIL = "laurencekidney@gmail.com"


# ── Intent classifier — should this be handled as an agent task? ────────────

# Phrases that signal "do something" rather than "tell me something"
_TASK_VERBS = {
    "build",
    "create",
    "make",
    "add",
    "write",
    "fix",
    "patch",
    "update",
    "install",
    "deploy",
    "run",
    "execute",
    "check",
    "scan",
    "monitor",
    "automate",
    "schedule",
    "set up",
    "configure",
    "enable",
    "disable",
    "delete",
    "remove",
    "refactor",
    "upgrade",
    "generate",
    "send",
    "read",
    "load",
    "save",
    "backup",
    "restore",
    "search",
    "find",
    "list all",
    "show me how",
    "do a",
    "do the",
    "apply",
    "implement",
    "integrate",
    "wire",
    "connect",
    "launch",
    "test",
    "debug",
    "profile",
    "optimize",
    "clean",
    "lint",
    "format",
    "compile",
    "package",
    "start",
    "stop",
    "restart",
    "kill",
    "watch",
    "tail",
    "log",
    "learn from",
    "track",
    "record",
    "measure",
    "analyse",
    "analyze",
}

_CONVERSATIONAL_PATTERNS = re.compile(
    r"^(what('s| is)|who('s| is)|where('s| is)|when('s| is)|why|how do you|"
    r"tell me about|explain|define|what does|what are|are you|do you|"
    r"can you tell|i('m| am) (feeling|thinking|wondering)|"
    r"good (morning|afternoon|evening|night)|hello|hi pup|hey)",
    re.IGNORECASE,
)

# Keywords that force the coding/quality model
_CODING_SIGNALS = {
    "code",
    "python",
    "java",
    "javascript",
    "typescript",
    "html",
    "css",
    "function",
    "class",
    "method",
    "endpoint",
    "api",
    "json",
    "yaml",
    "dockerfile",
    "script",
    "test",
    "bug",
    "error",
    "exception",
    "import",
    "module",
    "package",
    "library",
    "dependency",
    "build",
    "compile",
    "lint",
    "refactor",
    "algorithm",
    "data structure",
    "apk",
    "gradle",
    "android",
    "fastapi",
    "flask",
    "react",
    "vue",
    "git",
    "commit",
    "branch",
    "merge",
    "pull request",
}


# Factual / lookup questions that benefit from read-only tool access.
# These are short, can be answered with a tool, and are NOT pure chit-chat.
_LOOKUP_SIGNALS = {
    "what's in",
    "what is in",
    "show me",
    "show the",
    "read",
    "open",
    "list",
    "find",
    "search",
    "grep",
    "check the",
    "look at",
    "look in",
    "look up",
    "see the",
    "inspect",
    "view",
    "cat",
    "head",
    "tail",
    "diff",
    "compare",
    "where is",
    "where's",
    "which file",
    "how many",
    "how big",
    "how long",
    "summarize",
    "summary of",
    "contents of",
    "tell me about",
    "explain the",
    "describe the",
    "details on",
}


def intent_is_agentic(text: str) -> bool:
    """Return True if this message should enter the agent loop.

    Conversational questions and pure greetings stay in the normal
    handle_intent path. Anything that could benefit from tools (tasks,
    lookups, file reads, factual questions about the workspace) enters
    the agent loop. Pure small-talk ("hi", "how are you") is filtered
    out first.
    """
    lower = text.lower().strip()

    # Quick exit for clearly conversational patterns
    if _CONVERSATIONAL_PATTERNS.match(lower):
        return False

    # Anything with a "?" that's not pure small-talk is treated as agentic
    # so the LLM can decide whether to use a tool.
    if "?" in lower and len(lower) > 12:
        return True

    # Check for task verbs at the start or after "please/can you/could you"
    cleaned = re.sub(
        r"^(please|can you|could you|i need you to|i want you to|pup)\s+", "", lower
    ).strip()
    first_word = cleaned.split()[0] if cleaned.split() else ""
    if first_word in _TASK_VERBS:
        return True

    # Multi-word task verbs
    for verb in _TASK_VERBS:
        if cleaned.startswith(verb + " ") or cleaned.startswith(verb + ","):
            return True

    # Lookup / read-style signals: "what's in config.json", "show me the logs"
    for sig in _LOOKUP_SIGNALS:
        if cleaned.startswith(sig) or sig in cleaned:
            return True

    # Any coding signal with an action word nearby
    has_coding = any(sig in lower for sig in _CODING_SIGNALS)
    has_action = any(
        v in lower
        for v in {
            "fix",
            "write",
            "add",
            "build",
            "create",
            "update",
            "run",
            "check",
            "test",
            "debug",
        }
    )
    if has_coding and has_action:
        return True

    return False


def _needs_quality_model(text: str) -> bool:
    """Use the quality/coder model for coding and complex planning tasks."""
    lower = text.lower()
    return any(sig in lower for sig in _CODING_SIGNALS) or any(
        v in lower
        for v in {"build", "fix", "implement", "refactor", "debug", "architect"}
    )


# ── Tool registry ────────────────────────────────────────────────────────────


class ToolResult:
    def __init__(self, ok: bool, output: str, error: str = ""):
        self.ok = ok
        self.output = output
        self.error = error

    def to_observation(self) -> str:
        if self.ok:
            return self.output[:4000] if len(self.output) > 4000 else self.output
        return f"ERROR: {self.error}"


async def tool_read_file(path: str) -> ToolResult:
    """Read a file. Path is relative to WORKSPACE or absolute."""
    try:
        p = Path(path) if Path(path).is_absolute() else WORKSPACE / path
        if not p.exists():
            return ToolResult(False, "", f"File not found: {p}")
        if p.stat().st_size > 512_000:
            # Read first 500KB of large files
            content = p.read_text(errors="replace")[:512_000]
            return ToolResult(True, f"[truncated to 500KB]\n{content}")
        return ToolResult(True, p.read_text(errors="replace"))
    except Exception as e:
        return ToolResult(False, "", str(e))


async def tool_write_file(path: str, content: str) -> ToolResult:
    """Write content to a file. Creates parent dirs as needed.
    Automatically backs up the previous version so it can be undone."""
    try:
        p = Path(path) if Path(path).is_absolute() else WORKSPACE / path
        p.parent.mkdir(parents=True, exist_ok=True)
        # Auto-backup existing file before overwriting
        if p.exists():
            backup_dir = WORKSPACE / ".agent_backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            ts = int(time.time())
            safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", p.name)
            backup_path = backup_dir / f"{safe}.{ts}.bak"
            backup_path.write_bytes(p.read_bytes())
            # Track the backup in the undo stack
            _undo_stack.append(
                {
                    "original_path": str(p),
                    "backup_path": str(backup_path),
                    "ts": ts,
                    "existed": True,
                }
            )
        else:
            # Track new file creation so we can delete it on undo
            _undo_stack.append(
                {
                    "original_path": str(p),
                    "backup_path": None,
                    "ts": int(time.time()),
                    "existed": False,
                }
            )
        # Keep stack bounded
        if len(_undo_stack) > 50:
            oldest = _undo_stack.pop(0)
            if oldest.get("backup_path"):
                try:
                    Path(oldest["backup_path"]).unlink(missing_ok=True)
                except Exception:
                    pass
        p.write_text(content)
        return ToolResult(True, f"Written {len(content)} chars to {p}")
    except Exception as e:
        return ToolResult(False, "", str(e))


async def tool_list_dir(path: str = "") -> ToolResult:
    """List directory contents."""
    try:
        p = (
            Path(path)
            if (path and Path(path).is_absolute())
            else (WORKSPACE / path if path else WORKSPACE)
        )
        if not p.exists():
            return ToolResult(False, "", f"Directory not found: {p}")
        entries = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name))
        lines = []
        for e in entries[:200]:
            prefix = "📁" if e.is_dir() else "📄"
            size = f" ({e.stat().st_size:,}b)" if e.is_file() else ""
            lines.append(f"{prefix} {e.name}{size}")
        if len(list(p.iterdir())) > 200:
            lines.append(f"... ({len(list(p.iterdir()))} total)")
        return ToolResult(True, "\n".join(lines))
    except Exception as e:
        return ToolResult(False, "", str(e))


async def tool_run_shell(command: str, cwd: str = "") -> ToolResult:
    """Run a shell command. Returns stdout + stderr."""
    try:
        working_dir = cwd or str(WORKSPACE)
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=working_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=SHELL_TIMEOUT
            )
        except asyncio.TimeoutError:
            proc.kill()
            return ToolResult(False, "", f"Command timed out after {SHELL_TIMEOUT}s")
        out = stdout.decode(errors="replace").strip()
        err = stderr.decode(errors="replace").strip()
        combined = "\n".join(filter(None, [out, err]))
        if proc.returncode != 0:
            return ToolResult(False, combined, f"Exit code {proc.returncode}")
        return ToolResult(True, combined or "(no output)")
    except Exception as e:
        return ToolResult(False, "", str(e))


async def tool_search_code(
    query: str, path: str = "", file_glob: str = "*.py"
) -> ToolResult:
    """Search for text in code files."""
    try:
        search_path = str(
            Path(path)
            if (path and Path(path).is_absolute())
            else (WORKSPACE / path if path else WORKSPACE)
        )
        cmd = f'grep -r --include="{file_glob}" -n -i -l "{query}" {search_path} 2>/dev/null | head -20'
        proc = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        files = stdout.decode(errors="replace").strip()
        if not files:
            return ToolResult(True, f"No matches for '{query}' in {file_glob} files")
        # Now get actual matching lines from found files
        lines_cmd = f'grep -r --include="{file_glob}" -n -i "{query}" {search_path} 2>/dev/null | head -50'
        proc2 = await asyncio.create_subprocess_shell(
            lines_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout2, _ = await asyncio.wait_for(proc2.communicate(), timeout=10)
        return ToolResult(True, stdout2.decode(errors="replace").strip())
    except Exception as e:
        return ToolResult(False, "", str(e))


async def tool_read_sensors() -> ToolResult:
    """Fetch live sensor data from the Android phone."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{SENSOR_URL}/sensors/all")
            if r.status_code == 200:
                data = r.json()
                summary_parts = []
                for name, val in data.items():
                    if isinstance(val, dict):
                        summary_parts.append(f"{name}: {json.dumps(val)}")
                    else:
                        summary_parts.append(f"{name}: {val}")
                return ToolResult(True, "\n".join(summary_parts[:30]))
            return ToolResult(False, "", f"Sensor server returned {r.status_code}")
    except Exception as e:
        return ToolResult(False, "", f"Sensor server unreachable: {e}")


async def tool_create_automation(
    name: str, description: str, steps: list[dict]
) -> ToolResult:
    """Persist a new automation to disk in the automations directory."""
    try:
        AUTOMATIONS_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
        data = {
            "name": name,
            "description": description,
            "steps": steps,
            "created_at": time.time(),
            "last_run": None,
            "run_count": 0,
        }
        p = AUTOMATIONS_DIR / f"{safe_name}.json"
        p.write_text(json.dumps(data, indent=2))
        return ToolResult(True, f"Automation '{name}' saved to {p}")
    except Exception as e:
        return ToolResult(False, "", str(e))


async def tool_append_log(
    action: str, detail: str, avatar: str = "puppy"
) -> ToolResult:
    """Write an entry to the shared approval/broadcast log."""
    try:
        APPROVAL_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": time.time(),
            "avatar": avatar,
            "action": action,
            "detail": detail,
            "raw": detail,
        }
        with open(APPROVAL_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
        return ToolResult(True, f"Logged: {action} — {detail}")
    except Exception as e:
        return ToolResult(False, "", str(e))


async def tool_read_json(path: str, key: str = "") -> ToolResult:
    """Read a JSON file, optionally extracting a specific key."""
    result = await tool_read_file(path)
    if not result.ok:
        return result
    try:
        data = json.loads(result.output)
        if key:
            parts = key.split(".")
            for p in parts:
                if isinstance(data, dict):
                    data = data.get(p, {})
                elif isinstance(data, list) and p.isdigit():
                    data = data[int(p)]
                else:
                    return ToolResult(False, "", f"Key '{key}' not found")
        return ToolResult(True, json.dumps(data, indent=2)[:4000])
    except json.JSONDecodeError as e:
        return ToolResult(False, "", f"JSON parse error: {e}")


# ── Undo stack — populated by tool_write_file ───────────────────────────────
# Each entry: {original_path, backup_path, ts, existed}
_undo_stack: list[dict] = []

# ── UI context store — pushed by the browser via /api/ui_context ─────────────
_ui_context: dict = {}  # {"dom": "...", "viewport": {...}, "ts": float}


async def tool_undo_last_write() -> ToolResult:
    """Undo the most recent write_file call — restores the previous file version.
    If the file was newly created (didn't exist before), it is deleted.
    Can be called repeatedly to step back through multiple writes."""
    if not _undo_stack:
        return ToolResult(False, "", "Nothing to undo — undo stack is empty.")
    entry = _undo_stack.pop()
    original = Path(entry["original_path"])
    backup = Path(entry["backup_path"]) if entry.get("backup_path") else None
    existed = entry.get("existed", True)
    try:
        if not existed:
            # File was created by the agent — delete it
            original.unlink(missing_ok=True)
            return ToolResult(True, f"Undone: deleted {original} (was newly created)")
        if backup and backup.exists():
            original.write_bytes(backup.read_bytes())
            backup.unlink(missing_ok=True)
            return ToolResult(True, f"Undone: restored {original} from backup")
        return ToolResult(False, "", f"Backup file missing: {backup}")
    except Exception as e:
        return ToolResult(False, "", str(e))


async def tool_read_ui_context() -> ToolResult:
    """Read the current browser UI context — what's visible on screen right now.
    The browser pushes this automatically when the user sends a message.
    Returns the DOM structure, visible text, and viewport size."""
    if not _ui_context:
        return ToolResult(
            False,
            "",
            "No UI context available yet. Ask the user to describe what they see, or wait for the browser to push context.",
        )
    age = time.time() - _ui_context.get("ts", 0)
    if age > 120:
        return ToolResult(
            False,
            "",
            f"UI context is stale ({age:.0f}s old). User may have navigated away.",
        )
    parts = []
    vp = _ui_context.get("viewport", {})
    if vp:
        parts.append(f"Viewport: {vp.get('width')}x{vp.get('height')}px")
    url = _ui_context.get("url", "")
    if url:
        parts.append(f"URL: {url}")
    title = _ui_context.get("title", "")
    if title:
        parts.append(f"Page title: {title}")
    visible_text = _ui_context.get("visible_text", "")
    if visible_text:
        parts.append(f"\nVisible text (truncated):\n{visible_text[:2000]}")
    dom = _ui_context.get("dom", "")
    if dom:
        parts.append(f"\nDOM structure (truncated):\n{dom[:3000]}")
    return ToolResult(True, "\n".join(parts))


def push_ui_context(ctx: dict) -> None:
    """Called by the /api/ui_context endpoint to update the store."""
    global _ui_context
    _ui_context = {**ctx, "ts": time.time()}


# Tool dispatch table
TOOLS: dict[str, Any] = {
    "read_file": tool_read_file,
    "write_file": tool_write_file,
    "list_dir": tool_list_dir,
    "run_shell": tool_run_shell,
    "search_code": tool_search_code,
    "read_sensors": tool_read_sensors,
    "create_automation": tool_create_automation,
    "append_log": tool_append_log,
    "read_json": tool_read_json,
    "undo_last_write": tool_undo_last_write,
    "read_ui_context": tool_read_ui_context,
}

TOOL_SCHEMAS = """
Available tools (call exactly one per step):

read_file(path)                    — read a file (relative to workspace or absolute)
write_file(path, content)          — write/overwrite a file (auto-backs up previous version)
undo_last_write()                  — restore the file changed by the last write_file call
list_dir(path="")                  — list directory (default: workspace root)
run_shell(command, cwd="")         — run a shell command (timeout 30s)
search_code(query, path="", file_glob="*.py")  — grep code files
read_ui_context()                  — read what's currently visible in the user's browser
read_sensors()                     — live sensor data from phone
create_automation(name, description, steps)     — save a new automation
append_log(action, detail, avatar="puppy")      — write to approval/broadcast log
read_json(path, key="")            — read a JSON file, optional dot-path key

To call a tool, output EXACTLY this JSON block (nothing else on the line):
TOOL: {"name": "tool_name", "args": {"arg1": "val1", ...}}

To ask the user a question before continuing:
ASK: Your question here?

To signal you are done:
DONE: Summary of what was accomplished.
"""


# ── LLM call wrappers (call Ollama directly, no circular import) ─────────────


async def _llm(
    messages: list[dict],
    model: str = FAST_MODEL,
    max_tokens: int = 512,
    temperature: float = 0.3,
) -> str:
    """Non-streaming LLM call against Ollama."""
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": max_tokens},
        "keep_alive": "10m",
    }
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(f"{OLLAMA_URL}/api/chat", json=payload)
            r.raise_for_status()
            data = r.json()
            text = (data.get("message", {}).get("content") or "").strip()
            # Strip <think>...</think> blocks from reasoning models
            text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
            return text
    except Exception as e:
        logger.warning(f"LLM call failed ({model}): {e}")
        return ""


async def _llm_stream(
    messages: list[dict], model: str = FAST_MODEL, max_tokens: int = 300
) -> AsyncGenerator[str, None]:
    """Streaming LLM call — yields text chunks."""
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "options": {"temperature": 0.7, "num_predict": max_tokens},
        "keep_alive": "10m",
    }
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream(
                "POST", f"{OLLAMA_URL}/api/chat", json=payload
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                        token = chunk.get("message", {}).get("content", "")
                        if token:
                            yield token
                    except json.JSONDecodeError:
                        continue
    except Exception as e:
        logger.warning(f"LLM stream failed ({model}): {e}")
        yield ""


# ── Plan parser ──────────────────────────────────────────────────────────────


def _parse_tool_call(line: str) -> Optional[tuple[str, dict]]:
    """Parse a TOOL: {...} line and return (tool_name, args) or None."""
    m = re.match(r"^TOOL:\s*(\{.*\})\s*$", line.strip())
    if not m:
        return None
    try:
        obj = json.loads(m.group(1))
        return obj.get("name", ""), obj.get("args", {})
    except json.JSONDecodeError:
        return None


def _parse_ask(line: str) -> Optional[str]:
    """Parse an ASK: question line."""
    m = re.match(r"^ASK:\s*(.+)$", line.strip(), re.DOTALL)
    return m.group(1).strip() if m else None


def _parse_done(line: str) -> Optional[str]:
    """Parse a DONE: summary line."""
    m = re.match(r"^DONE:\s*(.+)$", line.strip(), re.DOTALL)
    return m.group(1).strip() if m else None


# ── Approval helpers ─────────────────────────────────────────────────────────

# Tools that require user confirmation before executing (unless admin auto-approves)
_DESTRUCTIVE_TOOLS = {"write_file", "run_shell", "create_automation"}


# Admin gets auto-approval for everything
def _auto_approve(user_email: str, tool_name: str, args: dict) -> bool:
    """Return True if this action can run without user confirmation."""
    if user_email == ADMIN_EMAIL:
        return True
    return tool_name not in _DESTRUCTIVE_TOOLS


# ── Plan generator ───────────────────────────────────────────────────────────

_PLANNER_SYSTEM = """You are Lilly Pup's task planner. Given a user request, produce a concise numbered plan of what needs to happen. Be specific about what files, commands, or data will be involved. Keep it to 3-7 steps. No code yet — just the plan.

Format:
1. Step description
2. Step description
...

End with: Ready to execute. Shall I proceed?"""


async def generate_plan(task: str, context: str = "") -> str:
    """Ask the planner LLM to break the task into numbered steps."""
    model = QUALITY_MODEL if _needs_quality_model(task) else FAST_MODEL
    messages = [
        {"role": "system", "content": _PLANNER_SYSTEM},
        {
            "role": "user",
            "content": f"Task: {task}\n\nContext: {context}"
            if context
            else f"Task: {task}",
        },
    ]
    parts: list[str] = []
    async for tok in _llm_stream(messages, model=model, max_tokens=400):
        parts.append(tok)
    return "".join(parts).strip()


# ── ReAct executor ───────────────────────────────────────────────────────────

_EXECUTOR_SYSTEM = f"""You are an autonomous agent executing a task for Laurence (admin). You have access to tools to read files, write code, run commands, check sensors, create automations and more.

Work iteratively: reason about what to do next, call exactly one tool, observe the result, then decide your next action.

{TOOL_SCHEMAS}

Rules:
- Be precise with file paths. If unsure, use list_dir() or search_code() first.
- For write_file, always read the file first if it exists so you don't overwrite other content unintentionally.
- For run_shell, prefer targeted commands (python -c, grep, cat) over broad ones.
- If you hit an error, diagnose it and try a different approach.
- When the task is complete, emit DONE: with a clear summary.
- Never emit placeholder content. If you don't know a value, use read_file or ask.
"""


class AgentSession:
    """
    One agent execution session. Created per user request that enters the
    agent loop. Streams step-by-step output as SSE-compatible events.
    """

    def __init__(self, task: str, user_email: str = "", session_id: str = ""):
        self.task = task
        self.user_email = user_email or ADMIN_EMAIL
        self.session_id = session_id or uuid.uuid4().hex[:8]
        self.history: list[dict] = []
        self.pending_ask: Optional[str] = None
        self.done = False
        self.result_summary = ""
        self._paused_for_input = asyncio.Event()
        self._user_reply: Optional[str] = None

    def inject_user_reply(self, text: str) -> None:
        """Feed a user reply when the agent issued an ASK."""
        self._user_reply = text
        self._paused_for_input.set()

    async def _await_user(self, question: str, timeout: float = 120.0) -> str:
        """Pause execution and wait for user input. Returns reply text."""
        self.pending_ask = question
        self._paused_for_input.clear()
        self._user_reply = None
        try:
            await asyncio.wait_for(self._paused_for_input.wait(), timeout=timeout)
            return self._user_reply or ""
        except asyncio.TimeoutError:
            return "(no reply — continuing with best judgement)"

    async def run_stream(self) -> AsyncGenerator[dict, None]:
        """
        Main agent loop. Yields event dicts:
          {"type": "plan", "text": "..."}          — initial plan
          {"type": "step", "text": "..."}          — agent reasoning
          {"type": "tool_call", "name": ..., "args": ...}  — tool invocation
          {"type": "tool_result", "ok": bool, "output": "..."}
          {"type": "ask", "question": "..."}       — agent needs user input
          {"type": "token", "value": "..."}        — streaming narrative token
          {"type": "done", "reply": "...", "session_id": "..."}
          {"type": "error", "message": "..."}
        """
        model = QUALITY_MODEL if _needs_quality_model(self.task) else FAST_MODEL

        # ── PLAN PHASE ────────────────────────────────────────────────
        yield {"type": "step", "text": "Thinking about the task..."}
        plan_text = await generate_plan(self.task)
        if not plan_text:
            yield {
                "type": "error",
                "message": "Planner returned empty response. Is Ollama running?",
            }
            return

        yield {"type": "plan", "text": plan_text}

        # Add plan to history
        self.history = [
            {"role": "system", "content": _EXECUTOR_SYSTEM},
            {"role": "user", "content": self.task},
            {
                "role": "assistant",
                "content": f"Plan:\n{plan_text}\n\nStarting execution.",
            },
        ]

        # ── EXECUTE LOOP ──────────────────────────────────────────────
        iterations = 0
        while not self.done and iterations < MAX_ITERATIONS:
            iterations += 1

            # Ask LLM what to do next (STREAMING — yield tokens as they arrive)
            response_parts: list[str] = []
            line_buf = ""
            last_token_at = time.time()
            yield {"type": "step", "text": "💭 thinking..."}
            async for token in _llm_stream(
                self.history,
                model=model,
                max_tokens=600,
            ):
                response_parts.append(token)
                # Strip <think>...</think> blocks from reasoning models on the fly
                cleaned = re.sub(
                    r"<think>.*?</think>", "", "".join(response_parts), flags=re.DOTALL
                )
                # Yield each token as it streams so the UI sees a live cursor
                yield {"type": "token", "value": token}
                last_token_at = time.time()
                # Give other coroutines a chance to run periodically
                if len(response_parts) % 8 == 0:
                    await asyncio.sleep(0)

            response = "".join(response_parts).strip()
            # Strip <think>...</think> blocks from reasoning models
            response = re.sub(
                r"<think>.*?</think>", "", response, flags=re.DOTALL
            ).strip()

            if not response:
                yield {"type": "error", "message": "LLM returned empty response."}
                break

            # Parse the response line by line
            lines = response.strip().split("\n")
            acted = False

            for line in lines:
                line = line.strip()
                if not line:
                    continue

                # ── DONE signal ───────────────────────────────────────
                done_msg = _parse_done(line)
                if done_msg:
                    self.done = True
                    self.result_summary = done_msg
                    yield {"type": "step", "text": f"✓ Complete: {done_msg}"}
                    acted = True
                    break

                # ── ASK signal ────────────────────────────────────────
                ask_msg = _parse_ask(line)
                if ask_msg:
                    yield {"type": "ask", "question": ask_msg}
                    user_reply = await self._await_user(ask_msg)
                    if user_reply:
                        self.history.append({"role": "assistant", "content": response})
                        self.history.append({"role": "user", "content": user_reply})
                    acted = True
                    break

                # ── TOOL CALL ─────────────────────────────────────────
                parsed = _parse_tool_call(line)
                if parsed:
                    tool_name, tool_args = parsed

                    if tool_name not in TOOLS:
                        obs = f"Unknown tool: {tool_name}"
                        yield {"type": "tool_result", "ok": False, "output": obs}
                    else:
                        # Approval gate for destructive tools
                        needs_confirm = not _auto_approve(
                            self.user_email, tool_name, tool_args
                        )
                        if needs_confirm:
                            q = f"I need to run `{tool_name}` with: {json.dumps(tool_args, indent=2)}\n\nApprove? (yes/no)"
                            yield {"type": "ask", "question": q}
                            user_reply = await self._await_user(q, timeout=60.0)
                            if user_reply.strip().lower() not in (
                                "yes",
                                "y",
                                "approve",
                                "ok",
                                "sure",
                                "do it",
                                "go ahead",
                            ):
                                obs = f"Skipped {tool_name} — user did not approve."
                                yield {"type": "step", "text": obs}
                                self.history.append(
                                    {"role": "assistant", "content": response}
                                )
                                self.history.append(
                                    {
                                        "role": "user",
                                        "content": f"I said no to {tool_name}. Find an alternative approach or skip this step.",
                                    }
                                )
                                acted = True
                                break

                        # Emit tool call event
                        yield {
                            "type": "tool_call",
                            "name": tool_name,
                            "args": tool_args,
                        }

                        # Execute the tool
                        try:
                            fn = TOOLS[tool_name]
                            result: ToolResult = await fn(**tool_args)
                        except TypeError as e:
                            result = ToolResult(False, "", f"Bad arguments: {e}")
                        except Exception as e:
                            result = ToolResult(False, "", f"Tool exception: {e}")

                        obs = result.to_observation()
                        yield {
                            "type": "tool_result",
                            "ok": result.ok,
                            "output": obs[:2000],
                        }

                        # Feed observation back to LLM
                        self.history.append({"role": "assistant", "content": response})
                        self.history.append(
                            {
                                "role": "user",
                                "content": f"Tool result for {tool_name}:\n{obs}",
                            }
                        )
                        acted = True
                        break  # one tool per iteration

            if not acted:
                # LLM produced narrative reasoning (no tool call or done signal)
                # Add it to history and let it continue
                yield {"type": "step", "text": response[:500]}
                self.history.append({"role": "assistant", "content": response})
                self.history.append(
                    {
                        "role": "user",
                        "content": "Continue. Call a tool or emit DONE: when finished.",
                    }
                )

        # ── REFLECT PHASE — narrate in Lilly Pup's voice ─────────────
        if self.done:
            reflect_messages = [
                {
                    "role": "system",
                    "content": (
                        "You are Lilly Pup — stoic, precise, dry wit, Amy Medium voice. "
                        "Summarise what was just accomplished in 1-2 sentences. "
                        "No filler, no hype. Direct and complete."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Task: {self.task}\n\n"
                        f"Result: {self.result_summary}\n\n"
                        "Give me the Lilly Pup summary."
                    ),
                },
            ]
            narrative = ""
            yield {"type": "token", "value": ""}  # signal start of stream
            async for chunk in _llm_stream(
                reflect_messages, model=FAST_MODEL, max_tokens=120
            ):
                narrative += chunk
                yield {"type": "token", "value": chunk}

            # Log the completion to the approval broadcast log
            await tool_append_log(
                action="agent_complete",
                detail=f"Task: {self.task[:100]} — {self.result_summary[:200]}",
                avatar="puppy",
            )

            yield {
                "type": "done",
                "reply": narrative.strip() or self.result_summary,
                "session_id": self.session_id,
            }
        else:
            msg = f"Reached iteration limit ({MAX_ITERATIONS}). Partial work may have been done."
            yield {"type": "done", "reply": msg, "session_id": self.session_id}


# ── Session registry (active sessions, keyed by session_id) ─────────────────
_active_sessions: dict[str, AgentSession] = {}


def get_session(session_id: str) -> Optional[AgentSession]:
    return _active_sessions.get(session_id)


def create_session(task: str, user_email: str = "") -> AgentSession:
    session = AgentSession(task=task, user_email=user_email)
    _active_sessions[session.session_id] = session
    return session


async def execute_task(task: str, user_email: str = "") -> str:
    """Run a coding agent task to completion. Returns the result summary.

    This is a convenience wrapper around AgentSession.run_stream() that
    collects all events and returns the final result string.
    """
    session = create_session(task=task, user_email=user_email)
    result_parts = []
    try:
        async for event in session.run_stream():
            etype = event.get("type", "")
            if etype == "done":
                result_parts.append(event.get("reply", ""))
            elif etype == "error":
                result_parts.append(f"ERROR: {event.get('message', '')}")
            elif etype == "tool_result":
                output = event.get("output", "")
                if output:
                    result_parts.append(output[:500])
    except Exception as e:
        result_parts.append(f"Agent error: {e}")
    finally:
        cleanup_session(session.session_id)
    return "\n".join(result_parts) if result_parts else "(no output)"


def cleanup_session(session_id: str) -> None:
    _active_sessions.pop(session_id, None)
