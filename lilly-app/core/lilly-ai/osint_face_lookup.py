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
# URL-flow: temporarily publish the crop through the public tunnel so Yandex
# can fetch it via ?rpt=imageview&url= (upload JSON endpoint returns empty).
FACE_OSINT_PUBLIC_BASE = os.environ.get(
    "FACE_OSINT_PUBLIC_BASE", "https://droolingwithsanity.ca"
).rstrip("/")
FACE_OSINT_TMP_DIR = Path(os.environ.get("FACE_OSINT_TMP_DIR", "/app/data/osint_tmp"))
FACE_OSINT_TMP_TTL = float(os.environ.get("FACE_OSINT_TMP_TTL", "600"))

_YANDEX_ENDPOINT = "https://yandex.com/images-apphost/image-details"
_YANDEX_URI = "?cbird=111&rpt=imageview&format=json&request=" + urllib.parse.quote(
    '{"blocks":[{"block":"b-identical-pages"}]}'
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


def get_cached_result(res_id: str) -> dict | None:
    """Return the latest reverse-search result for a face id, if present."""
    try:
        result = _load_cache().get(res_id)
        return dict(result) if isinstance(result, dict) else None
    except Exception as e:
        logger.debug(f"osint cache read failed: {e}")
        return None


# Runtime override (Admin Dashboard toggle) — None means "follow env var".
_RUNTIME_ENABLED: bool | None = None


def set_runtime_enabled(enabled: bool | None) -> None:
    """Admin-dashboard toggle: override the env-gated default at runtime."""
    global _RUNTIME_ENABLED
    _RUNTIME_ENABLED = enabled


def is_enabled() -> bool:
    """Whether autonomous unknown-face reverse search is configured."""
    if _RUNTIME_ENABLED is not None:
        return _RUNTIME_ENABLED
    return FACE_OSINT_ENABLED


async def handle_unknown_face(payload: dict) -> dict:
    """Entry point (called by lilly-ai). Returns {"handled": …}."""
    if not is_enabled():
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
    asyncio.get_running_loop().create_task(
        _lookup_and_push(res_id, payload, crop_bytes)
    )
    return {"handled": True, "cached": False}


async def _lookup_and_push(res_id: str, payload: dict, crop_bytes: bytes):
    result = {
        "name": None,
        "confidence": 0.0,
        "social_accounts": [],
        "sources": [],
        "error": None,
    }
    try:
        candidates = await _reverse_face_search(crop_bytes, res_id)
        if candidates:
            best = candidates[0]
            result = await _discover_accounts(best["name"], candidates)
    except Exception as e:
        logger.warning(f"OSINT lookup failed for {res_id}: {e}")
        result["error"] = str(e)

    images = _IMAGES_BY_ID.pop(res_id, [])
    result["images"] = images[:8]

    cache = _load_cache()
    cache[res_id] = {
        "ts": time.time(),
        "name": result.get("name"),
        "confidence": result.get("confidence"),
        "social_accounts": result.get("social_accounts", []),
        "sources": result.get("sources", []),
        "images": result.get("images", []),
        "error": result.get("error"),
    }
    _save_cache(cache)

    # Tier-2 identity event → alert + remember flow (face_identity).
    if result.get("name"):
        try:
            from face_identity import emit_identity_event

            emit_identity_event(
                result["name"],
                source="osint",
                confidence=result.get("confidence", 0),
                face_id=res_id,
                social_accounts=result.get("social_accounts", []),
                sources=result.get("sources", []),
                images=result.get("images", []),
                crop_b64=payload.get("crop_b64") or "",
            )
        except Exception as e:
            logger.debug(f"identity event failed: {e}")

        # Autonomous footprint dossier (background; notifies when ready).
        if os.environ.get("FACE_FOOTPRINT_AUTO", "1") == "1" and payload.get(
            "crop_b64"
        ):
            try:
                from footprint import start_footprint

                await start_footprint(
                    result["name"],
                    face_id=res_id,
                    crop_b64=payload.get("crop_b64") or "",
                    auto=True,
                )
            except Exception as e:
                logger.debug(f"footprint autostart failed: {e}")

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
                "images": result.get("images", []),
            },
        )
        logger.info(f"OSINT identity pushed for {res_id}: {result.get('name')}")
    except Exception as e:
        logger.debug(f"OSINT result push failed: {e}")


async def research_face(name: str, res_id: str = "") -> dict:
    """Force a fresh reverse search for a name even if known/cached.

    Uses the stored crop for a recent sighting (face_id or name match).
    Returns the lookup result dict.
    """
    from face_identity import find_event, get_crop

    ev = find_event(res_id or name)
    if not ev:
        return {"name": None, "error": f"no recent sighting of {name}"}
    fid = ev.get("face_id") or ""
    crop = get_crop(fid) if fid else None
    if not crop:
        return {"name": None, "error": "face crop expired — show them again first"}
    rid = f"research_{fid or 'x'}_{int(time.time())}"
    result = {
        "name": None,
        "confidence": 0.0,
        "social_accounts": [],
        "sources": [],
        "images": [],
        "error": None,
    }
    try:
        candidates = await _reverse_face_search(crop, rid)
        if candidates:
            # prefer the requested name if among candidates, else top hit
            want = (name or "").strip().lower()
            pick = next(
                (c for c in candidates if (c.get("name") or "").lower() == want),
                candidates[0],
            )
            result = await _discover_accounts(pick["name"], candidates)
    except Exception as e:
        result["error"] = str(e)
    result["images"] = _IMAGES_BY_ID.pop(rid, [])[:8]
    cache = _load_cache()
    cache[rid] = {
        "ts": time.time(),
        "name": result.get("name"),
        "confidence": result.get("confidence"),
        "social_accounts": result.get("social_accounts", []),
        "sources": result.get("sources", []),
        "images": result.get("images", []),
        "error": result.get("error"),
        "research_for": name,
    }
    _save_cache(cache)
    if result.get("name"):
        try:
            from face_identity import emit_identity_event

            emit_identity_event(
                result["name"],
                source="osint:research",
                confidence=result.get("confidence", 0),
                face_id=rid,
                social_accounts=result.get("social_accounts", []),
                sources=result.get("sources", []),
                images=result.get("images", []),
            )
        except Exception:
            pass
    return result


# ── Step 1: reverse face search ─────────────────────────────────────────
async def _reverse_face_search(crop_bytes: bytes, res_id: str = "") -> list[dict]:
    """Return [{name, href, confidence}] candidates for a face crop."""
    if FACE_OSINT_PROVIDER == "test":
        return [
            {
                "name": "Test Person",
                "href": "https://example.com",
                "confidence": _GUESS_CONF,
            }
        ]
    return await _yandex_reverse_search(crop_bytes, res_id=res_id)


def _publish_crop(crop_bytes: bytes, res_id: str) -> str | None:
    """Write crop to the tmp dir served at /osint_tmp/; return public URL."""
    try:
        import secrets

        FACE_OSINT_TMP_DIR.mkdir(parents=True, exist_ok=True)
        try:
            now = time.time()
            for f in FACE_OSINT_TMP_DIR.glob("*.jpg"):
                try:
                    if now - f.stat().st_mtime > FACE_OSINT_TMP_TTL:
                        f.unlink()
                except Exception:
                    pass
        except Exception:
            pass
        token = secrets.token_hex(12)
        safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", (res_id or "face")[:16])
        fname = f"{safe_id}_{token}.jpg"
        (FACE_OSINT_TMP_DIR / fname).write_bytes(crop_bytes)
        if not FACE_OSINT_PUBLIC_BASE:
            return None
        return f"{FACE_OSINT_PUBLIC_BASE}/osint_tmp/{fname}"
    except Exception as e:
        logger.debug(f"crop publish failed: {e}")
        return None


def _unpublish_crop(public_url: str | None):
    try:
        if not public_url:
            return
        fname = re.sub(r"[^A-Za-z0-9_.-]", "_", public_url.rsplit("/", 1)[-1])
        p = FACE_OSINT_TMP_DIR / fname
        if p.exists():
            p.unlink()
    except Exception:
        pass


_ADULT_TERMS = {
    "adult", "porn", "xxx", "nsfw", "explicit", "erotic", "pornography",
    "nude", "naked", "hot", "sexy", "18+", "18 plus", "adults",
    "mp4", "hd", "stream", "tube", "clips", "free",
    "pics", "gallery", "watch", "download",
}
def _is_adult_content(t: str) -> bool:
    """Check if a string contains adult/movie content."""
    lower = t.lower()
    for term in _ADULT_TERMS:
        if term.lower() in lower:
            return True
    if re.search(r"\b(xxx|hd|full|movie|mp4|stream|tube|clips)\b", lower) and len(re.findall(r"[a-z]+", lower)) >= 3:
        return True
    return False

def _parse_yandex_html(html: str) -> list[dict]:
    """Parse Yandex images/search HTML for entity tags + similar titles."""
    _STOP = {
        "privacy",
        "policy",
        "official",
        "presidential",
        "portrait",
        "media",
        "events",
        "times",
        "prompts",
        "stable",
        "diffusion",
        "yandex",
        "images",
        "search",
        "similar",
        "photo",
        "picture",
        "image",
        "pictures",
        "photos",
        "new",
        "end",
        "ht",
        # adult content filter
        "adult",
        "porn",
        "xxx",
        "nsfw",
        "explicit",
        "18+",
        "18 plus",
        "adults",
        "pornography",
        "erotic",
        "hot",
        "sexy",
        "nude",
        "naked",
        "mp4",
        "hd",
        "full",
        "movie",
        "movies",
        "film",
        "films",
        "video",
        "tube",
        "clips",
        "pics",
        "images",
        "picture",
        "photos",
        "gallery",
        "free",
        "stream",
        "watch",
        "download",
    }

    def _ok_name(t: str) -> bool:
        words = re.findall(r"[A-Za-z']+", t)
        if len(words) < 2:
            return False
        content = [w for w in words if w.lower() not in _STOP and len(w) > 1]
        if len(content) < 2:
            return False
        if _is_adult_content(t):
            return False
        return True

    out: list[dict] = []
    seen = set()
    for tag_text in re.findall(r'"text":"([^"]{2,80})"', html):
        t = tag_text.strip()
        if len(t) < 2 or t in seen or not re.search(r"[A-Za-z]", t):
            continue
        if not _ok_name(t):
            continue
        seen.add(t)
        out.append(
            {
                "name": _guess_name_from_title(t),
                "href": "",
                "confidence": _GUESS_CONF,
                "title": t,
            }
        )
    for title in re.findall(
        r'([A-Z][A-Za-z\'.-]+(?:\s+[A-Z][A-Za-z\'.-]+){1,3})[^<"]{0,60}?&quot;', html
    ):
        t = title.strip()
        if len(t) < 4 or t in seen:
            continue
        if not _ok_name(t):
            continue
        seen.add(t)
        out.append(
            {
                "name": _guess_name_from_title(t),
                "href": "",
                "confidence": _GUESS_CONF,
                "title": t,
            }
        )
    for u in re.findall(
        r"https?://[a-z0-9./_-]*(?:wikipedia\.org|whitehouse\.gov|britannica\.com|biography\.com)[a-z0-9./_?=%#-]*",
        html,
        re.I,
    ):
        if u not in seen:
            seen.add(u)
            out.append(
                {
                    "name": _guess_name_from_title(u.split("/")[-1].replace("_", " ")),
                    "href": u,
                    "confidence": _GUESS_CONF,
                }
            )
    # rank by frequency: the true entity repeats dozens of times, noise a few
    out.sort(
        key=lambda c: html.count(c.get("title") or c.get("name") or ""), reverse=True
    )
    return out[:8]


_SITE_NAMES = {
    "youtube.com": "YouTube",
    "youtu.be": "YouTube",
    "i.ytimg.com": "YouTube",
    "wikipedia.org": "Wikipedia",
    "whitehouse.gov": "White House",
    "britannica.com": "Britannica",
    "biography.com": "Biography",
    "facebook.com": "Facebook",
    "instagram.com": "Instagram",
    "linkedin.com": "LinkedIn",
    "x.com": "X",
    "twitter.com": "X",
    "tiktok.com": "TikTok",
    "vk.com": "VK",
    "ok.ru": "OK",
    "reddit.com": "Reddit",
    "bsky.app": "Bluesky",
    "github.com": "GitHub",
    "onlyfans.com": "OnlyFans",
    "fansly.com": "Fansly",
    "tinder.com": "Tinder",
    "bumble.com": "Bumble",
    "hinge.co": "Hinge",
    "okcupid.com": "OkCupid",
    "pof.com": "Plenty of Fish",
    "plentyoffish.com": "Plenty of Fish",
    "grindr.com": "Grindr",
    "feeld.co": "Feeld",
    "match.com": "Match",
    "eharmony.com": "eHarmony",
    "badoo.com": "Badoo",
    "happn.com": "happn",
    "herapp.co": "HER",
}

# Social + fan + dating domains surfaced as accounts.
_SOCIAL_DOMAINS = (
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "x.com/",
    "twitter.com",
    "tiktok.com",
    "vk.com",
    "ok.ru",
    "youtube.com/@",
    "reddit.com/user",
    "bsky.app",
    "onlyfans.com",
    "fansly.com",
    "tinder.com",
    "bumble.com",
    "hinge.co",
    "okcupid.com",
    "pof.com",
    "plentyoffish.com",
    "grindr.com",
    "feeld.co",
    "match.com",
    "eharmony.com",
    "badoo.com",
    "happn.com",
    "herapp.co",
)


def _site_name(url: str) -> str:
    try:
        host = urllib.parse.urlparse(url).netloc.lower().replace("www.", "")
        for dom, nice in _SITE_NAMES.items():
            if host == dom or host.endswith("." + dom):
                return nice
        return host.split(":")[0] or "web"
    except Exception:
        return "web"


def _parse_yandex_images(html: str, limit: int = 8) -> list[dict]:
    """Extract similar-image entries: thumbnail + title + source page image.

    Entries look like: {"imageUrl":"//avatars.mds.yandex.net/...","width":..,
    "title":"Barack Obama 2020 - YouTube","linkUrl":"/images/search?url=..&img_url=<orig>&.."}
    """
    out: list[dict] = []
    seen = set()
    # Page HTML-escapes quotes (&quot;) — normalize first.
    html = html.replace("&quot;", '"').replace("&#x27;", "'").replace("&amp;", "&")
    pat = re.compile(
        r'\{"imageUrl":"([^"]+)","width":\d+,"height":\d+,"title":"([^"]{3,120}?)"'
        r',"linkUrl":"([^"]{10,600}?)"'
    )
    for thumb, title, link in pat.findall(html):
        title = title.replace("&quot;", '"').replace("&#x27;", "'").strip()
        if len(title) < 3 or title in seen:
            continue
        m = re.search(r"img_url=([^&]+)", link)
        page_url = urllib.parse.unquote(m.group(1)) if m else ""
        if thumb.startswith("//"):
            thumb = "https:" + thumb
        seen.add(title)
        out.append(
            {
                "thumb": thumb,
                "title": title,
                "page_url": page_url,
                "site": _site_name(page_url or thumb),
            }
        )
        if len(out) >= limit:
            break
    return out


# Last similar-image sets by face id (populated by URL flow, consumed once).
_IMAGES_BY_ID: dict[str, list[dict]] = {}


async def _yandex_url_search(public_url: str, res_id: str = "") -> list[dict]:
    """Yandex ?rpt=imageview&url= HTML flow (JSON apphost returns empty)."""
    import httpx

    search_url = (
        "https://yandex.com/images/search?rpt=imageview&url="
        + urllib.parse.quote(public_url, safe="")
    )
    try:
        async with httpx.AsyncClient(
            timeout=25.0,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://yandex.com/images/",
            },
        ) as client:
            await client.get("https://yandex.com/images/")
            r = await client.get(search_url)
            if r.status_code != 200 or len(r.content) < 10000:
                return []
            if res_id:
                _IMAGES_BY_ID[res_id] = _parse_yandex_images(r.text)
            return _parse_yandex_html(r.text)
    except Exception as e:
        logger.debug(f"yandex url search failed: {e}")
        return []


async def _yandex_reverse_search(crop_bytes: bytes, res_id: str = "") -> list[dict]:
    import httpx

    # URL flow first (JSON upload endpoint returns empty blocks).
    public_url = _publish_crop(crop_bytes, res_id or "face")
    if public_url:
        try:
            matches = await _yandex_url_search(public_url, res_id)
            if matches:
                return matches[:6]
        finally:
            _unpublish_crop(public_url)

    async def attempt(url: str, *, use_file: bool) -> list[dict]:
        async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
            if use_file:
                r = await client.post(
                    url,
                    files={"file": ("crop.jpg", crop_bytes, "image/jpeg")},
                    headers={
                        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
                    },
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
        r"vk\.com|ok\.ru|tiktok|youtube|reddit|bio|photo of",
        re.I,
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
            out.append(
                {"name": name, "href": href, "confidence": _GUESS_CONF, "title": title}
            )
        elif href and href not in seen:
            seen.add(href)
            out.append({"name": href, "href": href, "confidence": _GUESS_CONF})

    walk(data)

    # Try to read Yandex's typed matches fields too (shape varies by version).
    for block in (
        data.get("image-details", {}) if isinstance(data, dict) else {}
    ).values():
        if isinstance(block, list):
            for m in block[:3]:
                if not isinstance(m, dict):
                    continue
                title = m.get("title") or m.get("name") or ""
                url = m.get("url") or m.get("page-url") or ""
                if title and title not in seen:
                    seen.add(title)
                    out.append(
                        {
                            "name": _guess_name_from_title(title),
                            "href": url,
                            "confidence": _GUESS_CONF,
                        }
                    )
    return out


def _guess_name_from_title(title: str) -> str:
    """Best-effort: extract a human name from a hit title like 'John Smith — LinkedIn'."""
    t = re.sub(r"[|•·]|&nbsp;", " | ", title)
    if "@" in t:
        handle = re.search(r"@([A-Za-z0-9_.]{3,40})", t)
        if handle:
            return handle.group(1)
    cleaned = re.sub(
        r"\b(linkedin|facebook|instagram|twitter|x\.com|vk|tiktok|reddit|profile)\b.*$",
        "",
        t,
        flags=re.I,
    )
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
    if _is_adult_content(name):
        return {"name": None, "social_accounts": [], "report": "filtered adult content"}
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

    urls = re.findall(r"https?://[^\s)\]]+", " ".join(s for s in sources if s))
    for u in urls:
        if any(d in u.lower() for d in _SOCIAL_DOMAINS):
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
