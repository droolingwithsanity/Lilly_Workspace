"""Lilly Memory Gateway — remote 1.5TB persistent memory server.

Implements the exact TencentDB Agent Memory gateway REST contract that
``tencentdb_memory.py`` (the in-container client) already speaks, so the
client needs ZERO changes:

  v1 (session_key based):
    POST /capture                 — store a conversation turn to L0
    POST /recall                  — semantic recall from L0 (substring + simple scoring)

  v3 (team/agent/user isolation):
    /v3/conversation/add|query|search|count|delete
    /v3/atomic/update|query|search|delete|count
    /v3/scenario/ls|read|write|rm|count
    /v3/core/read|write|count

  GET /health — availability probe

Storage is JSONL on the 1.4T drive (/my_data/lilly_memory by default).
Run:  python3 memory_gateway_server.py --port 8420
"""

from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI(title="Lilly Memory Gateway")

STORAGE_DIR = Path(os.environ.get("LILLY_MEMORY_DIR", "/my_data/lilly_memory"))
STORAGE_DIR.mkdir(parents=True, exist_ok=True)

_ATOMS = STORAGE_DIR / "atoms.jsonl"
_CONVS = STORAGE_DIR / "conversations.jsonl"
_SCEN_DIR = STORAGE_DIR / "scenarios"
_CORE_FILE = STORAGE_DIR / "core.json"
SCEN_DIR_OBJ = _SCEN_DIR
SCEN_DIR_OBJ.mkdir(exist_ok=True)


def _now() -> float:
    return time.time()


def _append(path: Path, record: dict) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": _now(), **record}, ensure_ascii=False) + "\n")


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except Exception:
        return []


def _ok(data: Any = None) -> dict:
    return {"code": 0, "message": "ok", "data": data or {}}


def _records_match(rec: dict, iso: dict) -> bool:
    # Memory is scoped at team + agent (single-user personal assistant): the
    # client always sends user_id="alex" while capture-created records default
    # to user_id="default", so user_id is intentionally NOT enforced.
    for k in ("team_id", "agent_id"):
        if k in iso and iso[k]:
            if rec.get(k) != iso[k]:
                return False
    # NOTE: session_id is intentionally NOT enforced here — atoms/scenarios/core
    # are shared across sessions (the client always sends a session_id, but those
    # records never carry one). Only conversation endpoints filter by session.
    return True


def _isolation(req: Request) -> dict:
    try:
        body = json.loads(await_body(req)) if hasattr(req, "_body") else {}
    except Exception:
        body = {}
    return {
        "team_id": body.get("team_id", "default"),
        "agent_id": body.get("agent_id", "default"),
        "user_id": body.get("user_id", "default"),
        "session_id": body.get("session_id"),
    }


async def await_body(req: Request) -> str:
    raw = await req.body()
    return raw.decode("utf-8")


def _req_iso(body: dict) -> dict:
    return {
        "team_id": body.get("team_id", "default"),
        "agent_id": body.get("agent_id", "default"),
        "user_id": body.get("user_id", "default"),
        "session_id": body.get("session_id"),
    }


# ── Auth ─────────────────────────────────────────────────────────────────────


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if request.url.path == "/health":
        return await call_next(request)
    api_key = request.headers.get("x-tdai-api-key", "")
    auth = request.headers.get("Authorization", "")
    if not api_key and not auth:
        return JSONResponse(status_code=401, content={"error": "missing api key"})
    return await call_next(request)


# ── Health ───────────────────────────────────────────────────────────────────


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "mode": "gateway",
        "storage": str(STORAGE_DIR),
        "atoms": len(_read(_ATOMS)),
        "conversations": len(_read(_CONVS)),
    }


# ── v1: capture / recall (session_key based) ────────────────────────────────


class CaptureBody(BaseModel):
    user_content: str = ""
    assistant_content: str = ""
    session_key: str = "lilly-chat"


@app.post("/capture")
async def capture(data: CaptureBody):
    conv_id = str(uuid.uuid4())
    record = {
        "id": conv_id,
        "role": "assistant",
        "content": data.assistant_content,
        "session_id": data.session_key,
        "team_id": "default",
        "agent_id": "default",
        "user_id": "default",
    }
    _append(_CONVS, record)
    record["id"] = str(uuid.uuid4())
    record["role"] = "user"
    record["content"] = data.user_content
    _append(_CONVS, record)

    # Simulate the LLM pipeline: the client sends [ATOM] key: value and
    # [BROADCAST:cat] payloads through capture — persist them as L1 atoms so
    # /v3/atomic/search + recall_broadcasts work even without an LLM extractor.
    _ingest_atoms_from_capture(data.user_content)
    return {"ok": True, "id": conv_id, "session_key": data.session_key}


def _ingest_atoms_from_capture(user_content: str) -> None:
    lines = (user_content or "").splitlines()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith("[ATOM]"):
            payload = line[len("[ATOM]") :].strip()
            key, _, value = payload.partition(":")
            if key.strip():
                _atomic_upsert(key.strip(), value.strip(), ["brain", "fact"])
        elif line.startswith("[BROADCAST:"):
            m = line[1 : line.find("]")]  # "BROADCAST:category"
            category = m.split(":", 1)[1] if ":" in m else "announcement"
            msg = line[line.find("]") + 1 :].strip()
            _atomic_upsert(
                f"broadcast.{int(time.time() * 1000)}",
                msg,
                ["broadcast", category, "hive-mind"],
            )


def _atomic_upsert(key: str, value: str, tags: list[str]) -> None:
    atoms = _read(_ATOMS)
    for a in atoms:
        if a.get("id") == key:
            a["content"] = value
            a["tags"] = list(dict.fromkeys(a.get("tags", []) + tags))
            a["updated_ts"] = _now()
            _rewrite(_ATOMS, atoms)
            return
    atoms.append(
        {
            "id": key,
            "content": value,
            "tags": tags,
            "team_id": "default",
            "agent_id": "default",
            "user_id": "default",
            "ts": _now(),
        }
    )
    _rewrite(_ATOMS, atoms)


class RecallBody(BaseModel):
    query: str = ""
    session_key: str = "lilly-chat"
    top_k: int = 10


@app.post("/recall")
async def recall(data: RecallBody):
    convs = _read(_CONVS)
    scored = []
    q = (data.query or "").lower()
    for rec in convs:
        if rec.get("session_id") != data.session_key:
            continue
        content = rec.get("content", "")
        score = _score(content, q)
        if score > 0:
            scored.append((score, rec))
    scored.sort(key=lambda t: t[0], reverse=True)
    top = [r for _, r in scored[: data.top_k]]
    context = "\n".join(
        f"[{r.get('ts', 0):.0f}] {r.get('role', '?')}: {r.get('content', '')}"
        for r in top
    )
    return {
        "code": 0,
        "data": {"context": context, "total": len(top)},
        "context": context,
        "total": len(top),
    }


def _normalize(text: str) -> str:
    # Underscores/hyphens/dots are common in atom keys (brain.fact.user_name).
    # Treat them as word separators so naive recall still matches semantically.
    return (text or "").replace("_", " ").replace("-", " ").replace(".", " ")


def _score(content: str, q: str) -> float:
    if not q:
        return 1.0
    cl = _normalize(content).lower()
    ql = _normalize(q).lower()
    if not ql.strip():
        return 1.0
    if ql in cl:
        return 10.0
    q_tokens = set(ql.split())
    c_tokens = set(cl.split())
    if not q_tokens:
        return 0.0
    return len(q_tokens & c_tokens) / len(q_tokens)


# ── v3: L1 atoms ─────────────────────────────────────────────────────────────


class AtomBody(BaseModel):
    team_id: str = "default"
    agent_id: str = "default"
    user_id: str = "default"
    session_id: str | None = None
    id: str = ""
    content: str = ""
    tags: list[str] = []
    query: str = ""
    limit: int = 10
    ids: list[str] = []
    keys: list[str] = []


@app.post("/v3/atomic/update")
async def atomic_update(body: AtomBody):
    # Upsert semantics: create if new, update if exists. (The original TencentDB
    # gateway was update-only returning 404 — but the in-container client's
    # record_vision / record_sense_memory / record_battery paths call this
    # without a 404 fallback, so upsert is required for them to work.)
    if not body.id:
        return JSONResponse(
            status_code=400, content={"code": 400, "message": "atom id required"}
        )
    atoms = _read(_ATOMS)
    iso = _req_iso(body.model_dump())
    for a in atoms:
        if a.get("id") == body.id:
            a["content"] = body.content
            a["tags"] = body.tags or []
            a["updated_ts"] = _now()
            _rewrite(_ATOMS, atoms)
            return _ok({"id": body.id, "content": body.content})
    atoms.append(
        {
            "id": body.id,
            "content": body.content,
            "tags": body.tags or [],
            "team_id": iso["team_id"],
            "agent_id": iso["agent_id"],
            "user_id": iso["user_id"],
            "ts": _now(),
        }
    )
    _rewrite(_ATOMS, atoms)
    return _ok({"id": body.id, "content": body.content})


@app.post("/v3/atomic/query")
async def atomic_query(body: AtomBody):
    iso = _req_iso(body.model_dump())
    atoms = [a for a in _read(_ATOMS) if _records_match(a, iso)]
    if body.ids:
        atoms = [a for a in atoms if a.get("id") in body.ids]
    return _ok({"items": atoms})


@app.post("/v3/atomic/search")
async def atomic_search(body: AtomBody):
    iso = _req_iso(body.model_dump())
    atoms = [a for a in _read(_ATOMS) if _records_match(a, iso)]
    q = (body.query or "").lower()
    if q:

        def combined(a_rec: dict) -> float:
            return (
                _score(a_rec.get("content", ""), q)
                + _score(a_rec.get("id", ""), q)
                + _score(" ".join(a_rec.get("tags", [])), q)
            )

        scored = [(combined(a), a) for a in atoms]
        scored.sort(key=lambda t: t[0], reverse=True)
        atoms = [a for s, a in scored if s > 0][: body.limit]
    else:
        atoms = atoms[-body.limit :]
    return _ok({"items": atoms, "count": len(atoms)})


def _rewrite(path: Path, records: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


@app.post("/v3/atomic/delete")
async def atomic_delete(body: AtomBody):
    iso = _req_iso(body.model_dump())
    atoms = [
        a
        for a in _read(_ATOMS)
        if not (a.get("id") in (body.keys or body.ids or []) and _records_match(a, iso))
    ]
    _rewrite(_ATOMS, atoms)
    return _ok({"deleted": True})


@app.post("/v3/atomic/count")
async def atomic_count(body: AtomBody):
    iso = _req_iso(body.model_dump())
    return _ok({"count": len([a for a in _read(_ATOMS) if _records_match(a, iso)])})


# ── v3: L0 conversations ─────────────────────────────────────────────────────


class ConvBody(BaseModel):
    team_id: str = "default"
    agent_id: str = "default"
    user_id: str = "default"
    session_id: str | None = None
    session_key: str | None = None
    messages: list[dict] = []
    query: str = ""
    limit: int = 20
    offset: int = 0


@app.post("/v3/conversation/add")
async def conversation_add(body: ConvBody):
    iso = _req_iso(body.model_dump())
    session_id = body.session_id or body.session_key or str(uuid.uuid4())
    added = []
    for m in body.messages:
        rec = {
            "id": str(uuid.uuid4()),
            "role": m.get("role", "user"),
            "content": m.get("content", ""),
            "session_id": session_id,
            "team_id": iso["team_id"],
            "agent_id": iso["agent_id"],
            "user_id": iso["user_id"],
        }
        _append(_CONVS, rec)
        added.append(rec["id"])
    return _ok({"ids": added, "session_id": session_id})


@app.post("/v3/conversation/query")
async def conversation_query(body: ConvBody):
    iso = _req_iso(body.model_dump())
    convs = [c for c in _read(_CONVS) if _records_match(c, iso)]
    convs.sort(key=lambda c: c.get("ts", 0))
    return _ok({"messages": convs[body.offset : body.offset + body.limit]})


@app.post("/v3/conversation/search")
async def conversation_search(body: ConvBody):
    iso = _req_iso(body.model_dump())
    convs = [c for c in _read(_CONVS) if _records_match(c, iso)]
    q = (body.query or "").lower()
    if q:
        scored = [(_score(c.get("content", ""), q), c) for c in convs]
        scored.sort(key=lambda t: t[0], reverse=True)
        convs = [c for s, c in scored if s > 0][: body.limit]
    else:
        convs = convs[-body.limit :]
    return _ok({"items": convs, "count": len(convs)})


@app.post("/v3/conversation/count")
async def conversation_count(body: ConvBody):
    iso = _req_iso(body.model_dump())
    return _ok({"count": len([c for c in _read(_CONVS) if _records_match(c, iso)])})


@app.post("/v3/conversation/delete")
async def conversation_delete(body: ConvBody):
    iso = _req_iso(body.model_dump())
    keep = [
        c
        for c in _read(_CONVS)
        if not (
            c.get("session_id") == (body.session_id or body.session_key)
            and _records_match(c, iso)
        )
    ]
    _rewrite(_CONVS, keep)
    return _ok({"deleted": True})


# ── v3: L2 scenarios ─────────────────────────────────────────────────────────


class ScenarioBody(BaseModel):
    team_id: str = "default"
    agent_id: str = "default"
    user_id: str = "default"
    session_id: str | None = None
    path: str = ""
    content: str = ""


def _scenario_file(iso: dict, path: str) -> Path:
    return SCEN_DIR_OBJ / f"{iso['team_id']}_{iso['agent_id']}_{iso['user_id']}_{path}"


@app.post("/v3/scenario/ls")
async def scenario_ls(body: ScenarioBody):
    iso = _req_iso(body.model_dump())
    prefix = f"{iso['team_id']}_{iso['agent_id']}_{iso['user_id']}_"
    entries = [p.name[len(prefix) :] for p in SCEN_DIR_OBJ.glob(f"{prefix}*")]
    return _ok({"entries": entries, "count": len(entries)})


@app.post("/v3/scenario/read")
async def scenario_read(body: ScenarioBody):
    iso = _req_iso(body.model_dump())
    f = _scenario_file(iso, body.path)
    if not f.exists():
        return JSONResponse(
            status_code=404, content={"code": 404, "message": "scenario not found"}
        )
    return _ok(
        {
            "path": body.path,
            "content": f.read_text(encoding="utf-8"),
            "mtime": f.stat().st_mtime,
        }
    )


@app.post("/v3/scenario/write")
async def scenario_write(body: ScenarioBody):
    # Upsert semantics: create if new, overwrite if exists. (Same reasoning as
    # atomic/update — the client's record_vision / record_motion /
    # record_air_quality call write_scenario for fresh scenarios with no fallback.)
    iso = _req_iso(body.model_dump())
    f = _scenario_file(iso, body.path)
    f.write_text(body.content, encoding="utf-8")
    return _ok({"path": body.path})


@app.post("/v3/scenario/rm")
async def scenario_rm(body: ScenarioBody):
    iso = _req_iso(body.model_dump())
    f = _scenario_file(iso, body.path)
    if f.exists():
        f.unlink()
    return _ok({"removed": body.path})


@app.post("/v3/scenario/count")
async def scenario_count(body: ScenarioBody):
    iso = _req_iso(body.model_dump())
    prefix = f"{iso['team_id']}_{iso['agent_id']}_{iso['user_id']}_"
    return _ok({"count": len(list(SCEN_DIR_OBJ.glob(f"{prefix}*")))})


# ── v3: L3 core ──────────────────────────────────────────────────────────────


class CoreBody(BaseModel):
    team_id: str = "default"
    agent_id: str = "default"
    user_id: str = "default"
    session_id: str | None = None
    content: str = ""


@app.post("/v3/core/read")
async def core_read(body: CoreBody):
    iso = _req_iso(body.model_dump())
    if not _CORE_FILE.exists():
        return _ok({"content": "", "metadata": {}})
    try:
        data = json.loads(_CORE_FILE.read_text(encoding="utf-8"))
    except Exception:
        data = {"content": ""}
    if data.get("team_id") != iso["team_id"] or data.get("agent_id") != iso["agent_id"]:
        return _ok({"content": "", "metadata": {}})
    return _ok(
        {"content": data.get("content", ""), "metadata": data.get("metadata", {})}
    )


@app.post("/v3/core/write")
async def core_write(body: CoreBody):
    iso = _req_iso(body.model_dump())
    record = {
        "team_id": iso["team_id"],
        "agent_id": iso["agent_id"],
        "user_id": iso["user_id"],
        "content": body.content,
        "metadata": {},
        "updated_at": _now(),
    }
    _CORE_FILE.write_text(
        json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return _ok({"ok": True})


@app.post("/v3/core/count")
async def core_count(body: CoreBody):
    iso = _req_iso(body.model_dump())
    if _CORE_FILE.exists():
        try:
            data = json.loads(_CORE_FILE.read_text(encoding="utf-8"))
            if (
                data.get("team_id") == iso["team_id"]
                and data.get("agent_id") == iso["agent_id"]
            ):
                return _ok({"count": 1 if data.get("content") else 0})
        except Exception:
            pass
    return _ok({"count": 0})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8420)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port)
