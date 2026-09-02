#!/usr/bin/env python3
"""
persona_optimizer.py — Lilly Persona Self-Optimization Engine
==============================================================
Scores, evolves, and persists persona configs based on interaction outcomes.

How it works
────────────
1. Every interaction that completes without error is scored on three axes:
     engagement  — did the user keep the conversation going?
     accuracy    — did the reply stay on-topic / factually correct?
     style_match — did response length/tone match user expectations?

2. Scores are accumulated per-persona in a rolling window (last 100 interactions).

3. A background optimisation cycle (triggered by lilly_ai.py via a hook)
   computes a composite score and flags low-performing personas for "evolution":
     - Tune voice_prompt instruction weights (shorten/lengthen, add emphasis)
     - Bump or reduce casualness level
     - Adjust response-length guidance

4. Every evolution step is versioned and persisted to
   WORKSPACE/persona_configs.json so changes survive restarts.

5. lilly_ai.py's HIVE_PERSONAS dict is patched at runtime to pick up evolved
   voice_prompt strings without restarting the server.

Usage (from lilly_ai.py)
────────────────────────
  from persona_optimizer import persona_optimizer, PersonaScore

  # After each interaction:
  persona_optimizer.record_score(
      persona="fox",
      engagement=0.8,
      accuracy=0.9,
      style_match=0.7,
      user_text_len=42,
      reply_text_len=85,
  )

  # Run optimisation cycle (call this periodically):
  persona_optimizer.optimise()

  # Get the current (possibly evolved) voice_prompt for a persona:
  prompt = persona_optimizer.voice_prompt_for("cat")
"""

from __future__ import annotations

import json
import logging
import math
import os
import random
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

logger = logging.getLogger("PersonaOptimizer")

WORKSPACE = Path(os.environ.get("LILLY_WORKSPACE", "")) or Path(__file__).parent
PERSONA_CONFIGS_FILE = WORKSPACE / "persona_configs.json"

# ─── Score weights ─────────────────────────────────────────────────────────────
SCORE_WEIGHTS = {
    "engagement": 0.45,
    "accuracy":   0.35,
    "style_match": 0.20,
}

# How many recent interactions to keep per persona
ROLLING_WINDOW = 100

# Composite score below this triggers an optimisation attempt
OPTIMISE_THRESHOLD = 0.60

# Minimum interactions before we attempt optimisation (avoid noise)
MIN_INTERACTIONS = 10

# Optimisation directions and their adjustments
_LENGTH_GUIDANCE = [
    "1 sentence preferred.",
    "1-2 sentences max.",
    "Keep replies tight: 1 sentence.",
    "Aim for brevity: under 25 words.",
    "2-3 sentences when depth is needed.",
]
_TONE_TWEAKS = [
    "Be a bit warmer.",
    "Be more direct.",
    "Inject a touch of dry humour.",
    "Be slightly more formal.",
    "Be more energetic.",
    "Be calmer and more measured.",
]


# ─── Data classes ─────────────────────────────────────────────────────────────
@dataclass
class PersonaScore:
    """A single scored interaction for a persona."""
    persona: str
    engagement: float      # 0.0 – 1.0
    accuracy: float        # 0.0 – 1.0
    style_match: float     # 0.0 – 1.0
    user_text_len: int = 0
    reply_text_len: int = 0
    timestamp: float = field(default_factory=time.time)

    @property
    def composite(self) -> float:
        return (
            self.engagement  * SCORE_WEIGHTS["engagement"] +
            self.accuracy    * SCORE_WEIGHTS["accuracy"] +
            self.style_match * SCORE_WEIGHTS["style_match"]
        )


@dataclass
class PersonaEvolution:
    """One evolution step applied to a persona."""
    persona: str
    version: int
    timestamp: float
    avg_score_before: float
    change_type: str        # "length" | "tone" | "emphasis"
    change_applied: str     # the actual text tweak
    voice_prompt_hash: str  # SHA1 of the new voice_prompt for diffing


@dataclass
class PersonaConfig:
    """Persisted config for one persona, including evolution history."""
    persona: str
    base_voice_prompt: str               # the original, never mutated
    current_voice_prompt: str            # the live (possibly evolved) version
    version: int = 0
    total_interactions: int = 0
    avg_score_recent: float = 0.0
    evolution_history: list[dict] = field(default_factory=list)
    last_optimised: float = 0.0


# ─── Optimizer ────────────────────────────────────────────────────────────────
class PersonaOptimizer:
    """
    Tracks per-persona performance and self-evolves voice prompts.
    Thread-safe for single-process use (no async needed — called from within
    the synchronous parts of lilly_ai.py or as a background thread).
    """

    def __init__(self):
        # per-persona rolling score windows
        self._scores: dict[str, deque[PersonaScore]] = {}
        # per-persona persisted configs
        self._configs: dict[str, PersonaConfig] = {}
        # reference to lilly_ai HIVE_PERSONAS (injected at runtime)
        self._hive_personas: Optional[dict] = None
        self._loaded = False

    # ── Injection ─────────────────────────────────────────────────────────────
    def attach_hive(self, hive_personas: dict):
        """
        Give the optimizer a reference to lilly_ai.HIVE_PERSONAS so it can
        patch voice prompts in-place when evolution runs.
        """
        self._hive_personas = hive_personas
        # Bootstrap configs from current HIVE_PERSONAS definitions
        for persona_key, persona_def in hive_personas.items():
            if persona_key not in self._configs:
                vp = persona_def.get("voice_prompt", "")
                self._configs[persona_key] = PersonaConfig(
                    persona=persona_key,
                    base_voice_prompt=vp,
                    current_voice_prompt=vp,
                )
            if persona_key not in self._scores:
                self._scores[persona_key] = deque(maxlen=ROLLING_WINDOW)
        logger.info(f"PersonaOptimizer attached to HIVE_PERSONAS ({len(hive_personas)} personas)")

    # ── Scoring ───────────────────────────────────────────────────────────────
    def record_score(
        self,
        persona: str,
        engagement: float,
        accuracy: float,
        style_match: float,
        user_text_len: int = 0,
        reply_text_len: int = 0,
    ):
        """Record one scored interaction for a persona."""
        score = PersonaScore(
            persona=persona,
            engagement=max(0.0, min(1.0, engagement)),
            accuracy=max(0.0, min(1.0, accuracy)),
            style_match=max(0.0, min(1.0, style_match)),
            user_text_len=user_text_len,
            reply_text_len=reply_text_len,
        )
        if persona not in self._scores:
            self._scores[persona] = deque(maxlen=ROLLING_WINDOW)
        self._scores[persona].append(score)

        cfg = self._get_config(persona)
        cfg.total_interactions += 1

        # Update rolling average
        recent = list(self._scores[persona])
        if recent:
            cfg.avg_score_recent = sum(s.composite for s in recent) / len(recent)

        logger.debug(
            f"PersonaScore: {persona} engagement={engagement:.2f} "
            f"accuracy={accuracy:.2f} style={style_match:.2f} "
            f"composite={score.composite:.2f} avg={cfg.avg_score_recent:.2f}"
        )

    def infer_scores_from_interaction(
        self,
        persona: str,
        user_text: str,
        reply_text: str,
        follow_up_received: bool = False,
        error_occurred: bool = False,
    ):
        """
        Auto-infer scores from observable signals when explicit scores are not
        available.  This is the main hook called from lilly_ai.py.
        """
        # Engagement: did the user follow up?
        engagement = 0.75 if follow_up_received else 0.5
        # Error penalty
        if error_occurred:
            engagement = 0.1
            accuracy = 0.1
            style_match = 0.5
        else:
            # Accuracy proxy: model didn't hallucinate common no-nos
            hallucination_patterns = [
                "like and subscribe", "thanks for watching", "link in description",
                "smash that bell", "hit that bell", "subscribe to",
                "in this video", "welcome back to my channel",
            ]
            penalise = any(p in reply_text.lower() for p in hallucination_patterns)
            accuracy = 0.3 if penalise else 0.85

            # Style match: ideal reply is roughly 1-2x the user's message length
            u_len = max(1, len(user_text))
            r_len = len(reply_text)
            ratio = r_len / u_len
            # Ideal ratio 0.5–2.5; penalise very short or very long replies
            if 0.5 <= ratio <= 2.5:
                style_match = 0.9
            elif ratio < 0.2:
                style_match = 0.5  # too terse
            elif ratio > 5.0:
                style_match = 0.4  # too verbose
            else:
                style_match = 0.7

        self.record_score(
            persona=persona,
            engagement=engagement,
            accuracy=accuracy,
            style_match=style_match,
            user_text_len=len(user_text),
            reply_text_len=len(reply_text),
        )

    # ── Optimisation cycle ────────────────────────────────────────────────────
    def optimise(self, force: bool = False) -> list[str]:
        """
        Run one optimisation pass over all personas.
        Returns a list of persona names that were evolved.
        """
        evolved = []
        for persona, score_window in self._scores.items():
            cfg = self._get_config(persona)
            if cfg.total_interactions < MIN_INTERACTIONS and not force:
                continue
            if not score_window:
                continue
            avg = sum(s.composite for s in score_window) / len(score_window)
            if avg < OPTIMISE_THRESHOLD or force:
                changed = self._evolve(persona, cfg, avg)
                if changed:
                    evolved.append(persona)
        if evolved:
            self._persist()
        return evolved

    def _evolve(self, persona: str, cfg: PersonaConfig, avg_score: float) -> bool:
        """
        Apply one small evolution step to a persona's voice prompt.
        Returns True if a change was made.
        """
        old_prompt = cfg.current_voice_prompt
        if not old_prompt:
            return False

        # Decide what kind of tweak to make based on score breakdown
        recent = list(self._scores.get(persona, []))
        if not recent:
            return False

        avg_engagement = sum(s.engagement for s in recent) / len(recent)
        avg_style     = sum(s.style_match for s in recent) / len(recent)

        if avg_style < 0.55:
            # Style mismatch — adjust length guidance
            change_type = "length"
            tweak = random.choice(_LENGTH_GUIDANCE)
        elif avg_engagement < 0.55:
            # Low engagement — adjust tone
            change_type = "tone"
            tweak = random.choice(_TONE_TWEAKS)
        else:
            # General emphasis tweak
            change_type = "emphasis"
            tweak = random.choice(_TONE_TWEAKS + _LENGTH_GUIDANCE)

        # Append the tweak as an instruction suffix (idempotent-ish)
        new_prompt = old_prompt.rstrip()
        # Remove previous auto-tune lines to avoid accumulation
        lines = new_prompt.split("\n")
        lines = [l for l in lines if not l.startswith("AUTO-TUNE:")]
        new_prompt = "\n".join(lines) + f"\nAUTO-TUNE: {tweak}"

        cfg.current_voice_prompt = new_prompt
        cfg.version += 1
        cfg.last_optimised = time.time()

        evolution = PersonaEvolution(
            persona=persona,
            version=cfg.version,
            timestamp=time.time(),
            avg_score_before=avg_score,
            change_type=change_type,
            change_applied=tweak,
            voice_prompt_hash=_short_hash(new_prompt),
        )
        cfg.evolution_history.append(asdict(evolution))

        # Patch HIVE_PERSONAS in-place if we have a reference
        if self._hive_personas and persona in self._hive_personas:
            self._hive_personas[persona]["voice_prompt"] = new_prompt

        logger.info(
            f"PersonaOptimizer evolved '{persona}' "
            f"(v{cfg.version}): [{change_type}] {tweak!r} "
            f"(avg_score was {avg_score:.2f})"
        )
        return True

    # ── Query API ─────────────────────────────────────────────────────────────
    def voice_prompt_for(self, persona: str) -> Optional[str]:
        """Return the current (possibly evolved) voice prompt for a persona."""
        cfg = self._configs.get(persona)
        if cfg:
            return cfg.current_voice_prompt or cfg.base_voice_prompt
        # Fallback: pull from HIVE_PERSONAS if attached
        if self._hive_personas and persona in self._hive_personas:
            return self._hive_personas[persona].get("voice_prompt", "")
        return None

    def set_voice_prompt(self, persona: str, new_prompt: str) -> bool:
        """Persist a user-authorized voice prompt update for a persona.

        Updates both the in-memory config and persona_configs.json so the
        change survives a server restart.  Also stamps an evolution_history
        entry so the change is auditable.

        Returns True on success, False if persistence failed.
        """
        cfg = self._get_config(persona)
        old_prompt = cfg.current_voice_prompt or cfg.base_voice_prompt or ""
        cfg.current_voice_prompt = new_prompt
        cfg.version += 1
        cfg.last_optimised = time.time()
        cfg.evolution_history.append(
            {
                "version": cfg.version,
                "ts": cfg.last_optimised,
                "source": "user_authorized",
                "change_summary": f"User-authorized update (len {len(old_prompt)} → {len(new_prompt)})",
            }
        )
        # Also patch the live HIVE_PERSONAS dict so the change is immediate
        if self._hive_personas and persona in self._hive_personas:
            self._hive_personas[persona]["voice_prompt"] = new_prompt
        try:
            self._persist()
            return True
        except Exception as e:
            logger.warning(f"set_voice_prompt: failed to persist for '{persona}': {e}")
            return False

    def avg_score_for(self, persona: str) -> float:
        """Return the recent composite score for a persona (0.0 if no data)."""
        return self._get_config(persona).avg_score_recent

    def scores_summary(self) -> dict[str, dict]:
        """Return a summary of scores for all tracked personas."""
        out = {}
        for persona, cfg in self._configs.items():
            recent = list(self._scores.get(persona, []))
            out[persona] = {
                "total_interactions": cfg.total_interactions,
                "avg_score_recent": round(cfg.avg_score_recent, 3),
                "version": cfg.version,
                "last_optimised": cfg.last_optimised,
                "recent_window_size": len(recent),
            }
        return out

    def evolution_history_for(self, persona: str) -> list[dict]:
        """Return the full evolution history for a persona."""
        return self._get_config(persona).evolution_history

    # ── Persistence ───────────────────────────────────────────────────────────
    def _persist(self):
        """Write all persona configs to persona_configs.json."""
        try:
            data = {
                "meta": {
                    "saved_at": time.time(),
                    "format_version": 1,
                },
                "personas": {
                    k: {
                        "persona": v.persona,
                        "version": v.version,
                        "total_interactions": v.total_interactions,
                        "avg_score_recent": round(v.avg_score_recent, 4),
                        "current_voice_prompt": v.current_voice_prompt,
                        "last_optimised": v.last_optimised,
                        "evolution_history": v.evolution_history[-20:],  # keep last 20
                    }
                    for k, v in self._configs.items()
                },
            }
            PERSONA_CONFIGS_FILE.write_text(json.dumps(data, indent=2))
            logger.debug(f"PersonaOptimizer: persisted {len(self._configs)} persona configs")
        except Exception as exc:
            logger.error(f"PersonaOptimizer._persist failed: {exc}")

    def load(self):
        """Load persisted persona configs from disk."""
        if not PERSONA_CONFIGS_FILE.exists():
            return
        try:
            data = json.loads(PERSONA_CONFIGS_FILE.read_text())
            for persona_key, p_data in data.get("personas", {}).items():
                cfg = self._get_config(persona_key)
                cfg.version = p_data.get("version", 0)
                cfg.total_interactions = p_data.get("total_interactions", 0)
                cfg.avg_score_recent = p_data.get("avg_score_recent", 0.0)
                cfg.last_optimised = p_data.get("last_optimised", 0.0)
                cfg.evolution_history = p_data.get("evolution_history", [])
                # Only restore evolved prompt if it differs from base
                evolved = p_data.get("current_voice_prompt", "")
                if evolved and evolved != cfg.base_voice_prompt:
                    cfg.current_voice_prompt = evolved
                    # Patch HIVE_PERSONAS if available
                    if self._hive_personas and persona_key in self._hive_personas:
                        self._hive_personas[persona_key]["voice_prompt"] = evolved
            logger.info(f"PersonaOptimizer: loaded {len(self._configs)} persona configs from disk")
        except Exception as exc:
            logger.warning(f"PersonaOptimizer.load failed: {exc}")

    # ── Internal helpers ──────────────────────────────────────────────────────
    def _get_config(self, persona: str) -> PersonaConfig:
        """Get or create a PersonaConfig for a persona."""
        if persona not in self._configs:
            base_vp = ""
            if self._hive_personas and persona in self._hive_personas:
                base_vp = self._hive_personas[persona].get("voice_prompt", "")
            self._configs[persona] = PersonaConfig(
                persona=persona,
                base_voice_prompt=base_vp,
                current_voice_prompt=base_vp,
            )
            self._scores[persona] = deque(maxlen=ROLLING_WINDOW)
        return self._configs[persona]


# ─── Helpers ──────────────────────────────────────────────────────────────────
def _short_hash(text: str) -> str:
    import hashlib
    return hashlib.sha1(text.encode()).hexdigest()[:8]


# ─── Module-level singleton ───────────────────────────────────────────────────
persona_optimizer = PersonaOptimizer()


# ─── CLI entry point ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    # Simulate a few scored interactions
    personas = ["puppy", "fox", "cat", "bear", "bunny"]
    print("Simulating 20 interactions per persona...\n")
    for persona in personas:
        for i in range(20):
            persona_optimizer.infer_scores_from_interaction(
                persona=persona,
                user_text="Hey, what's the weather like?",
                reply_text="Feels like rain — pressure's dropping." if i % 3 != 0 else (
                    "I detect that the barometric pressure reading from the onboard sensor is currently "
                    "showing a value of approximately 993 hPa which is below normal atmospheric pressure..."
                ),
                follow_up_received=(i % 4 == 0),
                error_occurred=(i == 7),
            )

    print("Scores summary (before optimisation):")
    for persona, summary in persona_optimizer.scores_summary().items():
        print(f"  {persona:10s}: avg={summary['avg_score_recent']:.3f} "
              f"n={summary['total_interactions']}")

    print("\nRunning optimisation...")
    evolved = persona_optimizer.optimise(force=True)
    print(f"Evolved personas: {evolved}")

    print("\nScores summary (after optimisation):")
    for persona, summary in persona_optimizer.scores_summary().items():
        print(f"  {persona:10s}: v={summary['version']} avg={summary['avg_score_recent']:.3f}")

    persona_optimizer._persist()
    print(f"\nPersisted to: {PERSONA_CONFIGS_FILE}")
