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
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

WORKSPACE = Path(__file__).parent
SCRIPT = WORKSPACE / "training_manager.sh"

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
        return _deny()
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

    extra_env = {}
    if body.get("llm"):
        extra_env["TRAINING_LLM_REVIEW"] = "1"

    r = _run_script(args, timeout=20, env=extra_env)
    result = {
        "args": args,
        "rc": r["rc"],
        "output": r["stdout"].strip().splitlines()[-8:],
        "llm_review": bool(extra_env),
    }
    if r["stderr"].strip():
        result["stderr"] = r["stderr"].strip().splitlines()[-6:]
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
    return {
        "ok": r["rc"] == 0,
        "rc": r["rc"],
        "output": r["stdout"].strip().splitlines()[-8:],
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

    r = _run_script(args, timeout=20)
    return {
        "ok": r["rc"] == 0,
        "rc": r["rc"],
        "output": r["stdout"].strip().splitlines()[-10:],
    }


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

    extra_env = {}
    if body.get("llm"):
        extra_env["TRAINING_LLM_REVIEW"] = "1"

    r = _run_script(args, timeout=25, env=extra_env)
    return {
        "ok": r["rc"] == 0,
        "rc": r["rc"],
        "output": r["stdout"].strip().splitlines()[-8:],
        "llm_review": bool(extra_env),
    }


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
