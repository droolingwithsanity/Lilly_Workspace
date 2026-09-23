#!/usr/bin/env python3
"""
trending.py — Compute trending topics from engagement data.

Sources:
  1. Automation session logs (what targets were engaged with)
  2. Followed accounts' interests (friends' interests)
  3. Seasonal patterns (current time-based trends)
  4. Scraped results (if available, for enrichment)

Output: Trending topics with strength, source, category.
"""

import json
import os
import time
import logging
import re
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from collections import Counter

logger = logging.getLogger("trending")

WORKSPACE = Path(os.environ.get("LILLY_WORKSPACE", str(Path(__file__).parent.parent)))
DATA_DIR = WORKSPACE / "data" / "training" / "trending"
TRENDING_FILE = DATA_DIR / "trending.json"
ENGAGEMENT_FILE = DATA_DIR / "engagement.json"
SEASONAL_FILE = DATA_DIR / "seasonal.json"

DATA_DIR.mkdir(parents=True, exist_ok=True)

# Seasonal keywords mapped to months/seasons
SEASONAL_MAP = {
    "winter": {
        "months": [12, 1, 2],
        "keywords": [
            "skiing",
            "snow",
            "holiday",
            "christmas",
            "new year",
            "cozy",
            "winter",
            "cold",
            "ice",
            "fireplace",
            "hot cocoa",
            "blanket",
        ],
    },
    "spring": {
        "months": [3, 4, 5],
        "keywords": [
            "garden",
            "flowers",
            "outdoor",
            "picnic",
            "walking",
            "running",
            "spring",
            "rain",
            "fresh",
            "nature",
            "green",
            "bloom",
        ],
    },
    "summer": {
        "months": [6, 7, 8],
        "keywords": [
            "beach",
            "surf",
            "sun",
            "vacation",
            "travel",
            "pool",
            " BBQ",
            "grilling",
            "summer",
            "heat",
            "ice cream",
            "outdoor",
        ],
    },
    "fall": {
        "months": [9, 10, 11],
        "keywords": [
            "pumpkin",
            "halloween",
            "autumn",
            "fall",
            "leaves",
            "harvest",
            "sweater",
            "coffee",
            "back to school",
            "football",
            "apple",
        ],
    },
}

GENERIC_INTERESTS = [
    "AI",
    "technology",
    "fitness",
    "health",
    "food",
    "travel",
    "music",
    "movies",
    "gaming",
    "fashion",
    "business",
    "finance",
    "crypto",
    "photography",
    "art",
    "design",
    "science",
    "education",
    "spirituality",
    "parenting",
    "pets",
    "sports",
    "cooking",
    "reading",
    "climate",
]


class TrendingTopic:
    def __init__(
        self,
        topic: str,
        source: str = "self",
        category: str = "interest",
        strength: float = 0.5,
    ):
        self.topic = topic
        self.source = source  # "self", "friends", "seasonal", "scraped"
        self.category = category  # "interest", "seasonal", "trending", "social"
        self.strength = strength  # 0.0 - 1.0
        self.count = 1
        self.last_seen = time.time()

    def to_dict(self) -> dict:
        return {
            "topic": self.topic,
            "source": self.source,
            "category": self.category,
            "strength": round(self.strength, 2),
            "count": self.count,
            "last_seen": self.last_seen,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TrendingTopic":
        t = cls(
            topic=d["topic"],
            source=d.get("source", "self"),
            category=d.get("category", "interest"),
            strength=d.get("strength", 0.5),
        )
        t.count = d.get("count", 1)
        t.last_seen = d.get("last_seen", time.time())
        return t


def compute_from_engagement(engagement: dict) -> List[TrendingTopic]:
    """Compute trending topics from engagement session data.

    Engagement dict format:
    {
        "sessions": [
            {
                "ig_account": "@handle",
                "targets": ["@user1", "#tech", "https://..."],
                "likes": 15, "follows": 3, "comments": 2,
                "targets_opened": 10,
            },
        ],
    }
    """
    topics: Dict[str, TrendingTopic] = {}

    def _add_topic(topic: str, source: str, category: str, strength: float):
        if not topic or len(topic.strip()) < 2:
            return
        topic = topic.strip().lower()
        # Skip generic usernames that are not topics
        if topic.startswith("@") and len(topic) < 4:
            return
        if topic.startswith("#"):
            topic = topic[1:]
        if topic in ("ai", "tech"):  # too generic unless repeated
            pass  # still add, just with lower strength

        if topic in topics:
            t = topics[topic]
            t.count += 1
            t.strength = min(1.0, t.strength + strength)
            t.last_seen = time.time()
        else:
            topics[topic] = TrendingTopic(
                topic=topic,
                source=source,
                category=category,
                strength=strength,
            )

    sessions = engagement.get("sessions", [])
    for session in sessions:
        targets = session.get("targets", [])
        likes = session.get("likes", 0)
        follows = session.get("follows", 0)
        comments = session.get("comments", 0)
        opened = session.get("targets_opened", 0)

        # Extract topics from targets
        for target in targets:
            t = target.lower().strip()
            if t.startswith("@"):
                # Following a user → their niche becomes our interest
                _add_topic(t[1:], "self", "interest", 0.6)
            elif t.startswith("#"):
                # Hashtag → direct interest
                _add_topic(t[1:], "self", "interest", 0.8)
            elif "instagram.com" in t:
                # URLs → extract last path segment
                parts = t.rstrip("/").split("/")
                if parts:
                    _add_topic(parts[-1], "self", "interest", 0.4)

        # Engagement type weighting
        if follows > 0:
            for target in targets:
                if target.startswith("@"):
                    _add_topic(target[1:], "self", "interest", 0.3)
        if comments > 0:
            for target in targets:
                _add_topic(f"commenting on {targets[0]}", "self", "interest", 0.2)

    return list(topics.values())


def compute_seasonal(now: Optional[datetime] = None) -> List[TrendingTopic]:
    """Return seasonal topics for current period."""
    if now is None:
        now = datetime.now()
    month = now.month
    topics: List[TrendingTopic] = []

    for season_name, season_data in SEASONAL_MAP.items():
        if month in season_data["months"]:
            for kw in season_data["keywords"]:
                topics.append(
                    TrendingTopic(
                        topic=kw.strip(),
                        source="seasonal",
                        category="seasonal",
                        strength=0.7,
                    )
                )
            break

    # Add current month as topic
    month_name = now.strftime("%B")
    topics.append(
        TrendingTopic(
            topic=f"{month_name} vibes",
            source="seasonal",
            category="seasonal",
            strength=0.5,
        )
    )

    # Add generic interests with low strength for variety
    for interest in GENERIC_INTERESTS[:5]:
        topics.append(
            TrendingTopic(
                topic=interest.lower(),
                source="seasonal",
                category="seasonal",
                strength=0.2,
            )
        )

    return topics


def compute_from_friends(engagement: dict) -> List[TrendingTopic]:
    """Compute friends' interests from followed accounts' activity.

    Friends = accounts you've engaged with (liked/followed/commented on).
    Their interests are inferred from their @username hints and shared hashtags.
    """
    friend_topics: Counter = Counter()
    sessions = engagement.get("sessions", [])

    for session in sessions:
        targets = session.get("targets", [])
        likes = session.get("likes", 0)
        follows = session.get("follows", 0)

        for target in targets:
            t = target.lower().strip()
            if t.startswith("#"):
                # Hashtags → friends share these interests
                friend_topics[t[1:]] += 2  # weight hashtags higher
            elif t.startswith("@") and follows > 0:
                # Following someone → their circle's interests count
                friend_topics[f"following {t[1:]}"] += 1

    topics = []
    for topic, count in friend_topics.most_common(10):
        strength = min(1.0, count * 0.15)
        topics.append(
            TrendingTopic(
                topic=topic,
                source="friends",
                category="social",
                strength=strength,
            )
        )

    return topics


def load_engagement() -> dict:
    """Load engagement data from file."""
    if ENGAGEMENT_FILE.exists():
        try:
            return json.loads(ENGAGEMENT_FILE.read_text())
        except Exception:
            pass
    return {"sessions": []}


def save_engagement(data: dict) -> None:
    """Save engagement data to file."""
    try:
        ENGAGEMENT_FILE.write_text(json.dumps(data, indent=2))
    except Exception as e:
        logger.warning(f"Failed to save engagement: {e}")


def add_session(engagement: dict, session: dict) -> dict:
    """Add a session to engagement data and recompute trending."""
    sessions = engagement.get("sessions", [])
    sessions.append(session)
    # Keep last 100 sessions max
    if len(sessions) > 100:
        sessions = sessions[-100:]
    engagement["sessions"] = sessions
    engagement["last_updated"] = time.time()
    save_engagement(engagement)
    return engagement


def compute_trending(
    engagement: Optional[dict] = None, now: Optional[datetime] = None
) -> dict:
    """Full trending computation. Returns dict with all topic lists."""
    if engagement is None:
        engagement = load_engagement()

    self_topics = compute_from_engagement(engagement)
    seasonal_topics = compute_seasonal(now)
    friend_topics = compute_from_friends(engagement)

    # Merge and deduplicate
    all_topics: Dict[str, TrendingTopic] = {}
    for t in self_topics + seasonal_topics + friend_topics:
        key = f"{t.topic}:{t.source}"
        if key in all_topics:
            existing = all_topics[key]
            existing.count += t.count
            existing.strength = min(1.0, max(existing.strength, t.strength))
        else:
            all_topics[key] = t

    # Sort by strength descending
    sorted_topics = sorted(all_topics.values(), key=lambda t: t.strength, reverse=True)

    return {
        "topics": [t.to_dict() for t in sorted_topics],
        "self_count": len(self_topics),
        "seasonal_count": len(seasonal_topics),
        "friend_count": len(friend_topics),
        "total": len(sorted_topics),
        "computed_at": time.time(),
        "engagement_sessions": len(engagement.get("sessions", [])),
    }


def export_as_targets(engagement: dict, top_n: int = 20) -> dict:
    """Export engagement-derived topics as scraper-style targets.

    This lets users skip manual target entry: their interactions
    become training/scraping targets automatically.

    Returns:
    {
        "targets": ["@user1", "#tech", ...],
        "from_engagement": True,
        "session_count": N,
        "exported_at": timestamp,
    }
    """
    trending = compute_trending(engagement)
    targets = []
    for t in trending["topics"]:
        if t["source"] == "self" and t["strength"] >= 0.3:
            if t["category"] == "interest":
                # Derive target format from topic
                if " " not in t["topic"] and len(t["topic"]) > 1:
                    targets.append(f"@{t['topic']}")
                else:
                    targets.append(f"#{t['topic'].replace(' ', '')}")

    targets = targets[:top_n]
    return {
        "targets": targets,
        "from_engagement": True,
        "session_count": trending["engagement_sessions"],
        "exported_at": time.time(),
        "note": "Auto-generated from your engagement history. Use these as scraper targets to find similar content.",
    }
