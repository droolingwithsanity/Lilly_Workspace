#!/usr/bin/env python3
"""
XCEB engines — free reverse image search providers
====================================================
Yandex, Bing Visual, TinEye and a best-effort Google Lens flow. All are free,
public and rate-limited (XCEB_ENGINE_DELAY between calls, jittered). Each
engine returns a normalized list of:

    {"name", "title", "href", "site", "thumb", "confidence", "engine"}

Confidence is intentionally conservative: a reverse-hit is a LEAD, never a
verdict. Agreeing engines + LLM rerank + FAISS index raises the final score.

Upload vs URL-flow:
    * yandex, lens, bing(imgurl) can consume a PUBLIC URL → XCEB_PUBLIC_BASE
    * tineye, bing(upload) accept a direct multipart upload → always available

Proxy: set XCEB_PROXY=socks5://… to route all engine traffic through Tor/VPN.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import urllib.parse

from xceb_core import EngineGate, make_client

logger = logging.getLogger("xceb.engines")

_GUESS_CONF = 0.35

GATES = {name: EngineGate(name) for name in ("yandex", "bing", "tineye", "lens")}

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


async def _wait(name: str):
    g = GATES[name]
    await g.wait()
    await asyncio.sleep(random.uniform(0.4, 1.6))


# ── normalized helpers ───────────────────────────────────────────────────
def _norm(hit: dict, engine: str) -> dict:
    return {
        "name": (hit.get("name") or "").strip(),
        "title": (hit.get("title") or "").strip(),
        "href": (hit.get("href") or "").strip(),
        "site": hit.get("site") or "",
        "thumb": (hit.get("thumb") or "").strip(),
        "confidence": float(hit.get("confidence") or _GUESS_CONF),
        "engine": engine,
    }


def _dedup(hits: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for h in hits:
        k = (h.get("href") or h.get("name") or h.get("title") or "").lower()
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(h)
    return out[:12]


# ── Yandex ───────────────────────────────────────────────────────────────
async def yandex(public_url: str = "", crop_bytes: bytes = b"") -> list[dict]:
    await _wait("yandex")
    results: list[dict] = []
    try:
        client = make_client(25.0)
        async with client:
            if public_url:
                url = (
                    "https://yandex.com/images/search?rpt=imageview&url="
                    + urllib.parse.quote(public_url, safe="")
                )
                r = await client.get(
                    url,
                    headers={**client.headers, "Referer": "https://yandex.com/images/"},
                )
                if r.status_code == 200 and len(r.content) > 10000:
                    results += _parse_yandex_html(r.text)
            elif crop_bytes:
                ep = (
                    "https://yandex.com/images-apphost/image-details"
                    "?cbird=111&rpt=imageview&format=json&request="
                    + urllib.parse.quote('{"blocks":[{"block":"b-identical-pages"}]}')
                )
                r = await client.post(
                    ep,
                    files={"file": ("crop.jpg", crop_bytes, "image/jpeg")},
                    headers={"User-Agent": UA},
                )
                if r.status_code == 200:
                    try:
                        results += _parse_yandex_json(r.json())
                    except Exception:
                        pass
    except Exception as e:
        logger.debug(f"yandex error: {e}")
    return _dedup([_norm(h, "yandex") for h in results])


def _parse_yandex_html(html: str) -> list[dict]:
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
    }

    def ok(t: str) -> bool:
        words = re.findall(r"[A-Za-z']+", t)
        if len(words) < 2:
            return False
        return len([w for w in words if w.lower() not in _STOP and len(w) > 1]) >= 2

    out: list[dict] = []
    seen = set()
    for tag in re.findall(r'"text":"([^"]{2,80})"', html):
        t = tag.strip()
        if len(t) < 2 or t in seen or not re.search(r"[A-Za-z]", t) or not ok(t):
            continue
        seen.add(t)
        out.append({"name": _guess(t), "title": t, "confidence": _GUESS_CONF})
    for title in re.findall(
        r'([A-Z][A-Za-z\'.-]+(?:\s+[A-Z][A-Za-z\'.-]+){1,3})[^<"]{0,60}?&quot;', html
    ):
        t = title.strip()
        if len(t) < 4 or t in seen or not ok(t):
            continue
        seen.add(t)
        out.append({"name": _guess(t), "title": t, "confidence": _GUESS_CONF})
    for u in re.findall(
        r"https?://[a-z0-9./_-]*(?:wikipedia\.org|whitehouse\.gov|britannica\.com|biography\.com)[a-z0-9./_?=%#-]*",
        html,
        re.I,
    ):
        if u not in seen:
            seen.add(u)
            out.append({"name": _guess(u.split("/")[-1].replace("_", " ")), "href": u})
    # frequency ranking: the true entity repeats many times
    out.sort(
        key=lambda c: html.count(c.get("title") or c.get("name") or ""), reverse=True
    )
    return out[:8]


def _parse_yandex_json(data) -> list[dict]:
    out: list[dict] = []
    seen = set()
    evidence = re.compile(
        r"person|people|profile|facebook|instagram|linkedin|twitter|vk\.com|ok\.ru|"
        r"tiktok|youtube|reddit|bio|photo of",
        re.I,
    )

    def walk(node):
        if isinstance(node, dict):
            node = list(node)
        if isinstance(node, list):
            for ch in node:
                walk(ch)
            return
        if not isinstance(node, str) or not node.strip() or len(node) > 240:
            return
        if evidence.search(node) and node not in seen:
            seen.add(node)
            out.append({"name": _guess(node), "title": node})

    walk(data)
    return out


# ── Bing Visual Search ───────────────────────────────────────────────────
async def bing(public_url: str = "", crop_bytes: bytes = b"") -> list[dict]:
    await _wait("bing")
    out: list[dict] = []
    try:
        client = make_client(25.0)
        async with client:
            if crop_bytes and not public_url:
                # upload flow
                r = await client.post(
                    "https://www.bing.com/images/search?q=imgurl:&view=detailv2&iss=sbiupload",
                    data={"_charset_": "UTF-8"},
                    files={"image": ("crop.jpg", crop_bytes, "image/jpeg")},
                    headers={"Referer": "https://www.bing.com/images/"},
                )
                if r.status_code in (200, 302):
                    html = (
                        r.text
                        if r.status_code == 200
                        else (r.headers.get("location") or "")
                    )
                    out += _parse_bing_html(html) if html.startswith("<") else []
            elif public_url:
                r = await client.get(
                    "https://www.bing.com/images/search",
                    params={
                        "q": f"imgurl:{public_url}",
                        "view": "detailv2",
                        "iss": "sbi",
                    },
                    headers={"Referer": "https://www.bing.com/images/"},
                )
                if r.status_code == 200:
                    out += _parse_bing_html(r.text)
    except Exception as e:
        logger.debug(f"bing error: {e}")
    return _dedup([_norm(h, "bing") for h in out])


def _parse_bing_html(html: str) -> list[dict]:
    out: list[dict] = []
    seen = set()
    # m="{...}" blobs embed title(t), pageurl(purl), thumb(turl), media(murl)
    for m in re.findall(r'm="(\{.*?\})"', html):
        try:
            d = json.loads(
                m.replace("&quot;", '"').replace("&amp;", "&").replace("&#39;", "'")
            )
        except Exception:
            continue
        t = d.get("t") or ""
        href = d.get("purl") or ""
        thumb = d.get("turl") or ""
        if not t and not href:
            continue
        key = href or t
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "name": _guess(t),
                "title": t,
                "href": href,
                "thumb": thumb,
                "confidence": _GUESS_CONF,
            }
        )
    if not out:
        # fallback: parse anchor titles + titles
        for t, href in re.findall(
            r'<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>', html
        ):
            txt = re.sub(r"<[^>]+>", "", href).strip()
            if not txt:
                continue
            txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", t))
            if len(txt) > 3 and re.search(r"[A-Za-z]", txt):
                out.append({"name": _guess(txt), "title": txt[:120], "href": t})
    return out[:10]


# ── TinEye (upload only) ─────────────────────────────────────────────────
async def tineye(crop_bytes: bytes) -> list[dict]:
    await _wait("tineye")
    out: list[dict] = []
    if not crop_bytes:
        return []
    try:
        client = make_client(30.0)
        async with client:
            r = await client.post(
                "https://tineye.com/search",
                data={"search_type": "search_type_upload", "g-recaptcha-response": ""},
                files={"image": ("crop.jpg", crop_bytes, "image/jpeg")},
                headers={"Referer": "https://tineye.com/"},
            )
            html = r.text
            # embedded JS objects: {"domain":..,"domain_id":..,"id":..,"image_url":..,"top":..}
            for m in re.finditer(
                r'"id"\s*:\s*"?(\d+)"?.*?"image_url"\s*:\s*"(.*?)"', html
            ):
                page = m.group(2).replace("\\/", "/")
                if "//" in page and page not in [o.get("href") for o in out]:
                    out.append({"name": "", "href": page, "confidence": 0.28})
            if not out:
                # match blocks contain "backlink" urls
                for u in re.findall(r'"backlink"\s*:\s*"(https?[^"]*)"', html):
                    u = u.replace("\\/", "/")
                    if u not in [o.get("href") for o in out]:
                        out.append({"name": "", "href": u, "confidence": 0.28})
            # image page links
            for u in re.findall(
                r"https?://(?:www\.)?tineye\.com/search\?.*?full=(https?[^&\"']+)", html
            ):
                u = urllib.parse.unquote(u).replace("\\/", "/")
                if u.startswith("http") and u not in [o.get("href") for o in out]:
                    out.append({"name": "", "href": u, "confidence": 0.3})
    except Exception as e:
        logger.debug(f"tineye error: {e}")
    return _dedup([_norm(h, "tineye") for h in out])


# ── Google Lens (best-effort) ────────────────────────────────────────────
async def lens(public_url: str = "") -> list[dict]:
    await _wait("lens")
    out: list[dict] = []
    if not public_url:
        return []
    try:
        client = make_client(30.0)
        async with client:
            r = await client.get(
                "https://lens.google.com/uploadbyurl",
                params={"url": public_url},
                headers={"Referer": "https://lens.google.com/"},
            )
            html = r.text
            # og tags + visual matches (often behind JS; parse what's available)
            og = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', html)
            if og:
                t = og.group(1).strip()
                if re.search(r"[A-Za-z]", t):
                    out.append({"name": _guess(t), "title": t, "confidence": 0.25})
            for t in re.findall(r'"text":"([^"]{6,100})"', html):
                t = urllib.parse.unquote(t)
                if re.search(r"[A-Za-z ]{5,}", t) and len(t) < 100:
                    out.append({"name": _guess(t), "title": t, "confidence": 0.22})
    except Exception as e:
        logger.debug(f"lens error: {e}")
    return _dedup([_norm(h, "lens") for h in out])


# ── orchestrator ─────────────────────────────────────────────────────────
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
    "dating": "Dating",
    "facebook": "Facebook",
    "instagram": "Instagram",
}


def site_name(url: str) -> str:
    try:
        host = urllib.parse.urlparse(url).netloc.lower().replace("www.", "")
        for dom, nice in _SITE_NAMES.items():
            if host == dom or host.endswith("." + dom) or dom in url.lower():
                return nice
        return host.split(":")[0] or "web"
    except Exception:
        return "web"


def _guess(title: str) -> str:
    t = (title or "").strip()
    if "@" in t:
        h = re.search(r"@([A-Za-z0-9_.]{3,40})", t)
        if h:
            return h.group(1).lower()
    cleaned = re.sub(
        r"\b(linkedin|facebook|instagram|twitter|x\.com|vk|tiktok|reddit|profile)\b.*$",
        "",
        t,
        flags=re.I,
    )
    cleaned = re.sub(r"[^A-Za-z' -]", "", cleaned)
    proper = [
        p.strip().title()
        for p in cleaned.split()
        if p.strip() and p.strip()[0].isupper()
    ]
    if len(proper) >= 2:
        return " ".join(proper[:3])
    if proper:
        return proper[0]
    return t[:40]


async def run_engines(
    crop_bytes: bytes,
    public_url: str = "",
    engines: list[str] | None = None,
    log=None,
) -> dict:
    """Run selected engines in parallel. Returns {"hits":[…],"engine_stats":{},"errors":[]}."""
    engines = engines or list(GATES.keys())
    engines = [e for e in engines if e in GATES]
    log = log or (lambda *_: None)
    tasks = {}
    if "yandex" in engines:
        tasks["yandex"] = yandex(public_url=public_url, crop_bytes=crop_bytes)
    if "bing" in engines:
        tasks["bing"] = bing(public_url=public_url, crop_bytes=crop_bytes)
    if "tineye" in engines:
        tasks["tineye"] = tineye(crop_bytes)
    if "lens" in engines:
        tasks["lens"] = lens(public_url=public_url)

    hits: list[dict] = []
    stats: dict[str, int] = {}
    errors: list[str] = []
    for name, coro in tasks.items():
        try:
            r = await asyncio.wait_for(coro, timeout=35)
            stats[name] = len(r)
            log(f"engine {name}: {len(r)} hit(s)")
            hits += r
        except Exception as e:
            errors.append(f"{name}: {e}")
            log(f"engine {name} failed: {e}", "warn")
    for h in hits:
        h["site"] = h.get("site") or site_name(h.get("href") or h.get("thumb") or "")
    return {"hits": _dedup(hits), "engine_stats": stats, "errors": errors}
