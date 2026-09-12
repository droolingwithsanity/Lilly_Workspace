#!/usr/bin/env python3
"""
Lilly Footprint Reports — autonomous digital-identity dossiers.

Flow: face seen on camera → Tier2 name → background footprint job →
notification + chat readout when ready.

  1. Username variants from the name (firstlast, first.last, first_last)
  2. Sherlock + Maigret scans across thousands of sites (parallel, budgeted)
  3. Social / fans / dating / news web search
  4. PHOTO VERIFICATION with Lilly's own ArcFace engine: candidate profile
     photos are embedded and cosine-matched against the camera crop — same
     face on multiple sites = actually verified, not same-handle guessing
  5. Human-readable report → phone notification + web chat + IDs panel

Env:
    FACE_FOOTPRINT_AUTO=1      # auto-start on Tier2 resolve
    FACE_FOOTPRINT_COOLDOWN=86400  # seconds between reports for same name
    FOOTPRINT_PHOTO_VERIFY=1   # ArcFace photo matching (0 = handle-only)
    FOOTPRINT_PHOTO_THRESHOLD=0.45
    FOOTPRINT_MAX_IMAGES=10    # profile photos to download + compare
"""

import asyncio
import json
import logging
import os
import re
import time
import urllib.parse
from pathlib import Path

logger = logging.getLogger("lilly-footprint")

FOOTPRINT_AUTO = os.environ.get("FACE_FOOTPRINT_AUTO", "1") == "1"
FOOTPRINT_COOLDOWN = float(os.environ.get("FACE_FOOTPRINT_COOLDOWN", "86400"))
FOOTPRINT_DIR = Path(os.environ.get("FOOTPRINT_DIR", "/app/data/footprints"))
PHOTO_VERIFY = os.environ.get("FOOTPRINT_PHOTO_VERIFY", "1") == "1"
PHOTO_THRESHOLD = float(os.environ.get("FOOTPRINT_PHOTO_THRESHOLD", "0.45"))
MAX_IMAGES = int(os.environ.get("FOOTPRINT_MAX_IMAGES", "10"))
SHERLOCK_TIMEOUT = int(os.environ.get("FOOTPRINT_SHERLOCK_TIMEOUT", "150"))
MAIGRET_TIMEOUT = int(os.environ.get("FOOTPRINT_MAIGRET_TIMEOUT", "180"))

_jobs: dict[str, dict] = {}
_last_started: dict[str, float] = {}
_announce_fn = None


def init(announce_fn):
    """Register completion announcer (lilly_ai: notify + chat + speak)."""
    global _announce_fn
    _announce_fn = announce_fn


def username_variants(name: str) -> list[str]:
    parts = re.findall(r"[a-z0-9]+", (name or "").lower())
    if not parts:
        return []
    if len(parts) == 1:
        return [parts[0]]
    first, last = parts[0], parts[-1]
    out = [first + last, f"{first}.{last}", f"{first}_{last}"]
    if len(parts) > 2:
        out.append("".join(parts))
    seen, uniq = set(), []
    for v in out:
        if v not in seen:
            seen.add(v)
            uniq.append(v)
    return uniq[:4]


async def _run_cli(cmd: list[str], timeout: int) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return out.decode(errors="replace")
    except Exception as e:
        logger.debug(f"cli failed {' '.join(cmd[:2])}: {e}")
        return ""


def _parse_sherlock(text: str) -> dict[str, str]:
    """{"Platform": url} from sherlock --print-found output."""
    found: dict[str, str] = {}
    for m in re.finditer(r"\[\+\]\s*([^:\n]{2,40}):\s*(https?://\S+)", text):
        found[m.group(1).strip()] = m.group(2).strip()
    return found


async def scan_sherlock(variant: str) -> dict[str, str]:
    out = await _run_cli(
        ["sherlock", variant, "--timeout", "10", "--print-found", "--no-color"],
        SHERLOCK_TIMEOUT,
    )
    return _parse_sherlock(out)


async def scan_maigret(variant: str) -> dict[str, str]:
    """{"Platform": url} for claimed profiles via maigret simple JSON."""
    import tempfile

    found: dict[str, str] = {}
    try:
        with tempfile.TemporaryDirectory() as td:
            out = await _run_cli(
                ["maigret", "--json", "simple", "-o", td, "--timeout", "15", variant],
                MAIGRET_TIMEOUT,
            )
            _ = out
            for jf in Path(td).rglob("*.json"):
                try:
                    data = json.loads(jf.read_text())
                except Exception:
                    continue

                def walk(node):
                    if isinstance(node, dict):
                        status = str(node.get("status", "")).lower()
                        url = node.get("url_user") or node.get("url") or ""
                        site = node.get("site_name") or node.get("site") or ""
                        if "claim" in status and url:
                            found[str(site or url)] = url
                        for v in node.values():
                            walk(v)
                    elif isinstance(node, list):
                        for v in node:
                            walk(v)

                walk(data)
    except Exception as e:
        logger.debug(f"maigret failed: {e}")
    return found


async def web_search_profiles(name: str) -> dict[str, list[str]]:
    """About/news mentions via bot-friendly APIs (Wikipedia + GDELT).

    Scraped engines (DDG/Bing/Mojeek) bot-block datacenter IPs, so DDG
    stays as a last-resort best effort only.
    """
    import httpx

    out: dict[str, list[str]] = {}
    try:
        async with httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=True,
            headers={"User-Agent": "LillyBot/1.0 (personal OSINT)"},
        ) as c:
            # Wikipedia: about pages for the name.
            try:
                r = await c.get(
                    "https://en.wikipedia.org/w/api.php?action=query&list=search"
                    "&format=json&srlimit=5&srsearch=" + urllib.parse.quote(name)
                )
                hits = r.json().get("query", {}).get("search", [])
                urls = [
                    "https://en.wikipedia.org/wiki/"
                    + urllib.parse.quote(h["title"].replace(" ", "_"))
                    for h in hits
                    if h.get("title")
                ]
                if urls:
                    out["about"] = urls
            except Exception as e:
                logger.debug(f"wiki search failed: {e}")
            # GDELT: recent news (single call, tolerant of 429 pacing).
            try:
                await asyncio.sleep(5)
                g = await c.get(
                    "https://api.gdeltproject.org/api/v2/doc/doc?query="
                    + urllib.parse.quote(f'"{name}"')
                    + "&mode=artlist&maxrecords=8&format=json"
                )
                if g.status_code == 200:
                    arts = g.json().get("articles", [])
                    urls = [a["url"] for a in arts if a.get("url")]
                    if urls:
                        out["news"] = urls
            except Exception as e:
                logger.debug(f"gdelt search failed: {e}")
            # Last resort: DuckDuckGo HTML (often bot-walled, best effort).
            if not out:
                try:
                    q = urllib.parse.quote(
                        f'"{name}" site:linkedin.com OR site:twitter.com OR '
                        f"site:facebook.com OR site:instagram.com"
                    )
                    r = await c.get("https://html.duckduckgo.com/html/?q=" + q)
                    links = re.findall(r'href="(https?://[^"]+)"', r.text)
                    urls = [u for u in links if "duckduckgo.com" not in u][:8]
                    if urls:
                        out["social"] = urls
                except Exception:
                    pass
    except Exception as e:
        logger.debug(f"profile web search failed: {e}")
    return out


def reference_embedding(crop_bytes: bytes):
    """ArcFace embedding of the camera crop (None if unavailable)."""
    try:
        import cv2 as _cv2

        import numpy as _np

        from face_recognition_engine import get_face_engine

        frame = _cv2.imdecode(
            _np.frombuffer(crop_bytes, dtype=_np.uint8), _cv2.IMREAD_COLOR
        )
        if frame is None:
            return None
        engine = get_face_engine()
        faces = engine.detect_faces(frame)
        box = (
            max(faces, key=lambda f: f["w"] * f["h"])
            if faces
            else {"x": 0, "y": 0, "w": int(frame.shape[1]), "h": int(frame.shape[0])}
        )
        enc = engine.encode_face(frame, box)
        if not enc:
            return None
        return _np.array(enc, dtype=float)
    except Exception as e:
        logger.debug(f"reference embedding failed: {e}")
        return None


def _og_image(page_html: str) -> str:
    for pat in (
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)',
    ):
        m = re.search(pat, page_html, re.I)
        if m:
            return m.group(1)
    return ""


async def photo_verify(ref_emb, urls: list[str]) -> list[dict]:
    """Download candidate profile photos, ArcFace-compare to the camera crop."""
    import httpx

    results: list[dict] = []
    if ref_emb is None:
        return results
    try:
        import numpy as _np

        from face_recognition_engine import get_face_engine

        engine = get_face_engine()
        if not engine.is_ready:
            return results

        async def check(url: str) -> dict | None:
            try:
                async with httpx.AsyncClient(
                    timeout=12.0,
                    follow_redirects=True,
                    headers={"User-Agent": "Mozilla/5.0"},
                ) as c:
                    r = await c.get(url)
                    if r.status_code != 200 or "text/html" not in r.headers.get(
                        "content-type", ""
                    ):
                        return None
                    img_url = _og_image(r.text[:200000])
                    if not img_url:
                        return None
                    img_url = urllib.parse.urljoin(url, img_url)
                    ri = await c.get(img_url)
                    if ri.status_code != 200 or len(ri.content) > 3_000_000:
                        return None
                    ctype = ri.headers.get("content-type", "")
                    if "image" not in ctype:
                        return None
                    import cv2 as _cv2

                    frame = _cv2.imdecode(
                        _np.frombuffer(ri.content, dtype=_np.uint8), _cv2.IMREAD_COLOR
                    )
                    if frame is None:
                        return None
                    faces = engine.detect_faces(frame)
                    if not faces:
                        return None
                    box = max(faces, key=lambda f: f["w"] * f["h"])
                    enc = engine.encode_face(frame, box)
                    if not enc:
                        return None
                    cand = _np.array(enc, dtype=float)
                    denom = float((ref_emb**2).sum() ** 0.5 * (cand**2).sum() ** 0.5)
                    score = float(ref_emb.dot(cand) / denom) if denom else 0.0
                    return {"url": url, "score": round(score, 3), "img": img_url}
            except Exception:
                return None

        sem = asyncio.Semaphore(4)

        async def gated(u: str):
            async with sem:
                return await check(u)

        for res in await asyncio.gather(*[gated(u) for u in urls[:MAX_IMAGES]]):
            if res and res["score"] >= PHOTO_THRESHOLD:
                results.append(res)
        results.sort(key=lambda r: r["score"], reverse=True)
    except Exception as e:
        logger.debug(f"photo verify failed: {e}")
    return results


def job_path(job_id: str) -> Path:
    FOOTPRINT_DIR.mkdir(parents=True, exist_ok=True)
    return FOOTPRINT_DIR / f"{job_id}.json"


def save_job(job: dict):
    try:
        job_path(job["id"]).write_text(json.dumps(job, indent=2)[:200000])
    except Exception:
        pass


def get_job(job_id: str) -> dict | None:
    if job_id in _jobs:
        return _jobs[job_id]
    try:
        p = job_path(job_id)
        if p.exists():
            return json.loads(p.read_text())
    except Exception:
        pass
    return None


def list_jobs(limit: int = 10) -> list[dict]:
    jobs = sorted(_jobs.values(), key=lambda j: j.get("started_ts", 0), reverse=True)
    if len(jobs) < limit:
        try:
            files = sorted(
                FOOTPRINT_DIR.glob("fp_*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            seen = {j["id"] for j in jobs}
            for f in files[:limit]:
                try:
                    j = json.loads(f.read_text())
                    if j.get("id") not in seen:
                        jobs.append(j)
                except Exception:
                    continue
        except Exception:
            pass
    return jobs[:limit]


async def start_footprint(
    name: str,
    face_id: str = "",
    crop_b64: str = "",
    auto: bool = False,
    force: bool = False,
) -> dict:
    """Start (or reuse) a footprint job. Returns the job dict immediately."""
    key = re.sub(r"\s+", " ", (name or "").strip().lower())
    if not key:
        return {"ok": False, "error": "name required"}
    now = time.time()
    if not force:
        for j in list_jobs(20):
            if j.get("key") == key and j.get("status") in ("running", "done"):
                age = now - j.get("started_ts", 0)
                if j["status"] == "running" or age < FOOTPRINT_COOLDOWN:
                    return {"ok": True, "job": j, "reused": True}
    if not force and now - _last_started.get(key, 0) < 600:
        return {"ok": False, "error": "a report just started for this name"}
    _last_started[key] = now
    job_id = f"fp_{int(now)}_{re.sub(r'[^a-z0-9]+', '_', key)[:24]}"
    job = {
        "id": job_id,
        "key": key,
        "name": name.strip(),
        "face_id": face_id,
        "status": "running",
        "progress": "starting username scans…",
        "auto": auto,
        "started_ts": now,
        "elapsed_s": 0,
        "variants": [],
        "site_hits": {},
        "web": {},
        "verified": [],
        "likely": [],
        "mentions": [],
        "report": "",
    }
    _jobs[job_id] = job
    save_job(job)
    asyncio.get_running_loop().create_task(_run_job(job, crop_b64))
    return {"ok": True, "job": job, "reused": False}


async def _run_job(job: dict, crop_b64: str = ""):
    t0 = time.time()
    name, key = job["name"], job["key"]
    try:
        job["progress"] = "scanning usernames across thousands of sites…"
        save_job(job)
        variants = username_variants(name)
        job["variants"] = variants

        # Sherlock + maigret per variant, all parallel.
        tasks = []
        for v in variants:
            tasks.append(scan_sherlock(v))
            tasks.append(scan_maigret(v))
        scan_results = await asyncio.gather(*tasks, return_exceptions=True)

        site_hits: dict[str, dict] = {}  # url -> {platforms:[], variant}
        for v, res in zip([x for v in variants for x in (v, v)], scan_results):
            if not isinstance(res, dict):
                continue
            for platform, url in res.items():
                e = site_hits.setdefault(url, {"platforms": [], "variant": v})
                if platform not in e["platforms"]:
                    e["platforms"].append(platform)

        job["progress"] = "searching social, fans, dating and news…"
        save_job(job)
        web = await web_search_profiles(name)
        job["web"] = web
        job["site_hits"] = {u: e for u, e in list(site_hits.items())[:60]}

        # Photo verification against the camera crop.
        verified: list[dict] = []
        if PHOTO_VERIFY and crop_b64:
            job["progress"] = "photo-verifying candidate profiles…"
            save_job(job)
            try:
                import base64

                ref = reference_embedding(base64.b64decode(crop_b64))
            except Exception:
                ref = None
            if ref is not None:
                profile_urls = [u for u in site_hits.keys()][:MAX_IMAGES]
                verified = await photo_verify(ref, profile_urls)

        # Handle clustering: same handle on 3+ sites = likely them.
        handle_sites: dict[str, list[str]] = {}
        for url, e in site_hits.items():
            for v in variants:
                m = re.search(re.escape(v), url.lower())
                if m:
                    handle_sites.setdefault(v, []).append(url)
                    break
        verified_urls = {v["url"] for v in verified}
        likely = []
        for handle, urls in handle_sites.items():
            fresh = [u for u in urls if u not in verified_urls][:8]
            if len(urls) >= 3 and fresh:
                likely.append({"handle": handle, "sites": len(urls), "urls": fresh})

        job["verified"] = verified
        job["likely"] = likely
        job["mentions"] = web.get("news", [])[:6]
        job["elapsed_s"] = round(time.time() - t0, 1)
        job["status"] = "done"
        job["progress"] = "complete"
        job["report"] = format_report(job)
        save_job(job)
        logger.info(
            f"Footprint done for {name}: {len(verified)} verified, "
            f"{len(likely)} likely, {len(site_hits)} hits"
        )
    except Exception as e:
        job["status"] = "error"
        job["progress"] = f"failed: {e}"
        job["elapsed_s"] = round(time.time() - t0, 1)
        save_job(job)
        logger.warning(f"Footprint failed for {name}: {e}")

    if _announce_fn is not None:
        try:
            res = _announce_fn(job)
            if asyncio.iscoroutine(res):
                await res
        except Exception as e:
            logger.debug(f"footprint announce failed: {e}")


def _mins(s: float) -> str:
    m, sec = divmod(int(s), 60)
    return f"{m}m{sec:02d}s" if m else f"{sec}s"


def format_report(job: dict) -> str:
    """Human-readable dossier."""
    n = job.get("name", "?")
    lines = [f"🔍 FOOTPRINT: {n}  ({_mins(job.get('elapsed_s', 0))})"]
    verified = job.get("verified", []) or []
    likely = job.get("likely", []) or []
    web = job.get("web", {}) or {}
    if verified:
        lines.append(f"\n✅ PHOTO-VERIFIED — same face ({len(verified)}):")
        for v in verified[:8]:
            lines.append(f"• {v['url']} (face {v['score']:.0%})")
    if likely:
        lines.append(f"\n👤 SAME HANDLE ON 3+ SITES — likely them ({len(likely)}):")
        for L in likely[:6]:
            lines.append(f"• @{L['handle']} — {L['sites']} sites")
            for u in L["urls"][:4]:
                lines.append(f"    {u}")
    social = web.get("social", []) or []
    fans = web.get("fans_dating", []) or []
    about = web.get("about", []) or []
    news = job.get("mentions", []) or web.get("news", []) or []
    if social:
        lines.append(f"\n📣 SOCIAL MENTIONS ({len(social)}):")
        for u in social[:6]:
            lines.append(f"• {u}")
    if fans:
        lines.append(f"\n💘 FANS / DATING MENTIONS ({len(fans)}):")
        for u in fans[:6]:
            lines.append(f"• {u}")
    if about:
        lines.append(f"\n📖 ABOUT ({len(about)}):")
        for u in about[:5]:
            lines.append(f"• {u}")
    if news:
        lines.append(f"\n📰 NEWS / WEB ({len(news)}):")
        for u in news[:6]:
            lines.append(f"• {u}")
    if (
        not verified
        and not likely
        and not social
        and not fans
        and not about
        and not news
    ):
        lines.append("\nNothing solid found — thin or private footprint.")
    else:
        if not verified:
            lines.append(
                "\n⚠️ No photo-verified match — treat handles as leads, not proof."
            )
    return "\n".join(lines)
