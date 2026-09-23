#!/usr/bin/env python3
"""
XCEB core — Face + OSINT Evidence Broker
========================================
Config, case store, per-case ledger, job runner and structured logging.

A "case" is a single investigation started from a face crop (camera sighting
or uploaded image). Each case owns a folder under XCEB_DATA/cases:

    cases/<slug>/
        face.jpg          face screenshot thumbnail (camera box / upload crop)
        original.jpg      full source frame when available
        case.json         live ledger (all findings, confidence, steps)
        profile.html      public-readable dossier with % confidence + sources
        profile.txt       plain-text version
        graph.json        entity graph (JSON)
        maltego_export.csv  Maltego CE import
        gallery/*.jpg     matched reference images from reverse search

The folder starts as cases/unknown_<id>/ and is RENAMED to the person's name
slug (e.g. cases/john_smith_20260915/) the moment the pipeline resolves an
identity at >= XCEB_RESOLVE_CONF.  `case_id` stays stable forever.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import secrets
import shutil
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import httpx

logger = logging.getLogger("xceb")

# ── Paths ────────────────────────────────────────────────────────────────
XCEB_DATA = Path(os.environ.get("XCEB_DATA", "/app/data"))
XCEB_SHARED = Path(
    os.environ.get("XCEB_SHARED", "/app/shared")
)  # ro: models/face_crops
CASES_DIR = XCEB_DATA / "cases"
FACES_DIR = XCEB_DATA / "faces"  # XCEB's own FAISS "file with faces"
GALLERY_DIR = XCEB_DATA / "gallery"
LOGS_DIR = XCEB_DATA / "logs"
PUB_DIR = XCEB_DATA / "pub"  # crops served for URL-flow engines
CATALOG = XCEB_DATA / "catalog.json"

# ── Environment / engine config ──────────────────────────────────────────
XCEB_PUBLIC_BASE = os.environ.get("XCEB_PUBLIC_BASE", "").rstrip("/")  # tunnel
XCEB_LLM_URL = os.environ.get("XCEB_LLM_URL", "http://127.0.0.1:11434")  # Ollama
XCEB_LLM_MODEL = os.environ.get("XCEB_LLM_MODEL", "qwen2.5:7b")
XCEB_LLM_FAST = os.environ.get("XCEB_LLM_FAST", "qwen2.5:1.5b")
# Where the vision overlay server lives — resolved identities are pushed back
# so the live camera box label switches from "Person" to the discovered name.
XCEB_VISION_URL = os.environ.get("XCEB_VISION_URL", "http://127.0.0.1:8198")
XCEB_PROXY = os.environ.get("XCEB_PROXY", "")  # socks5://… / http://…
XCEB_RESOLVE_CONF = float(
    os.environ.get("XCEB_RESOLVE_CONF", "0.55")
)  # rename threshold
# Autonomous high bar: the pipeline may auto-resolve + auto-rename the live
# camera box only above this confidence. Below it (but >= XCEB_RESOLVE_CONF)
# the case lands as "lead" and needs a human-confirmed resolve.
XCEB_RESOLVE_CONF_AUTO = float(os.environ.get("XCEB_RESOLVE_CONF_AUTO", "0.80"))
# Sources that can NEVER auto-resolve / auto-rename / auto-enroll.
# Phone camera, vision ingest, seeds and test fixtures are not human-confirmed
# identities: they must stop at "lead" until the user clicks Resolve.
XCEB_BLOCKED_SOURCES = [
    s.strip().lower()
    for s in os.environ.get(
        "XCEB_BLOCKED_SOURCES",
        "vision_test,vision_seed,vision,seed,test,phone,camera,sensor,sample",
    ).split(",")
    if s.strip()
]
XCEB_AUTO_ENROLL = os.environ.get("XCEB_AUTO_ENROLL", "1") == "1"  # resolve→faces.index
XCEB_SLEEP_WINDOW = os.environ.get("XCEB_SLEEP_WINDOW", "01:00-05:00")  # night batch
XCEB_TOKEN = os.environ.get("XCEB_TOKEN", "")  # optional bearer
XCEB_ENGINE_DELAY = float(os.environ.get("XCEB_ENGINE_DELAY", "2.5"))  # rate-limit s
XCEB_MAX_CASES = int(os.environ.get("XCEB_MAX_CASES", "400"))  # catalog cap
XCEB_RETRY_HOURS = float(os.environ.get("XCEB_RETRY_HOURS", "24"))  # unresolved retry

PROFILE_STAGES = (
    "reverse",
    "accounts",
    "records",
    "llm",
    "faces_index",
    "graph",
    "profile",
)
_ENGINE_NAMES = ("yandex", "bing", "tineye", "lens")

_PUBLIC_BASE_LOCK = asyncio.Lock()


def ensure_dirs():
    for d in (CASES_DIR, FACES_DIR, GALLERY_DIR, LOGS_DIR, PUB_DIR):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning(f"mkdir {d}: {e}")


def slugify(name: str, fallback: str = "unknown") -> str:
    """Clean a person name to a filesystem-safe slug."""
    s = re.sub(r"[^A-Za-z0-9]+", "_", (name or "").strip().lower()).strip("_")
    if not s:
        return fallback
    return s


def new_id(prefix: str = "c") -> str:
    return f"{prefix}_{int(time.time())}_{secrets.token_hex(3)}"


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


# ── Case store ───────────────────────────────────────────────────────────
def _load_catalog() -> dict:
    try:
        return json.loads(CATALOG.read_text())
    except Exception:
        return {"cases": []}


def _save_catalog(cat: dict):
    try:
        CATALOG.parent.mkdir(parents=True, exist_ok=True)
        CATALOG.write_text(json.dumps(cat, indent=2))
    except Exception as e:
        logger.debug(f"catalog save failed: {e}")


def catalog_list(limit: int = 200) -> list[dict]:
    cat = _load_catalog()
    cases = [] if cat.get("cases") is None else cat["cases"]
    cases.sort(key=lambda c: c.get("updated_ts", 0), reverse=True)
    return cases[: max(1, min(limit, XCEB_MAX_CASES))]


def _upsert_catalog(meta: dict):
    cat = _load_catalog()
    cases = cat.setdefault("cases", [])
    cases = [c for c in cases if c.get("case_id") != meta.get("case_id")]
    cases.append(meta)
    cases = cases[-XCEB_MAX_CASES:]
    cat["cases"] = cases
    _save_catalog(cat)


def _remove_catalog(case_id: str):
    cat = _load_catalog()
    cat["cases"] = [c for c in cat.get("cases", []) if c.get("case_id") != case_id]
    _save_catalog(cat)


def create_case(
    *,
    source: str = "upload",
    face_id: str = "",
    crop_b64: str = "",
    original_b64: str = "",
    geo_hint: str = "",
    category: str = "all",  # all | social | dating
    note: str = "",
    meta: dict | None = None,
) -> dict:
    """Create a case folder + ledger. Returns the live case dict."""
    ensure_dirs()
    case_id = new_id("c")
    slug = f"unknown_{slugify(face_id or case_id, case_id)}"
    cdir = CASES_DIR / slug
    cdir.mkdir(parents=True, exist_ok=True)

    case = {
        "case_id": case_id,
        "slug": slug,
        "dir": str(cdir),
        "status": "new",
        "source": source,
        "face_id": face_id,
        "geo_hint": geo_hint,
        "category": category or "all",
        "note": note or "",
        "best_name": None,
        "slug_name": None,
        "confidence": 0.0,
        "created": now_iso(),
        "created_ts": time.time(),
        "updated": now_iso(),
        "updated_ts": time.time(),
        "steps": [],
        "findings": {
            "reverse": [],
            "accounts": [],
            "emails": [],
            "phones": [],
            "addresses": [],
            "records": [],
            "aliases": [],
            "galleries": [],
        },
        "graph": {"nodes": [], "edges": []},
        "files": [],
        "next_retry": time.time() + XCEB_RETRY_HOURS * 3600,
        "meta": meta or {},
        "llm": {},
        "warnings": [],
    }

    # Save image assets.
    if crop_b64:
        _write_asset(cdir / "face.jpg", crop_b64)
    if original_b64:
        _write_asset(cdir / "original.jpg", original_b64)
    (cdir / ".gallery").mkdir(exist_ok=True)

    _write_case(case)
    _upsert_catalog(_meta_of(case))
    return case


def _write_asset(path: Path, b64: str) -> bool:
    try:
        data = base64.b64decode(b64)
        if len(data) < 200:
            return False
        path.write_bytes(data)
        return True
    except Exception:
        return False


def _meta_of(case: dict) -> dict:
    return {
        "case_id": case["case_id"],
        "slug": case.get("slug_name") or case["slug"],
        "status": case.get("status"),
        "source": case.get("source"),
        "face_id": case.get("face_id", ""),
        "best_name": case.get("best_name"),
        "confidence": case.get("confidence", 0.0),
        "geo_hint": case.get("geo_hint", ""),
        "category": case.get("category", "all"),
        "created_ts": case.get("created_ts", 0),
        "updated_ts": case.get("updated_ts", 0),
        "has_photo": (Path(case["dir"]) / "face.jpg").exists(),
    }


def load_case(case_id_or_slug: str) -> dict | None:
    """Resolve by exact case_id/slug, or fallback substring match."""
    q = case_id_or_slug.lower()
    # exact folder scan first
    for jsonp in CASES_DIR.glob("*/case.json"):
        try:
            case = json.loads(jsonp.read_text())
            if case.get("case_id") == q or case.get("slug") == q:
                return case
        except Exception:
            continue
    for jsonp in CASES_DIR.glob("*/case.json"):
        try:
            case = json.loads(jsonp.read_text())
            if (
                q in (case.get("slug") or "").lower()
                or q in (case.get("best_name") or "").lower()
            ):
                return case
        except Exception:
            continue
    # legacy flat file fallback
    p = CASES_DIR / f"{q}/case.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return None


def _write_case(case: dict):
    cdir = Path(case["dir"])
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "case.json").write_text(json.dumps(case, indent=2))


def rename_case_on_resolve(case: dict, name: str) -> dict:
    """Rename cases/<slug> → cases/<person_name_slug> and refresh ledger."""
    old_dir = Path(case["dir"])
    slug = f"{slugify(name)}_{time.strftime('%Y%m%d')}"
    new_dir = CASES_DIR / slug
    try:
        if old_dir != new_dir:
            if new_dir.exists():
                new_dir = CASES_DIR / f"{slug}_{secrets.token_hex(2)}"
                slug = new_dir.name
            shutil.move(str(old_dir), str(new_dir))
        case["dir"] = str(new_dir)
        case["slug"] = new_dir.name
        case["slug_name"] = name.strip()
        _write_case(case)
        _upsert_catalog(_meta_of(case))
        logger.info(f"case {case['case_id']} → resolved folder {new_dir.name}")
    except Exception as e:
        logger.warning(f"rename failed: {e}")
        case["slug_name"] = name.strip()
        _write_case(case)
    return case


def log_step(case: dict, msg: str, level: str = "info"):
    entry = {"ts": time.time(), "t": now_iso(), "level": level, "msg": msg}
    case.setdefault("steps", []).append(entry)
    case["steps"] = case["steps"][-2000:]
    case["updated"] = now_iso()
    case["updated_ts"] = time.time()
    _write_case(case)
    # mirror append to case.log for tail viewers
    try:
        with open(Path(case["dir"]) / "case.log", "a") as f:
            f.write(f"[{entry['t']}] {level.upper():7s} {msg}\n")
    except Exception:
        pass
    _upsert_catalog_quiet(case)


def _upsert_catalog_quiet(case: dict):
    try:
        _upsert_catalog(_meta_of(case))
    except Exception:
        pass


def update_findings(case: dict, key: str, items: list, replace: bool = False):
    if replace:
        case.setdefault("findings", {})[key] = items
    else:
        seen = set()

        def _u(item):
            k = (
                item.get("url")
                or item.get("value")
                or item.get("name")
                or str(item)[:80]
            )
            return k

        merged = list(case.setdefault("findings", {}).get(key, []))
        for it in items:
            k = _u(it)
            if k not in seen:
                seen.add(k)
                merged.append(it)
        case["findings"][key] = merged[-400:]
    _write_case(case)
    _upsert_catalog_quiet(case)


def case_files(case: dict) -> list[dict]:
    out = []
    cdir = Path(case["dir"])
    for p in sorted(cdir.glob("*")):
        if p.is_file():
            try:
                out.append({"name": p.name, "size": p.stat().st_size})
            except Exception:
                pass
    return out


def delete_case(case_id: str) -> bool:
    case = load_case(case_id)
    if not case:
        return False
    try:
        shutil.rmtree(Path(case["dir"]), ignore_errors=True)
    except Exception:
        pass
    _remove_catalog(case["case_id"])
    return True


# ── Crop publishing (for URL-flow reverse engines) ───────────────────────
def publish_crop(case: dict) -> str | None:
    """Copy the case face.jpg into the pub dir and return a public URL.

    Uses XCEB_PUBLIC_BASE (public tunnel) when configured; otherwise returns a
    local URL only meaningful when the XCEB host itself is reachable.
    """
    src = Path(case["dir"]) / "face.jpg"
    if not src.exists():
        return None
    try:
        PUB_DIR.mkdir(parents=True, exist_ok=True)
        token = secrets.token_hex(8)
        dst = PUB_DIR / f"{case['case_id']}_{token}_crop.jpg"
        shutil.copy2(src, dst)
        # keep pub dir tidy
        for f in PUB_DIR.glob("*.jpg"):
            try:
                if time.time() - f.stat().st_mtime > 3600:
                    f.unlink()
            except Exception:
                pass
        if XCEB_PUBLIC_BASE:
            return f"{XCEB_PUBLIC_BASE}/xceb/pub/{dst.name}"
        return f"/xceb/pub/{dst.name}"
    except Exception as e:
        logger.debug(f"publish_crop failed: {e}")
        return None


def unpublish_crop(local: str | None):
    if not local or "pub/" not in local:
        return
    try:
        name = local.rsplit("/", 1)[-1]
        (PUB_DIR / name).unlink(missing_ok=True)
    except Exception:
        pass


# ── LLM helper (optional Ollama rerank/extract) ──────────────────────────
_LLM_LOCK = asyncio.Lock()


async def llm_json(
    prompt: str, fast: bool = False, timeout: float = 90.0
) -> dict | None:
    """Ask Ollama for a strict JSON answer. Returns {} on failure."""
    if not XCEB_LLM_URL:
        return None
    import httpx

    model = XCEB_LLM_FAST if fast else XCEB_LLM_MODEL
    url = f"{XCEB_LLM_URL}/api/generate"
    body = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.1},
    }
    try:
        async with _LLM_LOCK:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(url, json=body)
                r.raise_for_status()
                text = (r.json().get("response") or "").strip()
        text = text.replace("```json", "").replace("```", "").strip()
        return json.loads(text)
    except Exception as e:
        logger.debug(f"llm_json failed: {e}")
        return None


# ── HTTP client shared (proxy-aware) ─────────────────────────────────────
def make_client(timeout: float = 30.0) -> httpx.AsyncClient:
    proxies = None
    if XCEB_PROXY:
        proxies = XCEB_PROXY
    return httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        proxy=proxies,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
    )


# ── stealth fetches (scrapling, when available) ──────────────────────────
async def stealth_fetch(
    url: str,
    timeout: float = 35.0,
    force: bool = False,
) -> str | None:
    """Fetch a page the way a real browser would.

    Uses scrapling's stealth fetcher when installed (handles Cloudflare/challenge
    pages); falls back to the plain httpx client (with proxy support) otherwise.
    Returns the page HTML text or None. Never fabricates results.
    """
    try:
        if not force:
            async with make_client(timeout) as client:
                r = await client.get(url)
                if r.status_code == 200 and len(r.content) > 500:
                    return r.text
        else:
            raise RuntimeError("force")
    except Exception:
        pass
    # Stealth tier: scrapling (any of its several public APIs)
    try:
        import asyncio as _aio
        from scrapling.fetchers import Fetcher, StealthyFetcher
        import scrapling

        def _text(page) -> str | None:
            for attr in ("html_content", "html", "content", "body"):
                v = getattr(page, attr, None)
                if v:
                    return str(v)
            return None

        page = None
        # API A: StealthyFetcher().post/get (older)
        for F in (StealthyFetcher, Fetcher):
            try:
                inst = F()
                if hasattr(inst, "get"):
                    page = inst.get(url, timeout=timeout)
                elif hasattr(inst, "async_fetch"):
                    page = inst.async_fetch(url)
                if page is not None:
                    break
            except Exception:
                continue
        # API B: newer standalone functions
        if page is None and hasattr(scrapling, "fetch"):
            page = scrapling.fetch(url)
        if page is None and hasattr(_aio, "timeout"):
            # API C: Fetcher.create(...).get
            try:
                fc = Fetcher.create(name="stealthy")
                page = fc.get(url)
            except Exception:
                pass
        if page is not None:
            text = _text(page)
            if text and len(text) > 200:
                return text
    except Exception as e:
        logger.debug(f"stealth_fetch fallback failed for {url}: {e}")
    return None


@dataclass
class EngineGate:
    """Per-engine rate limiter (min delay between calls)."""

    name: str
    last: float = 0.0

    async def wait(self):
        elapsed = time.time() - self.last
        delay = max(0.0, XCEB_ENGINE_DELAY - elapsed)
        if delay:
            await asyncio.sleep(delay)
        self.last = time.time()


# ── Push resolved identities back to the vision overlay ───────────────────
def osint_payload_for(case: dict) -> dict:
    """Build the body for vision `POST /api/vision/face/identify`."""
    f = case.get("findings", {})
    socials = [a for a in f.get("accounts", []) if a.get("type") == "social"]
    gallery = f.get("galleries", [])
    reverse = f.get("reverse", [])
    images: list[str] = []
    for g in gallery:
        u = g.get("thumb") or ""
        if u.startswith("http") and u not in images:
            images.append(u)
        if len(images) >= 3:
            break
    sources: list[str] = []
    for h in reverse:
        u = h.get("href") or ""
        if u.startswith("http") and u not in sources:
            sources.append(u)
        if len(sources) >= 6:
            break
    return {
        "id": case.get("face_id") or f"case_{case['case_id']}",
        "name": case.get("best_name"),
        "confidence": case.get("confidence", 0.0),
        "social_accounts": [a.get("url") or a.get("value", "") for a in socials[:8]],
        "sources": sources,
        "images": images[:3],
        "person_box": (case.get("meta") or {}).get("person_box", {}),
    }


async def push_identity_to_vision(case: dict, timeout: float = 15.0) -> bool:
    """Push a resolved identity to the overlay server so the live camera box
    label changes from 'Person' to the person's discovered name."""
    if not XCEB_VISION_URL or not (case.get("best_name") or ""):
        return False
    if not case.get("face_id"):
        return False
    body = osint_payload_for(case)
    try:
        async with make_client(timeout) as client:
            r = await client.post(
                f"{XCEB_VISION_URL}/api/vision/face/identify", json=body
            )
            ok = r.status_code == 200
            logger.info(
                f"vision identify push for {body['id']}: "
                f"{'ok' if ok else r.status_code} → {body['name']}"
            )
            return ok
    except Exception as e:
        logger.debug(f"vision identify push failed: {e}")
        return False
