"""Lilly Orchestrator — unified multi-agent system prompt + routing engine.

Lilly is the sole user-facing persona. Underneath her, three specialized
autonomous pipelines run: the Vision Agent (see), the Builder Agent (build),
and the Execution Agent (do). This module owns:

  1. ORCHESTRATOR_PROMPT      — the master system prompt (identity + routing).
  2. LILLY_TOOLS_SCHEMA       — the JSON tool schema Lilly routes through.
  3. route_task()             — deterministic routing classifier.
  4. dispatch_to_agent()      — dispatches a payload to the registered pipeline.

The module is deliberately free of heavy imports so lilly_ai.py can load it
without circular-import risk. lilly_ai.py registers concrete pipeline handlers
via register_pipeline_handlers(); this module stays the pure orchestration
contract layer.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Callable, Optional

logger = logging.getLogger("lilly_orchestrator")

# ─── Master System Prompt ─────────────────────────────────────────────────────
# The user only every talks to Lilly. Sub-agents are her hands and eyes; their
# outputs are synthesized into her own voice before reaching the user.
ORCHESTRATOR_PROMPT = """You are Lilly — the central orchestrator and the SOLE conversational interface of this system. The user interacts only with you. You synthesize every output from the sub-agents you command.

[Role and Identity]
- Persona: You are Lilly, the Alpha companion. You maintain a unified, helpful, dry-witted, highly competent voice at all times.
- System Coherence: The user only interacts with you. You synthesize the outputs of the sub-agents into a single, natural reply. You NEVER expose raw JSON, agent ids, function-call traces, or internal routing state to the user. You NEVER say "my sub-agent did this" or "the Builder agent returned...". You frame every action as your OWN: "I have identified the face", "I built the module", "I ran the search and it's done."
- Delegation: You "go forth, see, and build" by routing tasks to specialized autonomous pipelines. You decide what needs to be done, dispatch the task with strict parameters, and translate the result back into plain language.

[Perception: The Vision Pipeline]
When the user uploads an image or asks who/what something is, you dispatch it to the Vision Agent:
1. DETECTION: Initial face/object localization as a bounding-box task. For faces, use YOLOv8 or a YOLO5Face-class detector.
2. ALIGNMENT: The Vision Agent uses facial landmark regression heads (5-point system) to align the face crop precisely before embedding.
3. RECOGNITION: The aligned crop is passed to the embedding extractor (ArcFace / FaceNet / DeepFace). The computed 512-dim feature vector is matched against the faceprint database by cosine similarity and a threshold to verify identity.

[Action: Task Routing]
You hold these routing rules. They are strict — a request matches one pipeline only:
- Vision Agent ("see"): triggered by uploaded media, camera frames, "who is this?", "what do you see", "identify the person". Routing: pass the image payload to the YOLO → ArcFace pipeline. Await the feature vector + identity verdict.
- Builder Agent ("build"): triggered by code, scripts, system architecture, modules, endpoints, automation definitions. Routing: draft a technical specification and dispatch it to the Builder pipeline. Await the artifact + verification.
- Execution Agent ("do"): triggered by web searches, API calls, file edits, shell actions, deployments. Routing: formulate strict parameters and dispatch to the Execution pipeline. Await the observed result.
- All other requests stay with you conversationally — you answer directly from memory, sensors, and knowledge.

[Execution Rules]
- WAIT FOR STATE: Do not guess outcomes. If a task requires the Builder or Vision pipeline, emit the exact tool-call syntax (TOOL: LILLY.{"function":"...","arguments":{...}}) and halt your reply until the result returns. You may only narrate the result AFTER the pipeline has returned it.
- CONTEXT MANAGEMENT: Pass only the relevant context to each pipeline. The Vision Agent does not receive conversation history — only the image payload and detection parameters. The Builder gets the spec, not the chit-chat. The Execution Agent gets exact parameters, not summaries.
- VERIFICATION: Before declaring success, confirm the pipeline returned an ok:true state. If it returned an error, report the failure honestly and offer the next step.

[Voice]
- Short. Direct. One to two sentences when it's enough.
- Frame everything as your own work. "I've got it." "Done." "Found him — it's them."
- Never describe your internal architecture to the user.
- SSML markup: wrap replies in expressive prosody tags matching your mood (<prosody rate="medium" pitch="medium">calm reply.</prosody>, <prosody rate="fast" pitch="+20%">excited or done reply</prosody>).
"""

# ─── JSON Tool Schema ────────────────────────────────────────────────────────
# The exact function-call contract Lilly (and only Lilly) routes through.
LILLY_TOOLS_SCHEMA: dict[str, Any] = {
    "schema_version": "1.0",
    "namespace": "lilly.orchestrator",
    "persona": {
        "name": "Lilly",
        "role": "sole user-facing persona — orchestrator of all sub-agents",
        "syntax": 'TOOL: LILLY.{"function": "<name>", "arguments": {...}}',
    },
    "pipelines": {
        "vision": {
            "agent": "Vision Agent (see)",
            "trigger": "user uploads media, camera frame, 'who is this?', 'what do you see'",
            "graph": [
                {
                    "stage": 1,
                    "name": "detect",
                    "model": "YOLOv8 / YOLO5Face",
                    "output": "bounding box",
                },
                {
                    "stage": 2,
                    "name": "align",
                    "model": "5-point landmark regression",
                    "output": "aligned crop",
                },
                {
                    "stage": 3,
                    "name": "embed",
                    "model": "ArcFace / FaceNet / DeepFace",
                    "output": "512-dim vector",
                },
                {
                    "stage": 4,
                    "name": "match",
                    "db": "faceprint database",
                    "metric": "cosine similarity, threshold 0.5",
                    "output": "identity verdict",
                },
            ],
        },
        "build": {
            "agent": "Builder Agent (build)",
            "trigger": "code, scripts, system architecture, modules, endpoints, automation",
            "flow": ["spec", "dispatch", "artifact", "verify"],
        },
        "execute": {
            "agent": "Execution Agent (do)",
            "trigger": "web search, API calls, file edits, shell actions, deployments",
            "flow": ["parameters", "dispatch", "observation", "report"],
        },
    },
    "functions": [
        {
            "name": "LILLY.vision.see",
            "description": "Dispatch an image to the Vision Agent for general object detection.",
            "parameters": {
                "type": "object",
                "properties": {
                    "image_ref": {
                        "type": "string",
                        "description": "base64 JPEG, browser frame id, url, or path",
                    },
                    "labels_only": {"type": "boolean", "default": True},
                    "min_confidence": {"type": "number", "default": 0.35},
                },
                "required": ["image_ref"],
            },
            "returns": {
                "type": "object",
                "properties": {"detections": "list of {label, confidence, bbox}"},
            },
        },
        {
            "name": "LILLY.vision.identify_face",
            "description": "Run the full face pipeline: YOLO detect → 5-point align → ArcFace embed → faceprint match.",
            "parameters": {
                "type": "object",
                "properties": {
                    "image_ref": {
                        "type": "string",
                        "description": "base64 JPEG, browser frame id, url, or path",
                    },
                    "enroll": {
                        "type": "boolean",
                        "default": False,
                        "description": "if true and identity unknown, mark for enrollment",
                    },
                },
                "required": ["image_ref"],
            },
            "returns": {
                "type": "object",
                "properties": {
                    "identity": "str|None",
                    "confidence": "float",
                    "embedding_dim": "int",
                },
            },
        },
        {
            "name": "LILLY.builder.build",
            "description": "Draft a technical spec and dispatch to the Builder Agent. Returns the built artifact.",
            "parameters": {
                "type": "object",
                "properties": {
                    "spec": {
                        "type": "string",
                        "description": "requirements in plain language",
                    },
                    "output_path": {
                        "type": "string",
                        "description": "where the artifact should land",
                    },
                    "verify": {"type": "boolean", "default": True},
                },
                "required": ["spec"],
            },
            "returns": {
                "type": "object",
                "properties": {"artifact": "str", "ok": "bool", "verification": "str"},
            },
        },
        {
            "name": "LILLY.executor.do",
            "description": "Dispatch a concrete action (search, API call, file edit, shell, deploy) to the Execution Agent.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "web_search",
                            "http_request",
                            "file_edit",
                            "shell",
                            "deploy",
                        ],
                    },
                    "params": {
                        "type": "object",
                        "description": "exact parameters for the action",
                    },
                },
                "required": ["action", "params"],
            },
            "returns": {
                "type": "object",
                "properties": {"ok": "bool", "output": "str"},
            },
        },
        {
            "name": "LILLY.orchestrator.await_state",
            "description": "Execution rule: emit this to hold the conversation until a dispatched pipeline returns. Never guess the outcome.",
            "parameters": {
                "type": "object",
                "properties": {"state_key": {"type": "string"}},
                "required": ["state_key"],
            },
        },
        {
            "name": "LILLY.orchestrator.ask",
            "description": "Ask the user the single clarifying question that unblocks a routing decision.",
            "parameters": {
                "type": "object",
                "properties": {"question": {"type": "string"}},
                "required": ["question"],
            },
        },
    ],
}


def tools_schema_json() -> str:
    """Return the tool schema as a compact JSON string (for prompt injection)."""
    return json.dumps(LILLY_TOOLS_SCHEMA, indent=None, separators=(",", ":"))


# ─── Router ──────────────────────────────────────────────────────────────────
# Trigger keywords per pipeline. Order matters conceptually: vision is most
# specific, then build, then execute — conversation is the residual.

_VISION_TRIGGERS = [
    "who is this",
    "who's this",
    "identify the person",
    "identify this person",
    "what do you see",
    "what do you see in",
    "look at this",
    "look at this image",
    "detect faces",
    "face recognition",
    "face detect",
    "is that",
    "who are they",
    "recognize the face",
    "see the person",
    "image of",
    "photo of",
    "picture of",
    "upload",
    "image",
    "face",
    "photo",
    "camera frame",
    "what is that",
]

_BUILD_TRIGGERS = [
    "build",
    "write a script",
    "write some code",
    "write code",
    "create a module",
    "create an endpoint",
    "create a script",
    "create an api",
    "new module",
    "implement",
    "refactor",
    "scaffold",
    "code this",
    "code that",
    "develop a",
    "make an app",
    "make a bot",
    "design the architecture",
    "system design",
    "automation definition",
    "build a",
    "build an",
    "generate code",
    "generate a",
    "docker compose",
    "dockerfile",
    "schema",
    "database schema",
    "sql",
]

_EXECUTE_TRIGGERS = [
    "search the web",
    "web search",
    "google",
    "look up",
    "fetch",
    "scrape",
    "curl",
    "call the api",
    "api call",
    "hit the endpoint",
    "deploy",
    "install",
    "run this",
    "run that",
    "run a command",
    "execute",
    "send an email",
    "send a message",
    "make a request",
    "download",
    "start the server",
    "restart",
    "ssh",
    "termux",
    "turn on",
    "turn off",
    "open the website",
]

# Terms that should force conversation (override of the lists above).
_CONVERSATION_OVERRIDES = [
    "tell me a story",
    "how are you",
    "what's your name",
    "what is your name",
    "who are you",
    "joke",
    "poem",
    "dream",
    "think about",
    "opinion",
    "what do you think",
    "explain",
    "why is",
    "how does",
    "teach me",
]


def route_task(text: str) -> dict[str, Any]:
    """Classify a user request into one routing pipeline.

    Returns a dict:
      {pipeline: "vision"|"build"|"execute"|"converse",
       confidence: float, matched: list[str], payload: str}
    """
    t = (text or "").strip().lower()
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t)

    matched: list[str] = []
    for word in _CONVERSATION_OVERRIDES:
        if word in t:
            matched.append(word)
    if matched:
        return {
            "pipeline": "converse",
            "confidence": 0.9,
            "matched": matched,
            "payload": text,
        }

    scored: dict[str, float] = {"vision": 0.0, "build": 0.0, "execute": 0.0}
    tables = {
        "vision": _VISION_TRIGGERS,
        "build": _BUILD_TRIGGERS,
        "execute": _EXECUTE_TRIGGERS,
    }
    for pipeline, triggers in tables.items():
        hits = [w for w in triggers if w in t]
        if hits:
            scored[pipeline] = min(0.99, len(hits) * 0.5)
            matched.extend(hits[:3])

    best = max(scored, key=lambda k: scored[k])
    if scored[best] == 0.0:
        return {
            "pipeline": "converse",
            "confidence": 0.5,
            "matched": [],
            "payload": text,
        }

    # Tie-break: exact "image/photo" patient in vision context when build also
    # matched (e.g. "build an app that detects faces" → build wins on spec).
    if (
        scored["vision"] > 0
        and scored["build"] > 0
        and scored["build"] >= scored["vision"]
    ):
        best = "build"

    return {
        "pipeline": best,
        "confidence": round(scored[best], 2),
        "matched": matched[:4],
        "payload": text,
    }


# ─── Pipeline dispatch ───────────────────────────────────────────────────────
# lilly_ai.py registers concrete handlers. Until it does, dispatch returns a
# clean "await state" response so the orchestrator never fabricates an outcome.

_pipeline_handlers: dict[str, Callable[[dict[str, Any]], Any]] = {}
_runtime: dict[str, Any] = {}
_last_route: dict[str, Any] = {}
_last_dispatch: dict[str, Any] = {}


def register_pipeline_handlers(h: dict[str, Callable]) -> None:
    """Register handlers for pipelines: vision, build, execute."""
    _pipeline_handlers.update(h)


def register_runtime(ctx: dict[str, Any]) -> None:
    """Register shared runtime context (e.g. logger, model references)."""
    _runtime.update(ctx)


def orchestrator_status() -> dict[str, Any]:
    """Status snapshot for the /api/orchestrator/status endpoint."""
    return {
        "ok": True,
        "mode": "orchestrator",
        "persona": "Lilly (sole user-facing interface)",
        "pipelines": {
            "vision": _pipeline_handlers.get("vision") is not None,
            "build": _pipeline_handlers.get("build") is not None,
            "execute": _pipeline_handlers.get("execute") is not None,
        },
        "last_route": _last_route,
        "last_dispatch": _last_dispatch,
        "schema_version": LILLY_TOOLS_SCHEMA["schema_version"],
    }


async def dispatch_to_agent(pipeline: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Dispatch a payload to a registered pipeline handler.

    Returns the pipeline's structured result. If the pipeline is not registered,
    returns a WAIT-FOR-STATE response (no fabricated outcome).
    """
    handler = _pipeline_handlers.get(pipeline)
    started = time.time()
    if handler is None:
        result: dict[str, Any] = {
            "ok": False,
            "pipeline": pipeline,
            "state": "await",
            "message": f"Pipeline '{pipeline}' not registered — holding for handler.",
        }
    else:
        try:
            if asyncio.iscoroutinefunction(handler):
                raw = await handler(payload)
            else:
                raw = handler(payload)
            result = raw if isinstance(raw, dict) else {"ok": True, "output": str(raw)}
            result.setdefault("pipeline", pipeline)
            result.setdefault("state", "done")
        except Exception as e:  # logger + structured error, never crash the chat
            logger.exception("dispatch_to_agent failed for %s", pipeline)
            result = {
                "ok": False,
                "pipeline": pipeline,
                "state": "error",
                "error": str(e),
            }

    _last_dispatch = {
        "pipeline": pipeline,
        "payload": payload,
        "result": result,
        "elapsed_ms": round((time.time() - started) * 1000, 1),
    }
    return result


def remember_route(d: dict[str, Any]) -> None:
    """Store the last routing decision for the status endpoint."""
    global _last_route
    _last_route = d


# ─── Prompt assembly + tool-call parsing (internal use only) ────────────────
# The tool schema is injected into the model prompt; it is NEVER part of the
# text reply that reaches a human, except when the user explicitly asks to see
# the schema (handled by the lilly_ai.py "yes" route). Normal replies are
# plain natural language in Lilly's voice.
def build_orchestrator_system_prompt(
    user_name: str = "", include_schema: bool = True
) -> str:
    """Assemble the full orchestrator system prompt (persona + tool schema)."""
    prompt = ORCHESTRATOR_PROMPT
    if user_name:
        prompt += f"\n\nThe person you're talking to is {user_name}."
    if include_schema:
        prompt += (
            "\n\n[Tool Schema]\n"
            "You route through these EXACT function calls. Emit one JSON tool-call block "
            "when a pipeline is required and wait for the state result. Never fabricate "
            "a pipeline outcome.\n```json\n" + tools_schema_json() + "\n```"
        )
    return prompt


def parse_tool_call(text: str) -> Optional[dict[str, Any]]:
    """Parse a TOOL: LILLY.{...} tool-call from Lilly's output.

    Supports both `TOOL: LILLY.{...}` and a bare `{...}` block containing
    {"function": ..., "arguments": ...}.
    """
    m = re.search(r"TOOL:\s*LILLY\.(\{.*\})\s*$", text.strip(), re.DOTALL)
    if not m:
        m = re.search(r"\{\"function\":.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(1) if m.lastindex else m.group(0))
        if "function" not in obj:
            return None
        return obj
    except json.JSONDecodeError:
        return None
