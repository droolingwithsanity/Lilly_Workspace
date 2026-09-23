#!/usr/bin/env python3
"""instagram_memory.py — cross-platform (Instagram <-> web) memory bridge.

Keeps a shared log of Instagram interactions (DMs and comments) so the 9 hive
characters can be aware of what a person did on Instagram when they later talk
to any character on the web. A signed-in user explicitly links their Instagram
handle (stored per Auth0 user id under their private user dir), and their linked
handle also gets added to the scraper targets so public data is collected.

Public API:
    log_interaction(avatar, username, kind, direction, text, thread_id="")
    get_interactions(username, avatars=None, limit=40)
    build_context(handle, avatars=None, limit=30) -> str
    load_link(user_id) -> str
    save_link(user_id, handle) -> str
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("instagram_memory")

MAX_ITEMS = 8000
_lock = threading.Lock()


def _data_dir() -> Path:
    import lilly_pup_insta as lpi

    return Path(lpi.DATA_DIR)


def _interactions_file() -> Path:
    return _data_dir() / "instagram_interactions.json"


def _load() -> Dict[str, Any]:
    path = _interactions_file()
    if path.exists():
        try:
            data = json.loads(path.read_text())
            if isinstance(data, dict) and isinstance(data.get("items"), list):
                return data
        except Exception as e:
            logger.debug(f"interactions load failed: {e}")
    return {"items": []}


def _save(data: Dict[str, Any]) -> None:
    try:
        _interactions_file().write_text(json.dumps(data, default=str))
    except Exception as e:
        logger.debug(f"interactions save failed: {e}")


def _clean(username: str) -> str:
    return (username or "").strip().lstrip("@").strip()


def log_interaction(
    avatar: str,
    username: str,
    kind: str,
    direction: str,
    text: str,
    thread_id: str = "",
) -> bool:
    """Record one Instagram interaction. direction: 'in' (person -> avatar) or
    'out' (avatar -> person). Cheap, locked, capped."""
    username = _clean(username)
    text = (text or "").strip()
    if not username or not text:
        return False
    if direction not in ("in", "out"):
        direction = "in"
    try:
        with _lock:
            data = _load()
            data["items"].append(
                {
                    "avatar": avatar,
                    "username": username,
                    "kind": kind,
                    "direction": direction,
                    "text": text[:1200],
                    "thread_id": thread_id,
                    "ts": time.time(),
                }
            )
            if len(data["items"]) > MAX_ITEMS:
                data["items"] = data["items"][-MAX_ITEMS:]
            _save(data)
        return True
    except Exception as e:
        logger.debug(f"log_interaction failed: {e}")
        return False


def get_interactions(
    username: str, avatars: Optional[List[str]] = None, limit: int = 40
) -> List[Dict]:
    username = _clean(username).lower()
    if not username:
        return []
    items = _load().get("items", [])
    out = [
        i
        for i in items
        if str(i.get("username", "")).lower() == username
        and (not avatars or i.get("avatar") in avatars)
    ]
    return out[-limit:]


def _scraper_results(username: str, limit: int = 8) -> List[Dict]:
    try:
        path = _data_dir() / "scraper" / "results.json"
        if not path.exists():
            return []
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            data = data.get("results", [])
        u = _clean(username).lower()
        return [r for r in data if str(r.get("username", "")).lower() == u][:limit]
    except Exception:
        return []


def build_context(
    handle: str, avatars: Optional[List[str]] = None, limit: int = 30
) -> str:
    """Return a compact, prompt-ready block of what we know about this handle
    from Instagram. Empty string when there's nothing yet."""
    handle = _clean(handle)
    if not handle:
        return ""
    lines: List[str] = []

    inter = get_interactions(handle, avatars, limit)
    if inter:
        lines.append(f"Instagram interactions with @{handle} (the person you are talking to):")
        for i in inter[-15:]:
            who = "they posted" if i.get("direction") == "in" else f"{i.get('avatar')} replied"
            kind = i.get("kind", "interaction")
            lines.append(f"- [{kind}] {who}: {str(i.get('text', ''))[:160]}")

    pub = _scraper_results(handle)
    if pub:
        lines.append(f"\nPublic Instagram data scraped about @{handle}:")
        for r in pub:
            cap = (r.get("caption") or "").strip().replace("\n", " ")[:160]
            if cap:
                lines.append(f"- {cap}")

    if not lines:
        return ""
    lines.append(
        "\nUse this Instagram history naturally — as things you already know about "
        "them. Never announce that you looked it up."
    )
    return "\n".join(lines)


# ── per-user Instagram handle link (stored in their private user dir) ──


def _link_path(user_id: str) -> Path:
    from auth0_auth import get_user_data_dir

    return get_user_data_dir(user_id) / "instagram_link.json"


def load_link(user_id: str) -> str:
    if not user_id:
        return ""
    try:
        path = _link_path(user_id)
        if path.exists():
            return _clean((json.loads(path.read_text()) or {}).get("handle", ""))
    except Exception:
        pass
    return ""


def save_link(user_id: str, handle: str) -> str:
    handle = _clean(handle)
    path = _link_path(user_id)
    path.write_text(json.dumps({"handle": handle, "linked_at": time.time()}))
    # Also add the handle to the scraper targets so we collect public data.
    if handle:
        try:
            from instagram_scraper import get_scraper

            get_scraper().record_interaction(handle, "linked")
        except Exception as e:
            logger.debug(f"link -> scraper target failed: {e}")
    return handle


def find_user_by_handle(handle: str) -> str:
    """Reverse lookup: find user_id by Instagram handle."""
    handle = _clean(handle).lower()
    if not handle:
        return ""
    users_dir = _data_dir() / "users"
    if not users_dir.exists():
        return ""
    for user_dir in users_dir.iterdir():
        if not user_dir.is_dir():
            continue
        link_path = user_dir / "instagram_link.json"
        if not link_path.exists():
            continue
        try:
            data = json.loads(link_path.read_text())
            if _clean(data.get("handle", "")).lower() == handle:
                return user_dir.name
        except Exception:
            continue
    return ""
