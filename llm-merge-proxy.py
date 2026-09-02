#!/usr/bin/env python3
"""
LLM Cascade Proxy — exposes OpenAI-compatible endpoints on :5000.

Behavior:
  /v1/chat/completions
    - Calls the fast model first and returns its reply immediately as the buffer.
    -同时在后台调用 quality model.
    - When quality finishes, it is appended as a follow-up assistant message
      in the same HTTP response under `choices[0].quality_followup`.
    - For simple/short messages, quality may be skipped entirely.

  /v1/chat/cascade
    - Streams two SSE events:
        1. fast model delta (immediate buffer)
        2. quality model delta (refined answer, arrives later)

Env:
  OLLAMA_BASE   – upstream Ollama host (default http://100.73.249.14:11434)
  FAST_MODEL    – fast/low-latency model tag (default draft-fast:latest)
  QUALITY_MODEL – higher-quality model tag (default deepseek-coder-v2:16b)
  PORT          – listen port (default 5000)
  HOST          – listen interface (default 0.0.0.0)
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections import deque
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

# ── Config ──────────────────────────────────────────────────────────
OLLAMA_BASE = os.getenv("OLLAMA_BASE", "http://100.73.249.14:11434").rstrip("/")
FAST_MODEL = os.getenv("FAST_MODEL", "qwen2.5:1.5b")
QUALITY_MODEL = os.getenv("QUALITY_MODEL", "deepseek-coder-v2:16b")
LISTEN_PORT = int(os.getenv("PORT", "5006"))
LISTEN_HOST = os.getenv("HOST", "0.0.0.0")
BACKEND = os.getenv("LLM_BACKEND", "ollama")  # "ollama" or "bedrock"

# AWS Bedrock config
BEDROCK_MODEL = os.getenv("BEDROCK_MODEL", "anthropic.claude-3-5-sonnet-20240620-v1:0")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

# Timing/learning store
_cascade_timings: deque[dict] = deque(maxlen=200)

app = FastAPI(title="LLM Cascade Proxy")


# ── Helpers ─────────────────────────────────────────────────────────
def _ollama_chat(model: str, messages: list[dict], options: dict | None = None) -> dict:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "keep_alive": "5m",
    }
    if options:
        payload["options"] = options
    with httpx.Client(timeout=300.0) as client:
        r = client.post(f"{OLLAMA_BASE}/api/chat", json=payload)
        r.raise_for_status()
        return r.json()


async def _ollama_chat_async(
    model: str,
    messages: list[dict],
    options: dict | None = None,
    timeout: float = 300.0,
) -> dict:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "keep_alive": "5m",
    }
    if options:
        payload["options"] = options
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(f"{OLLAMA_BASE}/api/chat", json=payload)
        r.raise_for_status()
        return r.json()


def _extract_content(resp: dict) -> str:
    msg = resp.get("message") or {}
    return (msg.get("content") or "").strip()


def _extract_thinking(resp: dict) -> str:
    msg = resp.get("message") or {}
    return (msg.get("thinking") or "").strip()


def _openai_style(content: str, model_used: str, elapsed: float) -> dict:
    return {
        "id": f"cascade-{int(time.time() * 1000)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_used,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def _record_timing(fast_elapsed: float, quality_elapsed: float, msg_len: int):
    _cascade_timings.append(
        {
            "ts": time.time(),
            "fast_elapsed": fast_elapsed,
            "quality_elapsed": quality_elapsed,
            "msg_len": msg_len,
        }
    )


def _estimate_quality_delay() -> float:
    """From observed timings, estimate how long quality model takes after fast."""
    if not _cascade_timings:
        return 0.0
    # Use median of fast+quality total as a rough estimate
    delays = [
        t["quality_elapsed"]
        for t in _cascade_timings
        if t.get("quality_elapsed", 0) > 0
    ]
    if not delays:
        return 0.0
    delays.sort()
    return delays[len(delays) // 2]


# ── Health ──────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    est = _estimate_quality_delay()
    return {
        "ok": True,
        "fast": FAST_MODEL,
        "quality": QUALITY_MODEL,
        "ollama": OLLAMA_BASE,
        "estimated_quality_delay_s": round(est, 2),
        "observed_cascades": len(_cascade_timings),
    }


@app.get("/v1/health")
async def openai_health():
    return {"ok": True}


# ── Core cascade endpoint ─────────────────────────────────────────────
@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    t0 = time.time()
    try:
        body = await request.json()
    except Exception as e:
        return JSONResponse({"error": f"json parse: {e}"}, status_code=400)
    messages = body.get("messages", [])
    if not messages:
        return JSONResponse({"error": "no messages"}, status_code=400)

    stream = body.get("stream", False)
    if stream:
        return JSONResponse(
            {"error": "streaming not supported here, use /v1/chat/cascade"},
            status_code=400,
        )

    fast_model = body.get("fast_model", FAST_MODEL)
    quality_model = body.get("quality_model", QUALITY_MODEL)
    merge_model = body.get("merge_model", QUALITY_MODEL)

    # For very short messages, skip quality entirely
    last_user = next((m for m in reversed(messages) if m.get("role") == "user"), None)
    msg_len = len((last_user or {}).get("content", ""))
    skip_quality = msg_len < 20

    fast_opts = {"num_predict": 256, "num_ctx": 2048, "temperature": 0.3}
    quality_opts = {"num_predict": 1024, "num_ctx": 4096, "temperature": 0.5}

    # 1. Fast model — immediate buffer
    fast_start = time.time()
    try:
        fast_resp = await _ollama_chat_async(
            fast_model, messages, options=fast_opts, timeout=120.0
        )
    except Exception as e:
        return JSONResponse({"error": f"fast model failed: {e}"}, status_code=502)
    fast_text = _extract_content(fast_resp)
    fast_elapsed = time.time() - fast_start
    fast_thinking = _extract_thinking(fast_resp)
    print(f"[cascade] fast={fast_elapsed:.2f}s text={len(fast_text)}", flush=True)

    if not fast_text:
        return JSONResponse(
            {"error": "fast model returned empty response"}, status_code=502
        )

    # Build the base response from fast model
    result = _openai_style(fast_text, fast_model, fast_elapsed)
    result["cascade"] = {
        "fast_model": fast_model,
        "fast_elapsed_s": round(fast_elapsed, 3),
        "fast_thinking": fast_thinking[:200] if fast_thinking else "",
        "skip_quality": skip_quality,
    }

    # 2. Quality model — background refinement (non-blocking)
    async def _run_quality(fast_text: str, fast_elapsed: float):
        if skip_quality:
            print(f"[cascade] skipping quality (msg_len={msg_len})", flush=True)
            return
        quality_messages = messages + [
            {
                "role": "assistant",
                "content": f"[FAST_BUFFER]\n{fast_text}\n[/FAST_BUFFER]\n\nNow provide a refined, more detailed answer building on the above.",
            }
        ]
        quality_start = time.time()
        try:
            quality_resp = await asyncio.wait_for(
                _ollama_chat_async(
                    quality_model, quality_messages, options=quality_opts, timeout=300.0
                ),
                timeout=295.0,
            )
            quality_text = _extract_content(quality_resp)
            quality_elapsed = time.time() - quality_start
            print(
                f"[cascade] quality={quality_elapsed:.2f}s text={len(quality_text)}",
                flush=True,
            )

            if quality_text:
                _record_timing(fast_elapsed, quality_elapsed, msg_len)
        except asyncio.TimeoutError:
            print(f"[cascade] quality timeout after 295s", flush=True)
        except Exception as e:
            print(f"[cascade] quality error: {e}", flush=True)

    if not skip_quality:
        asyncio.create_task(_run_quality(fast_text, fast_elapsed))

    total = time.time() - t0
    result["cascade"]["total_elapsed_s"] = round(total, 3)
    result["cascade"]["skip_quality"] = skip_quality
    return JSONResponse(result)


# ── Streaming cascade endpoint ──────────────────────────────────────
@app.post("/v1/chat/cascade")
async def chat_cascade(request: Request):
    """Stream two SSE events: fast first, quality second."""
    body = await request.json()
    messages = body.get("messages", [])
    fast_model = body.get("fast_model", FAST_MODEL)
    quality_model = body.get("quality_model", QUALITY_MODEL)
    merge_model = body.get("merge_model", QUALITY_MODEL)

    last_user = next((m for m in reversed(messages) if m.get("role") == "user"), None)
    msg_len = len((last_user or {}).get("content", ""))
    skip_quality = msg_len < 20

    fast_opts = {"num_predict": 256, "num_ctx": 2048, "temperature": 0.3}
    quality_opts = {"num_predict": 1024, "num_ctx": 4096, "temperature": 0.5}

    async def event_stream():
        # Phase 1: fast model
        fast_start = time.time()
        fast_text = ""
        fast_elapsed = 0.0
        try:
            fast_resp = await _ollama_chat_async(
                fast_model, messages, options=fast_opts, timeout=120.0
            )
            fast_text = _extract_content(fast_resp)
            fast_elapsed = time.time() - fast_start
            fast_thinking = _extract_thinking(fast_resp)
            if fast_text:
                payload = json.dumps(
                    {
                        "phase": "fast",
                        "model": fast_model,
                        "delta": fast_text,
                        "thinking": fast_thinking[:200] if fast_thinking else "",
                        "elapsed_s": round(fast_elapsed, 3),
                    }
                )
                yield f"data: {payload}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'phase': 'fast', 'error': str(e)})}\n\n"

        # Phase 2: quality model
        if not skip_quality and fast_text:
            quality_messages = messages + [
                {
                    "role": "assistant",
                    "content": f"[FAST_BUFFER]\n{fast_text}\n[/FAST_BUFFER]\n\nNow provide a refined, more detailed answer building on the above.",
                }
            ]
            quality_start = time.time()
            try:
                quality_resp = await asyncio.wait_for(
                    _ollama_chat_async(
                        quality_model,
                        quality_messages,
                        options=quality_opts,
                        timeout=300.0,
                    ),
                    timeout=295.0,
                )
                quality_text = _extract_content(quality_resp)
                quality_elapsed = time.time() - quality_start
                if quality_text:
                    payload = json.dumps(
                        {
                            "phase": "quality",
                            "model": quality_model,
                            "delta": quality_text,
                            "elapsed_s": round(quality_elapsed, 3),
                        }
                    )
                    yield f"data: {payload}\n\n"
                    _record_timing(fast_elapsed, quality_elapsed, msg_len)
            except Exception as e:
                yield f"data: {json.dumps({'phase': 'quality', 'error': str(e)})}\n\n"

        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ── Warmup ──────────────────────────────────────────────────────────
@app.post("/warmup")
async def warmup():
    results = {}
    for model in (FAST_MODEL, QUALITY_MODEL):
        try:
            r = await _ollama_chat_async(
                model,
                [{"role": "user", "content": "warmup"}],
                {"num_predict": 1, "num_ctx": 256},
            )
            results[model] = {"ok": True, "content": _extract_content(r)}
        except Exception as e:
            results[model] = {"ok": False, "error": str(e)}
    return results


# ── Models listing ──────────────────────────────────────────────────
@app.get("/v1/models")
async def list_models():
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{OLLAMA_BASE}/api/tags")
            r.raise_for_status()
            data = r.json()
    except Exception:
        data = {"models": []}
    return {
        "object": "list",
        "data": [
            {"id": m.get("name", ""), "object": "model", "owned_by": "ollama"}
            for m in data.get("models", [])
        ],
    }


# ── Ollama-native passthrough ─────────────────────────────────────
@app.post("/api/chat")
async def ollama_api_chat(request: Request):
    body = await request.json()
    model = body.get("model", FAST_MODEL)
    messages = body.get("messages", [])
    options = body.get("options", {})
    stream = body.get("stream", False)
    payload = {
        "model": model,
        "messages": messages,
        "stream": stream,
        "keep_alive": "5m",
    }
    if options:
        payload["options"] = options
    async with httpx.AsyncClient(timeout=300.0) as client:
        r = await client.post(f"{OLLAMA_BASE}/api/chat", json=payload)
        r.raise_for_status()
        return JSONResponse(content=r.json())


@app.get("/api/tags")
async def ollama_api_tags():
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{OLLAMA_BASE}/api/tags")
        r.raise_for_status()
        return JSONResponse(content=r.json())


# ── Entrypoint ──────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    print(
        f"[cascade-proxy] listening on {LISTEN_HOST}:{LISTEN_PORT}"
        f"  fast={FAST_MODEL}  quality={QUALITY_MODEL}"
        f"  upstream={OLLAMA_BASE}"
    )
    uvicorn.run(app, host=LISTEN_HOST, port=LISTEN_PORT, log_level="info")
