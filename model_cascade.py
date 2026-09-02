"""
Model Cascade — Ryzen 5800X, CPU only, localhost:11434

Routes between qwen2.5:1.5b (~3s) and qwen2.5:3b (~6s) based on query complexity.
7b model excluded (43s on CPU, needs GPU).
"""

import httpx, time
from typing import Optional, Dict, Any
from dataclasses import dataclass


@dataclass
class Config:
    url: str = "http://localhost:11434"
    fast: str = "qwen2.5:1.5b"
    medium: str = "qwen2.5:3b"
    # threshold: below=fast, above=medium
    thresh: float = 0.35
    # token budgets
    classify_tok: int = 5
    fast_tok: int = 300
    med_tok: int = 500
    # timeouts
    classify_to: int = 15
    fast_to: int = 15
    med_to: int = 30


class Cascade:
    def __init__(self, cfg: Config = Config()):
        self.cfg = cfg
        self.http = httpx.AsyncClient(timeout=30.0)
        self.stats = {"total": 0, "fast": 0, "medium": 0, "avg_ms": 0.0, "_l": []}

    async def close(self):
        await self.http.aclose()

    async def gen(
        self, model: str, prompt: str, n: int, temp: float = 0.7, to: int = 30
    ) -> str:
        try:
            r = await self.http.post(
                f"{self.cfg.url}/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "keep_alive": "10m",
                    "options": {"num_predict": n, "temperature": temp},
                },
                timeout=to,
            )
            r.raise_for_status()
            return r.json().get("response", "").strip()
        except httpx.TimeoutException:
            return "[timeout]"
        except Exception as e:
            return f"[error: {e}]"

    async def classify(self, q: str) -> float:
        r = await self.gen(
            self.cfg.fast,
            f"Rate 0-9: 0-3=simple, 4-6=moderate, 7-9=complex.\nQuery: {q}\nNumber:",
            self.cfg.classify_tok,
            0.1,
            self.cfg.classify_to,
        )
        try:
            return min(max(float(r.strip().split()[0]) / 9.0, 0.0), 1.0)
        except:
            return 0.5

    async def ask(self, q: str, force: Optional[str] = None) -> Dict[str, Any]:
        t0 = time.time()
        self.stats["total"] += 1

        if force == "fast":
            c = 0.1
        elif force == "medium":
            c = 0.9
        else:
            c = await self.classify(q)

        if c < self.cfg.thresh:
            resp = await self.gen(
                self.cfg.fast, q, self.cfg.fast_tok, 0.7, self.cfg.fast_to
            )
            tier, model = "fast", self.cfg.fast
        else:
            resp = await self.gen(
                self.cfg.medium, q, self.cfg.med_tok, 0.6, self.cfg.med_to
            )
            tier, model = "medium", self.cfg.medium

        ms = round((time.time() - t0) * 1000)
        self.stats[tier] += 1
        self.stats["_l"].append(ms)
        self.stats["avg_ms"] = round(sum(self.stats["_l"]) / len(self.stats["_l"]))

        return {
            "response": resp,
            "tier": tier,
            "model": model,
            "complexity": round(c, 2),
            "latency_ms": ms,
            "stats": {k: v for k, v in self.stats.items() if k != "_l"},
        }


# ── FastAPI ──────────────────────────────────────────────────────
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Model Cascade", version="4.0.0")
cascade = Cascade()


class QReq(BaseModel):
    query: str
    force: Optional[str] = None


class QResp(BaseModel):
    response: str
    tier: str
    model: str
    complexity: float
    latency_ms: int
    stats: dict


@app.on_event("startup")
async def up():
    print("✓ Cascade ready → localhost:11434")
    print(f"  {cascade.cfg.fast} ~3s | {cascade.cfg.medium} ~6s")


@app.on_event("shutdown")
async def down():
    await cascade.close()


@app.post("/v1/chat/completions", response_model=QResp)
async def chat(r: QReq):
    try:
        return QResp(**await cascade.ask(r.query, r.force))
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/health")
async def health():
    return {"status": "ok", "models": [cascade.cfg.fast, cascade.cfg.medium]}


@app.get("/stats")
async def stats():
    return {k: v for k, v in cascade.stats.items() if k != "_l"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8099)
