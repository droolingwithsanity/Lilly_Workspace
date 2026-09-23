"""
Lilly Orchestrator Runtime — master persona + strict routing rules.

Provides:
  - ORCHESTRATOR_PROMPT        — system prompt prefix for Lilly's orchestrator identity
  - LILLY_TOOLS_SCHEMA         — JSON tool schema for vision/build/execute routing
  - tools_schema_json()        — returns schema as JSON string
  - route_task(text)           — classifies user message into vision|build|execute|converse
  - remember_route(decision)   — stores routing decisions for observability
  - dispatch_to_agent(pipeline, payload) — dispatches to the correct pipeline handler
  - register_pipeline_handlers(handlers) — registers concrete handler functions
  - register_runtime(ctx)      — registers runtime metadata
  - orchestrator_status()      — returns status info
  - build_orchestrator_system_prompt(user_name, include_schema) — builds the system prompt
"""

import json
import time
import logging
import re
from collections import deque

logger = logging.getLogger("lilly-orchestrator")

# ─── System Prompt ───────────────────────────────────────────────────────────

ORCHESTRATOR_PROMPT = """You are Lilly, the master orchestrator of a 9-avatar hive mind.

## Your Role
You are the SOLE user-facing interface. You route requests to three specialized pipelines:
- **Vision** (see): Camera, face detection, object recognition, scene understanding
- **Builder** (build): Code, APIs, automation, projects, multi-step creation
- **Execution** (do): Quick actions, fetches, lookups, phone commands, immediate tasks

## Routing Rules
1. ALWAYS respond as Lilly first — acknowledge the request naturally
2. Classify the request into ONE pipeline: vision | build | execute | converse
3. If the request needs a pipeline, announce which pipeline you're activating
4. For complex tasks, you may chain pipelines (e.g., Vision → Build)
5. Stay conversational — never output raw JSON tool calls to the user
6. You can delegate to any of the 9 avatars in the hive by calling them by name

## Hive Mind — Your Team
You have 8 avatar teammates, each with unique skills:
- 🦊 **Fox** — Creative Strategist. Marketing, branding, copywriting, design direction.
- 🐱 **Cat** — Precision Analyst. Data analysis, research, fact-checking, attention to detail.
- 🐻 **Bear** — Steadfast Guardian. Security, monitoring, reliability, protection.
- 🐰 **Bunny** — Energetic Scout. Exploration, discovery, new tools, quick experiments.
- 🦉 **Owl** — Wisdom Keeper. Strategy, planning, knowledge management, deep thinking.
- 🦌 **Deer** — Gentle Healer. Wellness, reminders, scheduling, nurturing routines.
- 🐺 **Wolf** — Fierce Protector. Aggressive defense, threat response, priority triage.
- 🦝 **Raccoon** — Tech Tinkerer. Hardware, debugging, system administration, hacking.

When a request matches an avatar's strength, mention you're pulling them in:
"Let me get Fox on this — she's our creative strategist."
Then respond AS Lilly with the team's combined insight.

## Personality
You are warm, capable, and in command. You don't just answer questions — you
orchestrate solutions. You know your team's strengths and delegate naturally.
You're the alpha, but you respect every member of the hive."""

# ─── Tool Schema ─────────────────────────────────────────────────────────────

LILLY_TOOLS_SCHEMA = {
    "name": "lilly_orchestrator",
    "description": "Lilly's routing and dispatch tools for vision/build/execute pipelines",
    "tools": [
        {
            "name": "route_to_vision",
            "description": "Route a request to the Vision pipeline (camera, face detection, object recognition)",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "What to look at or detect"},
                    "source": {"type": "string", "enum": ["webcam", "upload", "memory"], "default": "webcam"},
                },
                "required": ["task"],
            },
        },
        {
            "name": "route_to_build",
            "description": "Route a request to the Builder pipeline (code, APIs, automation, projects)",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "What to build or create"},
                    "language": {"type": "string", "description": "Preferred language/framework if specified"},
                },
                "required": ["task"],
            },
        },
        {
            "name": "route_to_execute",
            "description": "Route a request to the Execution pipeline (quick actions, fetches, phone commands)",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "What to do immediately"},
                },
                "required": ["task"],
            },
        },
        {
            "name": "delegate_to_avatar",
            "description": "Delegate a task to a specific hive mind avatar",
            "parameters": {
                "type": "object",
                "properties": {
                    "avatar": {"type": "string", "enum": ["fox", "cat", "bear", "bunny", "owl", "deer", "wolf", "raccoon"]},
                    "task": {"type": "string", "description": "What to delegate"},
                },
                "required": ["avatar", "task"],
            },
        },
    ],
}


def tools_schema_json() -> str:
    """Return the tool schema as a JSON string."""
    return json.dumps(LILLY_TOOLS_SCHEMA)


# ─── Routing ─────────────────────────────────────────────────────────────────

# Keyword patterns for pipeline classification
_VISION_KEYWORDS = re.compile(
    r"\b(look|see|camera|photo|picture|image|face|detect|recogni[zs]e|scan|"
    r"identify|who is|what do you see|show me|webcam|screenshot|video|frame)\b",
    re.IGNORECASE,
)

_BUILD_KEYWORDS = re.compile(
    r"\b(build|create|make|write|code|program|develop|design|implement|"
    r"automate|script|function|api|server|website|app|dashboard|refactor|"
    r"fix bug|debug|deploy|setup|configure|install)\b",
    re.IGNORECASE,
)

_EXECUTE_KEYWORDS = re.compile(
    r"\b(run|exec|fetch|download|upload|send|post|put|delete|curl|wget|"
    r"check|status|get|load|open|close|start|stop|restart|kill|ping|"
    r"call|text|message|remind|timer|alarm|set|update)\b",
    re.IGNORECASE,
)

# Avatar name → delegation
_AVATAR_NAMES = re.compile(
    r"\b(fox|cat|bear|bunny|owl|deer|wolf|raccoon|lilly)\b", re.IGNORECASE
)

# Routing history (bounded)
_routing_history: deque = deque(maxlen=200)

# Pipeline handlers (registered by lilly_ai.py)
_pipeline_handlers: dict = {}

# Runtime context
_runtime_ctx: dict = {}


def route_task(text: str) -> dict:
    """Classify a user message into one of 4 pipelines.

    Returns:
        {
            "pipeline": "vision" | "build" | "execute" | "converse",
            "confidence": 0.0–1.0,
            "matched": [...keywords matched...],
            "payload": text,
        }
    """
    if not text or not text.strip():
        return {"pipeline": "converse", "confidence": 0.0, "matched": [], "payload": text}

    t = text.strip()
    scores = {"vision": 0, "build": 0, "execute": 0}
    matched = {"vision": [], "build": [], "execute": []}

    # Vision matches
    for m in _VISION_KEYWORDS.finditer(t):
        scores["vision"] += 1
        matched["vision"].append(m.group())

    # Build matches
    for m in _BUILD_KEYWORDS.finditer(t):
        scores["build"] += 1
        matched["build"].append(m.group())

    # Execute matches
    for m in _EXECUTE_KEYWORDS.finditer(t):
        scores["execute"] += 1
        matched["execute"].append(m.group())

    # Check for avatar delegation
    avatar_match = _AVATAR_NAMES.search(t)

    # Pick the highest scoring pipeline
    best = max(scores, key=scores.get)
    total = sum(scores.values())

    if total == 0:
        pipeline = "converse"
        confidence = 0.0
    else:
        confidence = scores[best] / max(total, 1)
        # Require minimum confidence to route
        if confidence < 0.3:
            pipeline = "converse"
        else:
            pipeline = best

    result = {
        "pipeline": pipeline,
        "confidence": round(confidence, 2),
        "matched": matched.get(pipeline, []),
        "payload": t,
    }

    # Attach avatar delegation if detected
    if avatar_match and avatar_match.group(1).lower() != "lilly":
        result["delegate_to"] = avatar_match.group(1).lower()

    return result


def remember_route(decision: dict) -> None:
    """Store a routing decision for observability and history."""
    entry = {
        "ts": time.time(),
        "pipeline": decision.get("pipeline", "converse"),
        "confidence": decision.get("confidence", 0),
        "matched": decision.get("matched", []),
        "delegate_to": decision.get("delegate_to"),
    }
    _routing_history.append(entry)


# ─── Dispatch ────────────────────────────────────────────────────────────────

async def dispatch_to_agent(pipeline: str, payload: dict) -> dict:
    """Dispatch a payload to the correct pipeline handler.

    Handlers are registered by lilly_ai.py via register_pipeline_handlers().
    """
    handler = _pipeline_handlers.get(pipeline)
    if handler is None:
        return {"ok": False, "pipeline": pipeline, "state": "no_handler"}
    try:
        result = await handler(payload)
        return {"ok": True, "pipeline": pipeline, "result": result}
    except Exception as e:
        logger.error("dispatch_to_agent failed: %s", e)
        return {"ok": False, "pipeline": pipeline, "error": str(e)}


def register_pipeline_handlers(handlers: dict) -> None:
    """Register concrete handler functions for each pipeline.

    handlers = {"vision": async_fn, "build": async_fn, "execute": async_fn}
    """
    _pipeline_handlers.update(handlers)
    logger.info("Pipeline handlers registered: %s", list(handlers.keys()))


def register_runtime(ctx: dict) -> None:
    """Register runtime metadata (mode, webcam awareness, pipeline list)."""
    _runtime_ctx.update(ctx)
    logger.info("Runtime context registered: %s", ctx)


# ─── Status ──────────────────────────────────────────────────────────────────

def orchestrator_status() -> dict:
    """Return orchestrator status info."""
    return {
        "ok": True,
        "mode": _runtime_ctx.get("mode", "full"),
        "pipelines": _runtime_ctx.get("pipelines", []),
        "webcam_aware": _runtime_ctx.get("webcam_aware", False),
        "handlers_registered": list(_pipeline_handlers.keys()),
        "recent_routes": [
            {
                "pipeline": r["pipeline"],
                "confidence": r["confidence"],
                "ts": r["ts"],
            }
            for r in list(_routing_history)[-10:]
        ],
        "total_routes": len(_routing_history),
    }


# ─── System Prompt Builder ───────────────────────────────────────────────────

def build_orchestrator_system_prompt(
    user_name: str = "", include_schema: bool = True
) -> str:
    """Build the full orchestrator system prompt.

    This is prepended to the avatar's personality prompt so Lilly
    is always the master orchestrator persona.
    """
    prompt = ORCHESTRATOR_PROMPT

    if user_name:
        prompt += f"\n\nYou are talking to {user_name}."

    if include_schema:
        prompt += (
            "\n\n## Available Pipelines\n"
            "```json\n"
            + tools_schema_json()
            + "\n```\n"
            "Use these pipelines internally. Never expose raw JSON to the user."
        )

    return prompt
