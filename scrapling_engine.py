"""
Scrapling Engine for Lilly AI
──────────────────────────────
Natural-language → web scrape → cited results.

Usage:
    from scrapling_engine import scrape_for_lilly
    result = await scrape_for_lilly("What's the weather in Dublin?")
    # result = {"answer": "...", "citations": [...], "raw_snippets": [...]}

The engine classifies the user query, picks the right Scrapling fetcher,
extracts readable content, and returns cited results the LLM can weave
into a conversational reply.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import urllib.parse
from dataclasses import dataclass, field, asdict
from typing import Optional

logger = logging.getLogger("scrapling_engine")

# ─── Configuration ──────────────────────────────────────────────────
_SCRAPE_TIMEOUT = 15  # seconds per fetch
_MAX_SNIPPETS = 8  # max content snippets to return
_SNIPPET_MAX_CHARS = 600  # chars per snippet
_MAX_CACHE = 128  # in-memory LRU-ish cache
_CACHE_TTL = 600  # seconds

# ─── Cache ──────────────────────────────────────────────────────────
_cache: dict[str, dict] = {}  # key → {data, ts}


def _cache_key(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def _cache_get(url: str) -> Optional[dict]:
    k = _cache_key(url)
    entry = _cache.get(k)
    if entry and time.time() - entry["ts"] < _CACHE_TTL:
        return entry["data"]
    if entry:
        del _cache[k]
    return None


def _cache_set(url: str, data: dict):
    k = _cache_key(url)
    if len(_cache) >= _MAX_CACHE:
        # evict oldest
        oldest_k = min(_cache, key=lambda x: _cache[x]["ts"])
        del _cache[oldest_k]
    _cache[k] = {"data": data, "ts": time.time()}


# ─── Query Classification ──────────────────────────────────────────
_URL_RE = re.compile(
    r"https?://[^\s<>\"']+",
    re.IGNORECASE,
)

_SEARCH_PATTERNS = [
    # Direct questions that need web lookup
    r"\b(what|who|when|where|why|how|which)\s+(is|are|was|were|do|does|did|has|have|can|could|would|should)\b",
    r"\btell me (about|more)\b",
    r"\bexplain\b",
    r"\blook up\b",
    r"\bsearch (for|up)\b",
    r"\bfind (out|me)\b",
    r"\bgoogle\b",
    r"\bwhat('s| is) (the )?(latest|news|current|weather|price|score|result)\b",
    r"\bhow (much|many|old|far|long|tall|deep|fast)\b",
    r"\bwho (is|was|are|invented|created|founded|won)\b",
    r"\bwhen (did|was|is|will)\b",
    r"\bwhere (is|are|was|can|do)\b",
    r"\bwhy (do|does|did|is|are|would|should)\b",
]

_COMPARE_PATTERNS = [
    r"\bcompare\b",
    r"\bvs\.?\b",
    r"\bversus\b",
    r"\bbetter (than|then)\b",
    r"\bdifference between\b",
    r"\bwhich (is|one) (better|faster|cheaper|newer)\b",
]

_NEWS_PATTERNS = [
    r"\bnews\b",
    r"\bheadlines?\b",
    r"\bwhat('s| is) (happening|new|going on)\b",
    r"\blatest\b",
    r"\brecent\b",
    r"\bupdate[sd]?\b",
]

_LOOKUP_PATTERNS = [
    r"\bdefinition (of|for)\b",
    r"\bwhat (does|do) .+ mean\b",
    r"\bmeaning (of|for)\b",
    r"\btranslate\b",
    r"\bconvert\b",
    r"\bhow (do|does|to)\b",
    r"\brecipe (for|of)\b",
    r"\bstock (price|ticker)\b",
    r"\bexchange rate\b",
]


@dataclass
class ScrapeIntent:
    """Classified intent from a user query."""

    kind: str  # "url", "search", "compare", "news", "lookup", "unknown"
    query: str
    urls: list[str] = field(default_factory=list)
    search_terms: str = ""


def classify_query(text: str) -> ScrapeIntent:
    """Classify a user message into a scrape intent."""
    text_lower = text.lower().strip()

    # Direct URL?
    urls = _URL_RE.findall(text)
    if urls:
        # Check if it's JUST a URL or a URL + question
        has_question = any(
            p in text_lower for p in _SEARCH_PATTERNS + _COMPARE_PATTERNS
        )
        if has_question and len(urls) == 1:
            return ScrapeIntent(kind="search", query=text, urls=urls, search_terms=text)
        return ScrapeIntent(kind="url", query=text, urls=urls)

    # Compare?
    if any(re.search(p, text_lower) for p in _COMPARE_PATTERNS):
        return ScrapeIntent(kind="compare", query=text, search_terms=text)

    # News?
    if any(re.search(p, text_lower) for p in _NEWS_PATTERNS):
        return ScrapeIntent(kind="news", query=text, search_terms=text)

    # Lookup (how-to, definition, conversion)?
    if any(re.search(p, text_lower) for p in _LOOKUP_PATTERNS):
        return ScrapeIntent(kind="lookup", query=text, search_terms=text)

    # General search?
    if any(re.search(p, text_lower) for p in _SEARCH_PATTERNS):
        return ScrapeIntent(kind="search", query=text, search_terms=text)

    return ScrapeIntent(kind="unknown", query=text)


# ─── Scraping Helpers ──────────────────────────────────────────────
def _extract_text_snippet(element, max_chars: int = _SNIPPET_MAX_CHARS) -> str:
    """Extract clean text from a Scrapling element."""
    try:
        text = element.get_text(separator=" ", strip=True)
    except Exception:
        text = str(element)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0] + "…"
    return text


def _extract_readability(page) -> list[dict]:
    """Extract main content from a page using common content selectors."""
    snippets = []

    # Try common main-content selectors (order matters)
    content_selectors = [
        "article",
        "main",
        '[role="main"]',
        ".post-content",
        ".article-content",
        ".entry-content",
        ".content-body",
        ".story-body",
        "#content",
        ".content",
        ".markdown-body",
        "pre code",
    ]

    for sel in content_selectors:
        try:
            elements = page.css(sel)
            for el in elements[:3]:
                text = _extract_text_snippet(el)
                if len(text) > 80:  # meaningful content
                    snippets.append(
                        {
                            "text": text,
                            "selector": sel,
                        }
                    )
        except Exception:
            continue

    # Fallback: grab paragraphs
    if not snippets:
        try:
            paras = page.css("p")
            for p in paras[:10]:
                text = _extract_text_snippet(p)
                if len(text) > 50:
                    snippets.append({"text": text, "selector": "p"})
        except Exception:
            pass

    # Last fallback: body text
    if not snippets:
        try:
            body_text = _extract_text_snippet(page.css("body")[0], max_chars=1200)
            if body_text:
                snippets.append({"text": body_text, "selector": "body"})
        except Exception:
            pass

    return snippets[:_MAX_SNIPPETS]


def _extract_meta(page, url: str) -> dict:
    """Extract page metadata (title, description, etc.)."""
    meta = {"url": url, "title": "", "description": ""}
    try:
        title_el = page.css("title")
        if title_el:
            meta["title"] = _extract_text_snippet(title_el[0], max_chars=200)
    except Exception:
        pass
    try:
        desc_el = page.css('meta[name="description"]')
        if desc_el:
            meta["description"] = desc_el[0].attrib.get("content", "")
    except Exception:
        pass
    try:
        if not meta["description"]:
            og_desc = page.css('meta[property="og:description"]')
            if og_desc:
                meta["description"] = og_desc[0].attrib.get("content", "")
    except Exception:
        pass
    return meta


def _extract_links(page, base_url: str, max_links: int = 10) -> list[dict]:
    """Extract relevant links from page."""
    links = []
    try:
        anchors = page.css("a[href]")
        seen = set()
        for a in anchors:
            href = a.attrib.get("href", "")
            if not href or href.startswith("#") or href.startswith("javascript:"):
                continue
            # Make absolute
            if href.startswith("/"):
                parsed = urllib.parse.urlparse(base_url)
                href = f"{parsed.scheme}://{parsed.netloc}{href}"
            elif not href.startswith("http"):
                continue
            if href in seen:
                continue
            seen.add(href)
            link_text = _extract_text_snippet(a, max_chars=100)
            if link_text and len(link_text) > 3:
                links.append({"url": href, "text": link_text})
                if len(links) >= max_links:
                    break
    except Exception:
        pass
    return links


def _extract_images(page, base_url: str, max_images: int = 8) -> list[str]:
    """Extract image URLs from a page."""
    images = []
    seen = set()
    try:
        for img in page.css("img"):
            src = img.attrib.get("src", "") or img.get("src", "") or ""
            if not src:
                continue
            if src.startswith("//"):
                src = "https:" + src
            elif src.startswith("/"):
                parsed = urllib.parse.urlparse(base_url)
                src = f"{parsed.scheme}://{parsed.netloc}{src}"
            elif src.startswith("data:"):
                continue
            elif not src.startswith("http"):
                continue
            if src in seen:
                continue
            seen.add(src)
            images.append(src)
            if len(images) >= max_images:
                break
    except Exception:
        pass
    return images


# ─── DuckDuckGo Search (no API key needed) ────────────────────────
_DDG_URL = "https://html.duckduckgo.com/html/"


async def _duckduckgo_search(query: str, max_results: int = 5) -> list[dict]:
    """Search DuckDuckGo and return result URLs + snippets."""
    results = []
    try:
        # DuckDuckGo lite/HTML version is scrape-friendly
        encoded = urllib.parse.urlencode({"q": query, "kl": "us-en"})
        url = f"{_DDG_URL}?{encoded}"

        page = None
        try:
            from scrapling.fetchers import Fetcher

            loop = asyncio.get_event_loop()
            page = await loop.run_in_executor(
                None,
                lambda: Fetcher.get(url, timeout=_SCRAPE_TIMEOUT),
            )
        except (ImportError, ModuleNotFoundError):
            # Fallback: use httpx + lxml
            import httpx as _httpx
            from lxml import html as _html

            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            }
            async with _httpx.AsyncClient(
                follow_redirects=True, timeout=_SCRAPE_TIMEOUT
            ) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
            page = _html.fromstring(resp.text)

        # Extract search results — try multiple selector patterns
        links = (
            page.css(".result__a") or page.css("a.result__title") or page.css("a[href]")
        )
        snippets_el = page.css(".result__snippet") or page.css(".result__body")

        for i, link_el in enumerate(links[:max_results]):
            try:
                href = link_el.attrib.get("href", "") or link_el.get("href", "")
                title = _extract_text_snippet(link_el)
                snippet = ""
                if i < len(snippets_el):
                    snippet = _extract_text_snippet(snippets_el[i])

                # DuckDuckGo redirects through their proxy
                if "uddg=" in href:
                    actual = urllib.parse.unquote(href.split("uddg=")[1].split("&")[0])
                elif href.startswith("http"):
                    actual = href
                else:
                    actual = href

                if title and len(title) > 3:
                    results.append(
                        {
                            "url": actual,
                            "title": title,
                            "snippet": snippet,
                        }
                    )
            except Exception:
                continue

    except Exception as e:
        logger.warning(f"DuckDuckGo search failed: {e}")

    return results


# ─── Main Scraper ──────────────────────────────────────────────────
async def _fetch_and_extract(url: str) -> dict:
    """Fetch a URL with Scrapling and extract content."""
    cached = _cache_get(url)
    if cached:
        return cached

    result = {
        "url": url,
        "meta": {},
        "snippets": [],
        "links": [],
        "error": None,
    }

    try:
        try:
            from scrapling.fetchers import Fetcher

            loop = asyncio.get_event_loop()
            page = await loop.run_in_executor(
                None,
                lambda: Fetcher.get(url, timeout=_SCRAPE_TIMEOUT),
            )
            result["meta"] = _extract_meta(page, url)
            result["snippets"] = _extract_readability(page)
            result["links"] = _extract_links(page, url)
            result["images"] = _extract_images(page, url)
        except (ImportError, ModuleNotFoundError):
            # Fallback: use httpx directly + html parsing
            result = await _fetch_fallback(url, result)
    except Exception as e:
        logger.warning(f"Fetch failed for {url}: {e}")
        result["error"] = str(e)

    _cache_set(url, result)
    return result


async def _fetch_fallback(url: str, result: dict) -> dict:
    """Fallback fetcher using httpx + lxml when Scrapling's full stack isn't available."""
    import httpx as _httpx
    from lxml import html as _html

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    }
    async with _httpx.AsyncClient(
        follow_redirects=True, timeout=_SCRAPE_TIMEOUT
    ) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()

    content_type = resp.headers.get("content-type", "")
    if "text/html" not in content_type and "text/" not in content_type:
        result["meta"] = {
            "url": url,
            "title": url,
            "description": f"Non-HTML content: {content_type}",
        }
        return result

    tree = _html.fromstring(resp.text)

    # Title
    title_els = tree.cssselect("title")
    title = title_els[0].text_content().strip() if title_els else ""
    result["meta"] = {"url": url, "title": title, "description": ""}

    # Description
    for sel in ['meta[name="description"]', 'meta[property="og:description"]']:
        desc_els = tree.cssselect(sel)
        if desc_els:
            result["meta"]["description"] = desc_els[0].get("content", "")
            break

    # Content snippets
    snippets = []
    for sel in ["article", "main", '[role="main"]', ".content", ".markdown-body", "p"]:
        els = tree.cssselect(sel)
        for el in els[:3]:
            text = re.sub(r"\s+", " ", el.text_content()).strip()
            if len(text) > 80:
                snippets.append({"text": text[:_SNIPPET_MAX_CHARS], "selector": sel})
        if snippets:
            break

    result["snippets"] = snippets[:_MAX_SNIPPETS]

    # Links
    links = []
    seen = set()
    for a in tree.cssselect("a[href]"):
        href = a.get("href", "")
        if not href or href.startswith("#") or href.startswith("javascript:"):
            continue
        if href.startswith("/"):
            parsed = urllib.parse.urlparse(url)
            href = f"{parsed.scheme}://{parsed.netloc}{href}"
        elif not href.startswith("http"):
            continue
        if href in seen:
            continue
        seen.add(href)
        text = re.sub(r"\s+", " ", a.text_content()).strip()
        if text and len(text) > 3:
            links.append({"url": href, "text": text[:100]})
            if len(links) >= 10:
                break

    result["links"] = links
    return result


async def _scrape_search(intent: ScrapeIntent) -> dict:
    """Search the web and scrape top results."""
    search_results = await _duckduckgo_search(intent.search_terms)

    if not search_results:
        return {
            "kind": "search",
            "query": intent.query,
            "results": [],
            "answer_context": "No search results found.",
            "citations": [],
        }

    # Fetch top results in parallel
    urls_to_fetch = [r["url"] for r in search_results if r.get("url")]
    fetch_tasks = [_fetch_and_extract(url) for url in urls_to_fetch[:5]]
    fetched = await asyncio.gather(*fetch_tasks, return_exceptions=True)

    # Build cited results
    citations = []
    all_snippets = []
    for i, (sr, fetched_data) in enumerate(zip(search_results, fetched)):
        if isinstance(fetched_data, Exception):
            fetched_data = {
                "url": sr["url"],
                "meta": {},
                "snippets": [],
                "error": str(fetched_data),
            }

        citation = {
            "index": i + 1,
            "url": sr["url"],
            "title": sr.get("title") or fetched_data.get("meta", {}).get("title", ""),
            "snippet": sr.get("snippet", ""),
        }
        citations.append(citation)

        # Collect content snippets
        for s in fetched_data.get("snippets", []):
            all_snippets.append(
                {
                    "text": s["text"],
                    "source": sr["url"],
                    "source_title": citation["title"],
                    "index": i + 1,
                }
            )

    # Build answer context for the LLM
    context_parts = []
    for s in all_snippets[:6]:
        context_parts.append(f"[{s['index']}] {s['text']}")

    answer_context = (
        "\n\n".join(context_parts) if context_parts else "No detailed content found."
    )

    return {
        "kind": "search",
        "query": intent.query,
        "items": [
            {
                "title": sr.get("title", ""),
                "url": sr.get("url", ""),
                "snippet": sr.get("snippet", ""),
                "images": fetched_data.get("images", [])
                if not isinstance(fetched_data, Exception)
                else [],
            }
            for sr, fetched_data in zip(search_results, fetched)
            if not isinstance(fetched_data, Exception)
        ],
        "search_results": search_results,
        "answer_context": answer_context,
        "citations": citations,
        "all_snippets": all_snippets[:_MAX_SNIPPETS],
    }


async def _scrape_url(intent: ScrapeIntent) -> dict:
    """Scrape specific URL(s) the user provided."""
    results = []
    for url in intent.urls[:3]:
        data = await _fetch_and_extract(url)
        results.append(data)

    citations = []
    all_snippets = []
    for i, r in enumerate(results):
        meta = r.get("meta", {})
        citation = {
            "index": i + 1,
            "url": r["url"],
            "title": meta.get("title", ""),
            "description": meta.get("description", ""),
        }
        citations.append(citation)

        for s in r.get("snippets", []):
            all_snippets.append(
                {
                    "text": s["text"],
                    "source": r["url"],
                    "source_title": meta.get("title", ""),
                    "index": i + 1,
                }
            )

    context_parts = []
    for s in all_snippets[:6]:
        context_parts.append(f"[{s['index']}] {s['text']}")

    return {
        "kind": "url",
        "query": intent.query,
        "items": [
            {
                "title": r.get("meta", {}).get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("meta", {}).get("description", ""),
                "images": r.get("images", []),
            }
            for r in results
        ],
        "results": results,
        "answer_context": "\n\n".join(context_parts)
        if context_parts
        else "No content extracted.",
        "citations": citations,
        "all_snippets": all_snippets[:_MAX_SNIPPETS],
    }


async def _scrape_compare(intent: ScrapeIntent) -> dict:
    """Compare items — search for each and merge results."""
    # Extract comparison items from the query
    text = intent.query.lower()
    # Common patterns: "compare X and Y", "X vs Y", "X versus Y", "difference between X and Y"
    items = []
    for pattern in [
        r"compare\s+(.+?)\s+and\s+(.+?)(?:\?|$)",
        r"(.+?)\s+vs\.?\s+(.+?)(?:\?|$)",
        r"(.+?)\s+versus\s+(.+?)(?:\?|$)",
        r"difference between\s+(.+?)\s+and\s+(.+?)(?:\?|$)",
        r"which is (?:better|faster|cheaper|newer)\s+(.+?)\s+or\s+(.+?)(?:\?|$)",
    ]:
        m = re.search(pattern, text)
        if m:
            items = [m.group(1).strip(), m.group(2).strip()]
            break

    if len(items) < 2:
        # Fallback: just search the whole query
        return await _scrape_search(intent)

    # Search each item
    search_tasks = [
        _duckduckgo_search(items[0], max_results=3),
        _duckduckgo_search(items[1], max_results=3),
    ]
    results_a, results_b = await asyncio.gather(*search_tasks)

    # Fetch top result for each
    fetch_tasks = []
    if results_a:
        fetch_tasks.append(_fetch_and_extract(results_a[0]["url"]))
    if results_b:
        fetch_tasks.append(_fetch_and_extract(results_b[0]["url"]))

    fetched = await asyncio.gather(*fetch_tasks, return_exceptions=True)

    all_snippets = []
    citations = []
    for i, (fetched_data, item_name) in enumerate(zip(fetched, items)):
        if isinstance(fetched_data, Exception):
            fetched_data = {"url": "", "meta": {}, "snippets": []}

        sr = results_a if i == 0 else results_b
        url = sr[0]["url"] if sr else ""
        title = sr[0].get("title", "") if sr else ""

        citations.append(
            {
                "index": i + 1,
                "url": url,
                "title": title,
                "item": item_name,
            }
        )

        for s in fetched_data.get("snippets", []):
            all_snippets.append(
                {
                    "text": s["text"],
                    "source": url,
                    "source_title": title,
                    "index": i + 1,
                    "item": item_name,
                }
            )

    context_parts = [f"=== {items[0]} ==="]
    for s in all_snippets:
        if s.get("item") == items[0]:
            context_parts.append(f"[{s['index']}] {s['text']}")
    context_parts.append(f"\n=== {items[1]} ===")
    for s in all_snippets:
        if s.get("item") == items[1]:
            context_parts.append(f"[{s['index']}] {s['text']}")

    return {
        "kind": "compare",
        "query": intent.query,
        "items": items,
        "answer_context": "\n".join(context_parts),
        "citations": citations,
        "all_snippets": all_snippets[:_MAX_SNIPPETS],
    }


# ─── Public API ────────────────────────────────────────────────────
async def scrape_for_lilly(query: str) -> dict:
    """
    Main entry point. Takes a natural-language query, classifies intent,
    scrapes the web, and returns cited results.

    Returns:
        {
            "kind": "search"|"url"|"compare"|"news"|"lookup"|"unknown",
            "query": original query,
            "answer_context": text the LLM can use to answer,
            "citations": [{"index": N, "url": ..., "title": ...}],
            "all_snippets": [{"text": ..., "source": ..., "index": N}],
        }
    """
    intent = classify_query(query)

    if intent.kind == "unknown":
        return {
            "kind": "unknown",
            "query": query,
            "answer_context": "",
            "citations": [],
            "all_snippets": [],
        }

    try:
        if intent.kind == "url":
            return await _scrape_url(intent)
        elif intent.kind == "compare":
            return await _scrape_compare(intent)
        else:
            # search, news, lookup all go through search
            return await _scrape_search(intent)
    except Exception as e:
        logger.error(f"Scraping failed for '{query}': {e}")
        return {
            "kind": intent.kind,
            "query": query,
            "answer_context": "",
            "citations": [],
            "all_snippets": [],
            "error": str(e),
        }


def format_citations(citations: list[dict]) -> str:
    """Format citations into a readable string for the LLM context."""
    if not citations:
        return ""
    lines = ["Sources:"]
    for c in citations:
        idx = c.get("index", "?")
        title = c.get("title", "Untitled")
        url = c.get("url", "")
        lines.append(f"  [{idx}] {title} — {url}")
    return "\n".join(lines)


def should_scrape(query: str) -> bool:
    """Quick check: does this query look like it needs web scraping?"""
    intent = classify_query(query)
    return intent.kind != "unknown"
