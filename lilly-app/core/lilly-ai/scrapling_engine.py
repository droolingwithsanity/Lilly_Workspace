"""scrapling_engine — free, no-API-key web lookup for Lilly and the avatar team.

This module is imported by lilly_ai.py (main chat) and osint_face_lookup.py
(the "scrapidy" tier). It provides:

    should_scrape(query)          -> bool
    async scrape_for_lilly(query) -> {"ok", "answer_context", "citations", ...}
    format_citations(citations)   -> str

Design goals:
  * No API keys, no extra dependencies (httpx only).
  * Strict NSFW / nudity filtering on the query, the results and any page we
    fetch. Adult queries are refused outright and adult results are dropped.
  * Fast enough for the DM typing window (~15-60s) — batches providers with
    asyncio.gather and hard-caps the whole lookup.
"""

import asyncio
import base64
import html as _html
import logging
import re
import urllib.parse
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("scrapling")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
_TIMEOUT = httpx.Timeout(10.0, connect=6.0)
_MAX_CONTEXT_CHARS = 2400

# ── NSFW / nudity filtering ───────────────────────────────────────────────
# Hard block-list. Any query, result or fetched page touching these is dropped.
_NSFW_TERMS = [
    r"porn", r"porno", r"pornograph\w*", r"xxx", r"nsfw", r"nude", r"nudes",
    r"nudity", r"naked", r"topless", r"boobs?", r"tits?", r"nipples?", r"ass",
    r"pussy", r"vagina", r"penis", r"dick", r"cock", r"cum", r"cumshot",
    r"blowjob", r"handjob", r"anal", r"orgasm", r"masturbat\w*", r"hentai",
    r"milf", r"escort", r"hooker", r"prostitut\w*", r"camgirl", r"webcam\s*sex",
    r"onlyfans", r"fansly", r"chaturbate", r"stripchat", r"bongacams", r"cam4",
    r"livejasmin", r"xhamster", r"xvideos", r"xnxx", r"redtube", r"youporn",
    r"spankbang", r"eporner", r"pornhub", r"brazzers", r"fetish", r"bondage",
    r"erotic\w*", r"sex\b", r"sexual\w*", r"sexting", r"dildo", r"vibrator",
    r"stripper", r"strip\s*club", r"lingerie\s*model", r"nipslip", r"upskirt",
    r"creampie", r"deepthroat", r"threesome", r"orgy", r"swinger\w*",
]
_NSFW_RE = re.compile(r"(?:\b|_)(?:" + "|".join(_NSFW_TERMS) + r")(?:\b|_)", re.I)

_NSFW_DOMAINS = {
    "pornhub.com", "xvideos.com", "xnxx.com", "xhamster.com", "redtube.com",
    "youporn.com", "spankbang.com", "eporner.com", "brazzers.com", "onlyfans.com",
    "fansly.com", "chaturbate.com", "stripchat.com", "bongacams.com", "cam4.com",
    "livejasmin.com", "motherless.com", "efukt.com", "hqporner.com", "beeg.com",
    "txxx.com", "tnaflix.com", "porntrex.com", "erome.com", "rule34.xxx",
    "nhentai.net", "hanime.tv", "literotica.com", "adultfriendfinder.com",
    "ashleymadison.com", "seeking.com",
}


def is_nsfw(text: str) -> bool:
    """True if the text contains adult/nudity terms. Conservative: any hit blocks."""
    if not text:
        return False
    return bool(_NSFW_RE.search(text))


def _url_is_nsfw(url: str) -> bool:
    if not url:
        return False
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower().lstrip("www.")
    except Exception:
        host = ""
    if not host:
        return False
    return any(host == d or host.endswith("." + d) for d in _NSFW_DOMAINS)


# ── Lookup-intent detection ───────────────────────────────────────────────
_LOOKUP_HINTS = [
    "price", "prices", "pricing", "cost", "how much", "cheap", "cheapest",
    "deal", "deals", "discount", "coupon", "promo", "sale", "on sale", "offer",
    "best", "top", "review", "reviews", "rating", "ratings", "compare",
    "comparison", "vs", "versus", "worth it", "buy", "where can i", "where to",
    "stock", "in stock", "release", "specs", "specifications", "which",
    "how do i", "how to", "what is", "what's", "who is", "who's", "when is",
    "when did", "where is", "why does", "news", "score", "result", "weather",
    "near me", "open", "opening hours", "recipe", "facts", "statistics",
    "recommend", "suggestion", "options", "look up", "search",
]
# Conversational / emotional messages that should NOT trigger a lookup.
_SKIP_PATTERNS = re.compile(
    r"^\s*(hi|hey|hello|yo|sup|what'?s up|how are you|how'?s it going|"
    r"good (morning|night|evening)|you (ok|okay|good)|i (love|miss|like) you|"
    r"thank you|thanks|ty|lol|haha|ok|okay|k|bye|goodnight)\b",
    re.I,
)


def should_scrape(query: str) -> bool:
    """Heuristic: does this message actually need live web data?"""
    if not query:
        return False
    q = query.strip()
    if len(q) < 8:
        return False
    if is_nsfw(q):
        return False
    if _SKIP_PATTERNS.match(q) and "?" not in q and not any(
        h in q.lower() for h in ("price", "deal", "cost", "best", "buy", "where")
    ):
        return False
    low = q.lower()
    if any(h in low for h in _LOOKUP_HINTS):
        return True
    # A direct question about a concrete thing usually wants an answer.
    if "?" in q and len(q.split()) >= 4:
        return True
    return False


# ── HTML helpers ──────────────────────────────────────────────────────────
_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<(script|style|noscript|svg)\b.*?</\1>", re.I | re.S)
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_PRICE_RE = re.compile(
    r"(?:[$£€]\s?\d[\d,]*(?:\.\d{1,2})?|\b\d[\d,]*(?:\.\d{1,2})?\s?(?:USD|CAD|GBP|EUR|dollars?)\b)"
)


def _strip_html(raw: str) -> str:
    if not raw:
        return ""
    raw = _SCRIPT_RE.sub(" ", raw)
    raw = _TAG_RE.sub(" ", raw)
    raw = _html.unescape(raw)
    raw = _WS_RE.sub(" ", raw)
    return re.sub(r"\s{2,}", " ", raw).strip()


def _clean(text: str, limit: int = 0) -> str:
    t = _strip_html(text or "")
    if limit and len(t) > limit:
        t = t[:limit].rsplit(" ", 1)[0] + "…"
    return t


def _prices(text: str, n: int = 4) -> List[str]:
    out: List[str] = []
    for p in _PRICE_RE.findall(text or ""):
        p = p.strip()
        if p and p not in out:
            out.append(p)
        if len(out) >= n:
            break
    return out


def _rank_prices(prices: List[str], n: int = 8) -> List[str]:
    """Order scraped prices so the most likely *product* price comes first.

    Pages mix the item with its accessories (a $12 case on the same AirPods
    page), so the raw minimum is often junk. The most frequently seen price is
    usually the product; accessory-level outliers well below it are dropped.
    """
    buckets: List[tuple] = []
    for p in prices or []:
        m = re.search(r"\d[\d,]*(?:\.\d{1,2})?", p)
        if not m:
            continue
        try:
            v = float(m.group(0).replace(",", ""))
        except Exception:
            continue
        buckets.append((int(round(v)), p))
    if not buckets:
        return prices[:n]
    counts: Dict[int, int] = {}
    for b, _ in buckets:
        counts[b] = counts.get(b, 0) + 1
    if len(counts) == 1:
        return [buckets[0][1]][:n]
    corroborated = [b for b, c in counts.items() if c >= 2]
    if not corroborated:
        # Several one-off prices and no agreement: almost certainly a mix of
        # the product and its accessories. Don't guess — report no price.
        return []
    corroborated.sort(key=lambda b: (-counts[b], b))
    seen, out = set(), []
    for target in corroborated:
        for b, p in buckets:
            if b == target and b not in seen:
                seen.add(b)
                out.append(p)
                break
        if len(out) >= n:
            break
    return out


# ── Providers ─────────────────────────────────────────────────────────────
def _real_ddg(href: str) -> str:
    if "uddg=" in href:
        try:
            q = urllib.parse.urlparse(href).query
            return urllib.parse.parse_qs(q).get("uddg", [href])[0]
        except Exception:
            return href
    return href


def _real_bing(href: str) -> str:
    m = re.search(r"[?&]u=a1([^&]+)", href)
    if m:
        b = m.group(1).replace("-", "+").replace("_", "/")
        b += "=" * (-len(b) % 4)
        try:
            return base64.b64decode(b).decode("utf-8", "ignore")
        except Exception:
            return href
    return href


def _ddg_ads(url: str) -> bool:
    return "duckduckgo.com/y.js" in url or "/duckduckgo-help-pages/" in url


def _parse_ddg(raw: str, limit: int) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    tokens = []
    pat = re.compile(
        r"<a\b[^>]*class=['\"]?result-link['\"]?[^>]*>(.*?)</a>"
        r"|<td[^>]*class=['\"]?result-snippet['\"]?[^>]*>(.*?)</td>",
        re.S | re.I,
    )
    for m in pat.finditer(raw or ""):
        if m.group(1) is not None:
            tokens.append(("link", m.group(0)))
        else:
            tokens.append(("snip", m.group(2)))
    for i, (kind, val) in enumerate(tokens):
        if kind != "link":
            continue
        hm = re.search(r"""href=["']([^"']+)["']""", val)
        if not hm:
            continue
        url = _real_ddg(_html.unescape(hm.group(1)))
        if not url.startswith("http") or _ddg_ads(url):
            continue
        title = _clean(val, 160)
        snippet = ""
        for j in range(i + 1, min(i + 4, len(tokens))):
            if tokens[j][0] == "snip":
                snippet = _clean(tokens[j][1], 320)
                break
        out.append({"title": title, "url": url, "snippet": snippet})
        if len(out) >= limit:
            break
    return out


def _parse_bing(raw: str, limit: int) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for block in re.findall(r'<li class="b_algo".*?</li>', raw or "", re.S):
        hm = re.search(r"<h2[^>]*>(.*?)</h2>", block, re.S)
        if not hm:
            continue
        am = re.search(r"""href=["']([^"']+)["']""", hm.group(1))
        if not am:
            continue
        url = _real_bing(_html.unescape(am.group(1)))
        if not url.startswith("http"):
            continue
        title = _clean(hm.group(1), 160)
        sm = re.search(r"<p[^>]*>(.*?)</p>", block, re.S)
        snippet = _clean(sm.group(1), 320) if sm else ""
        out.append({"title": title, "url": url, "snippet": snippet})
        if len(out) >= limit:
            break
    return out


def _parse_bing_rss(raw: str, limit: int) -> List[Dict[str, str]]:
    """Parse Bing's RSS output — direct URLs, no ck/a redirects, hard to gate."""
    out: List[Dict[str, str]] = []
    for block in re.findall(r"<item>(.*?)</item>", raw or "", re.S):
        tm = re.search(r"<title>(.*?)</title>", block, re.S)
        lm = re.search(r"<link>(.*?)</link>", block, re.S)
        dm = re.search(r"<description>(.*?)</description>", block, re.S)
        if not (tm and lm):
            continue
        url = _html.unescape(lm.group(1)).strip()
        if not url.startswith("http"):
            continue
        out.append({
            "title": _clean(_html.unescape(tm.group(1)), 160),
            "url": url,
            "snippet": _clean(_html.unescape(dm.group(1)), 320) if dm else "",
        })
        if len(out) >= limit:
            break
    return out


async def _ddg_lite(client: httpx.AsyncClient, query: str, limit: int) -> List[Dict[str, str]]:
    url = "https://lite.duckduckgo.com/lite/"
    r = await client.get(url, params={"q": query}, headers=_HEADERS)
    if r.status_code == 200 and "result-link" in r.text:
        return _parse_ddg(r.text, limit)
    return []


async def _bing_rss(client: httpx.AsyncClient, query: str, limit: int) -> List[Dict[str, str]]:
    h = dict(_HEADERS)
    h["Accept"] = "application/rss+xml,application/xml;q=0.9,*/*;q=0.8"
    r = await client.get(
        "https://www.bing.com/search",
        params={"q": query, "format": "rss", "count": "20"},
        headers=h,
    )
    if r.status_code == 200 and "<item>" in r.text:
        return _parse_bing_rss(r.text, limit)
    return []


async def _bing(client: httpx.AsyncClient, query: str, limit: int) -> List[Dict[str, str]]:
    r = await client.get(
        "https://www.bing.com/search",
        params={"q": query, "setlang": "en"},
        headers=_HEADERS,
    )
    if r.status_code == 200:
        return _parse_bing(r.text, limit)
    return []


async def _wiki(client: httpx.AsyncClient, query: str, keys: Optional[List[str]] = None) -> Optional[Dict[str, str]]:
    try:
        r = await client.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "format": "json",
                "srlimit": 5,
                "srprop": "snippet",
            },
            headers={"User-Agent": USER_AGENT},
        )
        if r.status_code != 200:
            return None
        hits = (r.json().get("query") or {}).get("search") or []
        if not hits:
            return None

        def mk(hit):
            t = hit.get("title", "")
            return {
                "title": f"{t} — Wikipedia",
                "url": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(t.replace(" ", "_")),
                "snippet": _strip_html(hit.get("snippet", "")),
                "_raw_title": t,
            }

        cands = [mk(h) for h in hits if h.get("title")]
        # Prefer a hit whose title/snippet actually contains the query phrase
        # (avoids e.g. "Capital punishment in France" for "capital of france").
        if keys:
            for c in cands:
                if _phrase_ok(keys, c) or _phrase_ok(keys, {"title": c["_raw_title"], "snippet": ""}):
                    return c
        return cands[0] if cands else None
    except Exception:
        return None


async def _fetch_page(client: httpx.AsyncClient, url: str) -> str:
    try:
        r = await client.get(url, headers=_HEADERS)
        ctype = r.headers.get("content-type", "")
        if r.status_code != 200 or "html" not in ctype:
            return ""
        return _clean(r.text, 80000)
    except Exception:
        return ""


_PRICE_INTENT_RE = re.compile(
    r"\b(price|prices|cost|how much|deal|deals|cheap|cheapest|discount|coupon|"
    r"sale|buy|worth it|afford)\b",
    re.I,
)
_PRODUCT_HINT_RE = re.compile(
    r"(/product|/p/|/shop|/buy|buy-|/dp/|/ip/|/proddetail|/item)", re.I
)
_RETAIL_HOSTS = {
    "apple.com", "bestbuy.ca", "bestbuy.com", "amazon.ca", "amazon.com",
    "costco.ca", "walmart.ca", "walmart.com", "canadacomputers.com",
    "staples.ca", "thesource.ca", "newegg.ca", "newegg.com", "indigo.ca",
    "sportchek.ca", "mec.ca", "memoryexpress.com", "visions.ca", "bhphotovideo.com",
}


def _product_like(url: str) -> bool:
    """True for URLs that are likely real product pages (so prices live in HTML)."""
    try:
        p = urllib.parse.urlparse(url)
        host = (p.hostname or "").lower().lstrip("www.")
        if any(host == h or host.endswith("." + h) for h in _RETAIL_HOSTS):
            return True
        return bool(_PRODUCT_HINT_RE.search(p.path or ""))
    except Exception:
        return False


def _scrub(results: List[Dict[str, str]]) -> List[Dict[str, str]]:
    clean: List[Dict[str, str]] = []
    seen = set()
    for r in results:
        url = r.get("url", "")
        if not url or url in seen:
            continue
        if _url_is_nsfw(url):
            continue
        if is_nsfw(r.get("title", "")) or is_nsfw(r.get("snippet", "")):
            continue
        seen.add(url)
        clean.append(r)
    return clean


# ── Relevance ranking ─────────────────────────────────────────────────────
_STOP = {
    "best", "top", "good", "great", "cheap", "cheapest", "price", "prices",
    "pricing", "cost", "deal", "deals", "discount", "coupon", "promo", "sale",
    "the", "a", "an", "for", "in", "of", "to", "is", "are", "was", "were", "be",
    "who", "what", "when", "where", "why", "how", "much", "many", "me", "i",
    "you", "your", "my", "we", "do", "does", "did", "can", "could", "should",
    "would", "will", "on", "at", "and", "or", "with", "it", "this", "that",
    "from", "get", "buy", "find", "looking", "need", "want", "please", "now",
    "today", "review", "reviews", "vs", "versus", "near", "about", "for",
    "have", "has", "any", "some", "there", "here", "right", "just", "really",
    "whats", "whatre", "whatsapp", "hey", "hi",
}


def _content_words(text: str) -> List[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return [w for w in words if len(w) > 2 and w not in _STOP]


def _score(query_words: List[str], item: Dict[str, str]) -> float:
    if not query_words:
        return 0.0
    title = (item.get("title") or "").lower()
    snippet = (item.get("snippet") or "").lower()
    blob = title + " " + snippet
    score = 0.0
    for w in query_words:
        if w in title:
            score += 2.0
        elif w in snippet:
            score += 1.0
        elif w in blob:
            score += 0.5
    # Whole-phrase bonus (e.g. "airpods pro").
    phrase = " ".join(query_words)
    if phrase and phrase in blob:
        score += 2.0
    return score


def _phrase_keys(norm: str) -> List[str]:
    """Adjacent word pairs (and single words) from a normalized query, used to
    reject results that merely share a word (Bing returns 'Air Canada' for
    'air fryer canada' — no 'air fryer' phrase anywhere)."""
    toks = re.findall(r"[a-z0-9]+", (norm or "").lower())
    if len(toks) >= 2:
        return [f"{a} {b}" for a, b in zip(toks, toks[1:])]
    return toks


def _phrase_ok(keys: List[str], item: Dict[str, str]) -> bool:
    if not keys:
        return True
    blob = ((item.get("title") or "") + " " + (item.get("snippet") or "")).lower()
    return any(k in blob for k in keys)


def _dedupe_by_domain(items: List[Dict[str, str]], per_domain: int = 2) -> List[Dict[str, str]]:
    counts: Dict[str, int] = {}
    out: List[Dict[str, str]] = []
    for r in items:
        try:
            host = (urllib.parse.urlparse(r.get("url", "")).hostname or "").lower()
        except Exception:
            host = ""
        host = host.lstrip("www.")
        if host and counts.get(host, 0) >= per_domain:
            continue
        counts[host] = counts.get(host, 0) + 1
        out.append(r)
    return out


# Conversational filler that wrecks search relevance (e.g. Bing reads a leading
# "whats" as the WhatsApp brand and returns WhatsApp results for "whats the best
# price for airpods pro"). Strip it down to the actual subject before searching.
_FILLER_PREFIX_RE = re.compile(
    r"^\s*(?:hey|hi|hello|yo|so|ok|okay|please|pls|can you|could you|would you|"
    r"do you know|tell me|i want to know|i wanna know|i need to know|"
    r"i was wondering|whats|what's|what is|what are|who is|who's|where is|"
    r"wheres|where's|how much is|how much are|how much|is it|are there|"
    r"any idea|any)\b[\s,:?!.-]*",
    re.I,
)
_FILLER_TAIL_RE = re.compile(
    r"[\s,:.?!-]*(?:right now|these days|at the moment|for me|please|pls|"
    r"thanks|thank you|rn|lol|haha)\s*[?.!]*\s*$",
    re.I,
)
# Search engines key on these words and derail (Bing returns dictionary hits
# for "best" / Best Buy for "best price ..."). Intent is tracked separately by
# _PRICE_INTENT_RE, so the words themselves are dead weight in the query.
_NOISE_WORD_RE = re.compile(
    r"\b(?:best|cheapest|cheaper|cheap|top|good|great|affordable|budget|"
    r"the|whats|what)\b",
    re.I,
)


def _normalize_query(q: str) -> str:
    """Reduce a chatty question to a clean search query."""
    original = (q or "").strip()
    s = original
    prev = None
    while s and s != prev:
        prev = s
        s = _FILLER_PREFIX_RE.sub("", s)
    s = _NOISE_WORD_RE.sub(" ", s)
    prev = None
    while s and s != prev:
        prev = s
        s = _FILLER_TAIL_RE.sub("", s)
    s = re.sub(r"\s+", " ", s).strip(" ,:;.-?!")
    return s if len(s) >= 2 else original


# ── Public API ────────────────────────────────────────────────────────────
async def scrape_for_lilly(
    query: str,
    limit: int = 5,
    fetch_top: int = 2,
    timeout: float = 14.0,
) -> Dict[str, Any]:
    """Look up live web data for a query. Returns answer_context + citations.

    Adult/nudity queries are refused and any adult results are stripped.
    """
    q = (query or "").strip()
    if not q:
        return {"ok": False, "error": "empty query", "answer_context": "", "citations": []}
    if is_nsfw(q):
        logger.info("scrapling: refused NSFW query")
        return {
            "ok": False,
            "blocked": True,
            "error": "nsfw query refused",
            "answer_context": "",
            "citations": [],
        }

    async def _run() -> tuple:
        # Search engines reward keyword-shaped queries and choke on chatty ones
        # ("whats the best price for X" -> junk), so search on the noun keywords.
        price_intent = bool(_PRICE_INTENT_RE.search(q))
        norm = _normalize_query(q)
        words = _content_words(norm)
        keys = _phrase_keys(norm)
        sq = " ".join(words[:10]) if words else norm
        logger.debug("scrapling: query %r -> search %r", q[:60], sq[:60])
        async with httpx.AsyncClient(
            timeout=_TIMEOUT, follow_redirects=True, http2=False
        ) as client:
            tasks = [
                _ddg_lite(client, sq, limit),
                _bing(client, sq, limit),
                _bing_rss(client, sq, limit),
            ]
            if not price_intent:
                # Wikipedia is authoritative for factual questions but noise for
                # shopping/price ones (it returns unrelated product articles).
                tasks.append(_wiki(client, sq, keys))
            gathered = await asyncio.gather(*tasks, return_exceptions=True)

            results: List[Dict[str, str]] = []

            def add(items, src):
                for it in items or []:
                    if isinstance(it, dict) and it.get("url"):
                        it["_src"] = src
                        results.append(it)

            add(gathered[0] if isinstance(gathered[0], list) else [], "ddg")
            add(gathered[1] if isinstance(gathered[1], list) else [], "bing")
            add(gathered[2] if isinstance(gathered[2], list) else [], "bing")
            if len(gathered) > 3 and isinstance(gathered[3], dict):
                add([gathered[3]], "wiki")

            results = _scrub(results)

            # Rank by keyword overlap, then keep only results whose text actually
            # contains the query phrase. Wikipedia is exempt (its snippets
            # paraphrase). A wrong answer is worse than no answer, so when no
            # search hit is on-phrase we drop search results entirely.
            for r in results:
                r["_score"] = _score(words, r)
            scored = [r for r in results if r["_score"] > 0]
            search_hits = [r for r in scored if r.get("_src") != "wiki" and _phrase_ok(keys, r)]
            wiki_hits = [r for r in scored if r.get("_src") == "wiki"]
            scored = search_hits + wiki_hits if search_hits else wiki_hits
            scored.sort(key=lambda x: x["_score"], reverse=True)
            results = _dedupe_by_domain(scored)[: max(limit, 6)]

            # Enrich with real page text for the top results (price/spec details).
            # For shopping/price queries, prefer actual product pages — those
            # carry the price in their HTML; collection/category pages usually
            # render prices with JavaScript and show nothing to a scraper.
            fetch_pool = results
            if price_intent:
                prod = [r for r in results if _product_like(r.get("url", ""))]
                rest = [r for r in results if r not in prod]
                fetch_pool = prod + rest
            n_fetch = max(fetch_top, 5) if price_intent else max(1, fetch_top)
            pages = fetch_pool[:n_fetch]
            fetched = await asyncio.gather(
                *[_fetch_page(client, p["url"]) for p in pages],
                return_exceptions=True,
            )
            prices: List[str] = []
            for p, body in zip(pages, fetched):
                if isinstance(body, str) and body and not is_nsfw(body):
                    p["page_text"] = body
                    if price_intent:
                        for pr in _prices(body, 10):
                            if pr not in prices:
                                prices.append(pr)
            if price_intent:
                for r in results:
                    for pr in _prices(r.get("snippet", ""), 4):
                        if pr not in prices:
                            prices.append(pr)
                prices = _rank_prices(prices)
            return results, prices

    try:
        results, prices = await asyncio.wait_for(_run(), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("scrapling: lookup timed out for %r", q[:60])
        return {"ok": False, "error": "timeout", "answer_context": "", "citations": [], "prices": []}
    except Exception as e:
        logger.warning("scrapling: lookup failed: %s", e)
        return {"ok": False, "error": str(e), "answer_context": "", "citations": [], "prices": []}

    citations: List[Dict[str, str]] = []
    lines: List[str] = []
    if prices:
        lines.append(f"PRICES FOUND: {', '.join(prices[:8])}")
    for r in results[:limit]:
        title = r.get("title", "")
        url = r.get("url", "")
        snippet = r.get("snippet", "")
        lines.append(f"- {title} ({url})\n  {snippet}")
        citations.append({"title": title, "url": url, "snippet": snippet})

    context = "\n".join(lines)[:_MAX_CONTEXT_CHARS]
    return {
        "ok": bool(context),
        "query": q,
        "answer_context": context,
        "citations": citations,
        "prices": prices[:8],
        "count": len(citations),
    }


def format_citations(citations: List[Dict[str, str]]) -> str:
    if not citations:
        return ""
    out = ["Sources:"]
    for c in citations[:6]:
        t = (c.get("title") or "").strip()
        u = (c.get("url") or "").strip()
        if not u:
            continue
        out.append(f"- {t} — {u}" if t else f"- {u}")
    return "\n".join(out) if len(out) > 1 else ""
