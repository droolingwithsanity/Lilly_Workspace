#!/usr/bin/env python3
"""
entity_core.py — Lilly Entity Core
====================================
Central Entity class that defines Lilly's persistent identity, runtime state,
and a multi-model routing layer that assigns every available vocab GGUF model
to a specific cognitive task domain.

Architecture
────────────
  Entity                     — persistent identity + state container
  VocabModelRouter           — maps task tags → preferred model path
  ModelAssignment            — a (task_tag, model_path, rationale) record
  entity_router              — module-level singleton for lilly_ai.py to import

Vocab GGUF models discovered in three llama.cpp trees:
  ~/openenv/llama.cpp/models/
  ~/llama-cpp-turboquant/models/
  ~/SPAWN/llama.cpp/models/

Each vocab model is mapped to a task domain based on its architecture family:
  bert/nomic-bert → embedding / semantic search
  starcoder/deepseek-coder/refact → code generation / review
  deepseek-llm → long-context reasoning / analysis
  gpt-2 / gpt-neox / falcon → general text completion / drafting
  phi-3 → compact reasoning / on-device inference
  llama-bpe / llama-spm → conversational / instruct tasks
  command-r → RAG / grounded Q&A
  qwen2 / qwen35 → multilingual / instruction following
  baichuan / aquila → Chinese-language tasks
  mpt → summarization / document Q&A
  gemma-4 → creative / multi-modal reasoning

Usage
─────
  from entity_core import entity_router, get_entity

  model_path = entity_router.best_model_for("code")
  entity = get_entity()
  entity.record_interaction("user said X", persona="fox", outcome="positive")
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

logger = logging.getLogger("LillyEntity")

# ─── Paths ────────────────────────────────────────────────────────────────────
_HOME = Path.home()
WORKSPACE = Path(os.environ.get("LILLY_WORKSPACE", "")) or Path(__file__).parent
REGISTRY_FILE = WORKSPACE / "entity_registry.json"

# All known vocab-model directories (checked at runtime; missing ones are skipped)
_VOCAB_MODEL_DIRS: list[Path] = [
    _HOME / "openenv" / "llama.cpp" / "models",
    _HOME / "llama-cpp-turboquant" / "models",
    _HOME / "SPAWN" / "llama.cpp" / "models",
]

# Also check Lilly_Workspace's own openenv dir (relative)
_VOCAB_MODEL_DIRS += [
    WORKSPACE / "openenv" / "llama.cpp" / "models",
]

# ─── Task-domain taxonomy ─────────────────────────────────────────────────────
# Each key is a human-readable task tag; value is a priority-ordered list of
# model-name substrings.  The router picks the first match found on disk.
TASK_DOMAINS: dict[str, list[str]] = {
    # ── Natural language / conversation ──────────────────────────────────────
    "conversation":     ["llama-bpe", "llama-spm", "qwen35", "qwen2", "gpt-2", "gpt-neox"],
    "instruction":      ["qwen35", "qwen2", "llama-bpe", "llama-spm", "phi-3"],
    "creative":         ["gemma-4", "fox", "gpt-neox", "gpt-2", "falcon"],
    "reasoning":        ["deepseek-llm", "phi-3", "qwen35", "llama-bpe"],
    "multilingual":     ["qwen35", "qwen2", "baichuan", "aquila"],
    "chinese":          ["baichuan", "aquila", "qwen2", "qwen35"],
    "drafting":         ["gpt-2", "gpt-neox", "mpt", "falcon"],

    # ── Code tasks ────────────────────────────────────────────────────────────
    "code":             ["starcoder", "deepseek-coder", "refact", "phi-3"],
    "code_review":      ["deepseek-coder", "starcoder", "refact"],
    "debugging":        ["deepseek-coder", "starcoder", "refact", "phi-3"],
    "code_complete":    ["starcoder", "refact", "deepseek-coder"],

    # ── Analysis / document understanding ────────────────────────────────────
    "analysis":         ["deepseek-llm", "command-r", "mpt", "qwen35"],
    "summarization":    ["mpt", "command-r", "deepseek-llm", "falcon"],
    "document_qa":      ["command-r", "mpt", "llama-bpe"],
    "rag":              ["command-r", "mpt", "llama-bpe"],

    # ── Embedding / semantic ──────────────────────────────────────────────────
    "embedding":        ["bert-bge", "nomic-bert-moe"],
    "semantic_search":  ["bert-bge", "nomic-bert-moe"],
    "similarity":       ["bert-bge", "nomic-bert-moe"],

    # ── Compact / on-device ───────────────────────────────────────────────────
    "compact":          ["phi-3", "qwen2", "gpt-2"],
    "fast":             ["phi-3", "gpt-2", "falcon"],

    # ── Fallback ──────────────────────────────────────────────────────────────
    "default":          ["llama-bpe", "qwen2", "gpt-2", "deepseek-llm"],
}


# ─── Data classes ─────────────────────────────────────────────────────────────
@dataclass
class ModelAssignment:
    """Records which model was chosen for a task domain and why."""
    task_tag: str
    model_name: str        # filename without path, e.g. "ggml-vocab-starcoder.gguf"
    model_path: str        # absolute path
    model_tree: str        # which llama.cpp tree it came from
    rationale: str         # human-readable reason
    assigned_at: float = field(default_factory=time.time)
    use_count: int = 0
    avg_latency_ms: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EntityIdentity:
    """Lilly's persistent identity — who she is."""
    name: str = "Lilly"
    version: str = "3.0.0"
    archetype: str = "assistant_ai"
    created_at: float = field(default_factory=time.time)
    home_workspace: str = str(WORKSPACE)
    personas: list[str] = field(default_factory=lambda: [
        "puppy", "fox", "cat", "bear", "bunny",
        "owl", "deer", "wolf", "raccoon",
    ])
    primary_persona: str = "puppy"
    capabilities: list[str] = field(default_factory=lambda: [
        "conversation", "code", "analysis", "creative",
        "embedding", "reasoning", "sensor_interpretation",
        "persona_switching", "skill_learning",
    ])


@dataclass
class EntityState:
    """Lilly's runtime state — what she's doing right now."""
    current_persona: str = "puppy"
    interaction_count: int = 0
    last_interaction: float = 0.0
    session_start: float = field(default_factory=time.time)
    active_model_assignments: dict[str, str] = field(default_factory=dict)
    persona_scores: dict[str, float] = field(default_factory=dict)
    skill_evolution_count: int = 0
    last_skill_update: float = 0.0
    errors_this_session: int = 0
    successful_interactions: int = 0


# ─── Multi-model Router ───────────────────────────────────────────────────────
class VocabModelRouter:
    """
    Discovers all vocab GGUF models across the three llama.cpp trees and builds
    a task-domain → model-path routing table.

    These are *vocabulary-only* GGUF files (no weights), which serve as
    tokenizer backends and lightweight inference endpoints when combined with a
    full-weight model or used for embedding extraction.

    The router is intentionally stateless (no GPU loading) — it resolves paths
    so that the caller (llama-server, ctransformers, llama-cpp-python) can load
    the right tokenizer for each task.
    """

    def __init__(self):
        self._assignments: dict[str, ModelAssignment] = {}  # task_tag → assignment
        self._all_models: list[dict] = []                   # flat list of discovered files
        self._built = False

    # ── Discovery ─────────────────────────────────────────────────────────────
    def discover_models(self) -> list[dict]:
        """Scan all vocab-model directories and return a flat list of model records."""
        found: list[dict] = []
        seen_names: set[str] = set()

        for model_dir in _VOCAB_MODEL_DIRS:
            if not model_dir.exists():
                continue
            tree_label = _tree_label(model_dir)
            for gguf_path in sorted(model_dir.glob("ggml-vocab-*.gguf")):
                # De-duplicate by filename so we don't register the same model twice
                # from different trees (keep first occurrence, which is openenv)
                if gguf_path.name in seen_names:
                    continue
                seen_names.add(gguf_path.name)
                found.append({
                    "name": gguf_path.name,
                    "path": str(gguf_path),
                    "tree": tree_label,
                    "size_bytes": gguf_path.stat().st_size,
                })

        # Also pick up the real qwen2.5 instruct model if present
        qwen_path = WORKSPACE.parent / "openenv" / "qwen2.5-0.5b-instruct-q4_k_m.gguf"
        if not qwen_path.exists():
            qwen_path = _HOME / "openenv" / "qwen2.5-0.5b-instruct-q4_k_m.gguf"
        if qwen_path.exists() and qwen_path.name not in seen_names:
            found.append({
                "name": qwen_path.name,
                "path": str(qwen_path),
                "tree": "openenv",
                "size_bytes": qwen_path.stat().st_size,
            })

        self._all_models = found
        logger.info(f"VocabModelRouter: discovered {len(found)} models across {len(_VOCAB_MODEL_DIRS)} trees")
        return found

    # ── Build routing table ───────────────────────────────────────────────────
    def build(self) -> dict[str, ModelAssignment]:
        """
        Build a complete routing table mapping every task domain to the best
        available model.  Returns the assignments dict.
        """
        if not self._all_models:
            self.discover_models()

        # Index models by all possible name substrings for fast lookup
        name_index: dict[str, dict] = {}
        for m in self._all_models:
            lower = m["name"].lower()
            # Extract the token after "ggml-vocab-" and before ".gguf"
            stem = lower.replace("ggml-vocab-", "").replace(".gguf", "")
            name_index[stem] = m
            # Also index by full name
            name_index[lower] = m

        for task_tag, preference_list in TASK_DOMAINS.items():
            chosen: Optional[dict] = None
            for fragment in preference_list:
                # Try exact stem match first
                if fragment in name_index:
                    chosen = name_index[fragment]
                    break
                # Try substring match
                for stem, m_info in name_index.items():
                    if fragment in stem:
                        chosen = m_info
                        break
                if chosen:
                    break

            if chosen:
                assignment = ModelAssignment(
                    task_tag=task_tag,
                    model_name=chosen["name"],
                    model_path=chosen["path"],
                    model_tree=chosen["tree"],
                    rationale=f"Best match for '{task_tag}' from preference list {preference_list}",
                )
            else:
                # Fallback: just pick the first model we found
                fallback = self._all_models[0] if self._all_models else None
                if fallback:
                    assignment = ModelAssignment(
                        task_tag=task_tag,
                        model_name=fallback["name"],
                        model_path=fallback["path"],
                        model_tree=fallback["tree"],
                        rationale=f"Fallback — no preference match for '{task_tag}'",
                    )
                else:
                    # No models at all — create a placeholder
                    assignment = ModelAssignment(
                        task_tag=task_tag,
                        model_name="(none)",
                        model_path="",
                        model_tree="",
                        rationale="No GGUF vocab models found on disk",
                    )

            self._assignments[task_tag] = assignment

        self._built = True
        logger.info(f"VocabModelRouter: built routing table with {len(self._assignments)} task domains")
        return self._assignments

    # ── Public API ────────────────────────────────────────────────────────────
    def best_model_for(self, task_tag: str) -> Optional[str]:
        """Return the absolute path to the best model for a task, or None."""
        if not self._built:
            self.build()
        assignment = self._assignments.get(task_tag) or self._assignments.get("default")
        if assignment and assignment.model_path:
            assignment.use_count += 1
            return assignment.model_path
        return None

    def best_model_name_for(self, task_tag: str) -> Optional[str]:
        """Return the filename (not path) of the best model for a task."""
        path = self.best_model_for(task_tag)
        return Path(path).name if path else None

    def assignment_for(self, task_tag: str) -> Optional[ModelAssignment]:
        """Return the full ModelAssignment record."""
        if not self._built:
            self.build()
        return self._assignments.get(task_tag) or self._assignments.get("default")

    def all_assignments(self) -> list[ModelAssignment]:
        """Return every assignment in the routing table."""
        if not self._built:
            self.build()
        return list(self._assignments.values())

    def routing_table_dict(self) -> dict:
        """Serialisable routing table for persistence."""
        if not self._built:
            self.build()
        return {tag: asdict(a) for tag, a in self._assignments.items()}

    def all_discovered_models(self) -> list[dict]:
        """Return the raw list of discovered model files."""
        if not self._all_models:
            self.discover_models()
        return list(self._all_models)

    def infer_task_from_text(self, text: str) -> str:
        """
        Lightweight heuristic: infer a task domain from input text so the router
        can auto-select the right model without being told explicitly.
        """
        text_lower = text.lower()

        # Code tasks
        if any(kw in text_lower for kw in [
            "def ", "class ", "import ", "function", "bug", "debug",
            "syntax", "compile", "runtime error", "python", "javascript",
            "```", "code review", "pull request", "git"
        ]):
            if any(kw in text_lower for kw in ["review", "check", "lint", "audit"]):
                return "code_review"
            if any(kw in text_lower for kw in ["debug", "fix", "error", "exception", "traceback"]):
                return "debugging"
            return "code"

        # Embedding / search
        if any(kw in text_lower for kw in [
            "similar to", "find relevant", "embed", "vector", "semantic"
        ]):
            return "semantic_search"

        # Reasoning
        if any(kw in text_lower for kw in [
            "why", "because", "therefore", "logic", "reason", "explain",
            "analyse", "analyze", "think through", "step by step"
        ]):
            return "reasoning"

        # Chinese / multilingual
        if any(ord(c) > 0x4E00 for c in text):  # CJK characters
            return "chinese"

        # Creative
        if any(kw in text_lower for kw in [
            "story", "write a", "poem", "imagine", "create", "invent", "idea"
        ]):
            return "creative"

        # Summarization / analysis
        if any(kw in text_lower for kw in [
            "summarize", "summary", "tldr", "brief", "overview",
            "what does", "what is", "explain this"
        ]):
            return "summarization"

        # RAG / grounded Q&A
        if any(kw in text_lower for kw in [
            "according to", "based on", "from the document", "in the text",
            "what does it say"
        ]):
            return "rag"

        return "conversation"

    def record_latency(self, task_tag: str, latency_ms: float):
        """Update the running average latency for a task's assigned model."""
        if not self._built:
            return
        a = self._assignments.get(task_tag)
        if a:
            if a.avg_latency_ms == 0.0:
                a.avg_latency_ms = latency_ms
            else:
                # Exponential moving average (α = 0.2)
                a.avg_latency_ms = 0.8 * a.avg_latency_ms + 0.2 * latency_ms


# ─── Entity class ─────────────────────────────────────────────────────────────
class Entity:
    """
    Lilly — the persistent AI entity.

    Holds identity, runtime state, and the multi-model router.
    Provides methods for:
      - recording interactions
      - switching personas
      - persisting/restoring state via entity_registry.json
      - exposing the router for task-based model selection
    """

    def __init__(
        self,
        identity: Optional[EntityIdentity] = None,
        state: Optional[EntityState] = None,
        router: Optional[VocabModelRouter] = None,
    ):
        self.identity = identity or EntityIdentity()
        self.state = state or EntityState()
        self.router = router or VocabModelRouter()
        self._registry_path = REGISTRY_FILE
        self._last_persist: float = 0.0
        self._persist_interval: float = 30.0  # seconds between auto-saves

    # ── Lifecycle ─────────────────────────────────────────────────────────────
    def initialise(self) -> "Entity":
        """Build the router and load persisted state."""
        self.router.build()
        self._load_state()
        logger.info(
            f"Entity '{self.identity.name}' initialised — "
            f"{len(self.router.all_assignments())} model assignments, "
            f"persona={self.state.current_persona}"
        )
        return self

    # ── Persona management ────────────────────────────────────────────────────
    def switch_persona(self, persona_name: str) -> bool:
        """Switch active persona.  Returns True if the switch was valid."""
        if persona_name in self.identity.personas:
            self.state.current_persona = persona_name
            self._maybe_persist()
            return True
        logger.warning(f"Entity.switch_persona: unknown persona '{persona_name}'")
        return False

    def current_persona(self) -> str:
        return self.state.current_persona

    # ── Interaction recording ─────────────────────────────────────────────────
    def record_interaction(
        self,
        user_text: str,
        persona: str = "",
        outcome: str = "neutral",   # "positive" | "neutral" | "negative"
        latency_ms: float = 0.0,
        task_tag: str = "",
    ):
        """
        Record that an interaction occurred and update state accordingly.
        Called from lilly_ai.py after every handle_intent() cycle.
        """
        self.state.interaction_count += 1
        self.state.last_interaction = time.time()

        # Infer task if not provided
        if not task_tag:
            task_tag = self.router.infer_task_from_text(user_text)

        if latency_ms > 0:
            self.router.record_latency(task_tag, latency_ms)

        if outcome == "positive":
            self.state.successful_interactions += 1
        elif outcome == "negative":
            self.state.errors_this_session += 1

        # Update active model assignment snapshot
        a = self.router.assignment_for(task_tag)
        if a:
            self.state.active_model_assignments[task_tag] = a.model_name

        self._maybe_persist()

    def notify_skill_update(self):
        """Called by skills_engine when the skill set changes."""
        self.state.skill_evolution_count += 1
        self.state.last_skill_update = time.time()
        self._maybe_persist()

    def notify_persona_score(self, persona: str, score: float):
        """Called by persona_optimizer to log the latest score for a persona."""
        self.state.persona_scores[persona] = score
        self._maybe_persist()

    # ── Model routing helpers ─────────────────────────────────────────────────
    def model_for(self, task_tag: str) -> Optional[str]:
        """Shortcut to VocabModelRouter.best_model_for()."""
        return self.router.best_model_for(task_tag)

    def model_for_text(self, text: str) -> Optional[str]:
        """Infer task from text then return best model path."""
        tag = self.router.infer_task_from_text(text)
        return self.router.best_model_for(tag)

    # ── Persistence ───────────────────────────────────────────────────────────
    def _maybe_persist(self):
        """Persist to disk if more than _persist_interval seconds have passed."""
        now = time.time()
        if now - self._last_persist >= self._persist_interval:
            self.persist()

    def persist(self):
        """Write full entity state to entity_registry.json."""
        try:
            data = {
                "meta": {
                    "format_version": 1,
                    "saved_at": time.time(),
                    "saved_at_iso": _ts_iso(),
                },
                "identity": asdict(self.identity),
                "state": asdict(self.state),
                "routing_table": self.router.routing_table_dict(),
                "discovered_models": self.router.all_discovered_models(),
            }
            self._registry_path.write_text(json.dumps(data, indent=2))
            self._last_persist = time.time()
        except Exception as exc:
            logger.error(f"Entity.persist failed: {exc}")

    def _load_state(self):
        """Load previously persisted state (identity + routing table) from disk."""
        if not self._registry_path.exists():
            logger.info("entity_registry.json not found — starting fresh")
            return
        try:
            data = json.loads(self._registry_path.read_text())
            saved_state = data.get("state", {})
            # Restore mutable state fields (don't touch interaction counts from disk
            # because they accumulate across restarts, which is intentional)
            for field_name in [
                "current_persona", "interaction_count", "skill_evolution_count",
                "last_skill_update", "persona_scores", "active_model_assignments",
                "successful_interactions",
            ]:
                if field_name in saved_state:
                    setattr(self.state, field_name, saved_state[field_name])
            logger.info(
                f"Entity loaded state: "
                f"persona={self.state.current_persona}, "
                f"interactions={self.state.interaction_count}"
            )
        except Exception as exc:
            logger.warning(f"Entity._load_state failed (using defaults): {exc}")

    # ── Diagnostics ───────────────────────────────────────────────────────────
    def status_dict(self) -> dict:
        """Return a snapshot of entity state for API endpoints / logging."""
        return {
            "identity": {
                "name": self.identity.name,
                "version": self.identity.version,
                "primary_persona": self.identity.primary_persona,
            },
            "state": {
                "current_persona": self.state.current_persona,
                "interaction_count": self.state.interaction_count,
                "skill_evolution_count": self.state.skill_evolution_count,
                "persona_scores": self.state.persona_scores,
                "errors_this_session": self.state.errors_this_session,
                "successful_interactions": self.state.successful_interactions,
            },
            "router": {
                "total_assignments": len(self.router.all_assignments()),
                "discovered_models": len(self.router.all_discovered_models()),
                "active_assignments": self.state.active_model_assignments,
            },
        }

    def __repr__(self) -> str:
        return (
            f"Entity(name={self.identity.name!r}, "
            f"persona={self.state.current_persona!r}, "
            f"interactions={self.state.interaction_count})"
        )


# ─── Helpers ──────────────────────────────────────────────────────────────────
def _tree_label(path: Path) -> str:
    """Convert a model directory path to a short label."""
    parts = path.parts
    if "turboquant" in str(path):
        return "turboquant"
    if "SPAWN" in parts:
        return "spawn"
    if "openenv" in parts:
        return "openenv"
    return str(path.parent.name)


def _ts_iso() -> str:
    import datetime
    return datetime.datetime.now().isoformat(timespec="seconds")


# ─── Module-level singletons ──────────────────────────────────────────────────
# Lazily initialised so that importing this module doesn't do disk I/O at
# module load time unless explicitly requested.
_entity: Optional[Entity] = None
entity_router: VocabModelRouter = VocabModelRouter()


def get_entity() -> Entity:
    """Return the module-level Entity singleton, initialising it on first call."""
    global _entity
    if _entity is None:
        _entity = Entity(router=entity_router)
        _entity.initialise()
    return _entity


def get_router() -> VocabModelRouter:
    """Return the module-level router singleton (builds on first call)."""
    if not entity_router._built:
        entity_router.build()
    return entity_router


# ─── CLI entry point ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    print("=" * 60)
    print("Lilly Entity Core — initialising")
    print("=" * 60)

    entity = get_entity()
    entity.persist()

    print(f"\nEntity: {entity}")
    print(f"\nDiscovered models ({len(entity.router.all_discovered_models())}):")
    for m in entity.router.all_discovered_models():
        print(f"  [{m['tree']:12s}] {m['name']}")

    print(f"\nRouting table ({len(entity.router.all_assignments())} domains):")
    for a in sorted(entity.router.all_assignments(), key=lambda x: x.task_tag):
        print(f"  {a.task_tag:25s} → {a.model_name}")

    if len(sys.argv) > 1:
        test_text = " ".join(sys.argv[1:])
        tag = entity.router.infer_task_from_text(test_text)
        model = entity.model_for_text(test_text)
        print(f'\nText: "{test_text}"')
        print(f"  Inferred task: {tag}")
        print(f"  Selected model: {model or '(none)'}")

    print(f"\nStatus: {json.dumps(entity.status_dict(), indent=2)}")
    print(f"\nRegistry saved to: {REGISTRY_FILE}")
