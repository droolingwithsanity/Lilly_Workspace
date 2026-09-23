#!/usr/bin/env python3
"""
training_control.py — Lilly AI Training Dashboard backend
=========================================================
Serves the training dashboard on the main Lilly server (:8098) at /training.

Access is STRICTLY restricted to the single allowed owner email
(laurencekidney@gmail.com). When Auth0 is unavailable (AUTH_AVAILABLE=False)
the dashboard is denied outright, because identity cannot be proven.

All state/log/data comes from the real training_manager.sh script and the
actual files it manages (trainer/logs/*, trained_avatars/, trained_models/,
training_data/), so the UI always shows live data.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse

import httpx

WORKSPACE = Path(__file__).parent
SCRIPT = WORKSPACE / "training_manager.sh"

VISION_SERVER = os.environ.get("VISION_SERVER_URL", "http://127.0.0.1:8198")
TRAINER_SERVER = os.environ.get("TRAINER_SERVER_URL", "http://127.0.0.1:8199")
CLASS_CONFIG = WORKSPACE / "vision_class_config.json"

# Fallback class list if the vision server isn't answering (common OIV7/COCO set)
FALLBACK_CLASSES = [
    "Person",
    "Bicycle",
    "Car",
    "Motorcycle",
    "Airplane",
    "Bus",
    "Train",
    "Truck",
    "Boat",
    "Traffic light",
    "Fire hydrant",
    "Stop sign",
    "Parking meter",
    "Bench",
    "Bird",
    "Cat",
    "Dog",
    "Horse",
    "Sheep",
    "Cow",
    "Elephant",
    "Bear",
    "Zebra",
    "Giraffe",
    "Backpack",
    "Umbrella",
    "Handbag",
    "Tie",
    "Suitcase",
    "Frisbee",
    "Skis",
    "Snowboard",
    "Sports ball",
    "Kite",
    "Baseball bat",
    "Baseball glove",
    "Skateboard",
    "Surfboard",
    "Tennis racket",
    "Bottle",
    "Wine glass",
    "Cup",
    "Fork",
    "Knife",
    "Spoon",
    "Bowl",
    "Banana",
    "Apple",
    "Sandwich",
    "Orange",
    "Broccoli",
    "Carrot",
    "Hot dog",
    "Pizza",
    "Donut",
    "Cake",
    "Chair",
    "Couch",
    "Potted plant",
    "Bed",
    "Dining table",
    "Toilet",
    "TV",
    "Laptop",
    "Mouse",
    "Remote",
    "Keyboard",
    "Cell phone",
    "Microwave",
    "Oven",
    "Toaster",
    "Sink",
    "Refrigerator",
    "Book",
    "Clock",
    "Vase",
    "Scissors",
    "Teddy bear",
    "Hair drier",
    "Toothbrush",
    "Face",
    "Human face",
    "Human nose",
    "Man",
    "Woman",
]

# Config keys (Lilly-AI toggles) that the console is allowed to change
TRAINER_CONFIG_KEYS = {
    "auto_collect",
    "collect_threshold",
    "correction_threshold",
    "min_samples_for_training",
    "training_interval_hours",
    "max_epochs",
    "early_stopping_patience",
    "auto_deploy_threshold",
}

# The ONLY user allowed to use the dashboard.
ALLOWED_EMAIL = "laurencekidney@gmail.com"

# import auth the exact same way lilly_ai.py does
try:
    from auth0_auth import get_current_user  # type: ignore

    AUTH_AVAILABLE = True
except ImportError:
    get_current_user = None
    AUTH_AVAILABLE = False

router = APIRouter(tags=["training"])

# ── Resource profile (sliders pushed from the console UI) ───────────────
RESOURCE_PROFILE = WORKSPACE / ".training" / "resource_profile.json"
_RESOURCE_KEYS = (
    "threads",
    "gpu_pct",
    "batch_size",
    "epochs",
    "periodic",
    "interval_hours",
)

OLLAMA_URL = os.environ.get(
    "OLLAMA_URL", os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
)
OLLAMA_CHAT_MODEL = os.environ.get(
    "OLLAMA_CHAT_MODEL", os.environ.get("OLLAMA_MODEL", "qwen2.5:3b")
)

REPOS_DIR = WORKSPACE / "repos"
_TRAINING_CHAT_HISTORY: Dict[str, list] = {}
TRAINING_CHAT_MAX = 20

# ── OSINT (reverse face-search + footprint from training console) ──────
OSINT_CROPS_DIR = WORKSPACE / ".training" / "osint_crops"
OSINT_JOBS_FILE = WORKSPACE / ".training" / "osint_jobs.json"
OSINT_MAX_CROP_PX = 640  # resize largest face crop to this max side


def _osint_available() -> tuple[bool, str]:
    """Check whether the OSINT reverse-search pipeline is importable."""
    try:
        from osint_face_lookup import is_enabled  # noqa: F401

        return True, "ok"
    except Exception:
        return False, "osint_face_lookup module not found"


def _load_osint_jobs() -> list[dict]:
    try:
        if OSINT_JOBS_FILE.exists():
            return json.loads(OSINT_JOBS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return []


def _save_osint_jobs(jobs: list[dict]) -> None:
    OSINT_CROPS_DIR.mkdir(parents=True, exist_ok=True)
    OSINT_JOBS_FILE.write_text(
        json.dumps(jobs[-50:], indent=1, default=str), encoding="utf-8"
    )


def _osint_job_status(job: dict) -> dict:
    """Enrich a job record with live OSINT cache status."""
    res_id = job.get("res_id") or ""
    try:
        from osint_face_lookup import get_cached_result

        cached = get_cached_result(res_id)
    except Exception:
        cached = None
    if cached:
        job["status"] = "complete" if not cached.get("error") else "error"
        job["name"] = cached.get("name")
        job["confidence"] = cached.get("confidence")
        job["social_accounts"] = cached.get("social_accounts", [])
        job["sources"] = cached.get("sources", [])
        job["images"] = cached.get("images", [])[:8]
        job["error"] = cached.get("error")
    # Attach footprint job detail if available
    fp_id = job.get("footprint_job_id")
    if fp_id:
        try:
            import footprint as fp_mod

            fp_job = fp_mod.get_job(fp_id)
            if fp_job:
                job["footprint_status"] = fp_job.get("status")
                job["footprint_progress"] = fp_job.get("progress")
                job["footprint_report"] = fp_job.get("report")
        except Exception:
            pass
    return job


def _extract_face_crop(img_bytes: bytes) -> tuple[bytes | None, dict]:
    """Detect faces in an image; return (largest_face_crop_jpg, meta).

    Falls back to the whole image (resized) if no face detector or no face found.
    Returns (None, {}) on decode failure.
    """
    try:
        import cv2 as _cv2
        import numpy as np
    except ImportError:
        return img_bytes, {"warning": "cv2 unavailable — using full image"}

    buf = np.frombuffer(img_bytes, dtype=np.uint8)
    frame = _cv2.imdecode(buf, _cv2.IMREAD_COLOR)
    if frame is None:
        return None, {"error": "failed to decode image"}

    # Try SCRFD face detection via face_recognition_engine
    faces: list[dict] = []
    try:
        from face_recognition_engine import get_face_engine

        engine = get_face_engine()
        if engine and hasattr(engine, "detect_faces"):
            faces = engine.detect_faces(frame)
    except Exception:
        pass

    if not faces:
        # No faces found → fall back to whole image (user may upload a portrait)
        h, w = frame.shape[:2]
        max_side = OSINT_MAX_CROP_PX
        if max(h, w) > max_side:
            scale = max_side / float(max(h, w))
            frame = _cv2.resize(frame, (int(w * scale), int(h * scale)))
        ok, enc = _cv2.imencode(".jpg", frame, [_cv2.IMWRITE_JPEG_QUALITY, 88])
        crop_jpg = enc.tobytes() if ok else None
        return crop_jpg, {
            "warning": "no faces detected — using full image",
            "face_count": 0,
        }

    # Pick largest face by area
    best = max(faces, key=lambda f: f.get("w", 0) * f.get("h", 0))
    h, w = frame.shape[:2]
    bx, by, bw, bh = best["x"], best["y"], best["w"], best["h"]
    # Pad 30% around face
    pad_x = max(8, int(bw * 0.3))
    pad_y = max(8, int(bh * 0.3))
    x1, y1 = max(0, bx - pad_x), max(0, by - pad_y)
    x2, y2 = min(w, bx + bw + pad_x), min(h, by + bh + pad_y)
    if x2 <= x1 or y2 <= y1:
        return None, {"error": "face crop too small"}
    crop = frame[y1:y2, x1:x2]
    # Resize crop
    ch, cw = crop.shape[:2]
    max_side = OSINT_MAX_CROP_PX
    if max(ch, cw) > max_side:
        scale = max_side / float(max(ch, cw))
        crop = _cv2.resize(crop, (int(cw * scale), int(ch * scale)))
    ok, enc = _cv2.imencode(".jpg", crop, [_cv2.IMWRITE_JPEG_QUALITY, 88])
    crop_jpg = enc.tobytes() if ok else None
    meta = {
        "face_count": len(faces),
        "face_box": {"x": bx, "y": by, "w": bw, "h": bh},
        "crop_size": len(crop_jpg) if crop_jpg else 0,
    }
    return crop_jpg, meta


async def _run_training_osint(
    res_id: str, crop_bytes: bytes, name_hint: str, run_footprint: bool, crop_b64: str
) -> None:
    """Background task: run reverse-face search + optional footprint dossier."""
    import base64 as _b64

    jobs = _load_osint_jobs()
    job = next((j for j in jobs if j.get("res_id") == res_id), None)
    if not job:
        return

    try:
        from osint_face_lookup import (
            _reverse_face_search,
            _discover_accounts,
            _IMAGES_BY_ID,
        )

        candidates = await _reverse_face_search(crop_bytes, res_id)
        if candidates:
            best = candidates[0]
            account_result = await _discover_accounts(best["name"], candidates)
            job["name"] = account_result.get("name") or best.get("name")
            job["confidence"] = account_result.get("confidence", 0)
            job["social_accounts"] = account_result.get("social_accounts", [])
            job["sources"] = account_result.get("sources", [])
            job["images"] = _IMAGES_BY_ID.pop(res_id, [])[:8]
            job["status"] = "complete"
        else:
            job["candidates"] = candidates
            job["status"] = "no_match"
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)

    # Footprint dossier
    fp_name = name_hint or job.get("name") or "Unknown"
    if run_footprint and fp_name and fp_name.lower() != "unknown":
        try:
            import footprint as fp_mod

            fp_result = await fp_mod.start_footprint(
                fp_name,
                face_id=res_id,
                crop_b64=crop_b64,
                force=True,
            )
            job["footprint_job_id"] = fp_result.get("job_id")
            job["footprint_started"] = True
        except Exception as e:
            job["footprint_error"] = str(e)

    _save_osint_jobs(jobs)


def _load_resources() -> dict:
    d = {
        "threads": min(max((os.cpu_count() or 8), 1), 64),
        "gpu_pct": 0,
        "batch_size": 8,
        "epochs": 50,
        "periodic": False,
        "interval_hours": 24,
        "game_sched": False,
        "balancer": {"enabled": True, "mode": "split", "persona_weight": 50},
    }
    try:
        if RESOURCE_PROFILE.exists():
            saved = json.loads(RESOURCE_PROFILE.read_text(encoding="utf-8"))
            for k, v in saved.items():
                if k in _RESOURCE_KEYS:
                    d[k] = v
                elif k == "balancer" and isinstance(v, dict):
                    d[k] = v
    except Exception:
        pass
    return d


def _apply_resource_profile(args: list, body: dict, run_type: Optional[str] = None):
    """Merge the saved resource profile into a start/create/refresh run.

    Profile-provided flags are PREPENDED so any explicit flags in `body`
    (from the console) still win when the shell script parses them in order.

    Load balancer ("split" mode): when the *other* pipeline already has a run
    going, this run only receives its weighted share of the CPU threads (and
    YOLO also gets a scaled batch size). GPU is exclusive — first come keeps
    it; the later run yields and goes CPU-only.
    """
    prof = _load_resources()
    bal = _balancer_cfg(prof)
    env: dict = {}
    added: list = []

    if not _opt(body, "epochs") and prof.get("epochs"):
        added += ["--epochs", str(int(prof["epochs"]))]
    if not _opt(body, "batch_size") and prof.get("batch_size"):
        added += ["--batch-size", str(int(prof["batch_size"]))]

    try:
        cp = max(1, int(prof.get("threads", 8)))
    except Exception:
        cp = 8

    # ── Load balancer: fair-share split with the other pipeline ──────────
    other = _other_kind(run_type)
    split_with = False
    if (
        run_type
        and other
        and bal.get("enabled")
        and bal["mode"] == "split"
        and _training_run_alive(other)
    ):
        share = (
            bal["persona_weight"]
            if run_type == "persona"
            else 100 - bal["persona_weight"]
        )
        if share < 100:
            cp = max(1, min(cp, round(cp * share / 100)))
        env["BAL_SPLIT"] = f"other={other} share={share}%"
        split_with = True
        if run_type == "yolo":
            env["BAL_YOLO_BATCH_PCT"] = str(share)

    try:
        gpu_pct = max(0, min(100, int(prof.get("gpu_pct", 0))))
    except Exception:
        gpu_pct = 0
    if gpu_pct > 0:
        env["GPU_PERCENT"] = str(gpu_pct)
        env["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"
        if "--gpu" not in args and not body.get("gpu"):
            if split_with:
                env["BAL_GPU_YIELD"] = other
            else:
                added.append("--gpu")

    env["OMP_NUM_THREADS"] = str(cp)
    env["TORCH_NUM_THREADS"] = str(cp)
    env["MKL_NUM_THREADS"] = str(cp)
    # Profile flags must come AFTER the subcommand (start persona …) but
    # BEFORE any explicit flags in `body` — the shell script is last-wins.
    final_args = list(args[:2]) + added + list(args[2:])
    if split_with and run_type == "yolo":
        try:
            share = int(env.get("BAL_YOLO_BATCH_PCT", 50))
        except Exception:
            share = 50
        final_args = _scale_args(final_args, "--batch-size", share)
    return final_args, env


# ═══ Load balancer: arbitrate persona vs yolo training resources ═══════════

_BALANCER_TASK: Optional[asyncio.Task] = None


def _balancer_cfg(d: Optional[dict] = None) -> dict:
    d = d if d is not None else _load_resources()
    b = d.get("balancer") or {}
    mode = b.get("mode") if b.get("mode") in ("split", "queue") else "split"
    try:
        pw = max(0, min(100, int(b.get("persona_weight", 50))))
    except Exception:
        pw = 50
    return {
        "enabled": bool(b.get("enabled", True)),
        "mode": mode,
        "persona_weight": pw,
    }


def _other_kind(t: Optional[str]) -> Optional[str]:
    if t == "persona":
        return "yolo"
    if t == "yolo":
        return "persona"
    return None


def _scale_args(args: list, flag: str, pct: int) -> list:
    out: list = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == flag and i + 1 < len(args):
            try:
                out += [flag, str(max(1, round(int(args[i + 1]) * pct / 100)))]
            except Exception:
                out += [a, args[i + 1]]
            i += 2
        else:
            out.append(a)
            i += 1
    return out


def _queue_path() -> dict:
    try:
        saved = json.loads(RESOURCE_PROFILE.read_text(encoding="utf-8"))
        return dict(saved.get("balancer", {}).get("queue") or {})
    except Exception:
        return {}


def _set_queue(job: dict) -> None:
    try:
        saved = json.loads(RESOURCE_PROFILE.read_text(encoding="utf-8"))
    except Exception:
        saved = {}
    b = dict(saved.get("balancer") or {})
    b["queue"] = job
    saved["balancer"] = b
    try:
        RESOURCE_PROFILE.parent.mkdir(parents=True, exist_ok=True)
        RESOURCE_PROFILE.write_text(json.dumps(saved, indent=2), encoding="utf-8")
    except Exception:
        pass


def _clear_queue() -> None:
    try:
        saved = json.loads(RESOURCE_PROFILE.read_text(encoding="utf-8"))
    except Exception:
        saved = {}
    b = dict(saved.get("balancer") or {})
    b.pop("queue", None)
    saved["balancer"] = b
    try:
        RESOURCE_PROFILE.write_text(json.dumps(saved, indent=2), encoding="utf-8")
    except Exception:
        pass


def _balancer_alive() -> bool:
    return _training_run_alive("persona") or _training_run_alive("yolo")


async def _launch_queued(job: dict) -> dict:
    t = job.get("type") or "all"
    args = job.get("args") or []
    env = {k: str(v) for k, v in (job.get("env") or {}).items()}
    r = _run_script(args, timeout=20, env=env)
    result = {
        "args": args,
        "rc": r["rc"],
        "output": r["stdout"].strip().splitlines()[-8:],
        "queued_launch": True,
    }
    if r["stderr"].strip():
        result["stderr"] = r["stderr"].strip().splitlines()[-6:]
    if r["rc"] == 0:
        _ledger_start(t, args)
        result["llm_freed"] = await _maybe_free_llm_memory(t)
    return {"ok": r["rc"] == 0, **result}


async def _balancer_worker():
    while True:
        try:
            job = _queue_path()
            if job and job.get("args") and not _balancer_alive():
                _clear_queue()
                try:
                    await _launch_queued(job)
                except Exception as e:
                    print(f"[training] balancer queued launch failed: {e}")
        except Exception:
            pass
        await asyncio.sleep(20)


def _ensure_balancer_worker():
    global _BALANCER_TASK
    if _BALANCER_TASK is None or _BALANCER_TASK.done():
        try:
            _BALANCER_TASK = asyncio.create_task(_balancer_worker())
        except Exception:
            pass


# ═══ Run ledger: real post-session reports + avatar game rewards ═════════════

_RUNS_FILE = WORKSPACE / ".training" / "runs.json"
_REPORTS_DIR = WORKSPACE / ".training" / "reports"
_REPORTER_TASK: Optional[asyncio.Task] = None
_GAME_FILE = WORKSPACE / ".training" / "avatar_game.json"

AVATAR_META: Dict[str, tuple] = {
    "puppy": ("🐶", "Lilly"),
    "fox": ("🦊", "Fox"),
    "cat": ("🐱", "Cat"),
    "bear": ("🐻", "Bear"),
    "bunny": ("🐰", "Bunny"),
    "owl": ("🦉", "Owl"),
    "deer": ("🦌", "Deer"),
    "wolf": ("🐺", "Wolf"),
    "raccoon": ("🦝", "Raccoon"),
}


def _load_runs() -> dict:
    try:
        if _RUNS_FILE.exists():
            return json.loads(_RUNS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_runs(d: dict) -> None:
    try:
        _RUNS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _RUNS_FILE.write_text(json.dumps(d, indent=2), encoding="utf-8")
    except Exception:
        pass


def _ledger_start(t: str, args: list) -> None:
    d = _load_runs()
    d[t] = {"args": args, "started_at": time.time()}
    _save_runs(d)


def _args_avatar(args: list) -> list:
    try:
        i = args.index("--avatar")
        v = args[i + 1]
    except Exception:
        return []
    if isinstance(v, str):
        return [v]
    return list(v)


def _avatar_logs_since(started_at: float) -> list:
    logdir = WORKSPACE / "trainer" / "logs"
    out = []
    try:
        for f in sorted(
            logdir.glob("persona_*.log"), key=lambda p: p.stat().st_mtime, reverse=True
        ):
            mt = f.stat().st_mtime
            if mt >= started_at - 120:
                out.append(f)
    except Exception:
        pass
    return out


def _parse_persona_log(path: Path) -> dict:
    """Extract REAL signals from a persona training log (no synthesis)."""
    info = {
        "avatars": [],
        "datasets": [],
        "losses": [],
        "steps": None,
        "exported_gguf": False,
        "errors": [],
        "tail": [],
    }
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return info
    info["tail"] = lines[-6:]
    for ln in lines:
        if "Training " in ln and "(" in ln and ")" in ln:
            try:
                info["avatars"].append(ln.split("(")[1].split(")")[0].strip())
            except Exception:
                pass
        low = ln.lower()
        if "dataset" in low and ("download" in low or "cache" in low):
            for tok in (
                "persona-chat",
                "sharegpt",
                "openassistant",
                "dolly",
                "fineweb",
            ):
                if tok in low:
                    info["datasets"].append(tok)
        if "loss" in low:
            import re as _re

            m = _re.findall(r"loss[=\s:]+([0-9.]+)", ln.lower())
            if m:
                info["losses"].append(float(m[-1]))
            s = _re.search(r"step[=\s:]+([0-9]+)", ln.lower())
            if s:
                info["steps"] = max(info["steps"] or 0, int(s.group(1)))
        if "gguf" in low and ("saved" in low or "export" in low or "✓" in ln):
            info["exported_gguf"] = True
        if ("error" in low or "failed" in low) and "0" not in low[:8]:
            info["errors"].append(ln[:160])
    return info


def _persona_artifacts(avatars: list) -> dict:
    out = {}
    for a in avatars:
        d = WORKSPACE / "trained_avatars" / a
        ggs = (
            sorted(d.glob("gguf/*.gguf"), key=lambda p: p.stat().st_mtime)
            if d.exists()
            else []
        )
        if d.exists():
            dirs = [x for x in d.rglob("*") if x.is_file()]
            out[a] = {
                "dir": str(d),
                "files": len(dirs),
                "gguf": [
                    {
                        "name": g.name,
                        "bytes": g.stat().st_size,
                        "mtime": g.stat().st_mtime,
                    }
                    for g in ggs
                ],
            }
    return out


def _yolo_dataset() -> dict:
    try:
        r = httpx.get(f"{TRAINER_SERVER}/api/trainer/stats", timeout=4)
        j = r.json()
        return {
            "total_detections": j.get("total_detections", 0),
            "samples": j.get("sample_count", j.get("total_detections", 0)),
            "class_distribution": j.get("class_distribution", {}),
            "accuracy_estimate": j.get("accuracy_estimate"),
        }
    except Exception:
        return {"samples": 0, "class_distribution": {}}


def _yolo_trainer_state() -> dict:
    try:
        r = httpx.get(f"{TRAINER_SERVER}/api/trainer/status", timeout=4)
        return r.json()
    except Exception:
        return {"status": "down", "progress": {}}


def _confidence(score: int, reasons: list) -> dict:
    level = "high" if score >= 4 else ("medium" if score >= 2 else "low")
    return {"level": level, "score": score, "evidence": reasons}


async def _build_report(t: str, ent: dict) -> dict:
    started = ent.get("started_at", time.time())
    now = time.time()
    args = ent.get("args") or []
    report = {
        "id": f"{int(started)}_{t}",
        "type": t,
        "started_at": started,
        "ended_at": now,
        "duration_s": round(now - started, 1),
        "args": args,
        "data_sources": [],
        "what_trained": [],
        "stats": {},
        "outcome": "…",
        "confidence": _confidence(0, []),
    }
    if t == "persona":
        avs = _args_avatar(args) or ["all"]
        report["what_trained"] = avs
        logs = _avatar_logs_since(started)
        logrep = _parse_persona_log(logs[0]) if logs else {}
        report["data_sources"].append(
            {
                "name": "persona dialogue + backgrounds (trainer/dataset cache)",
                "origin": "HuggingFace (persona-chat etc.) / trainer/persona_backgrounds.py",
                "cache_dir": str(WORKSPACE / "trained_avatars" / "cache")
                if (WORKSPACE / "trained_avatars" / "cache").exists()
                else None,
                "datasets_seen": sorted(set(logrep.get("datasets", []))),
            }
        )
        arts = _persona_artifacts(["all" if avs == ["all"] else a for a in avs])
        stats: dict = {
            "log": str(logs[0]) if logs else None,
            "avatars_in_log": logrep.get("avatars", []),
            "datasets": sorted(set(logrep.get("datasets", []))),
            "steps_completed": logrep.get("steps"),
            "losses": logrep.get("losses"),
            "loss_first": logrep["losses"][0] if logrep.get("losses") else None,
            "loss_last": logrep["losses"][-1] if logrep.get("losses") else None,
            "exported_gguf": logrep.get("exported_gguf", False),
            "errors": logrep.get("errors", []),
        }
        if stats["loss_first"] and stats["loss_last"]:
            if stats["loss_first"]:
                stats["loss_delta_pct"] = round(
                    (stats["loss_last"] - stats["loss_first"])
                    / stats["loss_first"]
                    * 100,
                    1,
                )
        # real artifacts actually modified during this window
        changed = {}
        for a, info in arts.items():
            new = [g for g in info["gguf"] if g["mtime"] >= started - 120]
            if new:
                changed[a] = {"gguf": new[0]["name"], "bytes": new[0]["bytes"]}
        stats["artifacts"] = changed
        report["stats"] = stats
        # ── outcome + evidence-based confidence (no synthetic gains) ──
        score, ev, out = 0, [], []
        if changed:
            score += 2
            ev.append(f"model artifact produced for {', '.join(changed)}")
            out.append(f"new GGUF for {', '.join(a for a in changed)}")
        if stats["loss_first"] and stats["loss_last"]:
            d = stats["loss_delta_pct"]
            if d is not None:
                if d >= 20:
                    score, mode = score + 2, "strong"
                elif d >= 10:
                    score, mode = score + 1, "modest"
                elif d < 0:
                    score, mode = score - 1, "diverged"
                else:
                    mode = "flat"
                ev.append(
                    f"training loss {stats['loss_first']} → {stats['loss_last']} ({d}%)"
                )
                out.append(f"loss {mode} ({d}%)")
        if logrep.get("errors"):
            score -= 1
            ev.append(
                f"{len(logrep['errors'])} error line{'s' if len(logrep['errors']) > 1 else ''} in log"
            )
        if not changed and not stats["losses"]:
            ev.append(
                "no steps completed — run did not reach training (download/startup bottleneck?)"
            )
            out.append("no model produced")
        if stats.get("exported_gguf") and changed:
            score += 1
            ev.append("Unsloth GGUF export confirmed in log")
        report["outcome"] = "; ".join(out) if out else "no measurable change"
        report["confidence"] = _confidence(score, ev)
    else:  # yolo
        st = _yolo_trainer_state()
        ds = _yolo_dataset()
        hist = []
        try:
            h = httpx.get(f"{TRAINER_SERVER}/api/trainer/history", timeout=4).json()
            hist = h.get("detections", []) or []
        except Exception:
            pass
        report["what_trained"] = sorted(ds.get("class_distribution", {}).keys())
        report["data_sources"].append(
            {
                "name": "self-collected detection samples + imported phone photos",
                "origin": "camera detections (auto_collect) / termux gallery import",
                "sample_count": ds.get("samples", 0),
                "class_distribution": ds.get("class_distribution", {}),
            }
        )
        prog = st.get("progress", {})
        stats = {
            "status": st.get("status"),
            "phase": prog.get("phase"),
            "epochs_completed": prog.get("epoch"),
            "total_epochs": prog.get("total_epochs"),
            "loss": prog.get("loss"),
            "last_training": st.get("last_training"),
            "sample_count": ds.get("samples", 0),
            "accuracy_estimate": ds.get("accuracy_estimate"),
            "min_samples_for_training": st.get("config", {}).get(
                "min_samples_for_training"
            ),
        }
        report["stats"] = stats
        score, ev, out = 0, [], []
        if ds.get("samples", 0) >= (
            st.get("config", {}).get("min_samples_for_training") or 100
        ):
            score += 2
            ev.append(f"dataset ready: {ds['samples']} samples ≥ threshold")
        else:
            ev.append(
                f"only {ds.get('samples', 0)} samples (< threshold {st.get('config', {}).get('min_samples_for_training', 100)})"
            )
        if stats["phase"] == "error" or stats["status"] == "error":
            score -= 1
            ev.append("training run ended in error phase")
            out.append("run errored (no/minimal samples?)")
        if prog.get("epoch"):
            score += 1
            ev.append(f"{prog['epoch']} of {prog['total_epochs']} epochs completed")
            out.append(f"{prog['epoch']}/{prog['total_epochs']} epochs")
        if stats["loss"]:
            score += 1
            ev.append(f"final training loss {stats['loss']}")
            out.append(f"loss {stats['loss']}")
        if not out:
            out.append("no completed training run observed this window")
        report["outcome"] = "; ".join(out)
        report["confidence"] = _confidence(score, ev)
    return report


def _save_report(rep: dict) -> None:
    try:
        _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        (_REPORTS_DIR / f"{rep['id']}.json").write_text(
            json.dumps(rep, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


async def _run_reporter():
    yolo_was_training = False
    while True:
        try:
            # YOLO auto-train (not launched via console) is still real — capture it.
            try:
                yst = httpx.get(
                    f"{TRAINER_SERVER}/api/trainer/status", timeout=4
                ).json()
                yolo_training = yst.get("status") == "training"
            except Exception:
                yolo_training = False
            runs = _load_runs()
            for t in ("persona", "yolo"):
                ent = runs.get(t)
                alive = _training_run_alive(t)
                if ent and not alive:
                    rep = await _build_report(t, ent)
                    _save_report(rep)
                    _avatar_game_stamp(rep)
                    runs.pop(t)
                    _save_runs(runs)
            if yolo_was_training and not yolo_training:
                runs = _load_runs()
                if "yolo" not in runs:
                    _ledger_start("yolo", ["auto", "yolo", "self-train"])
                ent = _load_runs().get("yolo")
                if ent:
                    rep = await _build_report("yolo", ent)
                    _save_report(rep)
                    _avatar_game_stamp(rep)
            yolo_was_training = yolo_training
        except Exception:
            pass
        await asyncio.sleep(30)


# ── Avatar game: fair scheduling over REAL run history + artifacts ──────────
def _avatar_game_state() -> dict:
    now = time.time()
    reports = []
    if _REPORTS_DIR.exists():
        for f in _REPORTS_DIR.glob("persona_*.json"):
            try:
                reports.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                pass
    trained_counts = {k: 0 for k in AVATAR_META}
    last_trained: dict = {k: None for k in AVATAR_META}
    for rep in reports:
        for a in rep.get("what_trained", []):
            if a == "all":
                for k in AVATAR_META:
                    trained_counts[k] += 1
                continue
            if a in trained_counts:
                trained_counts[a] += 1
                if rep.get("ended_at"):
                    last_trained[a] = max(last_trained[a] or 0, rep["ended_at"])
    board = {}
    for k, (emoji, name) in AVATAR_META.items():
        d = WORKSPACE / "trained_avatars" / k
        ggs = (
            sorted(d.glob("gguf/*.gguf"), key=lambda p: p.stat().st_mtime)
            if d.exists()
            else []
        )
        model = ggs[-1] if ggs else None
        mt = model.stat().st_mtime if model else None
        last = max([x for x in (last_trained[k], mt, 0) if x] or [0])
        stale_h = (now - last) / 3600 if last else None
        never = trained_counts[k] == 0
        urgency = min(100.0, 50.0 * (min(stale_h or 24.0 * 30, 24 * 90) / (24 * 90)))
        need = 35.0 / (
            trained_counts[k] + 1
        )  # fairness: never-trained starves the queue
        bonus = 15.0 if never and not model else 0.0
        bid = round(min(100.0, urgency + need + bonus))
        board[k] = {
            "name": name,
            "emoji": emoji,
            "bid": bid,
            "trained_count": trained_counts[k],
            "never_trained": never,
            "model_exists": bool(model),
            "model_gguf": model.name if model else None,
            "model_bytes": model.stat().st_size if model else None,
            "stale_hours": round(stale_h, 1) if stale_h is not None else None,
            "last_trained": last or None,
            "reports": sum(
                1
                for r in reports
                if k in r.get("what_trained", []) or r.get("what_trained") == ["all"]
            ),
        }
    ranked = sorted(
        board.keys(), key=lambda k: (-board[k]["bid"], board[k]["name"].lower())
    )
    return {
        "board": board,
        "rank": ranked,
        "next": AVATAR_META[ranked[0]][0] + " " + AVATAR_META[ranked[0]][1]
        if ranked
        else None,
    }


def _avatar_game_stamp(rep: dict) -> None:
    """Game rewards are only ever awarded from REAL completed reports."""
    if rep.get("type") != "persona":
        return
    try:
        if rep.get("confidence", {}).get("score", 0) < 2:
            return  # no evidence the run actually improved anything
        st = (
            json.loads(_GAME_FILE.read_text(encoding="utf-8"))
            if _GAME_FILE.exists()
            else {}
        )
        for a in rep.get("what_trained", []):
            keys = list(AVATAR_META) if a == "all" else [a]
            for k in keys:
                st[k] = max(st.get(k, 0), rep.get("ended_at", 0))
        _GAME_FILE.parent.mkdir(parents=True, exist_ok=True)
        _GAME_FILE.write_text(json.dumps(st, indent=2), encoding="utf-8")
    except Exception:
        pass


def _pick_next_avatar() -> Optional[str]:
    st = _avatar_game_state()
    rk = st.get("rank")
    return rk[0] if rk else None


def _ensure_reporter():
    global _REPORTER_TASK
    if _REPORTER_TASK is None or _REPORTER_TASK.done():
        try:
            _REPORTER_TASK = asyncio.create_task(_run_reporter())
        except Exception:
            pass


@router.get("/api/training/reports")
async def training_reports(request: Request):
    user = await _allowed_user(request)
    if not user:
        return _deny()
    _ensure_reporter()
    reps = []
    if _REPORTS_DIR.exists():
        for f in sorted(
            _REPORTS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
        ):
            try:
                j = json.loads(f.read_text(encoding="utf-8"))
                reps.append(
                    {
                        "id": j.get("id"),
                        "type": j.get("type"),
                        "ended_at": j.get("ended_at"),
                        "duration_s": j.get("duration_s"),
                        "what_trained": j.get("what_trained"),
                        "outcome": j.get("outcome"),
                        "confidence": j.get("confidence", {}).get("level"),
                    }
                )
            except Exception:
                pass
    return {"ok": True, "count": len(reps), "reports": reps}


@router.get("/api/training/reports/{rid}")
async def training_report_detail(request: Request, rid: str):
    user = await _allowed_user(request)
    if not user:
        return _deny()
    try:
        p = _REPORTS_DIR / f"{rid}.json"
        if p.exists() and p.is_file():
            j = json.loads(p.read_text(encoding="utf-8"))
            return {"ok": True, "report": j}
    except Exception:
        pass
    return JSONResponse(status_code=404, content={"error": "report not found"})


@router.get("/api/training/game")
async def training_game(request: Request):
    user = await _allowed_user(request)
    if not user:
        return _deny()
    d = _avatar_game_state()
    d["game_sched"] = bool(_load_resources().get("game_sched"))
    return {"ok": True, **d}


@router.post("/api/training/game/next")
async def training_game_next(request: Request):
    user = await _allowed_user(request)
    if not user:
        return _deny()
    pick = _pick_next_avatar()
    prof = _load_resources()
    return {
        "ok": True,
        "avatar": pick,
        "game_sched": bool(prof.get("game_sched")),
        "board": _avatar_game_state().get("board", {}).get(pick) if pick else None,
    }


# ── Auth gate ────────────────────────────────────────────────────────────────
async def _allowed_user(request: Request) -> Optional[dict]:
    """Return the user dict if a verified laurencekidney@gmail.com, else None."""
    if not AUTH_AVAILABLE or get_current_user is None:
        return None  # cannot verify identity -> deny
    try:
        user = await get_current_user(request)
    except Exception:
        return None
    if not user:
        return None
    email = (user.get("email") or "").strip().lower()
    if email == ALLOWED_EMAIL:
        return user
    return None


def _deny() -> JSONResponse:
    return JSONResponse(
        status_code=403,
        content={
            "error": "forbidden",
            "message": f"Access restricted to {ALLOWED_EMAIL}. Sign in with that Google account.",
        },
    )


# ── backend helpers ──────────────────────────────────────────────────────────
def _run_script(args, timeout: int = 15, env: Optional[Dict[str, str]] = None) -> dict:
    """Run training_manager.sh and capture output."""
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    try:
        p = subprocess.run(
            ["bash", str(SCRIPT)] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(WORKSPACE),
            env=full_env,
        )
        return {
            "rc": p.returncode,
            "stdout": (p.stdout or "")[-6000:],
            "stderr": (p.stderr or "")[-3000:],
        }
    except subprocess.TimeoutExpired:
        return {"rc": -1, "stdout": "", "stderr": "command timed out"}
    except Exception as e:  # noqa: BLE001
        return {"rc": -255, "stdout": "", "stderr": str(e)}


def _script_json(args, timeout: int = 15):
    """Run the script and parse its JSON stdout."""
    r = _run_script(args, timeout=timeout)
    try:
        return json.loads(r["stdout"])
    except Exception:
        return {
            "error": "bad JSON from manager",
            "raw": r["stdout"][-500:],
            "stderr": r["stderr"],
        }


def _opt(body: Dict[str, Any], key: str) -> Optional[str]:
    v = body.get(key)
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def _h(html: str) -> str:
    import html as _h

    return _h.escape(html)


# ── Page ─────────────────────────────────────────────────────────────────────
@router.get("/training", response_class=HTMLResponse)
async def training_page(request: Request):
    user = await _allowed_user(request)
    if not user:
        # Not signed in → send the owner through Google sign-in (via Auth0) and
        # drop them back on /training once the session cookie is set.
        return RedirectResponse("/api/auth0/login?returnTo=/training", status_code=302)
    page_path = WORKSPACE / "training.html"
    if not page_path.exists():
        return HTMLResponse("training.html not found", status_code=404)
    content = page_path.read_text(encoding="utf-8")
    return HTMLResponse(
        content=content,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@router.get("/api/training/auth")
async def training_auth(request: Request):
    """Who is the signed-in user -> only the owner gets ok:true."""
    user = await _allowed_user(request)
    if not user:
        return _deny()
    return {"ok": True, "email": user.get("email", ""), "name": user.get("name", "")}


# ── Live data ────────────────────────────────────────────────────────────────
@router.get("/api/training/status")
async def training_status(request: Request):
    user = await _allowed_user(request)
    if not user:
        return _deny()
    return _script_json(["json", "status"])


@router.get("/api/training/list")
async def training_list(request: Request):
    user = await _allowed_user(request)
    if not user:
        return _deny()
    return _script_json(["json", "list"])


@router.get("/api/training/logs")
async def training_logs(request: Request, type: str = "all", lines: int = 120):
    user = await _allowed_user(request)
    if not user:
        return _deny()
    if type not in ("persona", "yolo", "all", "systemd"):
        type = "all"
    lines = max(10, min(int(lines), 2000))
    return _script_json(["json", "logs", type, str(lines)])


@router.get("/api/training/reviews")
async def training_reviews(request: Request, type: str = "all", limit: int = 10):
    """Orchestrator thought + review records for the training pipelines."""
    user = await _allowed_user(request)
    if not user:
        return _deny()
    if type not in ("persona", "yolo", "all"):
        type = "all"
    limit = max(1, min(int(limit), 100))
    return _script_json(["json", "reviews", type, str(limit)])


# ── Control ──────────────────────────────────────────────────────────────────
@router.post("/api/training/start")
async def training_start(request: Request):
    user = await _allowed_user(request)
    if not user:
        return _deny()
    try:
        body = await request.json()
    except Exception:
        body = {}
    t = _opt(body, "type") or "all"
    if t not in ("persona", "yolo", "all"):
        return JSONResponse(status_code=400, content={"error": "bad type"})

    args = ["start", t]
    if _opt(body, "avatar"):
        args += ["--avatar", str(body["avatar"])]
    if body.get("quick"):
        args += ["--quick"]
    if body.get("gpu"):
        args += ["--gpu"]
    if _opt(body, "output_dir"):
        args += ["--output-dir", str(body["output_dir"])]
    if _opt(body, "epochs"):
        args += ["--epochs", str(body["epochs"])]
    if _opt(body, "batch_size"):
        args += ["--batch-size", str(body["batch_size"])]
    if _opt(body, "lr"):
        args += ["--lr", str(body["lr"])]
    if _opt(body, "import_photos"):
        args += ["--import-photos", str(body["import_photos"])]
    if _opt(body, "name"):
        args += ["--name", str(body["name"])]

    if _opt(body, "gguf_quant"):
        args += ["--gguf-quant", str(body["gguf_quant"])]

    args, extra_env = _apply_resource_profile(args, body, run_type=t)
    if body.get("llm"):
        extra_env["TRAINING_LLM_REVIEW"] = "1"

    # ── Avatar game: when persona is started without an explicit avatar and
    #    game scheduling is on, train the avatar with the highest real bid.
    if (
        t == "persona"
        and not _opt(body, "avatar")
        and _load_resources().get("game_sched")
    ):
        pick = _pick_next_avatar()
        if pick:
            args += ["--avatar", pick]

    # ── Load balancer ("queue" mode): don't start if the other side is busy.
    bal = _balancer_cfg()
    if bal.get("enabled") and bal["mode"] == "queue" and t != "all":
        other = _other_kind(t)
        if other and _training_run_alive(other):
            job = {
                "type": t,
                "args": args,
                "env": {k: str(v) for k, v in extra_env.items()},
                "queued_at": _iso_now(),
                "waiting_on": other,
            }
            _set_queue(job)
            _ensure_balancer_worker()
            return {
                "ok": True,
                "queued": True,
                "waiting_on": other,
                "args": args,
                "note": f"{other} is training — your {t} run is queued and launches automatically when it finishes",
            }

    _ensure_balancer_worker()
    _ensure_reporter()
    r = _run_script(args, timeout=20, env=extra_env)
    result = {
        "args": args,
        "rc": r["rc"],
        "output": r["stdout"].strip().splitlines()[-8:],
        "llm_review": bool(extra_env),
    }
    if r["stderr"].strip():
        result["stderr"] = r["stderr"].strip().splitlines()[-6:]
    if r["rc"] == 0:
        _ledger_start(t, args)
        result["llm_freed"] = await _maybe_free_llm_memory(t)
    return {"ok": r["rc"] == 0, **result}


@router.post("/api/training/stop")
async def training_stop(request: Request):
    user = await _allowed_user(request)
    if not user:
        return _deny()
    try:
        body = await request.json()
    except Exception:
        body = {}
    t = _opt(body, "type") or "all"
    if t not in ("persona", "yolo", "all"):
        return JSONResponse(status_code=400, content={"error": "bad type"})
    r = _run_script(["stop", t], timeout=20)
    _clear_queue()
    return {
        "ok": r["rc"] == 0,
        "rc": r["rc"],
        "output": r["stdout"].strip().splitlines()[-8:],
        "queue_cleared": True,
    }


@router.post("/api/training/refresh")
async def training_refresh(request: Request):
    user = await _allowed_user(request)
    if not user:
        return _deny()
    try:
        body = await request.json()
    except Exception:
        body = {}
    t = _opt(body, "type") or "all"
    if t not in ("persona", "yolo", "all"):
        return JSONResponse(status_code=400, content={"error": "bad type"})

    args = ["refresh", t]
    if body.get("force"):
        args += ["--force"]
    if body.get("reset_progress"):
        args += ["--reset-progress"]
    if _opt(body, "import_photos"):
        args += ["--import-photos", str(body["import_photos"])]
    if _opt(body, "epochs"):
        args += ["--epochs", str(body["epochs"])]
    if body.get("foreground"):
        args += ["--foreground"]

    args, extra_env = _apply_resource_profile(args, body, run_type=t)
    _ensure_balancer_worker()
    _ensure_reporter()
    r = _run_script(args, timeout=20, env=extra_env)
    result = {
        "ok": r["rc"] == 0,
        "rc": r["rc"],
        "output": r["stdout"].strip().splitlines()[-10:],
    }
    if r["rc"] == 0:
        _ledger_start(t, args)
        result["llm_freed"] = await _maybe_free_llm_memory(t)
    return result


@router.post("/api/training/create")
async def training_create(request: Request):
    user = await _allowed_user(request)
    if not user:
        return _deny()
    try:
        body = await request.json()
    except Exception:
        body = {}
    t = _opt(body, "type") or "persona"
    if t not in ("persona", "yolo"):
        return JSONResponse(status_code=400, content={"error": "bad type"})

    args = ["create", t, "--name", _opt(body, "name") or f"job-{t}"]
    if _opt(body, "avatar"):
        args += ["--avatar", str(body["avatar"])]
    if body.get("quick"):
        args += ["--quick"]
    if body.get("gpu"):
        args += ["--gpu"]
    if _opt(body, "epochs"):
        args += ["--epochs", str(body["epochs"])]
    if _opt(body, "batch_size"):
        args += ["--batch-size", str(body["batch_size"])]
    if _opt(body, "lr"):
        args += ["--lr", str(body["lr"])]
    if _opt(body, "output_dir"):
        args += ["--output-dir", str(body["output_dir"])]
    if _opt(body, "import_photos"):
        args += ["--import-photos", str(body["import_photos"])]

    args, extra_env = _apply_resource_profile(args, body, run_type=t)
    if body.get("llm"):
        extra_env["TRAINING_LLM_REVIEW"] = "1"
    if _opt(body, "gguf_quant"):
        args += ["--gguf-quant", str(body["gguf_quant"])]

    _ensure_balancer_worker()
    _ensure_reporter()
    r = _run_script(args, timeout=25, env=extra_env)
    result = {
        "ok": r["rc"] == 0,
        "rc": r["rc"],
        "output": r["stdout"].strip().splitlines()[-8:],
        "llm_review": bool(extra_env),
    }
    if r["rc"] == 0:
        _ledger_start(t, args)
        result["llm_freed"] = await _maybe_free_llm_memory(t)
    return result


@router.post("/api/training/deploy")
async def training_deploy(request: Request):
    user = await _allowed_user(request)
    if not user:
        return _deny()
    try:
        body = await request.json()
    except Exception:
        body = {}
    t = _opt(body, "type") or "yolo"
    if t not in ("persona", "yolo"):
        return JSONResponse(status_code=400, content={"error": "bad type"})
    args = ["deploy", t]
    if _opt(body, "model_path"):
        args += ["--model-path", str(body["model_path"])]
    r = _run_script(args, timeout=30)
    return {
        "ok": r["rc"] == 0,
        "rc": r["rc"],
        "output": r["stdout"].strip().splitlines()[-10:],
    }


@router.post("/api/training/review")
async def training_review(request: Request):
    """Run an orchestrator review (thought + verdict) on the latest artifacts."""
    user = await _allowed_user(request)
    if not user:
        return _deny()
    try:
        body = await request.json()
    except Exception:
        body = {}
    t = _opt(body, "type") or "persona"
    if t not in ("persona", "yolo"):
        return JSONResponse(status_code=400, content={"error": "bad type"})

    args = ["review", t]
    avatar = _opt(body, "avatar")
    if avatar and t == "persona":
        args += ["--avatar", str(avatar)]

    extra_env = {}
    if body.get("llm"):
        extra_env["TRAINING_LLM_REVIEW"] = "1"

    r = _run_script(args, timeout=150, env=extra_env)
    review = None
    try:
        review = json.loads(r["stdout"])
    except Exception:
        pass
    return {
        "ok": r["rc"] == 0,
        "rc": r["rc"],
        "review": review,
        "output": r["stdout"].strip().splitlines()[-6:],
        "llm_review": bool(extra_env),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Vision object toggles + trainer config + live progress
# ═══════════════════════════════════════════════════════════════════════════


def _read_class_toggles() -> dict:
    """{lowerclass: bool} currently saved — same file the vision server enforces."""
    try:
        cfg = json.loads(CLASS_CONFIG.read_text(encoding="utf-8"))
        return cfg.get("classes", {}) or {}
    except Exception:
        return {}


def _read_class_default() -> bool:
    try:
        cfg = json.loads(CLASS_CONFIG.read_text(encoding="utf-8"))
        return bool(cfg.get("default", True))
    except Exception:
        return True


async def _trainer_get(path: str, timeout: float = 5.0) -> dict:
    """Small async GET proxy to the YOLO trainer API (:8199)."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(f"{TRAINER_SERVER}{path}")
            return (
                r.json() if r.status_code == 200 else {"error": f"HTTP {r.status_code}"}
            )
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:160]}


async def _trainer_post(path: str, body: Optional[dict], timeout: float = 8.0) -> dict:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(f"{TRAINER_SERVER}{path}", json=body or {})
            return (
                r.json() if r.status_code < 300 else {"error": f"HTTP {r.status_code}"}
            )
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:160]}


async def _vision_classes_raw() -> Optional[list]:
    """Class list straight from the vision server's loaded model."""
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            r = await client.get(f"{VISION_SERVER}/api/vision/classes")
            if r.status_code == 200:
                data = r.json()
                return data.get("classes")
    except Exception:
        pass
    return None


def _sample_counts() -> dict:
    """{lower_label: n} from training_data/samples/<label>/*.jpg."""
    counts: dict = {}
    sd = WORKSPACE / "training_data" / "samples"
    if sd.is_dir():
        for lab in sd.iterdir():
            if not lab.is_dir():
                continue
            n = len(list(lab.glob("*.jpg"))) + len(list(lab.glob("*.png")))
            if n:
                counts[lab.name.strip().lower()] = n
    return counts


@router.get("/api/training/classes")
async def training_classes(request: Request):
    """Vision object classes with enable toggles + sample/detection counts."""
    user = await _allowed_user(request)
    if not user:
        return _deny()

    toggles = _read_class_toggles()
    default = _read_class_default()
    raw = await _vision_classes_raw()

    if raw:
        classes = [
            {
                "key": c.get("key", ""),
                "name": c.get("name", c.get("key", "")),
                "enabled": toggles.get(str(c.get("key", "")).strip().lower(), default),
            }
            for c in raw
            if c.get("name")
        ]
        source = "vision-server"
        model = None
    else:
        seen = {}
        for n in FALLBACK_CLASSES:
            seen.setdefault(str(n).strip().lower(), str(n))
        classes = [
            {"key": k, "name": v, "enabled": toggles.get(k, default)}
            for k, v in seen.items()
        ]
        source = "fallback"
        model = None

    counts = _sample_counts()
    stats = await _trainer_get("/api/trainer/stats")
    dist = (stats or {}).get("class_distribution", {}) or {}

    for c in classes:
        key = c["key"]
        c["samples"] = counts.get(key, 0)
        c["detections"] = int(dist.get(key, 0))

    return {
        "ok": True,
        "source": source,
        "count": len(classes),
        "default_enabled": default,
        "updated_at": CLASS_CONFIG.stat().st_mtime if CLASS_CONFIG.exists() else None,
        "classes": classes,
        "stats": {
            "sample_count": sum(counts.values()),
            "detection_count": sum(dist.values()),
        },
    }


@router.post("/api/training/classes")
async def training_classes_save(request: Request):
    """Save vision object toggles → vision_class_config.json (enforced live)."""
    user = await _allowed_user(request)
    if not user:
        return _deny()
    try:
        body = await request.json()
    except Exception:
        body = {}

    new_toggles = body.get("classes")
    if not isinstance(new_toggles, dict):
        return JSONResponse(
            status_code=400, content={"error": "classes must be an object"}
        )

    cleaned = {}
    for k, v in new_toggles.items():
        key = str(k).strip().lower()
        if key and key != "default":
            cleaned[key] = bool(v)

    try:
        old = (
            json.loads(CLASS_CONFIG.read_text(encoding="utf-8"))
            if CLASS_CONFIG.exists()
            else {}
        )
    except Exception:
        old = {}
    old.setdefault("default", True)
    if "default" in body:
        old["default"] = bool(body["default"])
    old["classes"] = cleaned
    old["updated_at"] = _iso_now()

    tmp = CLASS_CONFIG.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(old, indent=2), encoding="utf-8")
    tmp.replace(CLASS_CONFIG)

    on = sum(1 for v in cleaned.values() if v)
    return {
        "ok": True,
        "saved": len(cleaned),
        "enabled": on,
        "disabled": len(cleaned) - on,
        "default_enabled": old["default"],
    }


def _iso_now() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).isoformat()


@router.get("/api/training/config")
async def training_config_get(request: Request):
    """Lilly-AI / trainer config for the console (mirrors :8199)."""
    user = await _allowed_user(request)
    if not user:
        return _deny()
    data = await _trainer_get("/api/trainer/config")
    return {"ok": "error" not in data, **data}


@router.post("/api/training/config")
async def training_config_save(request: Request):
    """Persist trainer config toggles (only whitelisted keys)."""
    user = await _allowed_user(request)
    if not user:
        return _deny()
    try:
        body = await request.json()
    except Exception:
        body = {}
    patch = body.get("config", body)
    if not isinstance(patch, dict):
        return JSONResponse(
            status_code=400, content={"error": "config must be an object"}
        )

    filtered = {k: v for k, v in patch.items() if k in TRAINER_CONFIG_KEYS}
    if not filtered:
        return JSONResponse(status_code=400, content={"error": "no allowed keys"})

    data = await _trainer_post("/api/trainer/config", filtered)
    return {"ok": "error" not in data, "saved": list(filtered.keys()), **data}


@router.post("/api/training/import-photos")
async def training_import_photos(request: Request):
    """Pull fresh training samples from the phone gallery (via :8199 bridge)."""
    user = await _allowed_user(request)
    if not user:
        return _deny()
    try:
        body = await request.json()
    except Exception:
        body = {}

    payload = {}
    for key in ("phone_url", "max_photos", "min_confidence", "max_confidence"):
        if _opt(body, key) is not None:
            payload[key] = body[key]
    if "label_filter" in body and isinstance(body.get("label_filter"), list):
        payload["label_filter"] = [
            str(x) for x in body["label_filter"] if str(x).strip()
        ]

    data = await _trainer_post("/api/trainer/import-photos", payload, timeout=120.0)
    return {"ok": "error" not in data, **data}


async def _vision_get(path: str, timeout: float = 5.0) -> dict:
    """Tiny GET proxy to the vision server (:8198)."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(f"{VISION_SERVER}{path}")
            return (
                r.json() if r.status_code == 200 else {"error": f"HTTP {r.status_code}"}
            )
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:140]}


@router.get("/api/training/observe")
async def training_observe(request: Request):
    """Live observation feed + real sample-cache state (vision :8198 / trainer :8199)."""
    user = await _allowed_user(request)
    if not user:
        return _deny()

    vision = await _vision_get("/api/vision/observations")
    vstatus = await _vision_get("/api/vision/status")
    tcfg = await _trainer_get("/api/trainer/config")
    tstats = await _trainer_get("/api/trainer/stats")
    tsamp = await _trainer_get("/api/trainer/samples")

    observations = vision.get("observations", []) if isinstance(vision, dict) else []
    cfg = tcfg if isinstance(tcfg, dict) else {}
    cfg_inner = cfg.get("config", cfg) if isinstance(cfg, dict) else {}
    stats = tstats if isinstance(tstats, dict) else {}
    samp = tsamp if isinstance(tsamp, dict) else {}
    dist = stats.get("class_distribution", {}) or {}
    labels = samp.get("labels", []) or []
    total = int(samp.get("total", 0) or 0)
    min_s = int(samp.get("min_samples_for_training", 100) or 100)
    readiness = round(min(100, total / min_s * 100), 1) if min_s else 100.0

    return {
        "ok": True,
        "generated_at": _iso_now(),
        "vision": {
            "online": isinstance(vstatus, dict) and "error" not in vstatus,
            "status": vstatus if isinstance(vstatus, dict) else {},
        },
        "observations": observations,
        "trainer": {
            "online": isinstance(tcfg, dict) and "error" not in tcfg,
            "config": cfg_inner,
            "stats": stats if isinstance(stats, dict) else {},
        },
        "cache": {
            "total": total,
            "min_samples_for_training": min_s,
            "readiness_pct": readiness,
            "labels": labels,
            "detections_by_label": dist,
        },
    }


@router.post("/api/training/observe/cache")
async def training_observe_cache(request: Request):
    """Manual observe/cache actions, all real:
    {action:"live"}            → phone screen capture → vision detect → forward/cache
    {action:"set_auto_collect", enabled:bool} → trainer config toggle
    {action:"train_if_ready"}  → trainer auto-check (real threshold gate)
    """
    user = await _allowed_user(request)
    if not user:
        return _deny()
    try:
        body = await request.json()
    except Exception:
        body = {}
    action = str(body.get("action", "")).strip().lower()

    if action == "live":
        # Frame sources, in order: phone screen → vision last real camera frame.
        phone = os.environ.get("SENSOR_SERVER_URL", "http://100.115.234.87:8099")
        img = None
        src_note = ""
        # 1) Phone current screen (newer sensor-server builds only).
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                r = await client.get(f"{phone}/screen/capture?force=true")
                if r.status_code == 200:
                    data = r.json()
                    img = (data or {}).get("image_base64")
                    if img:
                        src_note = "phone screen"
        except Exception:  # noqa: BLE001
            pass
        # 2) Fall back to the vision server's last real camera frame.
        if not img:
            lf = await _vision_get("/api/vision/last_frame")
            if isinstance(lf, dict) and lf.get("image_b64"):
                img = lf["image_b64"]
                src_note = "last camera frame"
        if not img:
            return {
                "ok": False,
                "action": "live",
                "error": "no frame source available — open the webcam page or the phone overlay so a real camera frame exists",
            }
        try:
            async with httpx.AsyncClient(timeout=40.0) as client:
                vr = await client.post(
                    f"{VISION_SERVER}/api/vision",
                    json={
                        "image_b64": img,
                        "mode": "auto",
                        "generate_audio": False,
                    },
                )
                if vr.status_code != 200:
                    return {
                        "ok": False,
                        "action": "live",
                        "error": f"vision HTTP {vr.status_code}: {vr.text[:200]}",
                    }
                vres = vr.json()
        except Exception as e:  # noqa: BLE001
            return {
                "ok": False,
                "action": "live",
                "error": f"vision unreachable: {str(e)[:160]}",
            }

        # Wait briefly for the async forwarder to record observations (real poll).
        before_ts = 0.0
        try:
            prev = await _vision_get("/api/vision/observations")
            ob = prev.get("observations", []) if isinstance(prev, dict) else []
            before_ts = ob[0]["ts"] if ob else 0.0
        except Exception:  # noqa: BLE001
            pass
        new_obs: list = []
        for _ in range(6):
            await asyncio.sleep(0.5)
            obs_resp = await _vision_get("/api/vision/observations")
            ob = obs_resp.get("observations", []) if isinstance(obs_resp, dict) else []
            new_obs = [o for o in ob if o.get("ts", 0.0) > before_ts]
            if new_obs:
                break
        cached_n = sum(1 for o in new_obs if o.get("cached"))
        return {
            "ok": True,
            "action": "live",
            "detections": vres.get("detections", []),
            "observations": new_obs,
            "cached_count": cached_n,
            "samples_total": (await _trainer_get("/api/trainer/samples")).get(
                "total", 0
            ),
            "reply": vres.get("reply", ""),
        }

    if action == "set_auto_collect":
        enabled = bool(body.get("enabled"))
        data = await _trainer_post("/api/trainer/config", {"auto_collect": enabled})
        return {
            "ok": isinstance(data, dict) and "error" not in data,
            "action": "set_auto_collect",
            "config": data.get("config", data) if isinstance(data, dict) else {},
        }

    if action == "train_if_ready":
        data = await _trainer_post("/api/trainer/auto-check", {})
        return {
            "ok": isinstance(data, dict) and "error" not in data,
            "action": "train_if_ready",
            "result": data if isinstance(data, dict) else {},
        }

    return {
        "ok": False,
        "error": "unknown action (live | set_auto_collect | train_if_ready)",
    }


@router.get("/api/training/progress")
async def training_progress(request: Request):
    """Live progress for both pipelines (persona via log parse, yolo via :8199)."""
    user = await _allowed_user(request)
    if not user:
        return _deny()

    import re
    import datetime
    import glob as globmod

    # ── YOLO ─────────────────────────────────────────────────────────────
    yolo = await _trainer_get("/api/trainer/status")
    yolo_out = {
        "api_up": "error" not in yolo,
        "state": yolo.get("status", "unknown"),
        "progress": yolo.get("progress") or {},
        "sample_count": yolo.get("sample_count", 0),
        "total_detections": yolo.get("total_detections", 0),
        "avg_confidence": yolo.get("avg_confidence", 0),
        "config": yolo.get("config") or {},
    }

    # ── Persona ──────────────────────────────────────────────────────────
    persona_out = {
        "running": False,
        "pid": None,
        "current": 0,
        "total": 0,
        "percent": 0,
        "loss": None,
        "epoch": None,
        "log": None,
    }
    pid_file = WORKSPACE / ".training" / "pids" / "persona.pid"
    pid = None
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)
        persona_out["running"] = True
        persona_out["pid"] = pid
    except Exception:
        try:
            procs = subprocess.check_output(
                [
                    "pgrep",
                    "-f",
                    "train_all_avatars.py|train_all_background.py|trainer/run.py",
                ],
                text=True,
            ).split()
            if procs:
                persona_out["running"] = True
                persona_out["pid"] = int(procs[0])
        except Exception:
            pass

    log_files = (
        globmod.glob(str(WORKSPACE / "trainer" / "logs" / "persona_*.log"))
        + globmod.glob(str(WORKSPACE / "trainer" / "logs" / "background_training*.log"))
        + globmod.glob(str(WORKSPACE / "trainer" / "logs" / "training_v*.log"))
        + globmod.glob(str(WORKSPACE / "trainer" / "logs" / "run_*.log"))
    )
    latest = None
    if log_files:
        latest = max(log_files, key=os.path.getmtime)

    if latest:
        try:
            raw = open(latest, "r", errors="replace").read()[-6000:]
            steps = re.findall(r"(\d+)%\|[^|]*\| (\d+)/(\d+)", raw)
            losses = re.findall(r"'loss':\s*([\d.e+-]+)", raw)
            epochs = re.findall(r"'epoch':\s*([\d.e+-]+)", raw)
            cur, total, pct = 0, 0, 0
            if steps:
                pct_s, cur_s, total_s = steps[-1]
                cur, total, pct = int(cur_s), int(total_s), float(pct_s)
            elif losses:
                pct = 100.0
            persona_out.update(
                {
                    "log": os.path.basename(latest),
                    "current": cur,
                    "total": total,
                    "percent": pct,
                    "loss": float(losses[-1]) if losses else None,
                    "epoch": float(epochs[-1]) if epochs else None,
                    "log_age_s": datetime.datetime.now().timestamp()
                    - os.path.getmtime(latest),
                }
            )
        except Exception:
            pass

    # ── Models / data quick numbers ──────────────────────────────────────
    samples = 0
    sd = WORKSPACE / "training_data" / "samples"
    if sd.is_dir():
        samples = sum(1 for _ in sd.rglob("*.jpg"))
    pa = WORKSPACE / "trained_avatars"
    persona_models = (
        len([d for d in pa.glob("*/final") if d.is_dir()]) if pa.is_dir() else 0
    )
    best = None
    md = WORKSPACE / "trained_models"
    if md.is_dir():
        pts = [p for p in md.rglob("*.pt") if p.exists()]
        if pts:
            best = str(max(pts, key=os.path.getmtime))

    return {
        "ok": True,
        "ts": _iso_now(),
        "persona": persona_out,
        "yolo": yolo_out,
        "data": {
            "samples": samples,
            "persona_models": persona_models,
            "best_yolo": best,
        },
    }


# ═══════════════════════════════════════════════════════════════════════
# Resource profile (console sliders) + LLM memory + chat + git
# ═══════════════════════════════════════════════════════════════════════


@router.get("/api/training/resources")
async def training_resources_get(request: Request):
    user = await _allowed_user(request)
    if not user:
        return _deny()
    d = _load_resources()
    d["llm_models"] = _ollama_models_cfg()
    return {"ok": True, **d}


@router.post("/api/training/resources")
async def training_resources_save(request: Request):
    user = await _allowed_user(request)
    if not user:
        return _deny()
    try:
        body = await request.json()
    except Exception:
        body = {}
    d = _load_resources()
    for k, coerce in (
        ("threads", int),
        ("gpu_pct", int),
        ("batch_size", int),
        ("epochs", int),
        ("periodic", bool),
        ("interval_hours", int),
        ("unload_llm", bool),
        ("llm_models", str),
        ("game_sched", bool),
    ):
        if k in body:
            try:
                v = coerce(body[k])
            except Exception:
                continue
            if k == "threads":
                v = max(1, min(int(v), 64))
            elif k == "gpu_pct":
                v = max(0, min(int(v), 100))
            elif k == "batch_size":
                v = max(1, min(int(v), 128))
            elif k == "epochs":
                v = max(1, min(int(v), 1000))
            elif k == "interval_hours":
                v = max(1, min(int(v), 720))
            d[k] = v
    # load balancer settings (nested object)
    if isinstance(body.get("balancer"), dict):
        d["balancer"] = {
            "enabled": bool(body["balancer"].get("enabled", True)),
            "mode": body["balancer"].get("mode")
            if body["balancer"].get("mode") in ("split", "queue")
            else "split",
            "persona_weight": max(
                0, min(100, int(body["balancer"].get("persona_weight", 50)))
            ),
        }
    try:
        RESOURCE_PROFILE.parent.mkdir(parents=True, exist_ok=True)
        RESOURCE_PROFILE.write_text(json.dumps(d, indent=2), encoding="utf-8")
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": f"could not save: {e}"})
    # periodic → also push the interval into the trainer API (:8199) cooldown
    if d.get("periodic"):
        await _trainer_post(
            "/api/trainer/config", {"training_interval_hours": int(d["interval_hours"])}
        )
    return {"ok": True, "saved": {k: d[k] for k in _RESOURCE_KEYS}}


@router.get("/api/training/balance")
async def training_balance(request: Request):
    """Live load-balancer view: who is training, who is queued, allocations."""
    user = await _allowed_user(request)
    if not user:
        return _deny()
    prof = _load_resources()
    bal = _balancer_cfg(prof)
    persona_a = _training_run_alive("persona")
    yolo_a = _training_run_alive("yolo")
    try:
        st = await _trainer_get("/api/trainer/status")
        yolo_status = st.get("status", "down")
    except Exception:
        yolo_status = "down"

    threads = max(1, int(prof.get("threads", 8)))
    pw = bal["persona_weight"] if bal["mode"] == "split" else 50
    both = bal.get("enabled") and bal["mode"] == "split" and persona_a and yolo_a
    alloc = {
        "threads_total": threads,
        "persona": (
            max(1, round(threads * pw / 100))
            if both
            else (threads if persona_a and not yolo_a else 0)
        ),
        "yolo": (
            max(1, round(threads * (100 - pw) / 100))
            if both
            else (threads if yolo_a and not persona_a else 0)
        ),
        "gpu": "yielded" if (both and prof.get("gpu_pct", 0) > 0) else "free",
    }
    q = _queue_path()
    return {
        "ok": True,
        "balancer": bal,
        "persona": {"active": persona_a},
        "yolo": {"active": yolo_a, "status": yolo_status},
        "queued": q or None,
        "allocation": alloc,
    }


# ── LLM memory: free Ollama chat models while training, reload after ────


def _ollama_models_cfg() -> list:
    try:
        saved = json.loads(RESOURCE_PROFILE.read_text(encoding="utf-8"))
        raw = saved.get("llm_models") or ""
        if isinstance(raw, str) and raw.strip():
            return [m.strip() for m in raw.split(",") if m.strip()]
    except Exception:
        pass
    return []


def _ollama_models_fallback() -> list:
    m = _ollama_models_cfg()
    return m or ["qwen2.5:3b", "qwen2.5:1.5b"]


def _training_run_alive(t: str) -> bool:
    """Is a persona or yolo training run still alive?"""
    if t == "yolo":
        try:
            out = subprocess.check_output(
                [
                    "curl",
                    "-sf",
                    "--max-time",
                    "3",
                    f"{TRAINER_SERVER}/api/trainer/status",
                ],
                text=True,
                stderr=subprocess.DEVNULL,
            )
            return json.loads(out).get("status") == "training"
        except Exception:
            return False
    pid_file = WORKSPACE / ".training" / "pids" / "persona.pid"
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)
        return True
    except Exception:
        pass
    try:
        procs = subprocess.check_output(
            [
                "pgrep",
                "-f",
                "train_all_avatars.py|train_all_background.py|trainer/run.py",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
        ).split()
        return len(procs) > 0
    except Exception:
        return False


async def _ollama_unload(force: bool = False) -> dict:
    """Unload chat models from Ollama memory (keep_alive=0 frees RAM/VRAM)."""
    prof = _load_resources()
    if not force and not prof.get("unload_llm"):
        return {"skipped": True}
    results = {}
    for m in _ollama_models_fallback():
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.post(
                    f"{OLLAMA_URL}/api/generate",
                    json={"model": m, "keep_alive": 0},
                )
            results[m] = {"status": r.status_code}
        except Exception as e:
            results[m] = {"error": str(e)}
    return {"ok": True, "unloaded": list(results), "results": results}


async def _ollama_reload(force: bool = False) -> dict:
    """Reload chat models so Lilly can chat again (empty warm-up call).

    Ollama keeps one model resident by default, so the primary chat model is
    warmed LAST and is the one that stays loaded.
    """
    results = {}
    models = list(_ollama_models_fallback())
    try:
        if OLLAMA_CHAT_MODEL in models:
            models.remove(OLLAMA_CHAT_MODEL)
            models.append(OLLAMA_CHAT_MODEL)
    except Exception:
        pass
    for m in models:
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                r = await client.post(
                    f"{OLLAMA_URL}/api/generate",
                    json={
                        "model": m,
                        "prompt": "",
                        "stream": False,
                        "keep_alive": "10m",
                    },
                )
            results[m] = {"status": r.status_code}
        except Exception as e:
            results[m] = {"error": str(e)}
    return {"ok": True, "loaded": list(results), "results": results}


async def _watch_training_end(t: str):
    """Poll until the run finishes, then reload the models we unloaded."""
    import asyncio as _aio

    await _aio.sleep(15)  # let the run actually start before watching
    for _ in range(240):  # up to ~2h
        try:
            if not _training_run_alive(t):
                await _ollama_reload()
                return
        except Exception:
            pass
        await _aio.sleep(30)
    try:
        await _ollama_reload()
    except Exception:
        pass


async def _maybe_free_llm_memory(t: str) -> bool:
    """Hook: unload LLMs before the run + schedule the reload watcher."""
    prof = _load_resources()
    if not prof.get("unload_llm"):
        return False
    try:
        await _ollama_unload()
        asyncio.create_task(_watch_training_end(t))
        return True
    except Exception:
        return False


@router.post("/api/training/llm")
async def training_llm(request: Request):
    """Manual control: unload / reload / status of the Ollama chat models."""
    user = await _allowed_user(request)
    if not user:
        return _deny()
    try:
        body = await request.json()
    except Exception:
        body = {}
    action = body.get("action", "")
    if action == "unload":
        return await _ollama_unload(force=True)
    if action == "reload":
        return await _ollama_reload(force=True)
    resident = {}
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(f"{OLLAMA_URL}/api/ps")
        if r.status_code == 200:
            for m in r.json().get("models") or []:
                resident[m.get("name")] = m.get("size_vram") or m.get("size")
    except Exception as e:
        resident = {"error": str(e)}
    return {"ok": True, "resident": resident, "configured": _ollama_models_fallback()}


# ── Training-aware chat (engaging chat window in the console) ───────────


def _training_chat_system() -> str:
    snap = []
    try:
        st = _script_json(["json", "status"])
        p = st.get("persona") or {}
        y = st.get("yolo") or {}
        snap.append(
            "persona trainer: "
            + ("RUNNING pid " + str(p.get("pid")) if p.get("running") else "idle")
        )
        snap.append(
            "yolo api: "
            + (
                "up, state " + str(y.get("status", "unknown"))
                if y.get("api_up")
                else "down"
            )
        )
        m = st.get("model") or {}
        if m.get("best"):
            snap.append("best model: " + str(m["best"]))
        data = st.get("data") or {}
        if data.get("samples") is not None:
            snap.append("samples: " + str(data.get("samples")))
        if data.get("sample_labels"):
            snap.append("labels: " + json.dumps(data["sample_labels"])[:400])
    except Exception:
        pass
    return (
        "You are Lilly Pup, the training-aware companion inside Lilly's /training console. "
        "You help the owner understand and steer model training (persona LoRA adapters + YOLO vision). "
        "Be concise, warm and practical; 3-6 sentences unless asked for detail. You can suggest: "
        "tuning the resource sliders, freeing LLM memory during training, toggling vision objects, "
        "importing phone photos, kicking off runs, periodic ('from time to time') training, and "
        "cloning/adapting git repos. Current live snapshot: "
        + ("; ".join(snap) if snap else "no live snapshot")
        + "."
    )


@router.post("/api/training/chat")
async def training_chat(request: Request):
    user = await _allowed_user(request)
    if not user:
        return _deny()
    uid = user.get("id") or user.get("email") or "owner"
    try:
        body = await request.json()
    except Exception:
        body = {}
    msg = (body.get("message") or "").strip()
    if not msg:
        return JSONResponse(status_code=400, content={"error": "message required"})

    hist = _TRAINING_CHAT_HISTORY.setdefault(uid, [])
    hist.append({"role": "user", "content": msg[:4000]})
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            r = await client.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": OLLAMA_CHAT_MODEL,
                    "stream": False,
                    "messages": [{"role": "system", "content": _training_chat_system()}]
                    + hist[-(TRAINING_CHAT_MAX - 1) :],
                },
            )
        data = r.json() if r.status_code == 200 else {}
        reply = (data.get("message") or {}).get("content") or data.get("response") or ""
        if not reply:
            reply = f"⚠ LLM returned nothing (HTTP {r.status_code})"
    except Exception as e:
        reply = f"⚠ Chat backend error: {e}. Check Ollama at {OLLAMA_URL}."
    hist.append({"role": "assistant", "content": reply})
    return {"ok": True, "reply": reply, "history": len(hist)}


# ── Git: clone / adapt / list / delete repos inside the workspace ───────


def _safe_repo_name(url: str, name: str = "") -> str:
    import re as _re

    if name and name.strip():
        n = _re.sub(r"[^A-Za-z0-9_.\-]+", "_", name.strip()).strip("._-")
        if n:
            return n
    n = url.rstrip("/").split("/")[-1]
    if n.endswith(".git"):
        n = n[:-4]
    n = _re.sub(r"[^A-Za-z0-9_.\-]+", "_", n).strip("._-")
    return n or "repo"


@router.get("/api/training/git")
async def training_git_list(request: Request):
    user = await _allowed_user(request)
    if not user:
        return _deny()
    REPOS_DIR.mkdir(parents=True, exist_ok=True)
    repos = []
    for d in sorted(REPOS_DIR.iterdir()):
        if not d.is_dir() or not (d / ".git").exists():
            continue
        entry: dict = {"name": d.name, "path": str(d)}
        try:
            head = subprocess.check_output(
                [
                    "git",
                    "-C",
                    str(d),
                    "log",
                    "-1",
                    "--format=%h %ad %s",
                    "--date=short",
                ],
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=5,
            ).strip()
            entry["head"] = head
        except Exception:
            entry["head"] = ""
        try:
            out = subprocess.check_output(
                ["git", "-C", str(d), "count-objects", "-vH"],
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=5,
            )
            import re as _re

            m = _re.search(r"size-pack:\s*([\w.]+ \w+)", out)
            entry["size"] = m.group(1) if m else ""
        except Exception:
            entry["size"] = ""
        try:
            entry["files"] = sum(1 for _ in d.rglob("*") if _.is_file())
        except Exception:
            entry["files"] = 0
        repos.append(entry)
    return {"ok": True, "count": len(repos), "repos": repos, "dir": str(REPOS_DIR)}


@router.post("/api/training/git")
async def training_git_clone(request: Request):
    user = await _allowed_user(request)
    if not user:
        return _deny()
    try:
        body = await request.json()
    except Exception:
        body = {}
    url = (body.get("url") or "").strip()
    if not url or not (
        url.startswith("https://")
        or url.startswith("http://")
        or url.startswith("git@")
    ):
        return JSONResponse(
            status_code=400, content={"error": "need a valid https:// or git@ URL"}
        )
    branch = (body.get("branch") or "").strip() or None
    name = _safe_repo_name(url, str(body.get("name") or ""))
    target = REPOS_DIR / name
    if target.exists():
        return JSONResponse(
            status_code=409, content={"error": f"{name} already exists in repos/"}
        )
    REPOS_DIR.mkdir(parents=True, exist_ok=True)
    cmd = ["git", "clone", "--depth", "1"]
    if branch:
        cmd += ["--branch", branch]
    cmd += [url, str(target)]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return JSONResponse(
            status_code=504, content={"error": "git clone timed out (180s)"}
        )
    if p.returncode != 0:
        return JSONResponse(
            status_code=400,
            content={
                "error": "clone failed",
                "output": (p.stderr or p.stdout or "")[-600:],
            },
        )
    result = {
        "ok": True,
        "name": name,
        "path": str(target),
        "clone_output": (p.stdout or p.stderr or "")[-500:],
    }
    if body.get("modify"):
        plan = await _git_adapt_plan(name, str(target))
        result["plan"] = plan
        # Also surface the plan in the console chat window
        _TRAINING_CHAT_HISTORY.setdefault(
            user.get("id") or user.get("email") or "owner", []
        ).append({"role": "assistant", "content": plan})
    return result


async def _git_adapt_plan(name: str, path: str) -> str:
    """Ask the LLM how this repo should be adapted into the Lilly workspace."""
    info = []
    try:
        head = subprocess.check_output(
            ["git", "-C", path, "log", "-1", "--format=%h %ad %s", "--date=short"],
            text=True,
            timeout=5,
        ).strip()
        tree = subprocess.check_output(
            ["git", "-C", path, "ls-files"], text=True, timeout=8
        ).splitlines()
        info.append(f"last commit: {head}")
        info.append(
            f"files ({len(tree)}): "
            + ", ".join(tree[:40])
            + (" …" if len(tree) > 40 else "")
        )
    except Exception as e:
        info.append(f"tree scan error: {e}")
    sys_prompt = (
        "You are Lilly's engineer assisting the owner inside the /training console. "
        "A repository was just cloned into the workspace. Propose a SHORT practical plan "
        "(max 5 bullet points, 3-6 sentences) for how to adapt it into the Lilly AI workspace "
        "(FastAPI backend lilly_ai.py, Termux sensor server, YOLO vision, 9 avatars, Docker), "
        "or state clearly that it has no obvious fit. Use ONLY the files listed — never hallucinate."
    )
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            r = await client.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": OLLAMA_CHAT_MODEL,
                    "stream": False,
                    "messages": [
                        {"role": "system", "content": sys_prompt},
                        {
                            "role": "user",
                            "content": "Repo '"
                            + name
                            + "' at "
                            + path
                            + "\n"
                            + "\n".join(info),
                        },
                    ],
                },
            )
        data = r.json() if r.status_code == 200 else {}
        return (
            (data.get("message") or {}).get("content")
            or data.get("response")
            or "⚠ no plan returned"
        )
    except Exception as e:
        return f"⚠ failed to analyze: {e}"


@router.delete("/api/training/git")
async def training_git_delete(request: Request, name: str = ""):
    user = await _allowed_user(request)
    if not user:
        return _deny()
    import shutil

    target = REPOS_DIR / _safe_repo_name("x.git", name)
    if not target.is_dir() or not (target / ".git").exists():
        return JSONResponse(status_code=404, content={"error": "repo not found"})
    shutil.rmtree(target)
    return {"ok": True, "removed": str(target)}


# ═══════════════════════════════════════════════════════════════════════════
#  OSINT · Reverse face-search + footprint dossier from the training console
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/api/training/osint/status")
async def osint_status(request: Request):
    """Report whether the OSINT pipeline is available."""
    user = await _allowed_user(request)
    if not user:
        return _deny()
    ok, reason = _osint_available()
    enabled = False
    try:
        from osint_face_lookup import is_enabled

        enabled = is_enabled()
    except Exception:
        pass
    return {"available": ok, "reason": reason, "enabled": enabled}


@router.post("/api/training/osint/upload")
async def osint_upload(
    request: Request,
    file: UploadFile = File(...),
    name: str = Form(""),
    run_footprint: bool = Form(False),
    auto: bool = Form(True),
):
    """Upload an image → detect face → start reverse-face search + optional footprint."""
    user = await _allowed_user(request)
    if not user:
        return _deny()

    ok, reason = _osint_available()
    if not ok:
        return JSONResponse(status_code=503, content={"error": reason})

    try:
        from osint_face_lookup import is_enabled

        if not is_enabled():
            return JSONResponse(
                status_code=503,
                content={"error": "OSINT is disabled (FACE_OSINT_ENABLED=0)"},
            )
    except Exception:
        return JSONResponse(
            status_code=503, content={"error": "osint_face_lookup unavailable"}
        )

    # Read uploaded file
    img_bytes = await file.read()
    if not img_bytes or len(img_bytes) < 100:
        return JSONResponse(
            status_code=400, content={"error": "file too small or empty"}
        )

    # Detect face + crop
    import base64 as _b64

    crop_jpg, meta = _extract_face_crop(img_bytes)
    if crop_jpg is None:
        return JSONResponse(
            status_code=422,
            content={"error": "could not extract face", **meta},
        )

    # Generate unique res_id
    import hashlib

    h8 = hashlib.sha256(crop_jpg).hexdigest()[:8]
    res_id = f"train_{int(time.time())}_{h8}"
    crop_b64 = _b64.b64encode(crop_jpg).decode()

    # Save crop file
    OSINT_CROPS_DIR.mkdir(parents=True, exist_ok=True)
    crop_path = OSINT_CROPS_DIR / f"{res_id}.jpg"
    crop_path.write_bytes(crop_jpg)

    # Create job record
    job = {
        "res_id": res_id,
        "ts": time.time(),
        "status": "searching",
        "source": "training-console",
        "name_hint": name.strip() or None,
        "run_footprint": run_footprint,
        "crop_path": str(crop_path),
        "crop_url": f"/api/training/osint/crop/{res_id}",
        "meta": meta,
        "auto": auto,
    }
    jobs = _load_osint_jobs()
    jobs.append(job)
    _save_osint_jobs(jobs)

    # Fire background OSINT search
    if auto:
        asyncio.get_event_loop().create_task(
            _run_training_osint(res_id, crop_jpg, name.strip(), run_footprint, crop_b64)
        )

    return {
        "ok": True,
        "res_id": res_id,
        "crop_url": job["crop_url"],
        "meta": meta,
        "status": "searching",
        "run_footprint": run_footprint,
    }


@router.get("/api/training/osint/jobs")
async def osint_jobs(request: Request, limit: int = 12):
    """List recent OSINT training jobs with live cache status."""
    user = await _allowed_user(request)
    if not user:
        return _deny()
    jobs = _load_osint_jobs()
    # Enrich with live cache data
    enriched = [_osint_job_status(dict(j)) for j in jobs[-limit:]]
    enriched.reverse()  # newest first
    return {"jobs": enriched, "total": len(jobs)}


@router.get("/api/training/osint/jobs/{res_id}")
async def osint_job_detail(request: Request, res_id: str):
    """Full detail of a single OSINT job including footprint report."""
    user = await _allowed_user(request)
    if not user:
        return _deny()
    jobs = _load_osint_jobs()
    job = next((j for j in jobs if j.get("res_id") == res_id), None)
    if not job:
        return JSONResponse(status_code=404, content={"error": "job not found"})
    job = _osint_job_status(dict(job))
    return {"job": job}


@router.get("/api/training/osint/crop/{res_id}")
async def osint_crop(res_id: str):
    """Serve a saved OSINT crop image."""
    # No auth gate — crop URLs are only revealed after upload
    import re as _re

    safe_id = _re.sub(r"[^A-Za-z0-9_-]", "", res_id)[:40]
    crop_path = OSINT_CROPS_DIR / f"{safe_id}.jpg"
    if not crop_path.exists():
        return JSONResponse(status_code=404, content={"error": "crop not found"})
    return FileResponse(crop_path, media_type="image/jpeg")


@router.delete("/api/training/osint/jobs/{res_id}")
async def osint_job_delete(request: Request, res_id: str):
    """Delete an OSINT job and its crop file."""
    user = await _allowed_user(request)
    if not user:
        return _deny()
    jobs = _load_osint_jobs()
    before = len(jobs)
    jobs = [j for j in jobs if j.get("res_id") != res_id]
    if len(jobs) == before:
        return JSONResponse(status_code=404, content={"error": "job not found"})
    _save_osint_jobs(jobs)
    # Also delete the crop file
    import re as _re

    safe_id = _re.sub(r"[^A-Za-z0-9_-]", "", res_id)[:40]
    crop_path = OSINT_CROPS_DIR / f"{safe_id}.jpg"
    if crop_path.exists():
        crop_path.unlink(missing_ok=True)
    return {"ok": True, "removed": res_id}
