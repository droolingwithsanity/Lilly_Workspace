#!/usr/bin/env python3
"""
skills_engine.py — Lilly Skills Self-Update Engine
====================================================
Learns new skills from interactions and merges them into lilly_skills.json.

How it works
────────────
1. After every interaction, the engine scans the user's text for intent signals
   that don't match any existing skill.

2. If a new intent pattern is detected confidently enough, a candidate skill
   entry is constructed and added to a pending queue.

3. On each persist cycle (or when explicitly triggered), high-confidence
   candidates are merged into lilly_skills.json, giving Lilly the ability to
   perform that action in future conversations.

4. Learned skills are tagged with their source so they can be reviewed or
   pruned.

5. The engine also reinforces existing skills: aliases that users frequently
   use bump the alias list; actions with high error rates get flagged.

Skill entry format (matches existing lilly_skills.json)
──────────────────────────────────────────────────────
  {
    "action_type": "intent_launch" | "shell_command" | "prompt_argument",
    "package": "com.example.app",            # optional
    "intent_action": "android.intent.action.VIEW",
    "label": "Human Readable Name",
    "canned_reply": "Opening!",              # optional
    "aliases": ["alias1", "alias2"],
    "uri_template": "https://...",           # optional
    # Added by skills_engine:
    "_learned": true,
    "_confidence": 0.82,
    "_source": "interaction",
    "_learned_at": 1783900000.0,
    "_use_count": 1,
  }

Usage (from lilly_ai.py)
────────────────────────
  from skills_engine import skills_engine

  # After each interaction (always call, engine decides if learning happened):
  skills_engine.observe_interaction(
      user_text="open Netflix",
      reply_text="Opening Netflix!",
      action_taken="intent_launch",
      success=True,
  )

  # Periodically flush learned skills to disk:
  skills_engine.flush()

  # Check if a skill was learned since last check:
  new_skills = skills_engine.drain_new_skills()
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("SkillsEngine")

WORKSPACE = Path(os.environ.get("LILLY_WORKSPACE", "")) or Path(__file__).parent
SKILLS_FILE = WORKSPACE / "lilly_skills.json"
PENDING_SKILLS_FILE = WORKSPACE / "pending_skills.json"

# Minimum observation count before a candidate becomes a confirmed skill
MIN_OBSERVATIONS = 2
# Minimum confidence score to auto-promote to skills file
AUTO_PROMOTE_THRESHOLD = 0.70
# Maximum aliases per skill (avoid bloat)
MAX_ALIASES = 12

# ─── Intent detectors ─────────────────────────────────────────────────────────
# Regex patterns that suggest the user wants to open/launch/use an app or website.
# Group "target" captures what they want to open.
_LAUNCH_PATTERNS = [
    re.compile(r'\b(?:open|launch|start|run|use|go to)\s+(?:the\s+)?(?P<target>[\w\s]+?)(?:\s+app|\s+for\s+me)?\s*$', re.IGNORECASE),
    re.compile(r'\b(?:take me to|navigate to|switch to)\s+(?P<target>[\w\s]+)', re.IGNORECASE),
    re.compile(r'\bshow me\s+(?P<target>[\w\s]+)', re.IGNORECASE),
]

# Patterns that suggest a shell command skill
_COMMAND_PATTERNS = [
    re.compile(r'\b(?:run|execute|do|perform)\s+(?P<cmd>[a-z][a-z0-9_\-\s]{2,30}?)(?:\s+command|\s+script)?\s*$', re.IGNORECASE),
    re.compile(r'\b(?:check|show|list|get)\s+(?P<cmd>[a-z][a-z0-9_\-\s]{2,30}?)\s*$', re.IGNORECASE),
]

# Natural language intent patterns for OSINT and utility skills
_NL_INTENT_PATTERNS = [
    # News lookup: "what's in the news", "news in another country", "latest news about X"
    (re.compile(r'\b(?:what(?:\'s| is| are) (?:in |the )?news|latest news|news (?:in|about|from)|show (?:me )?news|see (?:the )?news|headlines|current events)\b', re.IGNORECASE),
     "osint_news_search", "news"),
    # Account creation: "create an account", "make an account", "sign up for X"
    (re.compile(r'\b(?:create|make|sign up|register|open) (?:a |an |the )?(?:new )?(?:account|profile|membership)\b', re.IGNORECASE),
     "osint_account_create", "account"),
    # Anonymous SMS: "send anonymous sms", "anonymous text"
    (re.compile(r'\b(?:send|text|message) (?:an? )?(?:anonymous|anon|hidden|untraceable) (?:sms|text|message)\b', re.IGNORECASE),
     "osint_anonymous_sms", "sms"),
    # Anonymous email: "send anonymous email", "anonymous email"
    (re.compile(r'\b(?:send|email|mail) (?:an? )?(?:anonymous|anon|hidden|untraceable|disposable) (?:email|mail|message)\b', re.IGNORECASE),
     "osint_anonymous_email", "email"),
    # Temp email: "temp email", "disposable email", "burner email"
    (re.compile(r'\b(?:temp|temporary|disposable|burner|throwaway|fake) email\b', re.IGNORECASE),
     "osint_temp_email", "temp_email"),
    # Fake identity: "fake identity", "fake name", "generate identity"
    (re.compile(r'\b(?:fake|generate|create|make) (?:a )?(?:identity|name|persona|profile|person)\b', re.IGNORECASE),
     "osint_fake_identity", "identity"),
    # People search natural language: "who is X", "find person"
    (re.compile(r'\b(?:who is|find person|look up person|search for person|locate)\s+(?P<target>[\w\s]+)', re.IGNORECASE),
     "osint_people_search", "people"),
    # Username check natural language: "check if username exists"
    (re.compile(r'\b(?:check|verify|see if|look up)\s+(?:username|user|handle|account)\s+(?P<target>[\w\s]+)', re.IGNORECASE),
     "osint_username_check", "username"),
]

# Well-known Android app packages to attempt auto-resolution
_KNOWN_PACKAGES = {
    "netflix":       "com.netflix.mediaclient",
    "tiktok":        "com.zhiliaoapp.musically",
    "instagram":     "com.instagram.android",
    "twitter":       "com.twitter.android",
    "x":             "com.twitter.android",
    "snapchat":      "com.snapchat.android",
    "spotify":       "com.spotify.music",
    "discord":       "com.discord",
    "whatsapp":      "com.whatsapp",
    "telegram":      "org.telegram.messenger",
    "reddit":        "com.reddit.frontpage",
    "facebook":      "com.facebook.katana",
    "uber":          "com.ubercab",
    "lyft":          "com.lyft",
    "amazon":        "com.amazon.mShop.android.shopping",
    "ebay":          "com.ebay.mobile",
    "zoom":          "us.zoom.videomeetings",
    "teams":         "com.microsoft.teams",
    "slack":         "com.Slack",
    "chrome":        "com.android.chrome",
    "firefox":       "org.mozilla.firefox",
    "brave":         "com.brave.browser",
    "photos":        "com.google.android.apps.photos",
    "clock":         "com.android.deskclock",
    "files":         "com.google.android.documentsui",
    "contacts":      "com.android.contacts",
    "phone":         "com.android.phone",
    "messages":      "com.google.android.apps.messaging",
    "dialer":        "com.android.dialer",
    "gmail":         "com.google.android.gm",
    "drive":         "com.google.android.apps.docs",
    "maps":          "com.google.android.apps.maps",
    "translate":     "com.google.android.apps.translate",
    "keep":          "com.google.android.keep",
    "docs":          "com.google.android.apps.docs.editors.docs",
    "sheets":        "com.google.android.apps.docs.editors.sheets",
    "slides":        "com.google.android.apps.docs.editors.slides",
    "play store":    "com.android.vending",
    "youtube":       "com.google.android.youtube",
    "youtube music": "com.google.android.apps.youtube.music",
}


# ─── Candidate skill ──────────────────────────────────────────────────────────
@dataclass
class CandidateSkill:
    """A skill candidate built from observed user intent."""
    key: str                    # normalised key for skill dict
    label: str                  # human-readable name
    action_type: str            # "intent_launch" | "shell_command" | "prompt_argument"
    aliases: list[str] = field(default_factory=list)
    package: str = ""
    intent_action: str = ""
    uri_template: str = ""
    canned_reply: str = ""
    command: str = ""
    args: list[str] = field(default_factory=list)
    observations: int = 0
    confidence: float = 0.0
    source: str = "interaction"
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    use_count: int = 0          # times the skill was actually invoked

    def to_skill_entry(self) -> dict:
        """Convert to the format used in lilly_skills.json."""
        entry: dict = {
            "action_type": self.action_type,
            "label": self.label,
            "aliases": list(dict.fromkeys(self.aliases))[:MAX_ALIASES],
            "_learned": True,
            "_confidence": round(self.confidence, 3),
            "_source": self.source,
            "_learned_at": self.first_seen,
            "_use_count": self.use_count,
        }
        if self.package:
            entry["package"] = self.package
            entry["type"] = "intent_launch"
        if self.intent_action:
            entry["intent_action"] = self.intent_action
        if self.uri_template:
            entry["uri_template"] = self.uri_template
        if self.canned_reply:
            entry["canned_reply"] = self.canned_reply
        if self.command:
            entry["command"] = self.command
        if self.args:
            entry["args"] = self.args
        return entry


# ─── Skills Engine ────────────────────────────────────────────────────────────
class SkillsEngine:
    """
    Learns new skills from user interactions and merges them into lilly_skills.json.
    """

    def __init__(self):
        self._candidates: dict[str, CandidateSkill] = {}
        self._existing_skills: dict[str, dict] = {}
        self._existing_keys: set[str] = set()
        self._existing_aliases: set[str] = set()
        self._new_skills_since_flush: list[str] = []
        self._alias_observations: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._loaded = False
        self._last_flush: float = 0.0
        self._flush_interval: float = 60.0  # seconds between auto-flushes

    # ── Loading ───────────────────────────────────────────────────────────────
    def load(self):
        """Load existing skills and any pending candidates from disk."""
        self._load_skills()
        self._load_pending()
        self._loaded = True

    def _load_skills(self):
        """Load lilly_skills.json into the internal index."""
        if not SKILLS_FILE.exists():
            return
        try:
            raw = json.loads(SKILLS_FILE.read_text())
            self._existing_skills = raw
            self._existing_keys = set(raw.keys())
            # Build alias → key reverse index
            for key, skill in raw.items():
                self._existing_aliases.add(_normalise(key))
                for alias in skill.get("aliases", []):
                    self._existing_aliases.add(_normalise(alias))
            logger.info(f"SkillsEngine: loaded {len(self._existing_skills)} skills")
        except Exception as exc:
            logger.error(f"SkillsEngine._load_skills failed: {exc}")

    def _load_pending(self):
        """Load any previously saved candidate skills from pending_skills.json."""
        if not PENDING_SKILLS_FILE.exists():
            return
        try:
            data = json.loads(PENDING_SKILLS_FILE.read_text())
            for key, entry in data.items():
                if key not in self._candidates:
                    # Reconstruct CandidateSkill from dict
                    cs = CandidateSkill(
                        key=key,
                        label=entry.get("label", key),
                        action_type=entry.get("action_type", "intent_launch"),
                        aliases=entry.get("aliases", []),
                        package=entry.get("package", ""),
                        intent_action=entry.get("intent_action", ""),
                        uri_template=entry.get("uri_template", ""),
                        canned_reply=entry.get("canned_reply", ""),
                        command=entry.get("command", ""),
                        args=entry.get("args", []),
                        observations=entry.get("observations", 1),
                        confidence=entry.get("confidence", 0.5),
                        first_seen=entry.get("first_seen", time.time()),
                        last_seen=entry.get("last_seen", time.time()),
                        use_count=entry.get("use_count", 0),
                    )
                    self._candidates[key] = cs
            logger.info(f"SkillsEngine: loaded {len(self._candidates)} pending candidates")
        except Exception as exc:
            logger.warning(f"SkillsEngine._load_pending failed: {exc}")

    # ── Observation ───────────────────────────────────────────────────────────
    def observe_interaction(
        self,
        user_text: str,
        reply_text: str = "",
        action_taken: str = "",
        success: bool = True,
    ):
        """
        Main hook — called after every interaction in lilly_ai.py.
        Analyses user_text for unknown intents and builds candidates.
        """
        if not self._loaded:
            self.load()

        # Track alias usage for existing skills
        self._track_alias_usage(user_text)

        # Try to extract a new intent
        self._detect_new_intent(user_text, success=success)

        # Auto-flush if interval elapsed
        if time.time() - self._last_flush >= self._flush_interval:
            self.flush()

    def _track_alias_usage(self, user_text: str):
        """Bump alias usage counters for any existing skill that matches."""
        norm = _normalise(user_text)
        for key, skill in self._existing_skills.items():
            aliases = skill.get("aliases", [])
            for alias in aliases:
                if _normalise(alias) in norm or norm in _normalise(alias):
                    self._alias_observations[key][alias] += 1

    def _detect_new_intent(self, user_text: str, success: bool = True):
        """Detect if user_text contains an intent not covered by existing skills."""
        norm = _normalise(user_text)

        # Skip if this text already matches an existing skill/alias
        if self._matches_existing(norm):
            return

        # Try launch patterns
        for pattern in _LAUNCH_PATTERNS:
            m = pattern.search(user_text)
            if m:
                target = m.group("target").strip().lower()
                target = re.sub(r'\s+', ' ', target).strip("the ").strip()
                if len(target) < 2 or len(target) > 40:
                    continue
                self._register_candidate_launch(target, user_text, success)
                return  # one detection per message

        # Try command patterns (only for clearly command-like text)
        for pattern in _COMMAND_PATTERNS:
            m = pattern.search(user_text)
            if m:
                cmd_name = m.group("cmd").strip().lower()
                if len(cmd_name) < 3 or len(cmd_name) > 30:
                    continue
                if _is_common_word(cmd_name):
                    continue
                self._register_candidate_command(cmd_name, user_text, success)
                return

        # Try natural language OSINT/utility intent patterns
        for pattern, skill_key, category in _NL_INTENT_PATTERNS:
            m = pattern.search(user_text)
            if m:
                self._register_nl_intent(skill_key, category, user_text, success)
                return

    def _matches_existing(self, norm_text: str) -> bool:
        """Return True if normalised text matches any existing skill or alias."""
        for existing_alias in self._existing_aliases:
            if existing_alias and (existing_alias in norm_text or norm_text in existing_alias):
                if len(existing_alias) > 3:  # avoid matching single words like "open"
                    return True
        return False

    def _register_candidate_launch(self, target: str, original_text: str, success: bool):
        """Create or update a launch-type candidate skill."""
        key = _skill_key(target)
        if key in self._existing_keys:
            return

        if key not in self._candidates:
            # Try to resolve a known package
            package = _KNOWN_PACKAGES.get(target, "")
            intent_action = "android.intent.action.VIEW"
            canned_reply = f"Opening {target.title()}!"

            cs = CandidateSkill(
                key=key,
                label=target.title(),
                action_type="intent_launch",
                aliases=_generate_aliases(target),
                package=package,
                intent_action=intent_action,
                canned_reply=canned_reply,
                confidence=0.4 if not package else 0.65,
            )
            self._candidates[key] = cs
            logger.debug(f"SkillsEngine: new candidate '{key}' (package={package or 'unknown'})")
        else:
            cs = self._candidates[key]

        # Bump observation count and recalculate confidence
        cs.observations += 1
        cs.last_seen = time.time()
        if original_text not in [_normalise(a) for a in cs.aliases]:
            raw_alias = _extract_alias(original_text)
            if raw_alias and raw_alias not in cs.aliases:
                cs.aliases.append(raw_alias)

        # Confidence increases with observations; known package gives a bonus
        base = 0.6 if cs.package else 0.35
        obs_factor = min(1.0, cs.observations / 5.0)
        cs.confidence = base + 0.35 * obs_factor
        if success:
            cs.confidence = min(1.0, cs.confidence + 0.05)

    def _register_candidate_command(self, cmd_name: str, original_text: str, success: bool):
        """Create or update a shell-command-type candidate skill."""
        key = _skill_key(cmd_name)
        if key in self._existing_keys:
            return

        if key not in self._candidates:
            cs = CandidateSkill(
                key=key,
                label=cmd_name.replace("_", " ").title(),
                action_type="shell_command",
                aliases=_generate_aliases(cmd_name),
                command=cmd_name.split()[0],
                confidence=0.3,
            )
            self._candidates[key] = cs

        cs = self._candidates[key]
        cs.observations += 1
        cs.last_seen = time.time()
        base = 0.30
        obs_factor = min(1.0, cs.observations / 5.0)
        cs.confidence = base + 0.45 * obs_factor
        if success:
            cs.confidence = min(1.0, cs.confidence + 0.05)

    def _register_nl_intent(self, skill_key: str, category: str, original_text: str, success: bool):
        """Register a natural language intent (OSINT/utility) as a candidate."""
        if skill_key in self._existing_keys:
            return
        if skill_key not in self._candidates:
            cs = CandidateSkill(
                key=skill_key,
                label=skill_key.replace("osint_", "").replace("_", " ").title(),
                action_type="prompt_argument",
                aliases=[category, original_text.strip().lower()[:40]],
                confidence=0.6,
            )
            self._candidates[skill_key] = cs
        cs = self._candidates[skill_key]
        cs.observations += 1
        cs.last_seen = time.time()
        obs_factor = min(1.0, cs.observations / 5.0)
        cs.confidence = 0.6 + 0.3 * obs_factor
        if success:
            cs.confidence = min(1.0, cs.confidence + 0.05)

    # ── Promotion ─────────────────────────────────────────────────────────────
    def flush(self) -> int:
        """
        Promote high-confidence candidates to lilly_skills.json.
        Returns the number of new skills added.
        """
        if not self._loaded:
            self.load()

        added = 0
        to_promote = [
            cs for cs in self._candidates.values()
            if cs.confidence >= AUTO_PROMOTE_THRESHOLD
            and cs.observations >= MIN_OBSERVATIONS
            and cs.key not in self._existing_keys
        ]

        if to_promote:
            # Reload skills file fresh to avoid overwriting parallel changes
            try:
                raw = json.loads(SKILLS_FILE.read_text()) if SKILLS_FILE.exists() else {}
            except Exception:
                raw = {}

            for cs in to_promote:
                raw[cs.key] = cs.to_skill_entry()
                self._existing_keys.add(cs.key)
                for alias in cs.aliases:
                    self._existing_aliases.add(_normalise(alias))
                self._new_skills_since_flush.append(cs.key)
                del self._candidates[cs.key]
                added += 1
                logger.info(
                    f"SkillsEngine: promoted skill '{cs.key}' "
                    f"(confidence={cs.confidence:.2f}, "
                    f"observations={cs.observations})"
                )

            SKILLS_FILE.write_text(json.dumps(raw, indent=2))
            self._existing_skills = raw

        # Also reinforce aliases for existing skills
        self._reinforce_aliases()

        # Persist pending candidates
        self._persist_pending()
        self._last_flush = time.time()

        return added

    def _reinforce_aliases(self):
        """
        Add frequently observed new aliases to existing skills.
        Triggers a skills file rewrite only if changes were made.
        """
        changed = False
        try:
            raw = json.loads(SKILLS_FILE.read_text()) if SKILLS_FILE.exists() else {}
        except Exception:
            return

        for key, alias_counts in self._alias_observations.items():
            if key not in raw:
                continue
            existing_aliases = set(raw[key].get("aliases", []))
            for alias, count in alias_counts.items():
                if count >= 3 and alias not in existing_aliases:
                    raw[key].setdefault("aliases", []).append(alias)
                    existing_aliases.add(alias)
                    changed = True
                    logger.info(f"SkillsEngine: reinforced alias '{alias}' for skill '{key}'")

        if changed:
            SKILLS_FILE.write_text(json.dumps(raw, indent=2))

    def _persist_pending(self):
        """Write pending candidates to pending_skills.json."""
        try:
            data = {}
            for key, cs in self._candidates.items():
                data[key] = {
                    "label": cs.label,
                    "action_type": cs.action_type,
                    "aliases": cs.aliases,
                    "package": cs.package,
                    "intent_action": cs.intent_action,
                    "uri_template": cs.uri_template,
                    "canned_reply": cs.canned_reply,
                    "command": cs.command,
                    "args": cs.args,
                    "observations": cs.observations,
                    "confidence": cs.confidence,
                    "first_seen": cs.first_seen,
                    "last_seen": cs.last_seen,
                    "use_count": cs.use_count,
                }
            PENDING_SKILLS_FILE.write_text(json.dumps(data, indent=2))
        except Exception as exc:
            logger.error(f"SkillsEngine._persist_pending failed: {exc}")

    # ── Query API ─────────────────────────────────────────────────────────────
    def drain_new_skills(self) -> list[str]:
        """Return (and clear) the list of skill keys added since the last drain."""
        drained = list(self._new_skills_since_flush)
        self._new_skills_since_flush.clear()
        return drained

    def pending_candidates(self) -> dict[str, dict]:
        """Return a serialisable summary of all pending candidates."""
        return {
            key: {
                "label": cs.label,
                "action_type": cs.action_type,
                "confidence": round(cs.confidence, 3),
                "observations": cs.observations,
                "package": cs.package or None,
                "aliases": cs.aliases[:5],
            }
            for key, cs in self._candidates.items()
        }

    def status(self) -> dict:
        """Return engine status for diagnostics."""
        return {
            "total_existing_skills": len(self._existing_skills),
            "pending_candidates": len(self._candidates),
            "promoted_this_session": len(self._new_skills_since_flush),
            "skills_file": str(SKILLS_FILE),
        }

    def force_add_skill(
        self,
        key: str,
        label: str,
        action_type: str,
        aliases: list[str],
        **kwargs,
    ) -> bool:
        """
        Manually add a skill directly to lilly_skills.json.
        Returns True if the skill was added (False if key already exists).
        """
        if not self._loaded:
            self.load()
        if key in self._existing_keys:
            logger.warning(f"SkillsEngine.force_add_skill: key '{key}' already exists")
            return False
        try:
            raw = json.loads(SKILLS_FILE.read_text()) if SKILLS_FILE.exists() else {}
        except Exception:
            raw = {}

        entry = {
            "action_type": action_type,
            "label": label,
            "aliases": aliases,
            "_learned": True,
            "_confidence": 1.0,
            "_source": "manual",
            "_learned_at": time.time(),
            "_use_count": 0,
        }
        entry.update(kwargs)
        raw[key] = entry
        SKILLS_FILE.write_text(json.dumps(raw, indent=2))
        self._existing_keys.add(key)
        self._existing_skills = raw
        self._new_skills_since_flush.append(key)
        logger.info(f"SkillsEngine.force_add_skill: added '{key}'")
        return True


# ─── Helpers ──────────────────────────────────────────────────────────────────
def _normalise(text: str) -> str:
    return re.sub(r'[^a-z0-9\s]', '', text.lower()).strip()


def _skill_key(name: str) -> str:
    """Convert a name to a valid skill dict key."""
    return re.sub(r'[^a-z0-9_]', '_', name.lower().strip()).strip('_')


def _generate_aliases(target: str) -> list[str]:
    """Generate a reasonable set of aliases for a target."""
    aliases = [target]
    words = target.split()
    if len(words) == 1:
        aliases += [
            f"open {target}",
            f"launch {target}",
            f"start {target}",
        ]
    else:
        aliases += [
            f"open {target}",
            f"launch {target}",
        ]
    return list(dict.fromkeys(aliases))


def _extract_alias(user_text: str) -> Optional[str]:
    """Extract a short alias phrase from the user's original text."""
    norm = _normalise(user_text)
    # Try to get a short representative phrase (max 4 words)
    words = norm.split()
    if len(words) <= 4:
        return norm
    # Look for "open X" or "launch X" substrings
    m = re.search(r'(?:open|launch|start|run)\s+([\w\s]{2,30})', norm)
    if m:
        return m.group(1).strip()
    return None


def _is_common_word(word: str) -> bool:
    """Return True if the word is too generic to be a skill name."""
    common = {
        "it", "this", "that", "now", "here", "there", "please", "again",
        "some", "more", "less", "up", "down", "on", "off", "all", "any",
        "me", "you", "we", "them", "he", "she", "is", "are", "was",
    }
    return word.lower().strip() in common


# ─── Module-level singleton ───────────────────────────────────────────────────
skills_engine = SkillsEngine()


# ─── CLI entry point ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    print("SkillsEngine demo\n" + "=" * 40)
    skills_engine.load()
    print(f"Loaded {skills_engine.status()['total_existing_skills']} existing skills\n")

    test_inputs = [
        "open Netflix for me",
        "open Netflix please",
        "launch Netflix",
        "open TikTok",
        "open TikTok now",
        "open TikTok please",
        "navigate to the clock app",
        "show me Discord",
        "run disk cleanup",
        "check system uptime",
    ]

    print("Observing test interactions...")
    for text in test_inputs:
        skills_engine.observe_interaction(
            user_text=text,
            reply_text=f"Handling: {text}",
            success=True,
        )
        print(f"  '{text}'")

    print(f"\nPending candidates ({len(skills_engine.pending_candidates())}):")
    for key, info in skills_engine.pending_candidates().items():
        print(f"  {key:30s} conf={info['confidence']:.2f} obs={info['observations']}")

    print("\nFlushing (promoting high-confidence candidates)...")
    added = skills_engine.flush()
    print(f"  Added {added} new skills to lilly_skills.json")

    new_skills = skills_engine.drain_new_skills()
    if new_skills:
        print(f"  New skill keys: {new_skills}")

    print(f"\nStatus: {skills_engine.status()}")
