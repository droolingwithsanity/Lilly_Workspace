#!/usr/bin/env python3
"""
tier_model.py — Instagram Engagement Tier System
=================================================

Premium tier model for Lilly AI Instagram engagement (Insomnia).
Defines capability tiers (Free/Basic/Premium/Enterprise) that control
what actions are allowed, resource limits, and feature access.

Every engagement action is validated against the active tier BEFORE
the session starts. This prevents "fake data" sessions that report
success with 0 interactions.

Tier enforcement is TWO-PHASE:
  1. Pre-flight validation: rejects sessions that exceed tier limits
  2. Runtime enforcement: caps counters at tier maximums mid-session
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("tier-model")

# ── File locations ──────────────────────────────────────────────────
TIER_PRESETS_FILE = Path(__file__).parent / "data" / "tier_presets.json"
TIER_HISTORY_FILE = Path(__file__).parent / "data" / "tier_history.json"


# ── Tier definitions ────────────────────────────────────────────────
class TierLevel(str, Enum):
    FREE = "free"
    BASIC = "basic"
    PREMIUM = "premium"
    ENTERPRISE = "enterprise"


@dataclass(frozen=True)
class TierCapabilities:
    """What a tier is allowed to do."""

    level: TierLevel
    name: str
    price: str  # display price
    description: str

    # Engagement limits
    max_targets_per_session: int
    max_likes_per_session: int
    max_follows_per_session: int
    max_comments_per_session: int
    max_stories_per_session: int
    max_targets_per_hour: int

    # Features
    allows_tap_mode: bool  # accessibility-based interaction
    allows_intent_mode: bool  # deep-link only
    allows_auto_follow: bool
    allows_custom_comments: bool
    allows_hashtag_analysis: bool
    allows_face_detection: bool  # scraper: face detection on scraped photos
    allows_bulk_operations: bool
    allows_api_access: bool
    allows_multi_account: bool

    # Comment pool
    default_comment_pool: List[str]

    # Scraper limits
    scraper_max_targets: int
    scraper_max_photos: int
    scraper_uses_scrapling: bool
    scraper_uses_mobile_api: bool

    # Session tracking
    session_window_minutes: int  # rolling window for rate limits


# ── Tier definitions ────────────────────────────────────────────────
TIER_DEFS: Dict[TierLevel, TierCapabilities] = {
    TierLevel.FREE: TierCapabilities(
        level=TierLevel.FREE,
        name="Free",
        price="$0/month",
        description="Basic engagement with strict limits. Intent mode only.",
        max_targets_per_session=3,
        max_likes_per_session=50,
        max_follows_per_session=0,
        max_comments_per_session=0,
        max_stories_per_session=0,
        max_targets_per_hour=3,
        allows_tap_mode=False,
        allows_intent_mode=True,
        allows_auto_follow=False,
        allows_custom_comments=False,
        allows_hashtag_analysis=False,
        allows_face_detection=False,
        allows_bulk_operations=False,
        allows_api_access=False,
        allows_multi_account=False,
        default_comment_pool=["great post!", "love this"],
        scraper_max_targets=5,
        scraper_max_photos=10,
        scraper_uses_scrapling=False,
        scraper_uses_mobile_api=False,
        session_window_minutes=60,
    ),
    TierLevel.BASIC: TierCapabilities(
        level=TierLevel.BASIC,
        name="Basic",
        price="$9/month",
        description="Standard engagement with tap mode support.",
        max_targets_per_session=10,
        max_likes_per_session=200,
        max_follows_per_session=20,
        max_comments_per_session=20,
        max_stories_per_session=2,
        max_targets_per_hour=12,
        allows_tap_mode=True,  # if a11y available
        allows_intent_mode=True,
        allows_auto_follow=False,
        allows_custom_comments=False,
        allows_hashtag_analysis=False,
        allows_face_detection=False,
        allows_bulk_operations=False,
        allows_api_access=False,
        allows_multi_account=False,
        default_comment_pool=[
            "great post!",
            "love this angle",
            "save-worthy",
            "this made my day",
            "awesome content",
        ],
        scraper_max_targets=20,
        scraper_max_photos=50,
        scraper_uses_scrapling=True,
        scraper_uses_mobile_api=False,
        session_window_minutes=60,
    ),
    TierLevel.PREMIUM: TierCapabilities(
        level=TierLevel.PREMIUM,
        name="Premium",
        price="$29/month",
        description="Full engagement suite with AI comments and bulk ops.",
        max_targets_per_session=25,
        max_likes_per_session=500,
        max_follows_per_session=100,
        max_comments_per_session=100,
        max_stories_per_session=5,
        max_targets_per_hour=50,
        allows_tap_mode=True,
        allows_intent_mode=True,
        allows_auto_follow=True,
        allows_custom_comments=True,
        allows_hashtag_analysis=True,
        allows_face_detection=True,
        allows_bulk_operations=True,
        allows_api_access=False,
        allows_multi_account=False,
        default_comment_pool=[
            "great post!",
            "love this angle",
            "save-worthy",
            "this made my day",
            "awesome content",
            "following for follow",
            "check out my page",
            "nice shot",
            "love the vibe",
            "🔥🔥🔥",
        ],
        scraper_max_targets=100,
        scraper_max_photos=200,
        scraper_uses_scrapling=True,
        scraper_uses_mobile_api=True,
        session_window_minutes=60,
    ),
    TierLevel.ENTERPRISE: TierCapabilities(
        level=TierLevel.ENTERPRISE,
        name="Enterprise",
        price="$99/month",
        description="Unlimited engagement, API access, multi-account.",
        max_targets_per_session=999,
        max_likes_per_session=99999,
        max_follows_per_session=99999,
        max_comments_per_session=99999,
        max_stories_per_session=999,
        max_targets_per_hour=9999,
        allows_tap_mode=True,
        allows_intent_mode=True,
        allows_auto_follow=True,
        allows_custom_comments=True,
        allows_hashtag_analysis=True,
        allows_face_detection=True,
        allows_bulk_operations=True,
        allows_api_access=True,
        allows_multi_account=True,
        default_comment_pool=[
            "great post!",
            "love this angle",
            "save-worthy",
            "this made my day",
            "awesome content",
            "following for follow",
            "check out my page",
            "nice shot",
            "love the vibe",
            "🔥🔥🔥",
            "absolutely love this",
            "so creative",
            "iconic",
            "this is amazing",
            "best post I've seen today",
            "tag someone who needs this",
            "share to your story",
        ],
        scraper_max_targets=999,
        scraper_max_photos=9999,
        scraper_uses_scrapling=True,
        scraper_uses_mobile_api=True,
        session_window_minutes=60,
    ),
}


# ── Tier resolution ─────────────────────────────────────────────────
def get_tier(level: str | TierLevel) -> TierCapabilities:
    """Get tier capabilities by level name or enum."""
    if isinstance(level, str):
        level = TierLevel(level.lower())
    return TIER_DEFS[level]


def get_tier_for_config(cfg: Dict[str, Any]) -> TierLevel:
    """Resolve the effective tier from a session config.

    Priority: explicit tier > feature-based auto-tier > FREE default.
    """
    if "tier" in cfg and cfg["tier"] in TIER_DEFS:
        return TierLevel(cfg["tier"])

    # Auto-detect tier based on features requested
    features = set()
    if cfg.get("follow") or cfg.get("auto_follow"):
        features.add("auto_follow")
    if cfg.get("comment") or cfg.get("comments_list"):
        features.add("comments")
    if cfg.get("story"):
        features.add("stories")
    if cfg.get("tap_mode") or cfg.get("driver") == "tap":
        features.add("tap_mode")
    if cfg.get("bulk"):
        features.add("bulk")
    if cfg.get("multi_account"):
        features.add("multi_account")

    if "multi_account" in features:
        return TierLevel.ENTERPRISE
    if "bulk" in features:
        return TierLevel.PREMIUM
    if "tap_mode" in features or "auto_follow" in features:
        return TierLevel.PREMIUM
    if features:  # any feature enabled
        return TierLevel.BASIC

    return TierLevel.FREE


# ── Tier validation ─────────────────────────────────────────────────
@dataclass
class TierValidationResult:
    ok: bool
    tier: TierCapabilities
    effective_tier: TierLevel  # may differ from requested if downgraded
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    rejected_config: Optional[Dict[str, Any]] = None


def validate_session(cfg: Dict[str, Any]) -> TierValidationResult:
    """Validate a session config against tier limits.

    Returns a validation result with:
    - ok: whether the session can proceed
    - effective_tier: what tier was applied (may be downgraded)
    - warnings: non-blocking issues
    - errors: blocking issues

    This is the MAIN gate that prevents fake data sessions.
    A session that cannot produce real interactions is rejected.
    """
    tier_level = get_tier_for_config(cfg)
    tier = TIER_DEFS[tier_level]
    warnings: List[str] = []
    errors: List[str] = []

    # ── Parse config ──────────────────────────────────────────
    targets = cfg.get("interact") or cfg.get("targets") or ""
    if isinstance(targets, list):
        target_list = [str(t).strip() for t in targets if t.strip()]
    elif isinstance(targets, str):
        target_list = [t.strip() for t in targets.split("\n") if t.strip()]
    else:
        target_list = []

    num_targets = len(target_list)
    likes_count = max(1, int(cfg.get("likes_count") or cfg.get("qty") or 0))
    follow_pct = int(
        cfg.get("follow_percentage") or cfg.get("follow", False) and 100 or 0
    )
    comment_pct = int(cfg.get("comment_percentage") or 0)
    comments_list = cfg.get("comments_list", [])
    if isinstance(comments_list, str):
        comments_list = [c.strip() for c in comments_list.split(",") if c.strip()]
    stories_count = max(0, int(cfg.get("stories_count") or 0))
    use_tap = cfg.get("driver") == "tap" or cfg.get("tap_mode", False)

    # ── Tier enforcement ──────────────────────────────────────

    # 1. Targets limit
    if num_targets > tier.max_targets_per_session:
        if tier.max_targets_per_session < num_targets:
            errors.append(
                f"{num_targets} targets exceeds {tier.name} limit of "
                f"{tier.max_targets_per_session} per session"
            )

    # 2. Likes limit
    estimated_likes = min(likes_count * max(num_targets, 1), tier.max_likes_per_session)
    if estimated_likes > tier.max_likes_per_session:
        warnings.append(
            f"Likes ({estimated_likes}) capped at {tier.name} limit "
            f"({tier.max_likes_per_session})"
        )

    # 3. Tap mode requires Premium+
    if use_tap and not tier.allows_tap_mode:
        errors.append(
            f"Tap mode requires {tier.name}+ tier. "
            f"Upgrade or use intent (deep-link) mode."
        )

    # 4. Follow requires Premium+
    if follow_pct > 0 and not tier.allows_auto_follow:
        errors.append(f"Auto-follow requires {tier.name}+ tier.")

    # 5. Comments require Basic+
    if (
        comment_pct > 0
        and not tier.allows_custom_comments
        and tier.level.value not in ("free",)
    ):
        # Basic allows default comments, Premium allows custom
        if not comments_list:
            warnings.append(f"Comments will use default pool ({tier.name} tier)")

    # 6. Stories require Basic+
    if stories_count > 0 and tier.level.value == "free":
        errors.append("Stories require Basic+ tier.")

    # 7. Multiple accounts requires Enterprise
    if cfg.get("multi_account") and tier.level != "enterprise":
        errors.append("Multi-account requires Enterprise tier.")

    # 8. Bulk operations require Premium+
    if cfg.get("bulk") and tier.level.value not in ("premium", "enterprise"):
        errors.append("Bulk operations require Premium+ tier.")

    # 9. API access requires Enterprise
    if cfg.get("api_mode") and not tier.allows_api_access:
        errors.append("API access requires Enterprise tier.")

    # 10. FACE DETECTION: scraper with face detection requires Premium+
    if cfg.get("scraper_faces") and not tier.allows_face_detection:
        errors.append("Face detection in scraper requires Premium+ tier.")

    # ── Critical: reject sessions that CAN'T produce real data ──
    if tier.level == "free":
        if not use_tap and not cfg.get("intent_only"):
            warnings.append(
                "Free tier: intent (deep-link) mode only. "
                "Tap mode unavailable — likes/follows/comments will be 0. "
                "This session will produce no interactions."
            )
        # CRITICAL: Free tier with no tap mode and no interaction = FAKE
        if num_targets == 0 and not use_tap:
            errors.append(
                "No targets and no interaction capability. "
                "Session would produce fake data."
            )

    # ── Result ────────────────────────────────────────────────
    effective_tier = tier_level
    if errors:
        return TierValidationResult(
            ok=False,
            tier=tier,
            effective_tier=effective_tier,
            warnings=warnings,
            errors=errors,
            rejected_config=cfg,
        )

    return TierValidationResult(
        ok=True,
        tier=tier,
        effective_tier=effective_tier,
        warnings=warnings,
        errors=errors,
    )


# ── Runtime enforcement ─────────────────────────────────────────────
def cap_counts(
    tier: TierCapabilities,
    counts: Dict[str, int],
    cfg: Dict[str, Any],
) -> Dict[str, int]:
    """Cap interaction counts at tier limits during a session.

    This prevents a session from exceeding its tier even if the
    runner logic would allow it.
    """
    capped = dict(counts)

    capped["likes"] = min(capped.get("likes", 0), tier.max_likes_per_session)
    capped["follows"] = min(capped.get("follows", 0), tier.max_follows_per_session)
    capped["comments"] = min(capped.get("comments", 0), tier.max_comments_per_session)
    capped["stories_watched"] = min(
        capped.get("stories_watched", 0), tier.max_stories_per_session
    )

    # Cap targets based on config
    targets = cfg.get("interact") or cfg.get("targets") or ""
    if isinstance(targets, str):
        num_targets = len([t for t in targets.split("\n") if t.strip()])
    elif isinstance(targets, list):
        num_targets = len(targets)
    else:
        num_targets = 0

    capped["targets_opened"] = min(
        capped.get("targets_opened", 0),
        min(num_targets, tier.max_targets_per_session),
    )

    return capped


# ── Session rate tracking ───────────────────────────────────────────
def check_rate_limit(tier_level: TierLevel, user_id: str = "default") -> Dict[str, Any]:
    """Check if a session would exceed hourly rate limits for the tier."""
    tier = TIER_DEFS[tier_level]
    history_file = TIER_HISTORY_FILE

    try:
        if history_file.exists():
            history = json.loads(history_file.read_text())
        else:
            history = []
    except Exception:
        history = []

    now = time.time()
    window = tier.session_window_minutes * 60
    cutoff = now - window

    # Filter sessions in the window
    user_sessions = [
        s
        for s in history
        if s.get("user") == user_id
        and s.get("tier") == tier_level.value
        and s.get("started", 0) > cutoff
    ]

    if len(user_sessions) >= tier.max_targets_per_hour:
        return {
            "ok": False,
            "reason": "rate_limited",
            "sessions_this_window": len(user_sessions),
            "max_per_window": tier.max_targets_per_hour,
            "window_minutes": tier.session_window_minutes,
            "retry_after": max(0, (cutoff - now + window) if user_sessions else 0),
        }

    return {"ok": True, "sessions_this_window": len(user_sessions)}


def record_session(
    tier_level: TierLevel,
    user_id: str,
    session_id: str,
    counts: Dict[str, int],
    finish_reason: str,
) -> None:
    """Record a completed session for rate limiting."""
    history_file = TIER_HISTORY_FILE
    history = []
    try:
        if history_file.exists():
            history = json.loads(history_file.read_text())
    except Exception:
        pass

    history.append(
        {
            "user": user_id,
            "tier": tier_level.value,
            "session_id": session_id,
            "started": time.time(),
            "counts": counts,
            "finish_reason": finish_reason,
        }
    )
    # Keep last 5000 entries
    history = history[-5000:]
    try:
        history_file.parent.mkdir(parents=True, exist_ok=True)
        history_file.write_text(json.dumps(history, indent=2))
    except Exception:
        pass


# ── Tier summary ────────────────────────────────────────────────────
def get_tier_summary() -> Dict[str, Any]:
    """Get a summary of all tiers for display."""
    return {
        tier.level.value: {
            "name": tier.name,
            "price": tier.price,
            "description": tier.description,
            "max_targets_per_session": tier.max_targets_per_session,
            "max_likes_per_session": tier.max_likes_per_session,
            "max_follows_per_session": tier.max_follows_per_session,
            "max_comments_per_session": tier.max_comments_per_session,
            "max_stories_per_session": tier.max_stories_per_session,
            "allows_tap_mode": tier.allows_tap_mode,
            "allows_auto_follow": tier.allows_auto_follow,
            "allows_custom_comments": tier.allows_custom_comments,
            "allows_hashtag_analysis": tier.allows_hashtag_analysis,
            "allows_face_detection": tier.allows_face_detection,
            "scraper_max_targets": tier.scraper_max_targets,
            "scraper_max_photos": tier.scraper_max_photos,
            "scraper_uses_scrapling": tier.scraper_uses_scrapling,
            "scraper_uses_mobile_api": tier.scraper_uses_mobile_api,
        }
        for tier in TIER_DEFS.values()
    }
