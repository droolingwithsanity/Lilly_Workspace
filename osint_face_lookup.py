#!/usr/bin/env python3
"""
Lilly OSINT Face Lookup — the "scrapidy" tier
──────────────────────────────────────────────
When the vision server sees a face that is NOT in the enrolled FAISS
database, this module tries to identify them and surface social-media
accounts using the OSINT stack that already ships with Lilly:

  1. Reverse-face search   — uploads the crop to a photo-search provider
                             (Yandex image view by default; opt-in).
  2. Identity → accounts   — runs osint_agents.run_osint_agent("person", …)
                             and scrapling_engine.scrape_for_lilly on the
                             candidate name to collect social profiles.

Runs ONLY inside lilly-ai. Fully opt-in:

    FACE_OSINT_ENABLED=1        # gate (default off)
    FACE_OSINT_PROVIDER=yandex  # "yandex" | "test"
    FACE_OSINT_COOLDOWN=60      # seconds between attempts for the same face
    VISION_SERVER_URL=...       # where to push the identity result back

Privacy + rate-limit notes:
  - External photo-search uploads are legally grey and we make NO identity
    promise from them. The tier is off by default, one attempt per face per
    cooldown, and never blocks the live vision loop.
"""

import asyncio
import base64
import json
import logging
import os
import re
import time
import urllib.parse
from pathlib import Path

logger = logging.getLogger("lilly-osint")

FACE_OSINT_ENABLED = os.environ.get("FACE_OSINT_ENABLED", "0") == "1"
FACE_OSINT_PROVIDER = os.environ.get("FACE_OSINT_PROVIDER", "yandex").lower()
FACE_OSINT_COOLDOWN = float(os.environ.get("FACE_OSINT_COOLDOWN", "60"))
FACE_OSINT_CACHE_DIR = Path(os.environ.get("FACE_OSINT_CACHE_DIR", "/app/data/osint"))
FACE_OSINT_CACHE = FACE_OSINT_CACHE_DIR / "face_lookup_cache.json"
VISION_SERVER_URL = os.environ.get("VISION_SERVER_URL", "").rstrip("/")

_YANDEX_ENDPOINT = "https://yandex.com/images-apphost/image-details"
_YANDEX_URI = (
    "?cbird=111&rpt=imageview&format=json"
    "&request="
    + urllib.parse.quote(
        '{"blocks":[{"block":"b-identical-pages"}]}'
    )
)
_GUESS_CONF = 0.35  # reverse-face hits are a lead, not a verdict


def _load_cache() -> dict:
    try:
        return json.loads(FACE_OSINT_CACHE.read_text())
    except Exception:
        return {}


def _save_cache(cache: dict):
    try:
        FACE_OSINT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        FACE_OSINT_CACHE.write_text(json.dumps(cache, indent=2))
    except Exception as e:
        logger.debug(f"osint cache save failed: {e}")


async def handle_unknown_face(payload: dict) -> dict:
    """Entry point (called by lilly-ai). Returns {"handled": …}."""
    if not FACE_OSINT_ENABLED:
        return {"handled": False}
    if not FACE_OSINT_CACHE_DIR:
        pass

    res_id = payload.get("id") or "uf_unknown"
    now = time.time()
    cache = _load_cache()

    # Cooldown + short-circuit repeat work for the same face identity.
    prev = cache.get(res_id)
    if prev and now - prev.get("ts", 0) < FACE_OSINT_COOLDOWN:
        return {"handled": True, "cached": True}

    try:
        crop_bytes = base64.b64decode(payload.get("crop_b64") or "")
    except Exception:
        crop_bytes = b""
    if not crop_bytes:
        cache[res_id] = {"ts": now, "name": None, "error": "no crop"}
        _save_cache(cache)
        return {"handled": True, "cached": False}

    # Kick off the OSINT work in the background; do NOT block the caller.
    asyncio.get_running_loop().create_task(_lookup_and_push(res_id, payload, crop_bytes))
    return {"handled": True, "cached": False}


async def _lookup_and_push(res_id: str, payload: dict, crop_bytes: bytes):
    result = {"name": None, "confidence": 0.0, "social_accounts": [], "sources": [], "error": None}
    try:
        candidates = await _reverse_face_search(crop_bytes)
        if candidates:
            best = candidates[0]
            result = await _discover_accounts(best["name"], candidates)
    except Exception as e:
        logger.warning(f"OSINT lookup failed for {res_id}: {e}")
        result["error"] = str(e)

    cache = _load_cache()
    cache[res_id] = {
        "ts": time.time(),
        "name": result.get("name"),
        "confidence": result.get("confidence"),
        "social_accounts": result.get("social_accounts", []),
        "sources": result.get("sources", []),
        "error": result.get("error"),
    }
    _save_cache(cache)

    if not VISION_SERVER_URL:
        return
    import httpx

    try:
        await httpx.AsyncClient(timeout=20.0).post(
            f"{VISION_SERVER_URL}/api/vision/face/identify",
            json={
                "id": res_id,
                "name": result.get("name"),
                "confidence": result.get("confidence"),
                "social_accounts": result.get("social_accounts", []),
                "sources": result.get("sources", []),
            },
        )
        logger.info(f"OSINT identity pushed for {res_id}: {result.get('name')}")
    except Exception as e:
        logger.debug(f"OSINT result push failed: {e}")


# ── Step 1: reverse face search ─────────────────────────────────────────
async def _reverse_face_search(crop_bytes: bytes) -> list[dict]:
    """Return [{name, href, confidence}] candidates for a face crop."""
    if FACE_OSINT_PROVIDER == "test":
        return [{"name": "Test Person", "href": "https://example.com", "confidence": _GUESS_CONF}]
    return await _yandex_reverse_search(crop_bytes)


async def _yandex_reverse_search(crop_bytes: bytes) -> list[dict]:
    import httpx

    async def attempt(url: str, *, use_file: bool) -> list[dict]:
        async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
            if use_file:
                r = await client.post(
                    url,
                    files={"file": ("crop.jpg", crop_bytes, "image/jpeg")},
                    headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"},
                )
            else:
                r = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return []
        try:
            data = r.json()
        except Exception:
            return []
        return _parse_yandex_matches(data)

    # Upload flow first; fall back to the URL variant if the block missing.
    matches = await attempt(_YANDEX_ENDPOINT + _YANDEX_URI, use_file=True)
    return matches[:6]


def _parse_yandex_matches(data) -> list[dict]:
    """Defensively walk the Yandex imageview JSON for person-page hits."""
    out: list[dict] = []
    seen = set()
    marker = re.compile(r"^(https?://|@|[\w-]+\.\w{2,})", re.I)
    evidence_terms = re.compile(
        r"person|people|profile|facebook|instagram|linkedin|twitter|"
        r"vk\.com|ok\.ru|tiktok|youtube|reddit|bio|photo of", re.I
    )

    def walk(node):
        if isinstance(node, dict):
            node = list(node)
        if isinstance(node, list):
            for child in node:
                walk(child)
            return
        # leaf string
        if not isinstance(node, str) or not node.strip():
            return
        if len(node) > 240 or not marker.search(node):
            return
        href = node if marker.match(node) else ""
        title = node if not href else ""
        if title and evidence_terms.search(title) and title not in seen:
            seen.add(title)
            name = _guess_name_from_title(title)
            out.append({"name": name, "href": href, "confidence": _GUESS_CONF, "title": title})
        elif href and href not in seen:
            seen.add(href)
            out.append({"name": href, "href": href, "confidence": _GUESS_CONF})

    walk(data)

    # Try to read Yandex's typed matches fields too (shape varies by version).
    for block in (data.get("image-details", {}) if isinstance(data, dict) else {}).values():
        if isinstance(block, list):
            for m in block[:3]:
                if not isinstance(m, dict):
                    continue
                title = m.get("title") or m.get("name") or ""
                url = m.get("url") or m.get("page-url") or ""
                if title and title not in seen:
                    seen.add(title)
                    out.append({"name": _guess_name_from_title(title), "href": url, "confidence": _GUESS_CONF})
    return out


def _guess_name_from_title(title: str) -> str:
    """Best-effort: extract a human name from a hit title like 'John Smith — LinkedIn'."""
    t = re.sub(r"[|•·]|&nbsp;", " | ", title)
    if "@" in t:
        handle = re.search(r"@([A-Za-z0-9_.]{3,40})", t)
        if handle:
            return handle.group(1)
    cleaned = re.sub(r"\b(linkedin|facebook|instagram|twitter|x\.com|vk|tiktok|reddit|profile)\b.*$", "", t, flags=re.I)
    cleaned = re.sub(r"[^A-Za-z' -]", "", cleaned)
    parts = [p.strip().title() for p in cleaned.split() if p.strip()]
    proper = [p for p in parts if p and p[0].isupper()]
    if len(proper) >= 2:
        return " ".join(proper[:3])
    if proper:
        return proper[0]
    return t[:40]


# ── Step 2: identity → social accounts ──────────────────────────────────
async def _discover_accounts(name: str, candidates: list[dict]) -> dict:
    """Run the OSINT/scrapidy stack for the candidate name; collect accounts."""
    sources = [c.get("href") for c in candidates if c.get("href")]
    social_accounts: list[str] = []
    report = ""

    try:
        from osint_agents import run_osint_agent
        report = await asyncio.wait_for(run_osint_agent("person", name), timeout=90)
    except Exception as e:
        logger.debug(f"osint_agents skipped: {e}")

    try:
        from scrapling_engine import scrape_for_lilly
        scraped = await asyncio.wait_for(
            scrape_for_lilly(f"Find the social media profiles for {name}"),
            timeout=60,
        )
        for c in scraped.get("citations", []):
            u = c.get("url")
            if u:
                sources.append(u)
    except Exception as e:
        logger.debug(f"scrapling skipped: {e}")

    if report:
        sources.append(report[:2000])

    urls = re.findall(r"https?://[^\s)\]]+", " ".join(sources))
    for u in urls:
        if any(d in u.lower() for d in (
            "facebook.com", "instagram.com", "linkedin.com", "x.com/", "twitter.com",
            "tiktok.com", "vk.com", "ok.ru", "youtube.com/@", "reddit.com/user", "bsky.app",
        )):
            if u not in social_accounts:
                social_accounts.append(u)

    conf = max((c.get("confidence", 0) for c in candidates), default=0.0)
    return {
        "name": name,
        "confidence": round(conf, 3),
        "social_accounts": social_accounts[:8],
        "sources": sources[:10],
        "osint_report": report[:1200] if report else "",
    }