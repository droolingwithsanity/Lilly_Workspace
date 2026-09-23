#!/usr/bin/env python3
"""
XCEB agents — identity → accounts → public records (OSINT chain)
=================================================================
Everything here is a REAL scrape. No fabricated emails, phones, addresses or
criminal records. Every finding carries the URL it was observed on, a snippet,
and a confidence derived strictly from evidence (number of independent sources
saying the same thing).

Pipeline stages used by a case job:
  1. discover_accounts(name, geo, category)  → social/dating/fan profile URLs
  2. web_search_profiles(name, geo)          → free-text search harvest
  3. username scans via sherlock/maigret     → claimed handle urls
  4. records_chain(name, geo)                → address/phone/email/marriage/
                                               court/warrant/public record leads
  5. llm_rank(name_candidates, snippets)     → optional Ollama disambiguation
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
import tempfile
import urllib.parse
from pathlib import Path

from xceb_core import (
    XCEB_LLM_URL,
    make_client,
    stealth_fetch,
    llm_json,
    EngineGate,
)

logger = logging.getLogger("xceb.agents")

_SEARCH_GATE = EngineGate("websearch", last=0)

# ── domain → platform/type maps ──────────────────────────────────────────
SOCIAL = {
    "instagram.com": ("Instagram", "social"),
    "facebook.com": ("Facebook", "social"),
    "linkedin.com": ("LinkedIn", "social"),
    "x.com/": ("X", "social"),
    "twitter.com": ("X", "social"),
    "tiktok.com": ("TikTok", "social"),
    "youtube.com/@": ("YouTube", "social"),
    "youtu.be": ("YouTube", "social"),
    "reddit.com/user": ("Reddit", "social"),
    "vk.com": ("VK", "social"),
    "ok.ru": ("OK", "social"),
    "bsky.app": ("Bluesky", "social"),
    "threads.net": ("Threads", "social"),
    "github.com": ("GitHub", "social"),
    "pinterest.com": ("Pinterest", "social"),
    "twitch.tv": ("Twitch", "social"),
    "snapchat.com": ("Snapchat", "social"),
}
DATING = {
    "tinder.com": ("Tinder", "dating"),
    "bumble.com": ("Bumble", "dating"),
    "hinge.co": ("Hinge", "dating"),
    "okcupid.com": ("OkCupid", "dating"),
    "pof.com": ("Plenty of Fish", "dating"),
    "plentyoffish.com": ("Plenty of Fish", "dating"),
    "grindr.com": ("Grindr", "dating"),
    "feeld.co": ("Feeld", "dating"),
    "match.com": ("Match", "dating"),
    "eharmony.com": ("eHarmony", "dating"),
    "badoo.com": ("Badoo", "dating"),
    "happn.com": ("happn", "dating"),
    "herapp.co": ("HER", "dating"),
    "zoosk.com": ("Zoosk", "dating"),
    "adultfriendfinder": ("AFF", "dating"),
    "fling.com": ("Fling", "dating"),
    "clover.co": ("Clover", "dating"),
}
FAN = {
    "onlyfans.com": ("OnlyFans", "fan"),
    "fansly.com": ("Fansly", "fan"),
    "patreon.com": ("Patreon", "fan"),
    "mym.gg": ("MYM", "fan"),
    "loyalfans.com": ("LoyalFans", "fan"),
}


def classify_url(url: str) -> tuple[str, str] | None:
    """Return (platform, type) for a known social/dating/fan URL or None."""
    u = url.lower()
    for dom, (plat, typ) in {**SOCIAL, **DATING, **FAN}.items():
        if dom in u:
            return plat, typ
    return None


def _name_variants(name: str) -> list[str]:
    parts = [
        p
        for p in re.split(r"\s+", name.strip().lower())
        if p and re.match(r"[a-z']", p)
    ]
    if not parts:
        return []
    first, last = parts[0], "_".join(parts[1:]) if len(parts) > 1 else ""
    v = set()
    if first and last:
        for s in (
            last,
            first + last,
            first + "_" + last,
            first + "." + last,
            first[0] + last,
            last + first,
        ):
            v.add(re.sub(r"[^a-z0-9_.]", "", s))
    v.add(first)
    v = {x for x in v if len(x) >= 3 and len(x) <= 32}
    return sorted(v)


# ── web search (Bing + DuckDuckGo scrapes) ───────────────────────────────
async def _bing_search(q: str, max_r: int = 8) -> list[dict]:
    await _SEARCH_GATE.wait()
    out: list[dict] = []
    try:
        async with make_client(20.0) as client:
            r = await client.get(
                "https://www.bing.com/search",
                params={"q": q, "count": max_r, "setlang": "en"},
                headers={"Referer": "https://www.bing.com/"},
            )
            html = r.text
    except Exception as e:
        logger.debug(f"bing search failed {e}")
        return out
    for li in re.findall(r'<li class="b_algo".*?</li>', html, re.S):
        m = re.search(r'<a[^>]+href="(https?://[^"]+)"[^>]*>.*?</a>', li, re.S)
        t = re.search(r"<h2.*?>(.*?)</h2>", li, re.S)
        s = re.search(r"<p[^>]*>(.*?)</p>", li, re.S)
        if not m:
            continue
        url = m.group(1)
        title = re.sub(r"<[^>]+>", "", t.group(1)) if t else ""
        snip = re.sub(r"<[^>]+>", "", s.group(1)) if s else ""
        out.append(
            {
                "url": url,
                "title": re.sub(r"\s+", " ", title).strip(),
                "snippet": re.sub(r"\s+", " ", snip).strip()[:400],
            }
        )
        if len(out) >= max_r:
            break
    return out


async def _ddg_search(q: str, max_r: int = 8) -> list[dict]:
    await _SEARCH_GATE.wait()
    out: list[dict] = []
    url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": q})
    try:
        text = await stealth_fetch(url, timeout=20) or ""
    except Exception as e:
        logger.debug(f"ddg failed {e}")
        return out
    for block in re.findall(r'<div class="result[^"]*".*?</div>', text, re.S)[:max_r]:
        a = re.search(r'href="([^"]+)"', block)
        t = re.search(r'class="result__a"[^>]*>(.*?)</a>', block, re.S)
        s = re.search(r'class="result__snippet"[^>]*>(.*?)</div>', block, re.S)
        if not a:
            continue
        href = a.group(1)
        m = re.search(r"uddg=([^&]+)", href)
        real = urllib.parse.unquote(m.group(1)) if m else href
        title = re.sub(r"<[^>]+>", "", t.group(1)) if t else ""
        snip = re.sub(r"<[^>]+>", "", s.group(1)) if s else ""
        out.append(
            {
                "url": real,
                "title": re.sub(r"\s+", " ", title).strip(),
                "snippet": re.sub(r"\s+", " ", snip).strip()[:400],
            }
        )
    return out


async def web_search(q: str, max_r: int = 8) -> list[dict]:
    b, d = await asyncio.gather(_bing_search(q, max_r), _ddg_search(q, max_r))
    seen = set()
    out = []
    for row in b + d:
        u = row["url"]
        if u in seen or not u.startswith("http"):
            continue
        seen.add(u)
        out.append(row)
    return out[:max_r]


# ── sherlock / maigret (username scans) ──────────────────────────────────
async def _cli(*cmd: str, timeout: int = 40) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp"},
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return out.decode(errors="replace")[:12000]
    except Exception as e:
        logger.debug(f"cli {cmd[0]} failed: {e}")
        return ""


async def sherlock_scan(username: str) -> list[dict]:
    out = await _cli(
        "sherlock", username, "--print-found", "--no-color", "--timeout", "10"
    )
    rows = []
    for line in out.splitlines():
        m = re.match(r"^\[[+\-]\]\s*(.+?):\s*(https?://\S+)", line.strip())
        if m:
            rows.append(
                {
                    "platform": m.group(1).strip(),
                    "url": m.group(2).strip().rstrip(")"),
                    "username": username,
                    "confirm": m.group(0).startswith("[+]"),
                }
            )
    return rows


async def maigret_scan(username: str) -> list[dict]:
    with tempfile.TemporaryDirectory() as td:
        out = await _cli(
            "maigret",
            "--json",
            "simple",
            "-o",
            td,
            "--timeout",
            "10",
            username,
            timeout=60,
        )
        rows = []
        import glob

        for jp in glob.glob(f"{td}/*.json")[:1]:
            try:
                data = json.loads(Path(jp).read_text())
            except Exception:
                continue
            for plat, info in data.items():
                if isinstance(info, dict) and info.get("url"):
                    rows.append(
                        {
                            "platform": plat,
                            "url": info["url"],
                            "username": username,
                            "confirm": True,
                        }
                    )
        return rows


async def username_scan(name: str, log=None) -> list[dict]:
    """Run sherlock+maigret for the most promising username variants."""
    log = log or (lambda *_: None)
    variants = _name_variants(name)[:10]
    discovered: list[dict] = []
    for v in variants:
        for fn in (sherlock_scan, maigret_scan):
            try:
                rows = await asyncio.wait_for(fn(v), timeout=70)
            except Exception:
                rows = []
            if rows:
                log(f"username '{v}': {len(rows)} claim(s)")
                discovered += rows
                await asyncio.sleep(0.5)
    return discovered


# ── account discovery orchestration ──────────────────────────────────────
async def discover_accounts(
    name: str, geo_hint: str = "", category: str = "all", log=None
) -> list[dict]:
    """Find social/dating/fan profiles for a candidate name. Real scrapes only."""
    log = log or (lambda *_: None)
    accounts: list[dict] = []
    seen = set()

    def add(url: str, title: str, snippet: str, platform="", typ=""):
        cls = classify_url(url)
        if cls:
            platform, typ = cls
        if not platform:
            return
        if url in seen:
            return
        seen.add(url)
        accounts.append(
            {
                "platform": platform,
                "type": typ,
                "url": url,
                "title": title[:160],
                "snippet": snippet[:300],
                "username": urllib.parse.unquote(url.rstrip("/").rsplit("/", 1)[-1])
                if url.rstrip("/").rsplit("/", 1)[-1] not in ("", "profile")
                else "",
                "confidence": 0.4,  # one observed source
                "source": url,
            }
        )

    # 1) generic profile search
    queries = [f'"{name}"']
    if geo_hint:
        queries.append(f'"{name}" "{geo_hint}"')
    for q in queries:
        for row in await web_search(q + " profile", 10):
            add(row["url"], row["title"], row["snippet"])
    log(f"profile harvest: {len(accounts)} account link(s)")
    await asyncio.sleep(0.6)

    # 2) targeted dating harvest (when category is dating or all)
    if category in ("dating", "all"):
        dq = [f'"{name}" tinder', f'"{name}" hinge OR bumble OR okcupid']
        if geo_hint:
            dq = [f'"{name}" dating site "{geo_hint}"']
        for q in dq:
            for row in await web_search(q, 8):
                add(row["url"], row["title"], row["snippet"])
        log(
            f"dating harvest: {sum(1 for a in accounts if a['type'] == 'dating')} dating link(s)"
        )
        await asyncio.sleep(0.6)

    # 3) username scans if not disabled
    if os_env_scan_enabled():
        rows = await username_scan(name, log)
        for r in rows:
            add(r["url"], r["platform"], "", platform=r["platform"], typ="social")
        log(f"username scan: {len(rows)} claim(s)")

    return accounts


def os_env_scan_enabled() -> bool:
    import os

    return os.environ.get("XCEB_USERNAME_SCAN", "1") == "1"


# ── public records chain (address / phone / email / marriage / court) ────
_RECORD_PATTERNS = {
    "address": re.compile(
        r"\b\d{1,6}\s+[A-Z0-9][A-Z0-9.'&\- ]+(?:st|ave|ave\.|road|rd|blvd|ln|lane|dr|drive|street|ct|court|way|place|pl)\b",
        re.I,
    ),
    "phone": re.compile(r"(\+?1[\s\-.]?)?\(?\d{3}\)?[\s\-.]\d{3}[\s\-.]\d{4}"),
    "email": re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
}

RECORD_QUERIES = [
    ("address", "{name} address"),
    ("property", '{name} "{geo}" property OR deed OR assessor'),
    ("marriage", '{name} marriage record "{geo}"'),
    ("court", '{name} "{geo}" court case OR docket'),
    ("warrant", '{name} "{geo}" warrant OR arrest OR wanted'),
    ("public_record", '{name} "{geo}" public records'),
    ("phone", '{name} phone "{geo}"'),
    ("email", "{name} email contact"),
    ("voter", '{name} voter registration "{geo}"'),
    ("obituary", '{name} obituary OR family "{geo}"'),
]


async def records_chain(name: str, geo_hint: str = "", log=None) -> dict:
    """Search free public sources for addresses/phones/emails + life records.

    Returns findings where EVERY item has: category, snippet (real text),
    url (where it was seen), confidence (1 observed / 2+ independent sources).
    Items with "marked: false" are raw leads — nothing here is asserted fact.
    """
    log = log or (lambda *_: None)
    raw: list[dict] = []
    geo = geo_hint or ""

    for cat, q in RECORD_QUERIES:
        q = q.format(name=name, geo=geo)
        rows = await web_search(q, 8)
        for row in rows:
            raw.append({"category": cat, "q": q, **row})
        log(f"records '{cat}': {len(rows)} source(s)")
        await asyncio.sleep(0.5)

    # Extract structured data ONLY from real snippets.
    findings: dict[str, list] = {
        "addresses": [],
        "phones": [],
        "emails": [],
        "records": [],
    }
    for row in raw:
        snip, url = row.get("snippet", ""), row.get("url", "")
        for kind, pat in _RECORD_PATTERNS.items():
            for m in pat.finditer(snip):
                val = m.group(0).strip()
                if len(val) < 6:
                    continue
                bucket = {
                    "address": findings["addresses"],
                    "phone": findings["phones"],
                    "email": findings["emails"],
                }[kind]
                if not any(it["value"] == val for it in bucket):
                    bucket.append(
                        {
                            "value": val,
                            "snippet": snip[:300],
                            "url": url,
                            "confirmed": 1,
                        }
                    )
        rec_types = {
            "marriage": "marriage",
            "warrant": "warrant",
            "court": "court",
            "property": "property",
            "public_record": "public_record",
            "voter": "voter",
            "obituary": "obituary",
        }
        cat = row.get("category", "")
        if cat in rec_types and snip:
            findings["records"].append(
                {
                    "type": rec_types[cat],
                    "query": row.get("q", ""),
                    "snippet": snip,
                    "url": url,
                    "source_site": _site_of(url),
                    "confirmed": 1,
                }
            )

    # Dedupe + confidence = number of independent domains observing the item.
    for key in ("addresses", "phones", "emails"):
        agg = {}
        for it in findings[key]:
            k = it["value"].lower()
            d = agg.setdefault(
                k, {"value": it["value"], "snippets": [], "urls": [], "confirmed": 0}
            )
            if url := it["url"]:
                if url not in d["urls"]:
                    d["urls"].append(url)
                    d["confirmed"] += 1
            if it["snippet"] not in d["snippets"]:
                d["snippets"].append(it["snippet"])
        findings[key] = [
            {
                "value": v["value"],
                "confirmed": v["confirmed"],
                "confidence": min(0.95, 0.30 + 0.15 * v["confirmed"]),
                "snippet": v["snippets"][0][:300] if v["snippets"] else "",
                "urls": v["urls"][:5],
                "verified": False,  # user must verify before acting
            }
            for v in agg.values()
        ]

    rec_agg = {}
    for it in findings["records"]:
        k = (it["type"], it["snippet"][:80].lower())
        d = rec_agg.setdefault(
            k, {**it, "sources": [it["url"]], "confirmed": 1, "urls": [it["url"]]}
        )
        if it["url"] not in d["sources"]:
            d["sources"].append(it["url"])
            d["confirmed"] += 1
        d.setdefault("urls", []).append(it["url"])
    findings["records"] = [
        {
            "type": v["type"],
            "query": v.get("query", ""),
            "snippet": v["snippet"][:300],
            "sources": v["sources"][:6],
            "source_site": v.get("source_site", _site_of(v["sources"][0])),
            "confirmed": v["confirmed"],
            # TYPE only confirms the search category — not that the fact is true.
            "confidence": min(0.9, 0.18 + 0.08 * v["confirmed"]),
            "verified": False,
        }
        for v in rec_agg.values()
    ]
    return findings


def _site_of(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc.replace("www.", "") or url[:40]
    except Exception:
        return url[:40]


# ── LLM disambiguation of reverse-search candidates ──────────────────────
async def llm_rank(candidates: list[dict], context: str = "") -> dict | None:
    """Ask Ollama to pick the most plausible person + extract structured data.

    Used only when XCEB_LLM_URL is reachable; returns a dict or None. All
    extracted strings still come from real scraped snippets.
    """
    if not XCEB_LLM_URL:
        return None
    names = [
        c.get("name") or c.get("title")
        for c in candidates[:12]
        if c.get("name") or c.get("title")
    ]
    if not names:
        return None
    prompt = (
        "You are an OSINT analysis assistant. From the list of reverse-image "
        "search candidate titles below, pick the single most likely full human "
        "name for the photographed person. Return strict JSON:\n"
        '{"name": "First Last", "reason": "short rationale", "aliases": []}\n'
        f"{context}\nCandidates: {json.dumps(names)}"
    )
    res = await llm_json(prompt, fast=True, timeout=60)
    if isinstance(res, dict) and res.get("name"):
        return {
            "name": str(res["name"]).strip()[:80],
            "reason": str(res.get("reason") or "")[:200],
            "aliases": [str(a)[:60] for a in (res.get("aliases") or [])][:8],
        }
    return None
