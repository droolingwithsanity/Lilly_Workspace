#!/usr/bin/env python3
"""
instagram_scraper.py — Instagram scraper + dataset builder for the training dashboard.

Backs the "IG Scraper" tab in training.html. It uses the existing logged-in
avatar sessions (lilly_pup_insta) to:

  * scrape hashtags (top + recent) and usernames (profile + recent media)
  * auto-populate targets from the avatar accounts' followers
  * optionally run a daily scrape at a chosen local time (enable + time are
    stored in config and can be overridden live)
  * log every run and every target to scraper/log.jsonl, and persist all
    collected media to scraper/results.json

Routes (mounted by lilly_ai.py):
  GET    /api/scraper/status
  GET    /api/scraper/targets
  POST   /api/scraper/targets
  DELETE /api/scraper/targets/{target_id}
  POST   /api/scraper/targets/followers
  POST   /api/scraper/scrape
  POST   /api/scraper/stop
  GET    /api/scraper/results
  POST   /api/scraper/results/select
  POST   /api/scraper/csv-import
  GET    /api/scraper/export-training
  GET    /api/scraper/csv-sample
  GET    /api/scraper/schedule
  POST   /api/scraper/schedule
  GET    /api/scraper/logs

All routes are owner-gated via training_control._allowed_user.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import logging
import random
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import lilly_pup_insta as lpi
from training_control import _allowed_user, _deny

try:
    from scrapling_engine import is_nsfw
except Exception:
    def is_nsfw(_text: str) -> bool:
        return False

logger = logging.getLogger("instagram_scraper")

SCRAPER_DIR = lpi.DATA_DIR / "scraper"
TARGETS_FILE = SCRAPER_DIR / "targets.json"
RESULTS_FILE = SCRAPER_DIR / "results.json"
CONFIG_FILE = SCRAPER_DIR / "config.json"
LOG_FILE = SCRAPER_DIR / "log.jsonl"

DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": False,             # daily auto-scrape on/off
    "daily_time": "08:00",        # local HH:MM
    "auto_populate_followers": True,
    "followers_amount": 40,       # followers imported per avatar account
    "scrape_amount": 6,           # posts fetched per target
    "refresh_hours": 20,          # re-scrape a target after this many hours
    "max_results": 3000,          # hard cap on stored results
    "max_targets": 500,           # hard cap on stored targets
}

CSV_SAMPLE = (
    "id,type,value,mode\n"
    "t1,hashtag,technology,posts\n"
    "t2,username,openai,posts\n"
    "t3,username,somecreator,profile\n"
)


def _now() -> float:
    return time.time()


def _media_ts(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        if hasattr(value, "timestamp"):
            return float(value.timestamp())
        return float(value)
    except Exception:
        return 0.0


class InstagramScraper:
    """Thread-driven scraper so blocking instagrapi calls never block the app."""

    def __init__(self):
        SCRAPER_DIR.mkdir(parents=True, exist_ok=True)
        self.targets: List[Dict] = []
        self.results: List[Dict] = []
        self.config: Dict = dict(DEFAULT_CONFIG)
        self._result_keys: set = set()
        self._running = False
        self._status = "idle"
        self._stop_flag = False
        self._thread: Optional[threading.Thread] = None
        self._scheduler: Optional[threading.Thread] = None
        self._scheduler_started = False
        self._last_auto_date = ""
        self._avatar_i = 0
        self._lock = threading.Lock()
        self._load()
        self._result_keys = {
            self._key(r.get("username", ""), r.get("media_id", "")) for r in self.results
        }

    # ── persistence ───────────────────────────────────────────────────────
    def _load(self):
        for path, attr, default in (
            (TARGETS_FILE, "targets", []),
            (RESULTS_FILE, "results", []),
            (CONFIG_FILE, "config", dict(DEFAULT_CONFIG)),
        ):
            try:
                if path.exists():
                    setattr(self, attr, json.loads(path.read_text()))
            except Exception as e:
                logger.warning(f"scraper: failed to load {path.name}: {e}")
                setattr(self, attr, default)
        if not isinstance(self.targets, list):
            self.targets = []
        if not isinstance(self.results, list):
            self.results = []
        cfg = dict(DEFAULT_CONFIG)
        cfg.update(self.config or {})
        self.config = cfg

    def _save_targets(self):
        TARGETS_FILE.write_text(json.dumps(self.targets, indent=1, default=str))

    def _save_results(self):
        RESULTS_FILE.write_text(json.dumps(self.results, indent=1, default=str))

    def _save_config(self):
        CONFIG_FILE.write_text(json.dumps(self.config, indent=1, default=str))

    def _log(self, event: str, **fields):
        row = {"ts": _now(), "when": datetime.now().isoformat(timespec="seconds"),
               "event": event, **fields}
        try:
            with LOG_FILE.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, default=str) + "\n")
        except Exception as e:
            logger.debug(f"scraper: log write failed: {e}")

    # ── helpers ───────────────────────────────────────────────────────────
    @staticmethod
    def _key(username: str, media_id: str) -> str:
        return f"{(username or '').lower()}:{media_id}"

    @staticmethod
    def _slug(value: str) -> str:
        return (value or "").strip().lstrip("#@").strip()

    def _pick_client(self):
        mgr = lpi.get_session_manager()
        keys = list(lpi.AVATAR_INSTA_PERSONAS.keys())
        for i in range(len(keys)):
            key = keys[(self._avatar_i + i) % len(keys)]
            try:
                cl = mgr.get_client(key)
            except Exception:
                cl = None
            if cl is not None:
                self._avatar_i = (self._avatar_i + i + 1) % len(keys)
                return cl, key
        return None, ""

    def _add_target(self, ttype: str, value: str, mode: str = "posts",
                    notes: str = "", source: str = "manual") -> Optional[Dict]:
        ttype = (ttype or "").strip().lower()
        if ttype not in ("hashtag", "username"):
            return None
        value = self._slug(value)
        if not value:
            return None
        if is_nsfw(value):
            return None
        if any(t["type"] == ttype and t["value"].lower() == value.lower()
               for t in self.targets):
            return None
        if len(self.targets) >= int(self.config.get("max_targets", 500)):
            return None
        target = {
            "id": f"st_{int(_now())}_{hashlib.md5(f'{ttype}:{value}'.encode()).hexdigest()[:6]}",
            "type": ttype,
            "value": value,
            "mode": mode if mode in ("posts", "profile") else "posts",
            "notes": notes or "",
            "source": source,
            "status": "pending",
            "results_count": 0,
            "error": "",
            "added_at": _now(),
            "last_scraped": 0.0,
        }
        self.targets.append(target)
        return target

    def record_interaction(self, username: str, kind: str = "interaction") -> Optional[Dict]:
        """Auto-populate the target list from a live interaction (dm, comment, follow).
        Called by lilly_pup_insta whenever a real person engages. Never touches IG."""
        uname = self._slug(str(username or ""))
        if not uname:
            return None
        if any(t["type"] == "username" and t["value"].lower() == uname.lower()
               for t in self.targets):
            return None
        if is_nsfw(uname):
            return None
        if len(self.targets) >= int(self.config.get("max_targets", 500)):
            return None
        notes = f"auto ({kind}, {datetime.now().strftime('%b %d')})"
        target = self._add_target("username", uname, "posts", notes, source="interaction")
        if target:
            self._save_targets()
            self._log("target_auto", value=uname, kind=kind)
            logger.info(f"scraper: auto-added target @{uname} from {kind}")
        return target

    def _add_media_result(self, media, source_type: str, target_value: str,
                          author: str = "", followers: int = 0) -> bool:
        caption = (getattr(media, "caption_text", "") or "").strip()
        if is_nsfw(caption):
            return False
        user = getattr(media, "user", None)
        username = author or getattr(user, "username", "") or ""
        media_id = str(getattr(media, "id", "") or "")
        if not username or not media_id:
            return False
        key = self._key(username, media_id)
        if key in self._result_keys:
            return False
        if len(self.results) >= int(self.config.get("max_results", 3000)):
            return False
        code = getattr(media, "code", "") or ""
        row = {
            "id": f"sr_{int(_now())}_{hashlib.md5(key.encode()).hexdigest()[:8]}",
            "source_type": source_type,
            "target_value": target_value,
            "username": username,
            "full_name": getattr(user, "full_name", "") or "",
            "caption": caption[:500],
            "hashtags": " ".join(getattr(media, "hashtags", None) or []),
            "media_id": media_id,
            "media_code": code,
            "like_count": int(getattr(media, "like_count", 0) or 0),
            "comment_count": int(getattr(media, "comment_count", 0) or 0),
            "taken_at": _media_ts(getattr(media, "taken_at", None)),
            "followers": int(followers or 0),
            "url": f"https://www.instagram.com/p/{code}/" if code else "",
            "collected_at": _now(),
            "selected_for_training": False,
            "label": "",
        }
        self.results.append(row)
        self._result_keys.add(key)
        return True

    # ── scraping ──────────────────────────────────────────────────────────
    def _scrape_target(self, target: Dict) -> int:
        cl, avatar = self._pick_client()
        if cl is None:
            raise RuntimeError("no usable Instagram session (all logins failed)")
        amount = int(self.config.get("scrape_amount", 6))
        added = 0
        ttype = target["type"]
        value = target["value"]

        if ttype == "hashtag":
            medias = []
            try:
                medias += list(cl.hashtag_medias_top(value, amount=amount))
            except Exception as e:
                self._log("hashtag_top_error", target=value, error=str(e))
            try:
                medias += list(cl.hashtag_medias_recent(value, amount=amount))
            except Exception as e:
                self._log("hashtag_recent_error", target=value, error=str(e))
            for m in medias:
                if self._add_media_result(m, "hashtag", value):
                    added += 1
        else:
            info = cl.user_info_by_username(value)
            uid = getattr(info, "pk", None)
            followers = int(getattr(info, "follower_count", 0) or 0)
            target["ig_pk"] = str(uid or "")
            target["followers"] = followers
            if target.get("mode") == "profile":
                self._log("profile", target=value, followers=followers,
                          media=int(getattr(info, "media_count", 0) or 0))
            if uid:
                for m in cl.user_medias(uid, amount=amount):
                    if self._add_media_result(m, "profile", value,
                                              author=value, followers=followers):
                        added += 1
        time.sleep(random.uniform(1.5, 4.0))
        return added

    def _reset_stale_targets(self):
        refresh = float(self.config.get("refresh_hours", 20)) * 3600
        now = _now()
        for t in self.targets:
            if t["status"] == "done" and (now - float(t.get("last_scraped", 0) or 0)) > refresh:
                t["status"] = "pending"

    def _run_sync(self, mode: str = "both"):
        self._running = True
        self._status = "running"
        self._stop_flag = False
        self._log("run_start", mode=mode)
        try:
            if mode in ("both", "followers") and self.config.get("auto_populate_followers"):
                try:
                    added = self.populate_followers(int(self.config.get("followers_amount", 40)))
                    self._log("auto_populate_followers", added=added)
                except Exception as e:
                    self._log("auto_populate_followers_error", error=str(e))

            self._reset_stale_targets()
            queue = [t for t in self.targets if t["status"] in ("pending", "error")]

            for target in queue:
                if self._stop_flag:
                    self._log("stopped", remaining=len(queue))
                    break
                if len(self.results) >= int(self.config.get("max_results", 3000)):
                    self._log("max_results_reached", total=len(self.results))
                    break
                try:
                    n = self._scrape_target(target)
                    target["status"] = "done"
                    target["results_count"] = int(target.get("results_count", 0)) + n
                    target["last_scraped"] = _now()
                    target["error"] = ""
                    self._log("target_done", target=target["value"], results=n)
                except Exception as e:
                    target["status"] = "error"
                    target["error"] = str(e)
                    self._log("target_error", target=target["value"], error=str(e))
                self._save_targets()
                self._save_results()
                time.sleep(random.uniform(2.0, 6.0))
        finally:
            self._running = False
            self._status = "idle"
            self._save_targets()
            self._save_results()
            self._log("run_end", total_results=len(self.results))

    def start(self, mode: str = "both") -> Dict:
        if self._thread and self._thread.is_alive():
            return {"ok": False, "error": "already running", "status": self._status}
        self._thread = threading.Thread(target=self._run_sync, args=(mode,), daemon=True)
        self._thread.start()
        return {"ok": True, "status": "started", "mode": mode}

    def stop(self) -> Dict:
        self._stop_flag = True
        self._log("stop_requested")
        return {"ok": True, "status": "stopping"}

    def populate_followers(self, amount: int = 40) -> int:
        mgr = lpi.get_session_manager()
        seen = {t["value"].lower() for t in self.targets if t["type"] == "username"}
        added = 0
        for key in list(lpi.AVATAR_INSTA_PERSONAS.keys()):
            if added >= int(self.config.get("max_targets", 500)):
                break
            if self._stop_flag:
                break
            try:
                cl = mgr.get_client(key)
            except Exception:
                cl = None
            if cl is None:
                continue
            try:
                uid = cl.user_id
                followers = cl.user_followers(uid, amount=max(1, int(amount)))
            except Exception as e:
                self._log("followers_error", avatar=key, error=str(e))
                time.sleep(random.uniform(1.5, 3.5))
                continue
            for _pk, us in (followers or {}).items():
                username = getattr(us, "username", "") or ""
                if not username or username.lower() in seen or is_nsfw(username):
                    continue
                if self._add_target("username", username, mode="posts",
                                    notes=f"follower of {key}", source="follower"):
                    seen.add(username.lower())
                    added += 1
            self._save_targets()
            self._log("followers_scanned", avatar=key, added=added)
            time.sleep(random.uniform(2.0, 5.0))
        return added

    # ── scheduler ─────────────────────────────────────────────────────────
    def ensure_scheduler(self):
        if self._scheduler_started:
            return
        self._scheduler_started = True
        self._scheduler = threading.Thread(target=self._scheduler_loop, daemon=True)
        self._scheduler.start()
        self._log("scheduler_started", daily_time=self.config.get("daily_time"),
                  enabled=self.config.get("enabled"))

    def _scheduler_loop(self):
        while True:
            try:
                if self.config.get("enabled"):
                    now = datetime.now()
                    hhmm = now.strftime("%H:%M")
                    today = now.strftime("%Y-%m-%d")
                    if hhmm == self.config.get("daily_time", "08:00") and self._last_auto_date != today:
                        self._last_auto_date = today
                        self._log("auto_trigger", time=hhmm)
                        self.start("both")
                time.sleep(20)
            except Exception as e:
                self._log("scheduler_error", error=str(e))
                time.sleep(60)

    # ── views ─────────────────────────────────────────────────────────────
    def status(self) -> Dict:
        by_type: Dict[str, Dict[str, int]] = {}
        for t in self.targets:
            bucket = by_type.setdefault(t["type"], {"targets": 0, "results": 0})
            bucket["targets"] += 1
            bucket["results"] += int(t.get("results_count", 0) or 0)
        return {
            "status": self._status,
            "running": self._running,
            "targets_total": len(self.targets),
            "targets_pending": sum(1 for t in self.targets if t["status"] in ("pending", "error")),
            "results_total": len(self.results),
            "results_selected": sum(1 for r in self.results if r.get("selected_for_training")),
            "targets_by_type": by_type,
            "config": self.config,
            "next_run": self.config.get("daily_time") if self.config.get("enabled") else "",
        }

    def list_targets(self) -> List[Dict]:
        return self.targets

    def get_results(self, limit: int = 200, selected_only: bool = False) -> List[Dict]:
        rows = self.results
        if selected_only:
            rows = [r for r in rows if r.get("selected_for_training")]
        return rows[-max(1, int(limit)):][::-1]

    def select_results(self, ids: List[str], label: str = "") -> int:
        wanted = set(ids or [])
        n = 0
        for r in self.results:
            if r["id"] in wanted:
                r["selected_for_training"] = True
                if label:
                    r["label"] = label
                n += 1
        self._save_results()
        self._log("results_selected", count=n, label=label)
        return n

    def remove_target(self, target_id: str) -> bool:
        before = len(self.targets)
        self.targets = [t for t in self.targets if t["id"] != target_id]
        if len(self.targets) != before:
            self._save_targets()
            self._log("target_removed", target_id=target_id)
            return True
        return False

    def csv_import(self, csv_text: str) -> int:
        added = 0
        reader = csv.DictReader(io.StringIO(csv_text or ""))
        for row in reader:
            ttype = (row.get("type") or "").strip().lower()
            value = (row.get("value") or "").strip()
            mode = (row.get("mode") or "posts").strip().lower()
            notes = (row.get("notes") or "").strip()
            if self._add_target(ttype, value, mode=mode, notes=notes, source="csv"):
                added += 1
        if added:
            self._save_targets()
        self._log("csv_import", added=added)
        return added

    def export_training(self) -> List[Dict]:
        out = []
        for r in self.results:
            if not r.get("selected_for_training"):
                continue
            out.append({
                "source": "instagram",
                "target": r.get("target_value", ""),
                "username": r.get("username", ""),
                "caption": r.get("caption", ""),
                "hashtags": r.get("hashtags", ""),
                "followers": r.get("followers", 0),
                "label": r.get("label", ""),
                "quality_score": 0.5,
            })
        self._log("export_training", count=len(out))
        return out

    def read_logs(self, lines: int = 120) -> List[Dict]:
        lines = max(1, min(int(lines), 2000))
        if not LOG_FILE.exists():
            return []
        try:
            raw = LOG_FILE.read_text(encoding="utf-8").splitlines()[-lines:]
        except Exception:
            return []
        out = []
        for line in raw:
            try:
                out.append(json.loads(line))
            except Exception:
                continue
        return out

    def update_config(self, updates: Dict) -> Dict:
        for key in DEFAULT_CONFIG:
            if key not in updates:
                continue
            val = updates[key]
            if key in ("enabled", "auto_populate_followers"):
                self.config[key] = bool(val)
            elif key in ("followers_amount", "scrape_amount", "refresh_hours",
                         "max_results", "max_targets"):
                try:
                    self.config[key] = max(1, int(val))
                except Exception:
                    pass
            elif key == "daily_time":
                text = str(val).strip()
                if len(text) == 5 and text[2] == ":":
                    self.config[key] = text
        self._save_config()
        self._log("config_updated", **{k: self.config[k] for k in updates if k in DEFAULT_CONFIG})
        return self.config


_scraper: Optional[InstagramScraper] = None


def get_scraper() -> InstagramScraper:
    global _scraper
    if _scraper is None:
        _scraper = InstagramScraper()
        _scraper.ensure_scheduler()
    return _scraper


# ── routes ────────────────────────────────────────────────────────────────
router = APIRouter(tags=["scraper"])


@router.get("/api/scraper/status")
async def scraper_status(request: Request):
    if not await _allowed_user(request):
        return _deny()
    return get_scraper().status()


@router.get("/api/scraper/targets")
async def scraper_targets(request: Request):
    if not await _allowed_user(request):
        return _deny()
    return {"targets": get_scraper().list_targets()}


@router.post("/api/scraper/targets")
async def scraper_add_target(request: Request):
    if not await _allowed_user(request):
        return _deny()
    body = await request.json()
    scraper = get_scraper()
    target = scraper._add_target(
        body.get("type", ""),
        body.get("value", ""),
        mode=body.get("mode", "posts"),
        notes=body.get("notes", ""),
        source="manual",
    )
    if not target:
        return JSONResponse({"ok": False, "error": "invalid or duplicate target"}, status_code=400)
    scraper._save_targets()
    scraper._log("target_added", target=target["value"], type=target["type"])
    return {"ok": True, "target": target}


@router.delete("/api/scraper/targets/{target_id}")
async def scraper_remove_target(target_id: str, request: Request):
    if not await _allowed_user(request):
        return _deny()
    return {"ok": get_scraper().remove_target(target_id)}


@router.post("/api/scraper/targets/followers")
async def scraper_populate_followers(request: Request):
    if not await _allowed_user(request):
        return _deny()
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    scraper = get_scraper()
    amount = int(body.get("amount") or scraper.config.get("followers_amount", 40))
    added = await asyncio.to_thread(scraper.populate_followers, amount)
    return {"ok": True, "added": added, "targets_total": len(scraper.targets)}


@router.post("/api/scraper/scrape")
async def scraper_scrape(request: Request):
    if not await _allowed_user(request):
        return _deny()
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    return get_scraper().start(body.get("mode", "both"))


@router.post("/api/scraper/stop")
async def scraper_stop(request: Request):
    if not await _allowed_user(request):
        return _deny()
    return get_scraper().stop()


@router.get("/api/scraper/results")
async def scraper_results(request: Request, limit: int = 200, selected_only: bool = False):
    if not await _allowed_user(request):
        return _deny()
    return {"results": get_scraper().get_results(limit, selected_only)}


@router.post("/api/scraper/results/select")
async def scraper_select(request: Request):
    if not await _allowed_user(request):
        return _deny()
    body = await request.json()
    n = get_scraper().select_results(body.get("result_ids", []), body.get("label", ""))
    return {"ok": True, "selected": n}


@router.post("/api/scraper/csv-import")
async def scraper_csv_import(request: Request):
    if not await _allowed_user(request):
        return _deny()
    body = await request.json()
    added = get_scraper().csv_import(body.get("csv_text", ""))
    return {"ok": True, "added": added}


@router.get("/api/scraper/export-training")
async def scraper_export(request: Request):
    if not await _allowed_user(request):
        return _deny()
    return {"ok": True, "training_data": get_scraper().export_training()}


@router.get("/api/scraper/csv-sample")
async def scraper_csv_sample(request: Request):
    if not await _allowed_user(request):
        return _deny()
    return {"csv": CSV_SAMPLE}


@router.get("/api/scraper/schedule")
async def scraper_schedule(request: Request):
    if not await _allowed_user(request):
        return _deny()
    return {"ok": True, "config": get_scraper().config}


@router.post("/api/scraper/schedule")
async def scraper_schedule_update(request: Request):
    if not await _allowed_user(request):
        return _deny()
    body = await request.json()
    return {"ok": True, "config": get_scraper().update_config(body)}


@router.get("/api/scraper/logs")
async def scraper_logs(request: Request, lines: int = 120):
    if not await _allowed_user(request):
        return _deny()
    return {"ok": True, "logs": get_scraper().read_logs(lines)}
