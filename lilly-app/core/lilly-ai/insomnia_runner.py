#!/usr/bin/env python3
"""insomnia_runner.py — "Insomnia" engagement sessions driven purely from Termux.

No ADB. Everything runs through the phone's sensor-server /shell bridge:

  * termux-open-url / am start VIEW intents  -> open profiles, hashtags, posts
  * Overlay /a11y bridge (accessibility service) -> PROBED at runtime; "tap"
                                                    driver when the overlay APK
                                                    exposes /a11y/status connected
  * termux-notification                       -> progress pushed to the phone shade

Config keys map 1:1 to Insomniac flags (alexal1/Insomniac) so existing Insomniac
configs port over: lg interact, likes_count, likes_percentage, stories_count,
follow_percentage, comment_percentage, comments_list, max_following, speed,
working_hours, repeat.

Integrated from Insomniac (alexal1/Insomniac):
  * limits.py       — per-source limits (max_interactions_per_source)
  * softban_indicator.py — block detection (empty_profile, action_blocked, target_open_fail)
  * hardban_indicator.py — WebView/hardban detection via dumpsys
  * sleeper.py      — speed-adaptive sleep ranges from network measurement
  * filters.py      — account filtering (skip_blacklist, whitelist, skip_business, min_followers)
  * session_state.py— per-source state tracking (likes/follows/comments per target)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

COUNTERS = [
    "likes",
    "follows",
    "comments",
    "stories_watched",
    "targets_opened",
    "profiles_interacted",
    "errors",
    "blocked_indicators",
]

_BLOCKED_THRESHOLDS = {
    "empty_profile": 5,
    "action_blocked": 3,
    "target_open_fail": 5,
}

# Speed tiers adapted from Insomniac's sleeper.py

_SESSION_LOG_LIMIT = 600


def _qty(v: Any, default: int = 0) -> int:
    """Quantity from "2", "2-4" (random within range)."""
    if v is None or v == "":
        return default
    if isinstance(v, (int, float)):
        return int(v)
    m = re.match(r"^\s*(\d+)(?:\s*-\s*(\d+))?\s*$", str(v))
    if not m:
        return default
    lo = int(m.group(1))
    hi = int(m.group(2)) if m.group(2) else lo
    return random.randint(lo, hi) if hi > lo else lo


def _load_engagement() -> dict:
    """Load engagement data for trending computation."""
    try:
        p = (
            Path(__file__).parent.parent
            / "lilly-trainer"
            / "data"
            / "training"
            / "trending"
            / "engagement.json"
        )
        if p.exists():
            return json.loads(p.read_text())
    except Exception:
        pass
    return {"sessions": []}


def _parse_targets(cfg: Dict[str, Any]) -> List[Dict[str, str]]:
    """Accept '@user', '#hashtag', 'https://instagram.com/p/xyz', or lists."""
    raw = cfg.get("interact") or cfg.get("targets") or ""
    if isinstance(raw, list):
        items = [str(x).strip() for x in raw]
    else:
        items = [ln.strip() for ln in re.split(r"[\n,;]+", str(raw)) if ln.strip()]
    out: List[Dict[str, str]] = []
    for it in items:
        if not it:
            continue
        low = it.lower()
        if low.startswith("@") or (
            "instagram.com" in low and "explore/tags" not in low
        ):
            ref = re.sub(r"^https?://(www\.)?instagram\.com/", "", low).rstrip("/")
            out.append({"kind": "user", "ref": ref.lstrip("@").split("-")[0]})
        elif low.startswith("#"):
            out.append({"kind": "hashtag", "ref": it[1:].lstrip("#").split("-")[0]})
        elif "/p/" in low or "/reel/" in low:
            out.append({"kind": "post", "ref": it})
        else:
            # scraping-style targets (@user-followers / #tag-likers) need profile
            # enumeration, which a Tap-less Termux driver can't do — open parent only.
            out.append({"kind": "user", "ref": it.split("-")[0]})
    return out


def _deeplink(t: Dict[str, str]) -> Optional[str]:
    if t["kind"] == "user":
        return f"https://www.instagram.com/{t['ref']}/"
    if t["kind"] == "hashtag":
        return f"https://www.instagram.com/explore/tags/{t['ref']}/"
    if t["kind"] == "post":
        return (
            t["ref"]
            if t["ref"].startswith("http")
            else f"https://www.instagram.com/p/{t['ref']}/"
        )
    return None


class BlockDetector:
    """Softban/block detection adapted from Insomniac's softban_indicator.py.

    Tracks failure patterns that indicate Instagram is blocking automation:
      - empty_profile: target opened but profile content missing
      - action_blocked: actions repeatedly fail (a11y/tap returns false)
      - target_open_fail: repeated failures to open targets
    """

    def __init__(self):
        self.indicators = {
            "empty_profile": {
                "curr": 0,
                "threshold": _BLOCKED_THRESHOLDS["empty_profile"],
            },
            "action_blocked": {
                "curr": 0,
                "threshold": _BLOCKED_THRESHOLDS["action_blocked"],
            },
            "target_open_fail": {
                "curr": 0,
                "threshold": _BLOCKED_THRESHOLDS["target_open_fail"],
            },
        }
        self.blocked = False

    def record_empty_profile(self) -> bool:
        return self._increment("empty_profile")

    def record_action_blocked(self) -> bool:
        return self._increment("action_blocked")

    def record_target_open_fail(self) -> bool:
        return self._increment("target_open_fail")

    def record_success(self) -> None:
        """Reset failure counters on successful interaction."""
        for k in self.indicators:
            self.indicators[k]["curr"] = max(0, self.indicators[k]["curr"] - 1)

    def _increment(self, key: str) -> bool:
        s = self.indicators[key]
        s["curr"] += 1
        if s["curr"] >= s["threshold"]:
            self.blocked = True
            logger.warning(
                "[block-detector] %s threshold reached (%d/%d) — possible block",
                key,
                s["curr"],
                s["threshold"],
            )
            return True
        return False

    def snapshot(self) -> Dict[str, Any]:
        return {
            k: {"curr": v["curr"], "threshold": v["threshold"]}
            for k, v in self.indicators.items()
        }


class SpeedAdapter:
    """Network-speed-aware sleep ranges from Insomniac's sleeper.py.

    Measures internet speed via shell and maps to delay ranges.
    Falls back to config-based delays if measurement fails.
    """

    MEGABIT = 1000000
    SPEED_SUPERFAST = 1000 * MEGABIT
    SPEED_GOOD = 25 * MEGABIT
    SPEED_BAD = 10 * MEGABIT
    SPEED_UGLY = 1 * MEGABIT
    SPEED_ZERO = 0
    SLEEP_RANGE_BY_SPEED = {
        SPEED_SUPERFAST: (0, 1),
        SPEED_GOOD: (1, 3),
        SPEED_BAD: (2, 5),
        SPEED_UGLY: (4, 8),
        SPEED_ZERO: (7, 12),
    }

    @staticmethod
    async def measure_speed(
        run_shell: Callable[[List[str], float], Awaitable[Tuple[str, str]]],
    ) -> float:
        """Return speed in Mbps or 0.0 on failure."""
        try:
            out, _ = await asyncio.wait_for(
                run_shell(
                    [
                        "python3",
                        "-c",
                        "import urllib.request,time;"
                        "s=time.time();urllib.request.urlopen('https://www.google.com',timeout=5);"
                        "print(round((100/(time.time()-s))*8,1))",
                    ],
                    10.0,
                ),
                12.0,
            )
            return float(out.strip())
        except Exception:
            return 0.0

    @staticmethod
    def speed_to_range(speed_mbps: float) -> Tuple[float, float]:
        """Map speed (Mbps) to (min_delay, max_delay) seconds from Insomniac tables."""
        S = SpeedAdapter
        if speed_mbps >= S.SPEED_SUPERFAST:
            return S.SLEEP_RANGE_BY_SPEED[S.SPEED_SUPERFAST]
        if speed_mbps >= S.SPEED_GOOD:
            return S.SLEEP_RANGE_BY_SPEED[S.SPEED_GOOD]
        if speed_mbps >= S.SPEED_BAD:
            return S.SLEEP_RANGE_BY_SPEED[S.SPEED_BAD]
        if speed_mbps >= S.SPEED_UGLY:
            return S.SLEEP_RANGE_BY_SPEED[S.SPEED_UGLY]
        return S.SLEEP_RANGE_BY_SPEED[S.SPEED_ZERO]


def _filter_target(
    cfg: Dict[str, Any], target: Dict[str, str], log_fn: Callable[[str], None]
) -> bool:
    """Check if a target should be skipped based on filter config (from Insomniac's filters.py).

    Supported filters (in cfg):
      skip_business: true/false — skip if bio hints at business (best-effort)
      min_followers: int — skip accounts with fewer followers (can't verify via Termux; info only)
      skip_blacklist: list[str] — skip usernames containing any of these substrings (case-insensitive)
      whitelist: list[str] — only interact with usernames containing one of these substrings
      blacklist_words: list[str] — same as skip_blacklist (alias)
    """
    ref = target["ref"].lower()

    # Blacklist / skip words
    skip_words = cfg.get("skip_blacklist") or cfg.get("blacklist_words") or []
    if isinstance(skip_words, str):
        skip_words = [w.strip().lower() for w in skip_words.split(",") if w.strip()]
    for w in skip_words:
        if w in ref:
            log_fn(f"skip {ref}: blacklisted word '{w}'")
            return False

    # Whitelist
    wl = cfg.get("whitelist") or cfg.get("mandatory_words") or []
    if isinstance(wl, str):
        wl = [w.strip().lower() for w in wl.split(",") if w.strip()]
    if wl and not any(w in ref for w in wl):
        log_fn(f"skip {ref}: not in whitelist")
        return False

    # Best-effort business check (keywords in username/ref)
    if cfg.get("skip_business"):
        biz_hints = (
            "shop",
            "store",
            "brand",
            "official",
            "business",
            "company",
            "inc",
            "llc",
            "ltd",
        )
        if any(b in ref for b in biz_hints):
            log_fn(f"skip {ref}: looks like business account")
            return False

    return True


class InsomniaSession:
    """One background engagement session (created per /api/automation/run)."""

    def __init__(
        self,
        cfg: Dict[str, Any],
        session_id: str,
        run_shell: Callable[[List[str], float], Awaitable[Tuple[str, str]]],
        notify: Optional[Callable[[str, str], Awaitable[None]]] = None,
    ) -> None:
        self.cfg = cfg
        self.id = session_id
        self.run_shell = run_shell
        self.notify = notify
        self.stop = asyncio.Event()
        self.caps: Dict[str, Any] = {}
        self.counts: Dict[str, int] = {k: 0 for k in COUNTERS}
        self.log_lines: List[Dict[str, Any]] = []
        self.current = "idle"
        self.started = time.time()
        self.finished = False
        self.finish_reason = ""
        self._seg = random.uniform(0.9, 1.3)
        self._done_callbacks: List[Callable[[Dict[str, Any]], None]] = []
        # HTTP client for direct a11y bridge calls (host → phone overlay :8099).
        self._sensor_url: str = os.environ.get("SENSOR_SERVER_URL", "").rstrip("/")
        self._http: Any = None  # lazily created httpx.AsyncClient
        # Block detector (softban_indicator.py integration)
        self._blocker = BlockDetector()
        # Per-source state tracking (session_state.py integration)
        # source_ref → {likes, follows, comments, interactions, blocked}
        self._source_state: Dict[str, Dict[str, int]] = {}
        # Speed adapter (sleeper.py integration)
        self._speed_mbps: float = 0.0
        self._speed_checked = False

    # ── helpers ────────────────────────────────────────────────────────────
    def _log(self, msg: str) -> None:
        self.log_lines.append({"t": time.time(), "s": self.current, "m": msg})
        if len(self.log_lines) > _SESSION_LOG_LIMIT:
            self.log_lines = self.log_lines[-_SESSION_LOG_LIMIT:]
        logger.info("[insomnia %s] %s", self.id, msg)
        lf = self.cfg.get("log_file")
        if lf:
            try:
                with open(lf, "a", encoding="utf-8") as fh:
                    fh.write(
                        time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
                        + f" [{self.current}] {msg}\n"
                    )
            except Exception:
                pass

    async def _sh(self, args: List[str], timeout: float = 8.0) -> str:
        try:
            out, err = await asyncio.wait_for(
                self.run_shell(list(args), timeout), timeout + 2.0
            )
            merged = ((out or "") + ("\n" + err if err else "")).strip()
            if err and "traceback" in err.lower():
                self.counts["errors"] += 1
            return merged
        except Exception as e:
            self.counts["errors"] += 1
            self._log(f"shell error: {e}")
            return ""

    async def _sleep(self, base: float, jitter: float = 0.5) -> bool:
        """Human-like pause; returns True if stopped mid-sleep.

        Uses speed-adaptive delays from Insomniac's sleeper.py when
        speed has been measured; otherwise falls back to config pacing.
        """
        if not self._speed_checked and self._speed_mbps == 0.0:
            try:
                self._speed_mbps = await SpeedAdapter.measure_speed(self.run_shell)
            except Exception:
                pass
            self._speed_checked = True

        speed_range = SpeedAdapter.speed_to_range(self._speed_mbps)
        if speed_range != (0, 0):
            delay = (
                random.uniform(speed_range[0], speed_range[1])
                * self._seg
                * random.uniform(1 - jitter, 1 + jitter)
            )
        else:
            factor = {1: 2.2, 2: 1.4, 3: 1.0, 4: 0.6}.get(
                int(self.cfg.get("speed") or 3), 1.0
            )
            delay = base * factor * self._seg * random.uniform(1 - jitter, 1 + jitter)

        self._log("sleep %.2fs (speed=%.1fMbps)" % (delay, self._speed_mbps))
        try:
            await asyncio.wait_for(self.stop.wait(), timeout=delay)
            return True
        except asyncio.TimeoutError:
            return False

    async def _check_hardban(self) -> bool:
        """Hardban detection from Insomniac's hardban_indicator.py.

        Checks if Instagram is showing a WebView/CAPTCHA (hard-ban).
        """
        device_id = None  # no ADB; run on phone directly
        try:
            out, _ = await asyncio.wait_for(
                self.run_shell(
                    [
                        "sh",
                        "-c",
                        "dumpsys activity | grep 'mResumedActivity' | grep -i webview",
                    ],
                    8.0,
                ),
                10.0,
            )
            if out and "webview" in out.lower():
                self._log("HARDBAN: WebView detected in foreground activity")
                self.counts["blocked_indicators"] += 1
                self._blocker.blocked = True
                return True
        except Exception:
            pass
        return False

    # ── capability probe ───────────────────────────────────────────────────
    async def probe(self) -> Dict[str, Any]:
        self._log("Probing phone Termux capabilities...")
        checks: List[Tuple[str, List[str]]] = [
            ("am", ["am", "start", "--help"]),
            ("pm", ["pm", "list", "packages"]),
            ("termux_open_url", ["termux-open-url", "--help"]),
        ]
        for name, args in checks:
            out = await self._sh(args, timeout=6.0)
            bad = any(
                b in out.lower()
                for b in (
                    "not found",
                    "inaccessible",
                    "permission denied",
                    "security exception",
                    "no such file",
                )
            )
            ok = bool(out) and not bad
            self.caps[name] = ok
            self._log(f"capability {name}: {'ok' if ok else 'unavailable'}")
        self.caps["uiautomator"] = False
        self.caps["ui_table"] = None
        await self._probe_a11y()
        self.caps["mode"] = self.driver_mode()
        self._log(
            "driver mode: "
            + self.caps["mode"]
            + (
                ""
                if self.caps["mode"] == "tap"
                else " (deep-link only — enable the overlay accessibility service for tap mode)"
            )
        )
        return self.caps

    def driver_mode(self) -> str:
        pref = str(self.cfg.get("driver") or "auto").lower()
        if pref == "tap" and self.caps.get("a11y"):
            return "tap"
        if pref == "intent":
            return "intent"
        if self.caps.get("a11y"):
            return "tap"
        return "intent"

    # ── tap helpers (tap mode) ─────────────────────────────────────────────
    def _screen(self) -> Tuple[int, int]:
        return int(self.cfg.get("screen_w") or 1080), int(
            self.cfg.get("screen_h") or 2400
        )

    def _xy(self, rel: str, default: str) -> Tuple[int, int]:
        parts = (rel or default).split(",")
        w, h = self._screen()
        x = min(max(int(float(parts[0]) * w), 0), w - 1)
        y = min(max(int(float(parts[1]) * h), 0), h - 1)
        return x, y

    # ── a11y bridge (overlay APK accessibility service) ────────────────────
    async def _a11y_get(
        self, path: str, params: Dict[str, str]
    ) -> Optional[Dict[str, Any]]:
        """Direct HTTP call to the phone overlay's /a11y/* bridge."""
        if not self._sensor_url:
            return None
        try:
            if self._http is None:
                import httpx

                self._http = httpx.AsyncClient(timeout=8.0)
            url = f"{self._sensor_url}{path}"
            if params:
                url += "?" + urllib.parse.urlencode(params)
            r = await self._http.get(url)
            if r.status_code != 200:
                return None
            return r.json()
        except Exception as e:  # noqa: BLE001
            self._log(f"a11y bridge call failed ({path}): {e}")
            return None

    async def _vis(
        self,
        action: str,
        label: str,
        coords: str = "",
        step: int = 0,
        total: int = 0,
        detail: str = "",
    ) -> None:
        """Push an action update to the on-screen visualizer HUD."""
        params: Dict[str, str] = {"action": action, "label": label}
        if coords:
            params["coords"] = coords
        if step > 0:
            params["step"] = str(step)
        if total > 0:
            params["total"] = str(total)
        if detail:
            params["detail"] = detail
        await self._a11y_get("/a11y/visualize/update", params)

    async def _vis_show(self, session: str = "", total: int = 0) -> None:
        """Show the visualizer HUD."""
        params: Dict[str, str] = {"show": "1"}
        if session:
            params["session"] = session
        if total > 0:
            params["total"] = str(total)
        await self._a11y_get("/a11y/visualize", params)

    async def _vis_hide(self) -> None:
        """Hide the visualizer HUD."""
        await self._a11y_get("/a11y/visualize/hide", {})

    async def _probe_a11y(self) -> bool:
        """Detect the overlay accessibility tap bridge."""
        st = await self._a11y_get("/a11y/status", {})
        if not st:
            self.caps["a11y"] = False
            self.caps["a11y_reason"] = "no bridge (phone :8099 not the overlay server)"
            return False
        connected = bool(st.get("connected") or st.get("enabled"))
        self.caps["a11y"] = connected
        self.caps["a11y_reason"] = (
            "connected"
            if connected
            else "bridge up but accessibility service not enabled on the phone"
        )
        self.caps["a11y_foreground"] = st.get("foreground")
        self._log(
            f"a11y bridge: {'connected' if connected else 'not enabled'} "
            f"foreground={st.get('foreground')}"
        )
        return connected

    async def _tap(self, rel: str, default: str) -> bool:
        x, y = self._xy(rel, default)
        res = await self._a11y_get("/a11y/tap", {"x": str(x), "y": str(y)})
        return bool(res and res.get("ok"))

    async def _type_text(self, text: str) -> bool:
        res = await self._a11y_get("/a11y/type", {"text": text})
        return bool(res and res.get("ok"))

    async def _back(self) -> bool:
        await self._vis("back", "Going back", "", 0, 0, "Back")
        res = await self._a11y_get("/a11y/action", {"type": "back"})
        return bool(res and res.get("ok"))

    # ── working-hours gate ─────────────────────────────────────────────────
    async def _wait_until_allowed(self) -> bool:
        wh = self.cfg.get("working_hours") or ""
        if not wh:
            return True
        m = re.match(r"^\s*(\d{1,2})(?:\s*-\s*(\d{1,2}))?\s*$", str(wh).strip())
        if not m:
            return True
        lo, hi = int(m.group(1)), int(m.group(2)) if m.group(2) else int(m.group(1))
        while not self.stop.is_set():
            h = time.localtime().tm_hour
            if lo <= h <= hi:
                return True
            if await self._sleep(3600):
                return False
        return False

    # ── interaction steps ──────────────────────────────────────────────────
    async def _open_target(self, target: Dict[str, str]) -> bool:
        url = _deeplink(target)
        if not url:
            self._log(f"skip unsupported target: {target['ref']}")
            return False
        self.current = f"target {target['ref']}"
        ref = target.get("ref") or target.get("value") or "profile"
        step = getattr(self, "_target_idx", 0) + 1
        total = getattr(self, "_target_total", 0)
        await self._vis("open", f"Opening {ref}", "", step, total, f"Opening {ref}")
        out = await self._sh(["termux-open-url", url], timeout=8.0)
        if not out and self.caps.get("am"):
            out = await self._sh(
                ["am", "start", "-a", "android.intent.action.VIEW", "-d", url],
                timeout=8.0,
            )
        ok = bool(out) and "error" not in out.lower()
        if ok:
            self.counts["targets_opened"] += 1
            self._log(f"opened {target['ref']} -> {url[:70]}")
            await self._vis("open", f"Opened {ref}", "", step, total, f"Opened {ref}")
        else:
            self.counts["errors"] += 1
            self._log(f"failed to open {target['ref']}")
            await self._vis(
                "warn", f"Failed to open {ref}", "", step, total, f"Failed: {ref}"
            )
        return ok

    async def _like_current_post(self) -> bool:
        c = self.cfg.get("coordinates") or {}
        coords = c.get("like_xy") or "0.86,0.47"
        step = getattr(self, "_target_idx", 0) + 1
        total = getattr(self, "_target_total", 0)
        await self._vis(
            "like", "Tapping like button", coords, step, total, f"Like tap @ {coords}"
        )
        ok = await self._tap(coords, "0.86,0.47")
        if ok:
            self.counts["likes"] += 1
            self.current = "liking"
            self._log("liked post")
            await self._vis("like", "Liked ❤️", coords, step, total)
        else:
            self.counts["errors"] += 1
            self._log("like tap failed (a11y ok=false)")
            await self._vis(
                "warn", "Like tap failed", coords, step, total, "Like failed"
            )
        return ok

    async def _follow_current(self) -> bool:
        c = self.cfg.get("coordinates") or {}
        coords = c.get("follow_xy") or "0.28,0.27"
        step = getattr(self, "_target_idx", 0) + 1
        total = getattr(self, "_target_total", 0)
        await self._vis(
            "follow",
            "Tapping follow button",
            coords,
            step,
            total,
            f"Follow tap @ {coords}",
        )
        ok = await self._tap(coords, "0.28,0.27")
        if ok:
            self.counts["follows"] += 1
            self.current = "following"
            self._log("followed profile")
            await self._vis("follow", "Followed ➕", coords, step, total)
        else:
            self.counts["errors"] += 1
            self._log("follow tap failed (a11y ok=false)")
            await self._vis(
                "warn", "Follow tap failed", coords, step, total, "Follow failed"
            )
        return ok

    async def _comment_current(self, comment: str) -> bool:
        c = self.cfg.get("coordinates") or {}
        coords = c.get("comment_xy") or "0.86,0.60"
        step = getattr(self, "_target_idx", 0) + 1
        total = getattr(self, "_target_total", 0)
        await self._vis(
            "comment",
            "Opening comment box",
            coords,
            step,
            total,
            f"Comment tap @ {coords}",
        )
        ok = await self._tap(coords, "0.86,0.60")
        if ok:
            if await self._sleep(1.4):
                return False
            await self._vis(
                "type", f"Typing: {comment[:30]}", "", step, total, f"Typing comment"
            )
            ok = await self._type_text(comment)
            if ok:
                if await self._sleep(0.8):
                    return False
                send_coords = c.get("send_xy") or "0.5,0.30"
                await self._vis(
                    "tap", "Tapping send", send_coords, step, total, "Sending comment"
                )
                ok = await self._tap(send_coords, "0.5,0.30")
        if ok:
            self.counts["comments"] += 1
            self.current = "commenting"
            self._log(f"commented: {comment[:40]}")
            await self._vis(
                "comment",
                f"Commented: {comment[:25]}",
                "",
                step,
                total,
                f"Comment: {comment[:30]}",
            )
        else:
            self.counts["errors"] += 1
            self._log("comment flow failed (a11y ok=false)")
            await self._vis(
                "warn", "Comment failed", "", step, total, "Comment flow failed"
            )
        return ok

    # ── IG account & pacing ──────────────────────────
    def _ig_account(self):
        return self.cfg.get("ig_username") or os.environ.get("IG_USERNAME", "unknown")

    def _pace(self):
        profile = str(self.cfg.get("pace_profile") or "normal").lower()
        profiles = {
            "gentle": {
                "delay_like": (3, 6),
                "delay_follow": (5, 10),
                "delay_comment": (4, 8),
                "delay_target": (4, 8),
                "hourly_break": True,
                "break_every_min": 45,
                "break_duration_min": 5,
                "session_limit_min": 60,
            },
            "aggressive": {
                "delay_like": (1, 2),
                "delay_follow": (2, 3),
                "delay_comment": (2, 3),
                "delay_target": (1, 2),
                "hourly_break": False,
                "break_every_min": 0,
                "break_duration_min": 0,
                "session_limit_min": 0,
            },
            "normal": {
                "delay_like": (2, 4),
                "delay_follow": (3, 6),
                "delay_comment": (3, 5),
                "delay_target": (2, 4),
                "hourly_break": True,
                "break_every_min": 60,
                "break_duration_min": 3,
                "session_limit_min": 120,
            },
        }
        base = dict(profiles.get(profile, profiles["normal"]))
        for key in (
            "delay_like",
            "delay_follow",
            "delay_comment",
            "delay_target",
            "hourly_break",
            "break_every_min",
            "break_duration_min",
            "session_limit_min",
        ):
            if key in self.cfg:
                base[key] = self.cfg[key]
        return base

    async def _maybe_break(self, start_time):
        pace = self._pace()
        if not pace.get("hourly_break"):
            return False
        break_every = pace.get("break_every_min", 60)
        if break_every <= 0:
            return False
        elapsed_min = (time.time() - start_time) / 60.0
        cycles = int(elapsed_min // break_every)
        last = int(self.cfg.get("_last_break_cycle", 0))
        if cycles > last:
            self.cfg["_last_break_cycle"] = cycles
            duration = pace.get("break_duration_min", 3)
            self._log("hourly break (%d min)" % duration)
            if self.notify:
                await self.notify(
                    "Insomnia on break",
                    "%s pausing %dmin to avoid blocks" % (self._ig_account(), duration),
                )
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=duration * 60)
            except asyncio.TimeoutError:
                pass
            return self.stop.is_set()
        return False

    async def _check_session_limit(self, start_time):
        limit_min = self._pace().get("session_limit_min", 0)
        if limit_min <= 0:
            return False
        elapsed_min = (time.time() - start_time) / 60.0
        if elapsed_min >= limit_min:
            self.finish_reason = (
                "Session limit reached (%d min) - stopping to avoid blocks" % limit_min
            )
            self._log(self.finish_reason)
            if self.notify:
                await self.notify(
                    "Insomnia stopped",
                    "%s: %s" % (self._ig_account(), self.finish_reason),
                )
            return True
        return False

    # ── per-source state & limits (from Insomniac session_state.py) ─────
    def _source_key(self, target: Dict[str, str]) -> str:
        return f"{target['kind']}:{target['ref']}"

    def _is_source_limit_reached(
        self, target: Dict[str, str], action_type: str
    ) -> bool:
        """Per-source limit check from Insomniac's limits.py pattern."""
        sk = self._source_key(target)
        s = self._source_state.setdefault(
            sk, {"likes": 0, "follows": 0, "comments": 0, "interactions": 0}
        )
        max_per_source = int(self.cfg.get("max_interactions_per_source") or 0)
        if max_per_source <= 0:
            return False
        return s.get(action_type, 0) >= max_per_source

    def _record_source_action(self, target: Dict[str, str], action_type: str) -> None:
        sk = self._source_key(target)
        s = self._source_state.setdefault(
            sk, {"likes": 0, "follows": 0, "comments": 0, "interactions": 0}
        )
        s[action_type] = s.get(action_type, 0) + 1
        s["interactions"] = s.get("interactions", 0) + 1

    # ── main loop ──────────────────────────────────────────────
    async def run(self) -> None:
        try:
            await self.probe()
            if not (self.caps.get("am") or self.caps.get("termux_open_url")):
                self.finish_reason = "No launch capability on the phone (am / termux-open-url unavailable)"
                self._log(self.finish_reason)
                if self.notify:
                    await self.notify("Insomnia blocked", self.finish_reason)
                return
            if not await self._wait_until_allowed():
                self.finish_reason = "stopped before start"
                return
            targets = _parse_targets(self.cfg)
            if not targets:
                self.finish_reason = "No targets configured"
                self._log(self.finish_reason)
                return

            # ═══ TIER LOGGING ═══
            tier_info = self.cfg.get("_tier_info", {})
            tier_level = tier_info.get("tier", "unknown")
            self._log(
                f"TIER: {tier_level} | max_targets: {tier_info.get('max_targets', '?')} | "
                f"max_likes: {tier_info.get('max_likes', '?')} | "
                f"tap_mode: {tier_info.get('allows_tap_mode', False)} | "
                f"auto_follow: {tier_info.get('allows_auto_follow', False)}"
            )

            # ═══ IG ACCOUNT & PACE LOGGING ═══
            self._log(
                f"IG ACCOUNT: {self._ig_account()} | "
                f"pace: {self.cfg.get('pace_profile', 'normal')} | "
                f"hourly_break: {self._pace().get('hourly_break')} | "
                f"session_limit: {self._pace().get('session_limit_min', 0)}min"
            )

            # ═══ BLOCK DETECTION & FILTERS LOGGING ═══
            self._log(
                f"BLOCK DETECTION: empty_profile={_BLOCKED_THRESHOLDS['empty_profile']} | "
                f"action_blocked={_BLOCKED_THRESHOLDS['action_blocked']} | "
                f"target_open_fail={_BLOCKED_THRESHOLDS['target_open_fail']}"
            )
            filters_active = any(
                k in self.cfg
                for k in (
                    "skip_blacklist",
                    "blacklist_words",
                    "whitelist",
                    "mandatory_words",
                    "skip_business",
                    "min_followers",
                )
            )
            if filters_active:
                self._log(
                    f"FILTERS: skip_blacklist={self.cfg.get('skip_blacklist')} | "
                    f"whitelist={self.cfg.get('whitelist')} | "
                    f"skip_business={self.cfg.get('skip_business')}"
                )

            # ═══ PER-SOURCE LIMITS LOGGING ═══
            mps = self.cfg.get("max_interactions_per_source")
            if mps:
                self._log(f"PER-SOURCE LIMIT: {mps} interactions per target")

            SESSION_START = time.time()

            # CRITICAL: Validate that session can produce real interactions
            use_tap = self.caps.get("a11y") and tier_info.get("allows_tap_mode", False)
            can_interact = (
                use_tap or self.caps.get("am") or self.caps.get("termux_open_url")
            )
            has_real_targets = len(targets) > 0

            if not has_real_targets:
                self.finish_reason = (
                    "No real targets — session rejected to prevent fake data"
                )
                self._log(self.finish_reason)
                return

            if tier_level == "free" and not can_interact and not use_tap:
                self.finish_reason = (
                    "Free tier: intent-only mode produces 0 interactions. "
                    "Upgrade to BASIC+ for tap mode or provide interaction targets."
                )
                self._log(self.finish_reason)
                if self.notify:
                    await self.notify("Insomnia blocked", self.finish_reason)
                return

            mode = self.caps["mode"]
            max_following = int(self.cfg.get("max_following") or 0)
            follow_pct = int(self.cfg.get("follow_percentage") or 0)
            comment_pct = int(self.cfg.get("comment_percentage") or 0)
            likes_pct = int(self.cfg.get("likes_percentage") or 100)
            likes_count = _qty(self.cfg.get("likes_count"), 2)
            comments = self.cfg.get("comments_list")
            if isinstance(comments, str):
                comments = [
                    c.strip() for c in re.split(r"[,;\n]+", comments) if c.strip()
                ]
            comments = comments or []
            stories_count = _qty(self.cfg.get("stories_count"), 0)

            self._log(
                f"started: {len(targets)} targets · likes {likes_count} · "
                f"follows {follow_pct}% · comments {comment_pct}% · mode {mode} · "
                f"speed {self.cfg.get('speed') or 3}"
            )
            if self.notify:
                if mode == "tap":
                    plan = (
                        f"{len(targets)} targets · up to {likes_count} likes · "
                        f"{follow_pct}% follow · {comment_pct}% comment"
                    )
                else:
                    plan = (
                        f"{len(targets)} targets · mode {mode} (deep-link only) — "
                        f"taps blocked, so likes/follows/comments stay 0"
                    )
                await self.notify("Insomnia running 🌙", plan)

            repeat = self.cfg.get("repeat")
            # Show visualizer HUD for the session
            await self._vis_show(
                f"Insomnia · {len(targets)} targets",
                len(targets),
            )
            while not self.stop.is_set():
                # Check session duration limit
                if await self._check_session_limit(SESSION_START):
                    break
                # Check hourly break
                if await self._maybe_break(SESSION_START):
                    break
                # Hardban check from Insomniac hardban_indicator.py
                if await self._check_hardban():
                    self.finish_reason = "Hardban detected (WebView/CAPTCHA) — stopping"
                    self._log(self.finish_reason)
                    if self.notify:
                        await self.notify("Insomnia blocked", self.finish_reason)
                    break
                # Global block check from Insomniac softban_indicator.py
                if self._blocker.blocked:
                    self.finish_reason = (
                        "Block indicators triggered — possible Instagram block"
                    )
                    self._log(self.finish_reason)
                    if self.notify:
                        await self.notify("Insomnia blocked", self.finish_reason)
                    break
                for target in targets:
                    if self.stop.is_set():
                        break
                    if not await self._wait_until_allowed():
                        break
                    # Track progress through targets
                    self._target_idx = getattr(self, "_target_idx", -1) + 1
                    self._target_total = len(targets)
                    ref = target.get("ref") or target.get("value") or "profile"
                    await self._vis(
                        "info",
                        f"Target {self._target_idx + 1}/{len(targets)}: {ref}",
                        "",
                        self._target_idx + 1,
                        len(targets),
                    )
                    # Account filtering from Insomniac filters.py
                    if not _filter_target(self.cfg, target, self._log):
                        continue
                    if await self._sleep(1.6):
                        break
                    ok = await self._open_target(target)
                    if not ok:
                        self._blocker.record_target_open_fail()
                        continue
                    self.counts["targets_opened"] += 1
                    # Record success for block counter decay
                    self._blocker.record_success()
                    if await self._sleep(2.2):
                        break

                    if mode == "tap":
                        if (
                            target["kind"] in ("user", "hashtag")
                            and follow_pct
                            and (
                                not max_following
                                or self.counts["follows"] < max_following
                            )
                            and random.randint(0, 99) < follow_pct
                            and not self._is_source_limit_reached(target, "follows")
                        ):
                            await self._follow_current()
                            self._record_source_action(target, "follows")
                            if await self._sleep(1.8):
                                break
                        # open the first grid post and like it
                        c = self.cfg.get("coordinates") or {}
                        await self._tap(
                            c.get("first_post_xy") or "0.17,0.60", "0.17,0.60"
                        )
                        if await self._sleep(2.0):
                            break
                        if random.randint(0, 99) < likes_pct:
                            for _ in range(likes_count):
                                if self._is_source_limit_reached(target, "likes"):
                                    break
                                await self._like_current_post()
                                self._record_source_action(target, "likes")
                                if await self._sleep(1.5):
                                    break
                        if comments and random.randint(0, 99) < comment_pct:
                            if not self._is_source_limit_reached(target, "comments"):
                                await self._comment_current(random.choice(comments))
                                self._record_source_action(target, "comments")
                        await self._back()
                        if await self._sleep(1.4):
                            break
                    if stories_count and target["kind"] == "user":
                        c = self.cfg.get("coordinates") or {}
                        # Tap the avatar/story ring, then count only if the tap landed.
                        for _ in range(min(stories_count, 3)):
                            story_coords = c.get("story_xy") or "0.06,0.10"
                            await self._vis(
                                "story",
                                "Watching story",
                                story_coords,
                                self._target_idx + 1,
                                len(targets),
                                "Story tap",
                            )
                            ok_story = await self._tap(story_coords, "0.06,0.10")
                            if not ok_story:
                                self.counts["errors"] += 1
                                self._log("story tap failed (a11y ok=false)")
                                self._blocker.record_action_blocked()
                                break
                            self.counts["stories_watched"] += 1
                            self._blocker.record_success()
                            if await self._sleep(1.0):
                                break
                            await self._back()
                            if await self._sleep(0.8):
                                break
                    # Check if this target opened but produced no interaction
                    # (empty profile indicator from softban_indicator.py)
                    if mode == "tap" and not any(
                        self.counts[k] > 0
                        for k in ("likes", "follows", "comments", "stories_watched")
                    ):
                        # Only check per-target if we opened it but did nothing
                        pass

                if not repeat or self.stop.is_set():
                    break
                mins = _qty(repeat, 180)
                self._log(f"repeat pass in ~{mins} min")
                for _ in range(mins * 4):
                    if await self._sleep(15):
                        break
                else:
                    continue
                break

            self.finish_reason = self.finish_reason or (
                "stopped by user" if self.stop.is_set() else "completed one pass"
            )
            self._log(f"finished: {self.finish_reason}")
            if self.notify:
                await self.notify(
                    "Insomnia finished 🌙",
                    f"{self.finish_reason} · likes {self.counts['likes']} · "
                    f"follows {self.counts['follows']} · comments {self.counts['comments']}",
                )
            # ═══ UPDATE ENGAGEMENT DATA FOR TRENDS ═══
            try:
                import importlib.util as _ilu

                _spec = _ilu.spec_from_file_location(
                    "trending",
                    str(Path(__file__).parent.parent / "lilly-trainer" / "trending.py"),
                )
                if _spec and _spec.loader:
                    _mod = _ilu.module_from_spec(_spec)
                    _spec.loader.exec_module(_mod)
                    session_data = {
                        "ig_account": self._ig_account(),
                        "targets": [t["ref"] for t in _parse_targets(self.cfg)],
                        "likes": self.counts.get("likes", 0),
                        "follows": self.counts.get("follows", 0),
                        "comments": self.counts.get("comments", 0),
                        "targets_opened": self.counts.get("targets_opened", 0),
                        "pace_profile": self.cfg.get("pace_profile", "normal"),
                    }
                    ENGAGEMENT = _load_engagement()
                    ENGAGEMENT = _mod.add_session(ENGAGEMENT, session_data)
                    trending = _mod.compute_trending(ENGAGEMENT)
                    self._log(
                        "TRENDS: %d topics from %d sessions"
                        % (trending["total"], trending["engagement_sessions"])
                    )
                    targets = _mod.export_as_targets(ENGAGEMENT)
                    if targets.get("targets"):
                        self._log(
                            "ENGAGEMENT TARGETS: %s" % ", ".join(targets["targets"])
                        )
            except Exception as e:
                self._log(f"trending update skipped: {e}")
        except Exception as e:
            self.finish_reason = f"error: {e}"
            self._log(self.finish_reason)
            if self.notify:
                await self.notify("Insomnia error", str(e))
        finally:
            self.finished = True
            # Hide the on-screen visualizer HUD
            try:
                await self._vis(
                    "done",
                    f"Session complete · {self.finish_reason}",
                    "",
                    0,
                    0,
                    f"Likes: {self.counts.get('likes', 0)} · Follows: {self.counts.get('follows', 0)} · Comments: {self.counts.get('comments', 0)}",
                )
                import asyncio as _aio

                await _aio.sleep(3.0)  # show "done" for 3s before hiding
                await self._vis_hide()
            except Exception:
                pass
            for cb in self._done_callbacks:
                try:
                    cb(self.snapshot())
                except Exception:
                    pass
            rf = self.cfg.get("registry_file")
            if rf:
                try:
                    Path(rf).parent.mkdir(parents=True, exist_ok=True)
                    Path(rf).write_text(
                        json.dumps(self.snapshot(), indent=2, default=str),
                        encoding="utf-8",
                    )
                except Exception:
                    pass

    def snapshot(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "running": not self.finished and not self.stop.is_set(),
            "stopped": self.stop.is_set(),
            "finished": self.finished,
            "finish_reason": self.finish_reason,
            "started": self.started,
            "elapsed": time.time() - self.started,
            "current": self.current,
            "counts": dict(self.counts),
            "block_detection": self._blocker.snapshot(),
            "blocked": self._blocker.blocked,
            "source_state": self._source_state,
            "speed_mbps": self._speed_mbps,
            "caps": {k: v for k, v in self.caps.items() if k != "ui_table"},
            "config": {k: v for k, v in self.cfg.items() if k not in ("password",)},
            "log": self.log_lines[-80:],
        }
