"""Local memory server for Lilly AI — free alternative to TencentDB.

Provides the same REST API as TencentDB Agent Memory but uses local
JSON file storage instead of an external cloud service.

Endpoints:
  POST /v3/memory/atom          — write atomic fact
  GET  /v3/memory/atom/search   — search atomic facts
  POST /v3/memory/conversation  — write conversation
  GET  /v3/memory/conversation/search — search conversations
  POST /v3/memory/scenario      — write scenario
  GET  /v3/memory/scenario      — read scenario
  POST /v3/memory/core          — write core/persona
  GET  /v3/memory/core          — read core/persona
  GET  /health                  — health check

Run:
  python local_memory_server.py --port 8420
"""

import argparse
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel

app = FastAPI(title="Lilly Local Memory")

# ── Storage ──────────────────────────────────────────────────────────────────

STORAGE_DIR = Path(os.environ.get("LILLY_MEMORY_DIR", "/tmp/lilly_memory"))
STORAGE_DIR.mkdir(parents=True, exist_ok=True)

_ATOMS_FILE = STORAGE_DIR / "atoms.jsonl"
_CONVERSATIONS_FILE = STORAGE_DIR / "conversations.jsonl"
_SCENARIOS_DIR = STORAGE_DIR / "scenarios"
_CORE_FILE = STORAGE_DIR / "core.json"

_SCENARIOS_DIR.mkdir(exist_ok=True)


def _now() -> float:
    return time.time()


def _append_jsonl(path: Path, record: dict):
    with open(path, "a") as f:
        f.write(json.dumps({"ts": _now(), **record}) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        return [
            json.loads(line) for line in path.read_text().splitlines() if line.strip()
        ]
    except Exception:
        return []


# ── Models ───────────────────────────────────────────────────────────────────


class AtomWrite(BaseModel):
    team_id: str = "default"
    agent_id: str = "default"
    user_id: str = "default"
    content: str = ""
    metadata: dict | None = None


class ConversationWrite(BaseModel):
    team_id: str = "default"
    agent_id: str = "default"
    user_id: str = "default"
    role: str = "user"
    content: str = ""
    session_id: str | None = None


class ScenarioWrite(BaseModel):
    team_id: str = "default"
    agent_id: str = "default"
    user_id: str = "default"
    name: str = ""
    content: str = ""
    metadata: dict | None = None


class CoreWrite(BaseModel):
    team_id: str = "default"
    agent_id: str = "default"
    user_id: str = "default"
    content: str = ""
    metadata: dict | None = None


# ── Auth middleware ──────────────────────────────────────────────────────────


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    # Skip auth for health check
    if request.url.path == "/health":
        return await call_next(request)

    auth_header = request.headers.get("Authorization", "")
    api_key = request.headers.get("x-tdai-api-key", "")

    # Accept any non-empty key for local mode
    if not api_key and not auth_header:
        return JSONResponse(status_code=401, content={"error": "missing api key"})

    response = await call_next(request)
    return response


# ── Health ───────────────────────────────────────────────────────────────────


@app.get("/health")
async def health():
    return {"status": "ok", "mode": "local", "storage": str(STORAGE_DIR)}


# ── Atoms (L1) ───────────────────────────────────────────────────────────────


@app.post("/v3/memory/atom")
async def write_atom(data: AtomWrite):
    record = {
        "id": str(uuid.uuid4()),
        "team_id": data.team_id,
        "agent_id": data.agent_id,
        "user_id": data.user_id,
        "content": data.content,
        "metadata": data.metadata or {},
    }
    _append_jsonl(_ATOMS_FILE, record)
    return {"id": record["id"], "status": "ok"}


@app.get("/v3/memory/atom/search")
async def search_atoms(q: str = "", limit: int = 10):
    atoms = _read_jsonl(_ATOMS_FILE)
    if q:
        q_lower = q.lower()
        atoms = [a for a in atoms if q_lower in a.get("content", "").lower()]
    return {"results": atoms[:limit], "count": len(atoms[:limit])}


# ── Conversations (L0) ───────────────────────────────────────────────────────


@app.post("/v3/memory/conversation")
async def write_conversation(data: ConversationWrite):
    record = {
        "id": str(uuid.uuid4()),
        "team_id": data.team_id,
        "agent_id": data.agent_id,
        "user_id": data.user_id,
        "role": data.role,
        "content": data.content,
        "session_id": data.session_id or str(uuid.uuid4()),
    }
    _append_jsonl(_CONVERSATIONS_FILE, record)
    return {"id": record["id"], "status": "ok"}


@app.get("/v3/memory/conversation/search")
async def search_conversations(q: str = "", limit: int = 10):
    convs = _read_jsonl(_CONVERSATIONS_FILE)
    if q:
        q_lower = q.lower()
        convs = [c for c in convs if q_lower in c.get("content", "").lower()]
    return {"results": convs[:limit], "count": len(convs[:limit])}


# ── Scenarios (L2) ───────────────────────────────────────────────────────────


@app.post("/v3/memory/scenario")
async def write_scenario(data: ScenarioWrite):
    scenario_path = (
        _SCENARIOS_DIR
        / f"{data.team_id}_{data.agent_id}_{data.user_id}_{data.name}.json"
    )
    record = {
        "name": data.name,
        "content": data.content,
        "metadata": data.metadata or {},
        "updated_at": _now(),
    }
    scenario_path.write_text(json.dumps(record, indent=2))
    return {"status": "ok", "name": data.name}


@app.get("/v3/memory/scenario")
async def read_scenario(
    name: str,
    team_id: str = "default",
    agent_id: str = "default",
    user_id: str = "default",
):
    scenario_path = _SCENARIOS_DIR / f"{team_id}_{agent_id}_{user_id}_{name}.json"
    if not scenario_path.exists():
        raise HTTPException(status_code=404, detail="scenario not found")
    return json.loads(scenario_path.read_text())


@app.get("/v3/memory/scenario/list")
async def list_scenarios(
    team_id: str = "default", agent_id: str = "default", user_id: str = "default"
):
    prefix = f"{team_id}_{agent_id}_{user_id}_"
    scenarios = []
    for p in _SCENARIOS_DIR.glob(f"{prefix}*.json"):
        scenarios.append(p.name[len(prefix) : -5])
    return {"scenarios": scenarios}


# ── Core (L3) ────────────────────────────────────────────────────────────────


@app.post("/v3/memory/core")
async def write_core(data: CoreWrite):
    record = {
        "team_id": data.team_id,
        "agent_id": data.agent_id,
        "user_id": data.user_id,
        "content": data.content,
        "metadata": data.metadata or {},
        "updated_at": _now(),
    }
    _CORE_FILE.write_text(json.dumps(record, indent=2))
    return {"status": "ok"}


@app.get("/v3/memory/core")
async def read_core(
    team_id: str = "default", agent_id: str = "default", user_id: str = "default"
):
    if not _CORE_FILE.exists():
        return {"content": "", "metadata": {}}
    data = json.loads(_CORE_FILE.read_text())
    if data.get("team_id") != team_id or data.get("agent_id") != agent_id:
        return {"content": "", "metadata": {}}
    return data


# ── Run ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8420)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port)
