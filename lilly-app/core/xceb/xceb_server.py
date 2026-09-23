#!/usr/bin/env python3
"""
XCEB broker server — Face + OSINT Evidence Broker (FastAPI).

Runs the XCEB investigation service for Lilly AI:

  • POST /xceb/cases                  → start a case from a face crop (auto-runs)
  • POST /xceb/cases/{id}/run         → run / re-run the pipeline
  • GET  /xceb/cases[/{id}]           → cases list / full ledger
  • GET  /xceb/cases/{id}/profile.html→ human-readable dossier (with sources)
  • GET  /xceb/cases/{id}/graph.svg   → entity graph (svg) / json / maltego csv
  • POST /xceb/vision/faces/osint_unknown → ingest from the vision overlay server
  • GET  /xceb/faces                  → the faces "file" (FAISS index + ledger)
  • POST /xceb/faces/search           → FAISS search narrowing by geo/social/dating
  • GET  /xceb/stream                 → SSE live feed (cases, scheduler, logs)
  • GET  /                            → xceb.html dashboard

Resolved identities are pushed back to the vision overlay so the live camera
box label swaps from "Person" to the discovered name (autonomous rename).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from collections import deque
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)

import xceb_core
import xceb_faces
import xceb_pipeline
import xceb_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
)
logger = logging.getLogger("xceb.server")

app = FastAPI(title="XCEB — Face + OSINT Evidence Broker", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

WEB_UI = Path(__file__).with_name("xceb.html")

xceb_core.ensure_dirs()
try:
    xceb_faces.ensure_init()
    logger.info("faces index ready")
except Exception as e:
    logger.warning(f"faces index init failed: {e}")

# ── live feed (SSE) ───────────────────────────────────────────────────────
_history: deque = deque(maxlen=250)
_subs: set[asyncio.Queue] = set()


def emit(kind: str, payload: dict):
    msg = f"event: {kind}\ndata: {json.dumps(payload, default=str)}\n\n"
    _history.append((kind, payload))
    for q in list(_subs):
        try:
            q.put_nowait(msg)
        except Exception:
            _subs.discard(q)


# ── background pipeline runner ─────────────────────────────────────────────
async def _run_in_background(case_id: str, stages: list[str] | None = None):
    try:
        case = xceb_core.load_case(case_id)
        if not case:
            emit("case", {"event": "missing", "case_id": case_id})
            return
        emit("case", {"event": "start", "case_id": case_id})
        case = await xceb_pipeline.run_pipeline(case, stages=stages)
        emit(
            "case",
            {
                "event": "done",
                "case_id": case_id,
                "status": case.get("status"),
                "name": case.get("best_name"),
                "confidence": case.get("confidence"),
                "slug": case.get("slug"),
            },
        )
    except Exception as e:
        logger.exception(f"case {case_id} failed")
        try:
            case = xceb_core.load_case(case_id)
            if case:
                xceb_core.log_step(case, f"run failed: {e}", "error")
                case["status"] = "error"
                xceb_core._write_case(case)
        except Exception:
            pass
        emit("case", {"event": "error", "case_id": case_id, "error": str(e)})


# ── token gate ─────────────────────────────────────────────────────────────
def _check_token(req: Request):
    tok = xceb_core.XCEB_TOKEN
    if not tok:
        return None
    auth = req.headers.get("authorization", "")
    if auth == f"Bearer {tok}" or (req.headers.get("x-xceb-token") or "") == tok:
        return None
    return JSONResponse(status_code=401, content={"error": "unauthorized"})


@app.middleware("http")
async def _token_middleware(request: Request, call_next):
    if request.url.path in ("/xceb/health", "/xceb/stream"):
        return await call_next(request)
    denied = _check_token(request)
    if denied:
        return denied
    return await call_next(request)


# ── public helpers exposed to modules ─────────────────────────────────────
async def _file_or_b64(up: UploadFile | None, b64: str = "") -> bytes:
    if up:
        data = await up.read()
        if data:
            b64 = base64.b64encode(data).decode()
    try:
        return base64.b64decode(b64)
    except Exception:
        return b""


# ── health / config ────────────────────────────────────────────────────────
@app.get("/xceb/health")
async def health():
    return {
        "ok": True,
        "service": "xceb",
        "faces": xceb_faces.stats(),
        "cases": len(xceb_core.catalog_list(limit=400)),
        "scheduler": xceb_scheduler.scheduler_status(),
        "time": time.time(),
    }


@app.get("/xceb/config")
async def config():
    return {
        "data_path": str(xceb_core.XCEB_DATA),
        "shared_path": str(xceb_core.XCEB_SHARED),
        "public_base": xceb_core.XCEB_PUBLIC_BASE,
        "llm_url": xceb_core.XCEB_LLM_URL,
        "llm_model": xceb_core.XCEB_LLM_MODEL,
        "proxy": xceb_core.XCEB_PROXY or "",
        "resolve_conf": xceb_core.XCEB_RESOLVE_CONF,
        "auto_enroll": xceb_core.XCEB_AUTO_ENROLL,
        "sleep_window": xceb_core.XCEB_SLEEP_WINDOW,
        "engine_delay": xceb_core.XCEB_ENGINE_DELAY,
        "max_cases": xceb_core.XCEB_MAX_CASES,
        "retry_hours": xceb_core.XCEB_RETRY_HOURS,
        "vision_url": xceb_core.XCEB_VISION_URL,
        "scheduler_enabled": xceb_scheduler.scheduler_status()["enabled"],
    }


@app.get("/xceb/pub/{name}")
async def pub_crop(name: str):
    """Serve a published crop for URL-flow reverse engines."""
    safe = Path(name).name
    p = xceb_core.PUB_DIR / safe
    if not p.exists():
        raise HTTPException(404, "crop not found or expired")
    return FileResponse(p, media_type="image/jpeg")


# ── cases ─────────────────────────────────────────────────────────────────
@app.post("/xceb/cases")
async def create_case(
    body: dict,
    run_now: bool = Query(default=True, description="auto-run pipeline"),
):
    crop_b64 = body.get("crop_b64", "")
    if not crop_b64:
        raise HTTPException(400, "crop_b64 required (JPEG of the face)")
    meta = dict(body.get("meta") or {})
    if body.get("person_box"):
        meta["person_box"] = body["person_box"]
    if body.get("kps"):
        meta["kps"] = body["kps"]
    case = xceb_core.create_case(
        source=body.get("source", "upload"),
        face_id=body.get("face_id", ""),
        crop_b64=crop_b64,
        original_b64=body.get("original_b64", ""),
        geo_hint=body.get("geo_hint", ""),
        category=body.get("category", "all"),
        note=body.get("note", ""),
        meta=meta,
    )
    stages = body.get("stages")
    emit("case", {"event": "created", "case_id": case["case_id"], "status": "new"})
    if run_now:
        asyncio.create_task(_run_in_background(case["case_id"], stages=stages))
        status = "queued"
    else:
        status = "created (paused)"
    return {
        "ok": True,
        "case_id": case["case_id"],
        "slug": case["slug"],
        "status": status,
    }


@app.get("/xceb/cases")
async def list_cases(limit: int = Query(default=100, le=400)):
    metas = xceb_core.catalog_list(limit=limit)
    out = []
    for m in metas:
        # enrich with name/conf/status from ledger when cheap
        out.append(
            {
                "case_id": m.get("case_id"),
                "slug": m.get("slug"),
                "status": m.get("status"),
                "source": m.get("source"),
                "best_name": m.get("best_name"),
                "confidence": m.get("confidence", 0.0),
                "face_id": m.get("face_id"),
                "geo_hint": m.get("geo_hint"),
                "category": m.get("category"),
                "created_ts": m.get("created_ts"),
                "updated_ts": m.get("updated_ts"),
                "has_photo": m.get("has_photo"),
            }
        )
    return {"cases": out, "count": len(out)}


@app.get("/xceb/cases/{cid}")
async def get_case(cid: str):
    case = xceb_core.load_case(cid)
    if not case:
        raise HTTPException(404, "case not found")
    strip = {k: case[k] for k in case if k != "steps"}
    strip["last_steps"] = case.get("steps", [])[-15:]
    strip["files"] = xceb_core.case_files(case)
    return strip


@app.post("/xceb/cases/{cid}/run")
async def run_case(cid: str, body: dict | None = None):
    case = xceb_core.load_case(cid)
    if not case:
        raise HTTPException(404, "case not found")
    body = body or {}
    stages = body.get("stages")
    asyncio.create_task(_run_in_background(case["case_id"], stages=stages))
    return {"ok": True, "case_id": case["case_id"], "status": "queued"}


@app.delete("/xceb/cases/{cid}")
async def delete_case(cid: str):
    if not xceb_core.delete_case(cid):
        raise HTTPException(404, "case not found")
    return {"ok": True}


@app.get("/xceb/cases/{cid}/face.jpg")
async def case_face(cid: str):
    case = xceb_core.load_case(cid)
    if not case:
        raise HTTPException(404, "case not found")
    p = Path(case["dir"]) / "face.jpg"
    if not p.exists():
        raise HTTPException(404, "no face crop")
    return FileResponse(p, media_type="image/jpeg")


@app.get("/xceb/cases/{cid}/brief")
async def case_brief(cid: str):
    """Structured dossier summary for the camera IDS profile card."""
    case = xceb_core.load_case(cid)
    if not case:
        raise HTTPException(404, "case not found")
    f = case.get("findings", {}) or {}
    reverse = f.get("reverse") or []
    accounts = f.get("accounts") or []
    verified = f.get("verified") or []
    mentions = f.get("mentions") or []
    hits = {}
    for h in reverse:
        site = h.get("site") or h.get("engine") or "engine"
        hits[site] = hits.get(site, 0) + 1
    # Distinct platforms corroborating an account (social/dating/fan):
    platforms = sorted({a.get("platform") for a in accounts if a.get("platform")})
    return {
        "ok": True,
        "case_id": case["case_id"],
        "name": case.get("best_name") or case["case_id"],
        "confidence": case.get("confidence", 0.0),
        "status": case.get("status", "new"),
        "face_id": case.get("face_id"),
        "geo_hint": (case.get("meta") or {}).get("geo_hint"),
        "category": (case.get("meta") or {}).get("category"),
        "description": case.get("description") or _describe_findings(f),
        "engine_hits": hits,
        "accounts": accounts[:8],
        "verified": verified[:8],
        "mentions": sorted({u for u in (mentions or []) if isinstance(u, str)})[:12],
        "platforms": platforms,
        "resolved": case.get("status") == "resolved",
        "profile_url": f"/xceb/cases/{case['case_id']}/profile.html",
        "graph_url": f"/xceb/cases/{case['case_id']}/graph.svg",
    }


def _describe_findings(f: dict) -> str:
    """Plain-language one-liner about what was found for the brief."""
    parts = []
    rev = len(f.get("reverse") or [])
    acc = len(f.get("accounts") or [])
    ver = len(f.get("verified") or [])
    if rev:
        parts.append(f"{rev} reverse-image hit(s)")
    if acc:
        parts.append(f"{acc} linked account(s)")
    if ver:
        parts.append(f"{ver} photo-verified profile(s)")
    if not parts:
        return "No evidence found yet — scheduled for background retry."
    return "Found: " + ", ".join(parts) + "."


@app.get("/xceb/cases/{cid}/original.jpg")
async def case_original(cid: str):
    case = xceb_core.load_case(cid)
    if not case:
        raise HTTPException(404, "case not found")
    p = Path(case["dir"]) / "original.jpg"
    if not p.exists():
        raise HTTPException(404, "no original frame")
    return FileResponse(p, media_type="image/jpeg")


def _case_file(cid: str, fname: str, mime: str):
    case = xceb_core.load_case(cid)
    if not case:
        raise HTTPException(404, "case not found")
    p = Path(case["dir"]) / fname
    if not p.exists():
        raise HTTPException(404, f"{fname} not rendered yet")
    return FileResponse(p, media_type=mime)


@app.get("/xceb/cases/{cid}/profile.html")
async def case_profile(cid: str):
    return _case_file(cid, "profile.html", "text/html")


@app.get("/xceb/cases/{cid}/profile.txt")
async def case_profile_txt(cid: str):
    return _case_file(cid, "profile.txt", "text/plain")


@app.get("/xceb/cases/{cid}/graph.json")
async def case_graph(cid: str):
    return _case_file(cid, "graph.json", "application/json")


@app.get("/xceb/cases/{cid}/maltego_export.csv")
async def case_maltego(cid: str):
    return _case_file(cid, "maltego_export.csv", "text/csv")


@app.get("/xceb/cases/{cid}/graph.svg")
async def case_graph_svg(cid: str):
    return Response(_case_file_bytes(cid, "graph.svg"), media_type="image/svg+xml")


def _case_file_bytes(cid: str, fname: str) -> bytes:
    case = xceb_core.load_case(cid)
    if not case:
        raise HTTPException(404, "case not found")
    p = Path(case["dir"]) / fname
    if not p.exists():
        raise HTTPException(404, f"{fname} not rendered yet")
    return p.read_bytes()


# ── faces file (FAISS index + ledger) ─────────────────────────────────────
@app.get("/xceb/faces")
async def list_faces(
    geo: str = "",
    category: str = "",
    platform: str = "",
    limit: int = Query(default=100, le=500),
):
    faces = xceb_faces.list_faces(
        limit=limit, geo=geo, category=category, platform=platform
    )
    for f in faces:
        f["thumb"] = f"/xceb/faces/thumbs/{f['fid']}"
        cids = f.get("case_ids") or []
        f["case_id"] = cids[0] if cids else None
        f["profile_url"] = f"/xceb/cases/{cids[0]}/profile.html" if cids else None
    return {
        "stats": xceb_faces.stats(),
        "faces": faces,
        "count": len(faces),
        "filters": {"geo": geo, "category": category, "platform": platform},
    }


@app.get("/xceb/faces/thumbs/{fid}")
async def face_thumb(fid: str):
    """Thumbnail of the enrolled face (the crop image that was searched)."""
    p = Path(xceb_faces.THUMBS_DIR) / f"{fid}.jpg"
    if not p.exists():
        return JSONResponse(status_code=404, content={"error": "no thumb"})
    media = p.read_bytes()
    return Response(content=media, media_type="image/jpeg")


@app.post("/xceb/faces/search")
async def search_faces(
    request: Request,
    geo: str = "",
    category: str = "",
    platform: str = "",
    min_sim: float = Query(default=0.45, ge=0.0, le=1.0),
    top_k: int = Query(default=10, le=50),
):
    ct = request.headers.get("content-type", "")
    if ct.startswith("multipart/form-data"):
        form = await request.form()
        up = form.get("image")
        upload: UploadFile | None = up if isinstance(up, UploadFile) else None
        data = await upload.read() if upload else b""
    else:
        body = await request.json()
        data = base64.b64decode(body.get("image_b64", ""))
    if not data:
        raise HTTPException(400, "image required")
    emb, _box = xceb_faces.embed_crop_bytes(data)
    if emb is None:
        raise HTTPException(422, "no face detected in image")
    matches = xceb_faces.search_face(
        emb, top_k=top_k, min_sim=min_sim, geo=geo, category=category, platform=platform
    )
    return {"matches": matches, "count": len(matches), "min_sim": min_sim}


@app.post("/xceb/faces/enroll")
async def enroll_face(body: dict):
    """Enroll a named face directly (name + image_b64)."""
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name required")
    data = base64.b64decode(body.get("image_b64", ""))
    if not data:
        raise HTTPException(400, "image_b64 required")
    emb, _ = xceb_faces.embed_crop_bytes(data)
    if emb is None:
        raise HTTPException(422, "no face detected in image")
    rec = xceb_faces.add_face(
        embedding=emb,
        name=name,
        source=body.get("source", "manual"),
        thumb_b64=str(body.get("image_b64") or ""),
        geo={"raw": body.get("geo_hint", ""), "city": body.get("geo_hint", "")},
        category=body.get("category", "social"),
        confidence=float(body.get("confidence", 1.0)),
    )
    if not rec:
        raise HTTPException(422, "enrollment failed")
    return {"ok": True, **rec}


# ── vision overlay ingest (autonomous rename pipeline) ─────────────────────
@app.post("/xceb/vision/faces/osint_unknown")
async def vision_osint_unknown(request: Request):
    """Called by the overlord vision server with an unsolved face crop.

    Body (from yolov8_vision_server._osint_push_loop):
        {id, person_box, kps, crop_b64, ts}

    We immediately create a case and run the pipeline. On resolution the
    identity is pushed back to the vision server which renames the live box
    (label 'Person' → person's name). Reply keeps `handled` so the vision
    server knows OSINT is active and won't cooldown spam.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "json required")
    face_id = str(body.get("id") or "")
    crop_b64 = str(body.get("crop_b64") or "")
    if not face_id or not crop_b64:
        return JSONResponse({"handled": False, "error": "id + crop_b64 required"})
    meta = {
        "person_box": body.get("person_box", {}),
        "kps": body.get("kps", {}),
        "vision_ts": body.get("ts", 0),
    }
    case = xceb_core.create_case(
        source="vision",
        face_id=face_id,
        crop_b64=crop_b64,
        geo_hint="",
        category="all",
        meta=meta,
    )
    asyncio.create_task(_run_in_background(case["case_id"]))
    emit("vision", {"event": "ingest", "face_id": face_id, "case_id": case["case_id"]})
    return {"handled": True, "ok": True, "case_id": case["case_id"]}


# ── scheduler ──────────────────────────────────────────────────────────────
@app.get("/xceb/scheduler")
async def scheduler_status():
    return xceb_scheduler.scheduler_status()


@app.post("/xceb/scheduler")
async def scheduler_control(body: dict):
    if "enabled" in body:
        xceb_scheduler.scheduler_set_enabled(bool(body["enabled"]))
    if body.get("tick_now"):
        res = await xceb_scheduler.scheduler_tick(emit=emit)
        return {"ok": True, **res}
    return {"ok": True, **xceb_scheduler.scheduler_status()}


async def _scheduler_loop():
    """Drive the scheduler every tick from the server process."""
    while True:
        try:
            if xceb_scheduler.scheduler_status()["enabled"]:
                res = await xceb_scheduler.scheduler_tick(emit=emit)
                if res.get("last_tick_result") not in ("disabled", "outside window"):
                    emit(
                        "scheduler",
                        {"event": "tick", "result": res["last_tick_result"]},
                    )
        except Exception as e:
            logger.debug(f"scheduler loop error: {e}")
        tick = xceb_scheduler.scheduler_status().get("tick_sec", 300)
        await asyncio.sleep(max(30, min(tick, 1800)))


# ── live feed (SSE) ────────────────────────────────────────────────────────
@app.get("/xceb/stream")
async def stream():
    async def gen():
        q: asyncio.Queue = asyncio.Queue()
        _subs.add(q)
        try:
            # replay recent history
            for kind, payload in list(_history):
                yield f"event: {kind}\ndata: {json.dumps(payload, default=str)}\n\n"
            yield ": connected\n\n"
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=20)
                    yield msg
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            _subs.discard(q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── web dashboard ──────────────────────────────────────────────────────────
@app.get("/", include_in_schema=False)
@app.get("/xceb", include_in_schema=False)
async def webui():
    if WEB_UI.exists():
        return HTMLResponse(WEB_UI.read_text(encoding="utf-8"))
    return HTMLResponse(
        "<h2>XCEB</h2><p>dashboard not found — run from the container "
        "where xceb.html ships beside the server.</p>"
    )


@app.on_event("startup")
async def _startup():
    asyncio.create_task(_scheduler_loop())
    logger.info("XCEB scheduler loop started")


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("XCEB_PORT", "8200"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
