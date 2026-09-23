#!/usr/bin/env python3
"""
Instagram Scraper for Lilly AI Training Console
Scrapes Instagram @usernames and #hashtags with CSV target management.
Uses scrapling for anti-bot bypass.
"""

import os
import json
import csv
import time
import logging
import asyncio
import requests
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, asdict
from enum import Enum
from concurrent.futures import ThreadPoolExecutor
import threading

logger = logging.getLogger("instagram-scraper")

# Paths
BASE_DIR = Path(__file__).parent.parent.parent
DATA_DIR = Path(
    os.environ.get(
        "INSTAGRAM_DATA_DIR",
        BASE_DIR / "data" / "training" / "instagram",
    )
)
TARGETS_CSV = DATA_DIR / "targets.csv"
RESULTS_CSV = DATA_DIR / "scraped_results.csv"
SESSIONS_CSV = DATA_DIR / "sessions.csv"

# Ensure directories exist
DATA_DIR.mkdir(parents=True, exist_ok=True)


class ScraperStatus(Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    ERROR = "error"
    COMPLETED = "completed"


class ScrapingMode(Enum):
    USERNAME = "username"
    HASHTAG = "hashtag"
    BOTH = "both"


@dataclass
class ScrapeTarget:
    """A single Instagram scraping target."""

    id: str
    type: str  # "username" or "hashtag"
    value: str  # the @username or #hashtag
    mode: str  # "followers", "posts", "profile"
    status: str  # "pending", "scraping", "done", "failed"
    added_at: float
    completed_at: Optional[float] = None
    results_count: int = 0
    error: Optional[str] = None
    notes: str = ""


@dataclass
class ScrapedResult:
    """A single scraped Instagram result."""

    id: str
    target_id: str
    target_value: str
    source_type: str  # "username" or "hashtag"
    username: Optional[str] = None
    full_name: Optional[str] = None
    bio: Optional[str] = None
    followers_count: Optional[int] = None
    posts_count: Optional[int] = None
    is_verified: bool = False
    profile_url: Optional[str] = None
    caption: Optional[str] = None
    hashtags: Optional[str] = None
    scraped_at: float = 0.0
    quality_score: Optional[float] = None
    selected_for_training: bool = False
    training_label: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class ScrapeSession:
    """A scraping session record."""

    id: str
    started_at: float
    ended_at: Optional[float] = None
    targets_total: int = 0
    targets_completed: int = 0
    results_total: int = 0
    status: str = "idle"
    mode: str = "both"
    config: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    def __post_init__(self):
        if self.config is None:
            self.config = {}


class InstagramScraper:
    """
    Instagram scraping engine with CSV-based target management.

    Features:
    - Load targets from CSV
    - Scrape @usernames (profile data)
    - Scrape #hashtags (post metadata)
    - Store results in CSV
    - Filter/select results for training
    - Anti-bot bypass via scrapling
    """

    def __init__(self):
        self.status = ScraperStatus.IDLE
        self.targets: List[ScrapeTarget] = []
        self.results: List[ScrapedResult] = []
        self.sessions: List[ScrapeSession] = []
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._current_session: Optional[ScrapeSession] = None

        # Scraper config
        self.config = {
            "concurrent_limit": 3,
            "request_delay": 2.0,
            "request_timeout": 30,
            "max_retries": 3,
            "proxy": None,
            "use_stealthy": True,
            "output_format": "json",
            "save_images": False,
            "min_followers_threshold": 0,
            "max_results_per_target": 100,
        }

    def load_targets_from_csv(self, path: str = None) -> int:
        """Load scraping targets from CSV file."""
        csv_path = Path(path) if path else TARGETS_CSV
        if not csv_path.exists():
            logger.warning(f"Targets CSV not found: {csv_path}")
            return 0

        loaded = 0
        with open(csv_path, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:

                def _f(rows, key, default):
                    try:
                        v = rows.get(key, "")
                        return float(v) if v not in (None, "") else default
                    except (TypeError, ValueError):
                        return default

                def _i(rows, key, default):
                    try:
                        v = rows.get(key, "")
                        return int(v) if v not in (None, "") else default
                    except (TypeError, ValueError):
                        return default

                target = ScrapeTarget(
                    id=row.get("id", f"target_{int(time.time() * 1000)}_{loaded}"),
                    type=row.get("type", "hashtag"),
                    value=row.get("value", "").strip(),
                    mode=row.get("mode", "posts"),
                    status=row.get("status", "pending"),
                    added_at=_f(row, "added_at", time.time()),
                    completed_at=_f(row, "completed_at", 0) or None,
                    results_count=_i(row, "results_count", 0),
                    error=row.get("error", ""),
                    notes=row.get("notes", ""),
                )
                self.targets.append(target)
                loaded += 1

        logger.info(f"Loaded {loaded} targets from {csv_path}")
        return loaded

    def save_targets_to_csv(self, path: str = None) -> bool:
        """Save current targets to CSV file."""
        csv_path = Path(path) if path else TARGETS_CSV
        DATA_DIR.mkdir(parents=True, exist_ok=True)

        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "id",
                    "type",
                    "value",
                    "mode",
                    "status",
                    "added_at",
                    "completed_at",
                    "results_count",
                    "error",
                    "notes",
                ]
            )
            for target in self.targets:
                writer.writerow(
                    [
                        target.id,
                        target.type,
                        target.value,
                        target.mode,
                        target.status,
                        target.added_at,
                        target.completed_at or "",
                        target.results_count,
                        target.error or "",
                        target.notes,
                    ]
                )

        logger.info(f"Saved {len(self.targets)} targets to {csv_path}")
        return True

    def save_results_to_csv(self, path: str = None) -> bool:
        """Save scraped results to CSV file."""
        csv_path = Path(path) if path else RESULTS_CSV
        DATA_DIR.mkdir(parents=True, exist_ok=True)

        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "id",
                    "target_id",
                    "target_value",
                    "source_type",
                    "username",
                    "full_name",
                    "bio",
                    "followers_count",
                    "posts_count",
                    "is_verified",
                    "profile_url",
                    "caption",
                    "hashtags",
                    "scraped_at",
                    "quality_score",
                    "selected_for_training",
                    "training_label",
                ]
            )
            for result in self.results:
                writer.writerow(
                    [
                        result.id,
                        result.target_id,
                        result.target_value,
                        result.source_type,
                        result.username or "",
                        result.full_name or "",
                        result.bio or "",
                        result.followers_count or "",
                        result.posts_count or "",
                        result.is_verified,
                        result.profile_url or "",
                        result.caption or "",
                        result.hashtags or "",
                        result.scraped_at,
                        result.quality_score or "",
                        result.selected_for_training,
                        result.training_label or "",
                    ]
                )

        logger.info(f"Saved {len(self.results)} results to {csv_path}")
        return True

    def add_target(
        self, target_type: str, value: str, mode: str = "posts", notes: str = ""
    ) -> str:
        """Add a new target manually."""
        target_id = f"target_{int(time.time() * 1000)}"
        target = ScrapeTarget(
            id=target_id,
            type=target_type,
            value=value.strip(),
            mode=mode,
            status="pending",
            added_at=time.time(),
            notes=notes,
        )
        with self._lock:
            self.targets.append(target)
        self.save_targets_to_csv()
        return target_id

    def add_targets_from_csv_text(self, csv_text: str) -> int:
        """Add targets from CSV text content."""
        import io

        reader = csv.DictReader(io.StringIO(csv_text))
        added = 0
        for row in reader:
            self.add_target(
                target_type=row.get("type", "hashtag"),
                value=row.get("value", "").strip(),
                mode=row.get("mode", "posts"),
                notes=row.get("notes", ""),
            )
            added += 1
        return added

    def get_pending_targets(self) -> List[ScrapeTarget]:
        """Get all targets that haven't been scraped yet."""
        return [t for t in self.targets if t.status == "pending"]

    def get_results_for_target(self, target_id: str) -> List[ScrapedResult]:
        """Get all scraped results for a specific target."""
        return [r for r in self.results if r.target_id == target_id]

    def get_selected_results(self) -> List[ScrapedResult]:
        """Get all results selected for training."""
        return [r for r in self.results if r.selected_for_training]

    def select_results(self, result_ids: List[str], label: str = "") -> int:
        """Mark results as selected for training."""
        selected = 0
        with self._lock:
            for result in self.results:
                if result.id in result_ids:
                    result.selected_for_training = True
                    if label:
                        result.training_label = label
                    selected += 1
        self.save_results_to_csv()
        return selected

    def deselect_results(self, result_ids: List[str]) -> int:
        """Mark results as not selected for training."""
        deselected = 0
        with self._lock:
            for result in self.results:
                if result.id in result_ids:
                    result.selected_for_training = False
                    result.training_label = None
                    deselected += 1
        self.save_results_to_csv()
        return deselected

    # ── Actual Scraping Methods ──────────────────────────────

    def _init_scrapling(self):
        """Initialize scrapling for stealth fetching (modern 0.4.x API).

        No persistent browser factory is held — modern Scrapling uses one-off
        `StealthyFetcher.fetch(...)` calls. We just verify the import works.
        """
        try:
            from scrapling.fetchers import StealthyFetcher  # noqa: F401

            # Optional: session cookies for logged-in scraping (full post data)
            self._ig_session_path = DATA_DIR / "ig_session.json"
            self._ig_session = None
            if self._ig_session_path.exists():
                try:
                    self._ig_session = json.loads(
                        self._ig_session_path.read_text(encoding="utf-8")
                    )
                    logger.info(
                        f"Loaded Instagram session cookies from {self._ig_session_path}"
                    )
                except Exception as e:
                    logger.warning(f"Could not load ig_session.json: {e}")

            self._scrapling = True  # type: ignore
            logger.info("Scrapling initialized successfully (StealthyFetcher)")
            return True
        except ImportError:
            logger.warning("Scrapling not installed, falling back to requests mode")
            self._scrapling = None
            return False  # type: ignore
        except Exception as e:
            logger.error(f"Failed to initialize scrapling: {e}")
            self._scrapling = None
            return False  # type: ignore

    # ── Modern Scrapling fetch helpers ────────────────────────

    def _stealth_fetch(self, url: str):
        """Fetch a URL with StealthyFetcher (browser, anti-bot).

        NOTE: Scrapling 0.4.x timeout parameter is in MILLISECONDS (default
        30_000). Our config stores seconds, so multiply by 1_000.
        """
        from scrapling.fetchers import StealthyFetcher, StealthySession

        timeout_ms = self.config["request_timeout"] * 1000

        if self._ig_session:
            # Reuse logged-in cookies for full post data (passed as Cookie header)
            cookie_str = "; ".join(f"{k}={v}" for k, v in self._ig_session.items())
            with StealthySession(headless=True, network_idle=True) as session:
                return session.fetch(
                    url,
                    extra_headers={"Cookie": cookie_str},
                    timeout=timeout_ms,
                )
        return StealthyFetcher.fetch(
            url,
            headless=True,
            network_idle=True,
            timeout=timeout_ms,
        )

    def _scrape_username(self, username: str) -> Optional[Dict[str, Any]]:
        """Scrape Instagram user profile data (best-effort without login)."""
        # Try Scrapling (browser) first — can render JS and may extract
        # data from embedded JSON that plain requests can't see.
        try:
            if not getattr(self, "_scrapling", None):
                self._init_scrapling()
            if getattr(self, "_scrapling", None):
                result = self._stealth_fetch(f"https://www.instagram.com/{username}/")
                html = result.html_content
                logger.info(
                    f"Profile {username}: status={result.status}, html_len={len(html)}"
                )
                data = self._parse_profile_html(html, username)
                # Only accept if we got meaningful data beyond just the username
                if data.get("full_name") or data.get("followers_count") is not None:
                    return data
        except Exception as e:
            logger.warning(f"Scrapling profile fetch failed for {username}: {e}")

        # Fallback A: mobile API endpoint with proper device headers
        mobile_data = self._fetch_username_mobile(username)
        if mobile_data:
            return mobile_data

        # Fallback B: plain requests + beautifulsoup
        try:
            import requests

            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
            resp = requests.get(
                f"https://www.instagram.com/{username}/",
                headers=headers,
                timeout=self.config["request_timeout"],
            )
            if resp.status_code == 200:
                data = self._parse_profile_html(resp.text, username)
                if data.get("full_name") or data.get("followers_count") is not None:
                    return data
        except Exception as e:
            logger.warning(f"Plain requests failed for {username}: {e}")

        logger.warning(f"Could not extract profile data for {username}")
        return None

    def _fetch_username_mobile(self, username: str) -> Optional[Dict[str, Any]]:
        """Try Instagram's mobile API endpoints with device headers.

        These endpoints sometimes return JSON data without a login
        when called with the proper mobile headers and device ID.
        """
        try:
            import requests

            headers = {
                "User-Agent": "Mozilla/5.0 (Linux; Android 13; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
                "Accept": "application/json",
                "Accept-Language": "en-US",
                "X-IG-App-ID": "936619743392459",
                "X-IG-Device-ID": "android-b3a2e4f7c9e5f1a8",
                "X-IG-Mapped-Locale": "en_US",
                "X-Pigeon-SessionID": "1000000000000000.10000.0.0.0",
                "X-Pigeon-Rawclienttime": str(time.time()),
                "X-Client-Connection-Data": "1",
            }

            # Endpoint 1: user info via username
            for endpoint in [
                f"https://www.instagram.com/api/v1/users/{username}/info/",
                f"https://i.instagram.com/api/v1/users/{username}/info/",
            ]:
                try:
                    resp = requests.get(endpoint, headers=headers, timeout=15)
                    if resp.status_code == 200:
                        try:
                            data = resp.json()
                            user = data.get("user", data)
                            if user.get("username"):
                                return {
                                    "username": user.get("username", username),
                                    "full_name": user.get("full_name"),
                                    "bio": user.get("biography"),
                                    "followers_count": user.get("follower_count"),
                                    "posts_count": user.get("media_count"),
                                    "is_verified": user.get("is_verified", False),
                                    "profile_url": (
                                        f"https://www.instagram.com/{username}/"
                                    ),
                                    "metadata": {"source": "mobile_api"},
                                }
                        except (json.JSONDecodeError, ValueError):
                            pass
                except Exception:
                    continue

            # Endpoint 2: GraphQL query (needs query hash)
            try:
                user_id = self._resolve_user_id(username, headers)
                if user_id:
                    graphql_url = (
                        "https://www.instagram.com/graphql/query/?"
                        "query_hash=e769aa130647d2354c40ea6ea4d60e93"
                        f"&variables={{"
                        f'"id":"{user_id}",'
                        f'"first":12,'
                        f'"after":null'
                        f"}}"
                    )
                    resp = requests.get(graphql_url, headers=headers, timeout=15)
                    if resp.status_code == 200:
                        data = resp.json()
                        user = data.get("data", {}).get("user", {})
                        if user.get("username"):
                            return {
                                "username": user.get("username", username),
                                "full_name": user.get("full_name"),
                                "bio": user.get("biography"),
                                "followers_count": user.get("edge_followed_by", {}).get(
                                    "count"
                                ),
                                "posts_count": user.get(
                                    "edge_owner_to_timeline_media", {}
                                ).get("count"),
                                "is_verified": user.get("is_verified", False),
                                "profile_url": (
                                    f"https://www.instagram.com/{username}/"
                                ),
                                "metadata": {"source": "graphql"},
                            }
            except Exception:
                pass
        except Exception:
            pass
        return None

    def _resolve_user_id(self, username: str, headers: dict) -> Optional[str]:
        """Resolve a username to a numeric user ID via Instagram endpoints."""
        try:
            for endpoint in [
                f"https://www.instagram.com/api/v1/users/{username}/",
                f"https://i.instagram.com/api/v1/users/{username}/",
            ]:
                try:
                    resp = requests.get(endpoint, headers=headers, timeout=15)
                    if resp.status_code == 200:
                        data = resp.json()
                        uid = data.get("pk") or data.get("id")
                        if uid:
                            return str(uid)
                except Exception:
                    continue
        except Exception:
            pass
        return None

    def _scrape_hashtag(self, hashtag: str) -> List[Dict[str, Any]]:
        """Scrape Instagram hashtag page for related profiles + metrics.

        Logged-out hashtag pages show related-profile usernames, the reel/post
        count and the hashtag description (no post-level data without a login).
        """
        posts = []
        try:
            if not getattr(self, "_scrapling", None):
                self._init_scrapling()
            if getattr(self, "_scrapling", None):
                result = self._stealth_fetch(
                    f"https://www.instagram.com/explore/tags/{hashtag}/"
                )
                html = result.html_content
                logger.info(
                    f"Hashtag #{hashtag}: status={result.status}, html_len={len(html)}"
                )
                posts = self._parse_hashtag_html(html, hashtag)
            else:
                import requests

                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                }
                resp = requests.get(
                    f"https://www.instagram.com/explore/tags/{hashtag}/",
                    headers=headers,
                    timeout=self.config["request_timeout"],
                )
                if resp.status_code == 200:
                    posts = self._parse_hashtag_html(resp.text, hashtag)
        except Exception as e:
            logger.error(f"Failed to scrape hashtag #{hashtag}: {e}")
        return posts

    def _parse_profile_html(self, html_content: str, username: str) -> Dict[str, Any]:
        """Parse Instagram profile HTML to extract data.

        Logged-out profile pages are login-walled; the parser extracts what
        remains (og meta, embedded JSON) and falls back to the username itself.
        """
        try:
            import re
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html_content, "html.parser")

            profile_data = {
                "username": username,
                "full_name": None,
                "bio": None,
                "followers_count": None,
                "posts_count": None,
                "is_verified": False,
                "profile_url": f"https://www.instagram.com/{username}/",
            }

            # og: meta tags (present even on login-wall pages)
            meta_tags = soup.find_all("meta")
            for tag in meta_tags:
                prop = str(tag.get("property") or "")
                content = str(tag.get("content") or "")
                if "og:title" in prop and content != "Instagram":
                    profile_data["full_name"] = content
                elif (
                    "og:description" in prop and content and "Instagram" not in content
                ):
                    profile_data["bio"] = content

            # JSON-LD structured data (present when not walled)
            scripts = soup.find_all("script", type="application/ld+json")
            for script in scripts:
                try:
                    data = json.loads(script.string or "")
                    if isinstance(data, dict):
                        if data.get("name"):
                            profile_data["full_name"] = data.get("name")
                        if data.get("description"):
                            profile_data["bio"] = data.get("description")
                        stats = data.get("interactionStatistic") or []
                        if stats:
                            profile_data["followers_count"] = stats[0].get(
                                "userInteractionCount"
                            )
                except Exception:
                    continue

            # profile meta (verified badge, follower counts) embedded as JSON
            embed = re.search(
                r'"edge_followed_by":\s*\{\s*"count":\s*(\d+)', html_content
            )
            if embed:
                profile_data["followers_count"] = int(embed.group(1))
            embed_posts = re.search(
                r'"edge_owner_to_timeline_media":\s*\{\s*"count":\s*(\d+)',
                html_content,
            )
            if embed_posts:
                profile_data["posts_count"] = int(embed_posts.group(1))
            if re.search(r'"is_verified":\s*true', html_content):
                profile_data["is_verified"] = True
            name_embed = re.search(r'"full_name":\s*"([^"]+)', html_content)
            if name_embed and not profile_data["full_name"]:
                profile_data["full_name"] = name_embed.group(1)
            bio_embed = re.search(r'"biography":\s*"([^"]*)', html_content)
            if bio_embed and not profile_data["bio"]:
                profile_data["bio"] = bio_embed.group(1)

            return profile_data
        except Exception as e:
            logger.warning(f"Failed to parse profile HTML for {username}: {e}")
            return {"username": username}

    def _parse_hashtag_html(
        self, html_content: str, hashtag: str
    ) -> List[Dict[str, Any]]:
        """Parse Instagram hashtag page HTML.

        Logged-out Instagram hashtag pages expose:
        - related-profile usernames in href="/NAME/" links
        - reel/post count (e.g. "178M Reels")
        - hashtag description text
        Each related profile becomes one result entry so the training console
        gets real, usable data.
        """
        try:
            import re

            posts = []

            # 1) Related profile usernames from nav links
            hrefs = re.findall(r'href="/([a-zA-Z0-9._]{2,30})/"', html_content)
            skip = {
                "explore",
                "popular",
                "accounts",
                "reels",
                "direct",
                "stories",
                "p",
                "media",
                "about",
                "legal",
                "meta",
                "settings",
                "help",
                "privacy",
            }
            usernames = sorted(
                {h for h in hrefs if h not in skip and "." not in h.lstrip(".")}
            )

            # 2) Post/reel count shown in the header
            posts_count = None
            count_m = re.search(
                r"([\d.,]+[KMB]?)\s+(?:Reels|Posts|Posts Reels)", html_content
            )
            if count_m:
                posts_count = count_m.group(1)

            # 3) Hashtag description (the intro paragraph)
            description = None
            desc_m = re.search(
                r'<meta\s+name="description"\s+content="([^"]+)"', html_content
            )
            if not desc_m:
                text_body = re.sub(r"<[^>]+>", " ", html_content)
                text_body = re.sub(r"\s+", " ", text_body)
                # The intro text typically starts right after the count line
                try:
                    idx = text_body.index("Instagram")
                    tail = text_body[idx + 9 : idx + 400]
                    # skip Log In / Sign Up then grab remaining sentence
                    tail = tail.replace("Log In", " ").replace("Sign Up", " ").strip()
                    if tail:
                        description = tail.split("  ")[0][:300]
                except ValueError:
                    pass
            else:
                description = desc_m.group(1)

            for u in usernames:
                posts.append(
                    {
                        "username": u,
                        "caption": description,
                        "hashtags": f"#{hashtag}",
                        "followers_count": None,
                        "metadata": {
                            "source": "related_profiles",
                            "posts_count": posts_count,
                            "description": description,
                        },
                        "scraped_at": time.time(),
                    }
                )

            # Occasional embedded GraphQL JSON (present only when logged-in)
            if re.search(r"window\._sharedData", html_content):
                try:
                    from bs4 import BeautifulSoup

                    soup = BeautifulSoup(html_content, "html.parser")
                    for script in soup.find_all("script"):
                        if script.string and "window._sharedData" in script.string:
                            json_str = script.string.split("window._sharedData = ", 1)[
                                1
                            ].rsplit(";</script>", 1)[0]
                            data = json.loads(json_str)
                            edge_media = (
                                data.get("entry_data", {})
                                .get("TagPage", [{}])[0]
                                .get("graphql", {})
                                .get("hashtag", {})
                                .get("edge_hashtag_to_media", {})
                                .get("edges", [])
                            )
                            for edge in edge_media[
                                : self.config["max_results_per_target"]
                            ]:
                                node = edge.get("node", {})
                                posts.append(
                                    {
                                        "username": node.get("owner", {}).get(
                                            "username", ""
                                        ),
                                        "caption": node.get("edge_media_to_caption", {})
                                        .get("edges", [{}])[0]
                                        .get("node", {})
                                        .get("text", ""),
                                        "hashtags": " ".join(
                                            h.get("text", "")
                                            for h in node.get(
                                                "edge_media_to_caption", {}
                                            ).get("edges", [])
                                        ),
                                        "metadata": {
                                            "source": "graphql_media",
                                            "post_shortcode": node.get("shortcode", ""),
                                        },
                                        "scraped_at": time.time(),
                                    }
                                )
                            break
                except Exception as e:
                    logger.warning(f"Failed to parse embedded IG JSON: {e}")

            return posts
        except Exception as e:
            logger.warning(f"Failed to parse hashtag HTML for #{hashtag}: {e}")
            return []

    # ── Main Scrape Engine ───────────────────────────────────

    def scrape_target(self, target: ScrapeTarget) -> bool:
        """Scrape a single target and save results."""
        try:
            target.status = "scraping"
            self.save_targets_to_csv()

            if target.type == "username":
                data = self._scrape_username(target.value)
                if data:
                    result = ScrapedResult(
                        id=f"result_{int(time.time() * 1000)}",
                        target_id=target.id,
                        target_value=target.value,
                        source_type="username",
                        username=data.get("username"),
                        full_name=data.get("full_name"),
                        bio=data.get("bio"),
                        followers_count=data.get(
                            "follower_count", data.get("followers_count")
                        ),
                        posts_count=data.get("posts_count"),
                        is_verified=data.get("is_verified", False),
                        profile_url=data.get("profile_url"),
                        metadata=data.get("metadata") or {"source": "profile"},
                        scraped_at=time.time(),
                    )
                    self.results.append(result)
                    target.results_count += 1

            elif target.type == "hashtag":
                posts = self._scrape_hashtag(target.value.replace("#", ""))
                for post in posts:
                    result = ScrapedResult(
                        id=f"result_{int(time.time() * 1000)}_{len(self.results)}",
                        target_id=target.id,
                        target_value=target.value,
                        source_type="hashtag",
                        username=post.get("username"),
                        caption=post.get("caption"),
                        hashtags=post.get("hashtags"),
                        followers_count=post.get("followers_count"),
                        quality_score=0.5,
                        metadata=post.get("metadata") or {"source": "related_profiles"},
                        scraped_at=post.get("scraped_at", time.time()),
                    )
                    self.results.append(result)
                    target.results_count += 1

            target.completed_at = time.time()
            logger.info(
                f"Scraped target {target.value}: {target.results_count} results"
            )
            # Only mark as "done" if we actually got data; otherwise
            # "completed_empty" signals the scrape ran but returned nothing
            # (e.g. Instagram login wall) so results are never treated as
            # valid / fake-success.
            if target.results_count > 0:
                target.status = "done"
            else:
                target.status = "completed_empty"
                target.error = "No data extracted (login wall or block)"
            self.save_results_to_csv()
            self.save_targets_to_csv()
            return True

        except Exception as e:
            target.status = "failed"
            target.error = str(e)
            target.completed_at = time.time()
            logger.error(f"Failed to scrape {target.value}: {e}")
            self.save_targets_to_csv()
            return False  # type: ignore

    def run_scraper(self, mode: str = "both", max_targets: int = 0):
        """Run the full scraping pipeline."""
        if not self._init_scrapling():
            logger.warning("Scrapling not available, using fallback mode")

        self.status = ScraperStatus.RUNNING
        session = ScrapeSession(
            id=f"session_{int(time.time() * 1000)}",
            started_at=time.time(),
            targets_total=0,
            status="running",
            mode=mode,
            config=self.config,
        )

        with self._lock:
            targets = self.get_pending_targets()
            if mode == "username":
                targets = [t for t in targets if t.type == "username"]
            elif mode == "hashtag":
                targets = [t for t in targets if t.type == "hashtag"]

            if max_targets > 0:
                targets = targets[:max_targets]

            session.targets_total = len(targets)
            self._current_session = session
            self.sessions.append(session)
            logger.info(f"Starting scrape session: {len(targets)} targets")

        for target in targets:
            if self._stop_event.is_set():
                logger.info("Scraping stopped by user")
                break

            success = self.scrape_target(target)
            with self._lock:
                session.targets_completed += 1
                session.results_total = len(self.results)

            # Rate limiting
            time.sleep(self.config["request_delay"])

        # Session complete
        self.status = (
            ScraperStatus.COMPLETED
            if not self._stop_event.is_set()
            else ScraperStatus.PAUSED
        )
        if self._current_session:
            self._current_session.ended_at = time.time()
            self._current_session.status = self.status.value
            self._current_session.results_total = len(self.results)
            self.save_results_to_csv()

        logger.info(f"Scraping session complete. Total results: {len(self.results)}")
        return len(self.results)

    def stop_scraper(self):
        """Stop the running scraper."""
        self._stop_event.set()
        logger.info("Stop requested for scraper")

    def reset_stop(self):
        """Reset the stop event."""
        self._stop_event.clear()

    def get_status(self) -> Dict[str, Any]:
        """Get current scraper status."""
        return {
            "status": self.status.value,
            "targets_total": len(self.targets),
            "targets_pending": len(self.get_pending_targets()),
            "targets_done": len([t for t in self.targets if t.status == "done"]),
            "targets_completed_empty": len(
                [t for t in self.targets if t.status == "completed_empty"]
            ),
            "targets_failed": len([t for t in self.targets if t.status == "failed"]),
            "results_total": len(self.results),
            "results_selected": len(self.get_selected_results()),
            "sessions": len(self.sessions),
            "current_session": self._current_session.status
            if self._current_session
            else None,
            "config": self.config,
        }

    def export_selected_for_training(self) -> List[Dict[str, Any]]:
        """Export selected results as training data."""
        selected = self.get_selected_results()
        training_data = []
        for result in selected:
            training_data.append(
                {
                    "source": result.source_type,
                    "target": result.target_value,
                    "username": result.username,
                    "caption": result.caption,
                    "hashtags": result.hashtags,
                    "followers": result.followers_count,
                    "label": result.training_label or "unknown",
                    "quality_score": result.quality_score or 0.5,
                    "scraped_at": result.scraped_at,
                }
            )
        return training_data

    def create_sample_targets_csv(self) -> str:
        """Create a sample targets CSV with Instagram examples."""
        sample = """id,type,value,mode,status,added_at,completed_at,results_count,error,notes
target_001,hashtag,#technology,posts,pending,,,0,,
target_002,hashtag,#ai,posts,pending,,,0,,
target_003,hashtag,#machinelearning,posts,pending,,,0,,
target_004,hashtag,#openai,posts,pending,,,0,,
target_005,username,@openai,profile,pending,,,0,,
target_006,hashtag,#deeplearning,posts,pending,,,0,,
target_007,hashtag,#neuralnetwork,posts,pending,,,0,,
target_008,username,@stabilityai,profile,pending,,,0,,
target_009,hashtag,#datascience,posts,pending,,,0,,
target_010,hashtag,#python,posts,pending,,,0,,
"""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(TARGETS_CSV, "w", encoding="utf-8") as f:
            f.write(sample)
        return str(TARGETS_CSV)

    def get_dashboard_stats(self) -> Dict[str, Any]:
        """Get aggregated stats for dashboard display."""
        by_type = {}
        for target in self.targets:
            t = target.type
            if t not in by_type:
                by_type[t] = {"total": 0, "done": 0, "failed": 0, "results": 0}
            by_type[t]["total"] += 1
            if target.status == "done":
                by_type[t]["done"] += 1
            elif target.status == "failed":
                by_type[t]["failed"] += 1
            by_type[t]["results"] += target.results_count

        selected = self.get_selected_results()
        labels = {}
        for r in selected:
            label = r.training_label or "untagged"
            labels[label] = labels.get(label, 0) + 1

        return {
            "scraper_status": self.status.value,
            "targets_by_type": by_type,
            "targets_total": len(self.targets),
            "targets_pending": len(self.get_pending_targets()),
            "results_total": len(self.results),
            "results_selected": len(selected),
            "selected_labels": labels,
            "sessions": [asdict(s) for s in self.sessions[-5:]],
        }


# ── Global instance ──────────────────────────────────────────
_scraper = InstagramScraper()


def get_scraper() -> InstagramScraper:
    """Get or create global scraper instance."""
    global _scraper
    if _scraper is None:
        _scraper = InstagramScraper()
    return _scraper


# ── CLI usage ────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Instagram Scraper for Lilly AI")
    parser.add_argument(
        "--init-csv", action="store_true", help="Create sample targets CSV"
    )
    parser.add_argument(
        "--add",
        nargs="+",
        help="Add targets (e.g., --add hashtag #ai username @openai)",
    )
    parser.add_argument("--scrape", action="store_true", help="Start scraping")
    parser.add_argument("--stop", action="store_true", help="Stop scraping")
    parser.add_argument("--status", action="store_true", help="Show scraper status")
    parser.add_argument("--results", action="store_true", help="Show scraped results")
    parser.add_argument("--select", nargs="+", help="Select results for training by ID")
    parser.add_argument(
        "--label", default="", help="Training label for selected results"
    )
    parser.add_argument(
        "--csv-export", action="store_true", help="Export selected as training CSV"
    )

    args = parser.parse_args()

    scraper = get_scraper()
    scraper.load_targets_from_csv()

    if args.init_csv:
        print(scraper.create_sample_targets_csv())
    elif args.add:
        for item in args.add:
            t_type, value = item.split(" ", 1) if " " in item else ("hashtag", item)
            target_id = scraper.add_target(t_type, value)
            print(f"Added: {target_id} -> {value}")
        scraper.save_targets_to_csv()
    elif args.scrape:
        count = scraper.run_scraper()
        print(f"Scraped {count} results")
    elif args.stop:
        scraper.stop_scraper()
        print("Scraper stopped")
    elif args.status:
        print(json.dumps(scraper.get_status(), indent=2))
    elif args.results:
        for r in scraper.results:
            print(
                f"{r.id}: {r.source_type}={r.target_value} username={r.username} label={r.training_label}"
            )
    elif args.select:
        count = scraper.select_results(args.select, args.label)
        print(f"Selected {count} results for training")
    elif args.csv_export:
        data = scraper.export_selected_for_training()
        print(json.dumps(data, indent=2))
    else:
        parser.print_help()
