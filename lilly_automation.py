"""Lilly AI Automation Engine

Provides:
- Named automation storage and execution
- Variable substitution in automation steps
- Figranium browser automation API integration
- TikHub social media data API integration
- Scrapling web scraping / crawling integration
- Quick web scraping via httpx + BeautifulSoup
- Step recorder for "follow me and learn" mode
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

AUTOMATIONS_DIR = Path("/home/labhrasd/Lilly_Workspace/automations")
AUTOMATIONS_DIR.mkdir(parents=True, exist_ok=True)

FIGRANIUM_URL = os.environ.get("FIGRANIUM_URL", "https://figranium.dev")
FIGRANIUM_API_KEY = os.environ.get("FIGRANIUM_API_KEY", "")

TIKHUB_API_URL = os.environ.get("TIKHUB_API_URL", "https://api.tikhub.io/api/v1")
TIKHUB_API_KEY = os.environ.get("TIKHUB_API_KEY", "")

SHELL_TIMEOUT = 300  # seconds
API_TIMEOUT = 60  # seconds
SCRAPE_TIMEOUT = 30  # seconds


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class AutomationStep:
    type: str  # shell_command | api_call | figranium_task | browser_action | delay | condition
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AutomationStep":
        return cls(
            type=data.get("type", "shell_command"),
            content=data.get("content", ""),
            metadata=data.get("metadata", {}),
        )


@dataclass
class Automation:
    name: str
    description: str
    steps: List[AutomationStep]
    created_at: float = field(default_factory=time.time)
    last_run: Optional[float] = None
    run_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "steps": [s.to_dict() for s in self.steps],
            "created_at": self.created_at,
            "last_run": self.last_run,
            "run_count": self.run_count,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Automation":
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            steps=[AutomationStep.from_dict(s) for s in data.get("steps", [])],
            created_at=data.get("created_at", time.time()),
            last_run=data.get("last_run"),
            run_count=data.get("run_count", 0),
        )

    def path(self) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in self.name)
        return AUTOMATIONS_DIR / f"{safe}.json"


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------


def _load_automation(name: str) -> Optional[Automation]:
    p = Automation(name=name, description="", steps=[]).path()
    if not p.exists():
        return None
    try:
        return Automation.from_dict(json.loads(p.read_text()))
    except Exception:
        return None


def _save_automation(automation: Automation) -> None:
    automation.path().write_text(json.dumps(automation.to_dict(), indent=2))


def _delete_automation_file(name: str) -> bool:
    p = Automation(name=name, description="", steps=[]).path()
    if p.exists():
        p.unlink()
        return True
    return False


# ---------------------------------------------------------------------------
# Variable substitution
# ---------------------------------------------------------------------------


def _substitute(content: str, variables: Dict[str, Any]) -> str:
    """Replace {var} placeholders in a string with values from variables dict."""
    result = content
    for key, value in variables.items():
        result = result.replace("{" + str(key) + "}", str(value))
    return result


# ---------------------------------------------------------------------------
# Step execution
# ---------------------------------------------------------------------------


def _execute_phone_command(content: str, variables: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a command on the paired phone via the lilly_ai.py /api/phone_cmd proxy.

    Content format: "command_type|payload" or just "command_type"
    Examples:
      "termux|termux-battery-status"
      "shell|ls -la"
      "open_url|https://maps.google.com"
      "notification|list"
    """
    try:
        parts = content.split("|", 1)
        cmd_type = parts[0].strip()
        payload = parts[1].strip() if len(parts) > 1 else ""

        # Try calling the local lilly_ai phone_cmd endpoint
        phone_cmd_url = os.environ.get(
            "LILLY_PHONE_CMD_URL", "http://localhost:8098/api/phone_cmd"
        )
        token = variables.get("_phone_token", "") or os.environ.get(
            "LILLY_DEVICE_TOKEN", ""
        )

        body = {"type": cmd_type, "token": token}
        if payload:
            body["payload"] = payload
        if variables:
            body["variables"] = variables

        with httpx.Client(timeout=API_TIMEOUT) as client:
            resp = client.post(phone_cmd_url, json=body)
            return {
                "ok": resp.status_code < 400,
                "type": "phone_command",
                "cmd_type": cmd_type,
                "status": resp.status_code,
                "result": resp.text[:4000],
            }
    except Exception as e:
        return {"ok": False, "type": "phone_command", "error": str(e)}


def _execute_step(step: AutomationStep, variables: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a single automation step and return its result."""
    step_type = step.type.lower()
    content = _substitute(step.content, variables)

    if step_type == "delay":
        try:
            seconds = float(content)
        except ValueError:
            seconds = 1.0
        time.sleep(min(seconds, 60))
        return {"ok": True, "type": "delay", "result": f"Waited {seconds}s"}

    if step_type == "shell_command":
        try:
            proc = subprocess.run(
                content,
                shell=True,
                capture_output=True,
                text=True,
                timeout=SHELL_TIMEOUT,
            )
            return {
                "ok": proc.returncode == 0,
                "type": "shell_command",
                "result": proc.stdout.strip(),
                "error": proc.stderr.strip() if proc.returncode != 0 else "",
                "returncode": proc.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"ok": False, "type": "shell_command", "error": "Command timed out"}
        except Exception as e:
            return {"ok": False, "type": "shell_command", "error": str(e)}

    if step_type == "phone_command":
        return _execute_phone_command(content, variables)

    if step_type == "api_call":
        try:
            parts = content.split()
            if len(parts) >= 2:
                method = parts[0].upper()
                url = parts[1]
                body = variables.get("_api_body", None)
            else:
                method = "GET"
                url = content
                body = None

            headers = {"Content-Type": "application/json"}
            if body is None:
                body = {}

            with httpx.Client(timeout=API_TIMEOUT) as client:
                resp = client.request(method, url, json=body, headers=headers)
                return {
                    "ok": resp.status_code < 400,
                    "type": "api_call",
                    "status": resp.status_code,
                    "result": resp.text[:4000],
                }
        except Exception as e:
            return {"ok": False, "type": "api_call", "error": str(e)}

    if step_type == "figranium_task":
        return scrapling_execute(url=content, action="fetch")

    if step_type == "tikhub_task":
        return tikhub_execute(content, variables)

    if step_type == "browser_action":
        return browser_scrape(content, variables.get("selector", ""))

    return {"ok": False, "error": f"Unknown step type: {step_type}"}


# ---------------------------------------------------------------------------
# Public API: learn / run / list / delete
# ---------------------------------------------------------------------------


def learn_automation(
    name: str,
    description: str,
    steps: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Create or update a named automation."""
    try:
        automation = Automation(
            name=name,
            description=description,
            steps=[AutomationStep.from_dict(s) for s in steps],
        )
        _save_automation(automation)
        return {
            "ok": True,
            "result": f"Learned automation '{name}' with {len(steps)} steps.",
            "automation": automation.to_dict(),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def run_automation(
    name: str, variables: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Execute a named automation with optional variable substitution."""
    variables = variables or {}
    automation = _load_automation(name)
    if automation is None:
        return {"ok": False, "error": f"Automation '{name}' not found."}

    results: List[Dict[str, Any]] = []
    for idx, step in enumerate(automation.steps):
        result = _execute_step(step, variables)
        result["step"] = idx + 1
        result["step_type"] = step.type
        results.append(result)
        if not result.get("ok", False):
            automation.last_run = time.time()
            automation.run_count += 1
            _save_automation(automation)
            return {
                "ok": False,
                "error": result.get("error", "Step failed"),
                "failed_at_step": idx + 1,
                "results": results,
            }

    automation.last_run = time.time()
    automation.run_count += 1
    _save_automation(automation)

    return {
        "ok": True,
        "result": f"Automation '{name}' completed successfully.",
        "results": results,
    }


def list_automations() -> Dict[str, Any]:
    """Return all stored automations."""
    automations = []
    for p in AUTOMATIONS_DIR.glob("*.json"):
        try:
            automations.append(json.loads(p.read_text()))
        except Exception:
            continue
    return {"ok": True, "automations": automations}


def delete_automation(name: str) -> Dict[str, Any]:
    """Delete a named automation."""
    if _delete_automation_file(name):
        return {"ok": True, "result": f"Deleted automation '{name}'."}
    return {"ok": False, "error": f"Automation '{name}' not found."}


def export_automation(name: str) -> Dict[str, Any]:
    """Export an automation as JSON."""
    automation = _load_automation(name)
    if automation is None:
        return {"ok": False, "error": f"Automation '{name}' not found."}
    return {"ok": True, "automation": automation.to_dict()}


def import_automation(data: Dict[str, Any]) -> Dict[str, Any]:
    """Import an automation from JSON dict."""
    try:
        automation = Automation.from_dict(data)
        _save_automation(automation)
        return {
            "ok": True,
            "result": f"Imported automation '{automation.name}'.",
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Figranium integration
# ---------------------------------------------------------------------------


def scrapling_execute(
    url: str,
    action: str = "fetch",
    selector: str = "",
    wait_seconds: float = 0,
    max_results: int = 20,
) -> Dict[str, Any]:
    """Execute a browser automation task using Scrapling.

    Actions:
      - fetch: get page content/text
      - extract: extract elements by CSS selector or XPath
      - screenshot: return page screenshot (base64)
      - crawl: follow links and return discovered URLs
    """
    try:
        from scrapling.fetchers import Fetcher, DynamicFetcher
    except ImportError:
        return {
            "ok": False,
            "error": "scrapling not installed. Run: pip install 'scrapling[fetchers]'",
        }

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; LillyAI/1.0; +https://droolingwithsanity.ca)",
            "Accept": "text/html,application/xhtml+xml",
        }

        if action == "screenshot":
            try:
                with DynamicFetcher(headless=True, disable_resources=False) as fetcher:
                    page = fetcher.fetch(url, network_idle=True, load_dom=False)
                    screenshot_bytes = page.screenshot(full_page=False)
                    import base64

                    return {
                        "ok": True,
                        "type": "scrapling_screenshot",
                        "url": url,
                        "screenshot_b64": base64.b64encode(screenshot_bytes).decode(
                            "utf-8"
                        ),
                    }
            except Exception as e:
                return {"ok": False, "type": "scrapling_screenshot", "error": str(e)}

        if action == "crawl":
            discovered = []
            try:
                with Fetcher(headless=True) as fetcher:
                    page = fetcher.fetch(url, load_dom=False)
                    links = (
                        page.xpath("//a/@href").getall()
                        if hasattr(page, "xpath")
                        else []
                    )
                    discovered = list(dict.fromkeys([url, *links]))[:max_results]
            except Exception as e:
                return {"ok": False, "type": "scrapling_crawl", "error": str(e)}
            return {
                "ok": True,
                "type": "scrapling_crawl",
                "url": url,
                "discovered_urls": discovered,
                "count": len(discovered),
            }

        # Default: fetch + optional extract
        use_dynamic = False
        try:
            with Fetcher(headless=True) as fetcher:
                page = fetcher.fetch(url, load_dom=False)
                if wait_seconds > 0:
                    import time as _time

                    _time.sleep(min(wait_seconds, 30))
        except Exception:
            use_dynamic = True

        if use_dynamic:
            try:
                with DynamicFetcher(headless=True, disable_resources=False) as fetcher:
                    page = fetcher.fetch(url, network_idle=True, load_dom=False)
                    if wait_seconds > 0:
                        import time as _time

                        _time.sleep(min(wait_seconds, 30))
            except Exception as e:
                return {"ok": False, "type": "scrapling_fetch", "error": str(e)}

        if action == "extract" and selector:
            try:
                if selector.startswith("//") or selector.startswith("("):
                    elements = page.xpath(selector).getall()
                else:
                    elements = page.css(selector).getall()
                data = [str(el).strip() for el in elements[:max_results]]
                return {
                    "ok": True,
                    "type": "scrapling_extract",
                    "url": url,
                    "selector": selector,
                    "count": len(data),
                    "data": data,
                }
            except Exception as e:
                return {"ok": False, "type": "scrapling_extract", "error": str(e)}

        # Default fetch: return title + text
        title = ""
        try:
            title = page.xpath("//title/text()").get() or ""
        except Exception:
            pass

        text = ""
        try:
            text = page.get_text(strip=True)
            lines = [line for line in text.split("\n") if line.strip()][:max_results]
            text = "\n".join(lines)
        except Exception:
            pass

        return {
            "ok": True,
            "type": "scrapling_fetch",
            "url": url,
            "title": title,
            "text": text[:8000],
        }

    except Exception as e:
        return {"ok": False, "type": "scrapling_execute", "error": str(e)}


# ---------------------------------------------------------------------------
# TikHub social media API integration
# ---------------------------------------------------------------------------


def tikhub_execute(
    endpoint: str, variables: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Execute a TikHub social media API call.

    Args:
        endpoint: API path, e.g. "tiktok/videos/search" or raw URL
        variables: Query parameters or request body

    Supported platforms:
      - TikTok: videos, users, trending, search
      - Douyin: videos, search, billboards
      - Instagram: posts, reels, stories, profiles
      - YouTube: videos, channels, shorts, comments
      - X/Twitter: tweets, profiles, trends
      - Rednote/Xiaohongshu: notes, users, search
      - Bilibili: videos, users, comments, live
      - Weibo: posts, users, topics
      - Kuaishou: videos, users, live
      - WeChat: articles, videos
      - Lemon8: posts, users, trending
      - Zhihu: articles, answers, topics
    """
    variables = variables or {}
    if not TIKHUB_API_KEY:
        return {"ok": False, "error": "TIKHUB_API_KEY not set in environment."}

    # Allow raw full URLs
    if endpoint.startswith("http://") or endpoint.startswith("https://"):
        url = endpoint
    else:
        # Normalize endpoint path
        path = endpoint.lstrip("/")
        if not path.startswith("hub/"):
            path = f"hub/{path}"
        url = f"{TIKHUB_API_URL}/{path}"

    headers = {
        "Authorization": f"Bearer {TIKHUB_API_KEY}",
        "Accept": "application/json",
    }

    try:
        with httpx.Client(timeout=API_TIMEOUT) as client:
            method = variables.pop("_method", "GET").upper()
            body = variables.pop("_body", None)
            params = variables if method == "GET" else None
            json_body = body if method == "POST" else None

            if method == "GET":
                resp = client.get(url, params=params, headers=headers)
            elif method == "POST":
                resp = client.post(url, json=json_body or variables, headers=headers)
            else:
                resp = client.request(
                    method, url, json=json_body or variables, headers=headers
                )

            return {
                "ok": resp.status_code < 400,
                "type": "tikhub_api",
                "platform": _detect_tikhub_platform(endpoint),
                "endpoint": endpoint,
                "status": resp.status_code,
                "result": resp.text[:4000],
            }
    except Exception as e:
        return {"ok": False, "type": "tikhub_api", "error": str(e)}


def figranium_execute(
    task_id: str, variables: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Execute a Figranium browser automation task.

    Args:
        task_id: Figranium task ID or full task path
        variables: Runtime variables to inject into the workflow

    Returns:
        Dict with ok, type, status, result (JSON/text), screenshot_url, logs
    """
    variables = variables or {}
    if not FIGRANIUM_API_KEY:
        return {"ok": False, "error": "FIGRANIUM_API_KEY not set in environment."}

    # Allow raw full URLs
    if task_id.startswith("http://") or task_id.startswith("https://"):
        url = task_id
    else:
        path = task_id.lstrip("/")
        if not path.startswith("api/tasks/"):
            path = f"api/tasks/{path}/api"
        elif not path.endswith("/api"):
            path = f"{path}/api"
        url = f"{FIGRANIUM_URL}/{path}"

    headers = {
        "Authorization": f"Bearer {FIGRANIUM_API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    try:
        with httpx.Client(timeout=API_TIMEOUT) as client:
            resp = client.post(url, json={"variables": variables}, headers=headers)

            result_text = resp.text[:4000]
            parsed = None
            try:
                parsed = resp.json()
            except Exception:
                pass

            return {
                "ok": resp.status_code < 400,
                "type": "figranium_task",
                "task_id": task_id,
                "status": resp.status_code,
                "result": parsed or result_text,
                "screenshot_url": parsed.get("screenshot_url")
                if isinstance(parsed, dict)
                else None,
                "logs": parsed.get("logs", []) if isinstance(parsed, dict) else [],
            }
    except Exception as e:
        return {"ok": False, "type": "figranium_task", "error": str(e)}


def _detect_tikhub_platform(endpoint: str) -> str:
    """Detect platform from TikHub endpoint path."""
    path = endpoint.lower()
    if "tiktok" in path:
        return "tiktok"
    if "douyin" in path:
        return "douyin"
    if "instagram" in path:
        return "instagram"
    if "youtube" in path:
        return "youtube"
    if "twitter" in path or "x/" in path:
        return "twitter"
    if "rednote" in path or "xiaohongshu" in path:
        return "rednote"
    if "bilibili" in path:
        return "bilibili"
    if "weibo" in path:
        return "weibo"
    if "kuaishou" in path:
        return "kuaishou"
    if "wechat" in path:
        return "wechat"
    if "lemon" in path:
        return "lemon8"
    if "zhihu" in path:
        return "zhihu"
    return "unknown"


# ---------------------------------------------------------------------------
# Quick browser scraping (legacy fallback)
# ---------------------------------------------------------------------------


def browser_scrape(url: str, selector: str = "") -> Dict[str, Any]:
    """Quick web scrape using Scrapling or httpx + BeautifulSoup fallback."""
    result = scrapling_execute(url, action="fetch", selector=selector)
    if result.get("ok"):
        return result
    if "scrapling not installed" in result.get("error", ""):
        return result
    return _browser_scrape_bs4(url, selector)


def _browser_scrape_bs4(url: str, selector: str = "") -> Dict[str, Any]:
    """Fallback scrape using httpx + BeautifulSoup."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return {
            "ok": False,
            "error": "beautifulsoup4 not installed. Run: pip install beautifulsoup4",
        }

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; LillyAI/1.0; +https://droolingwithsanity.ca)",
            "Accept": "text/html,application/xhtml+xml",
        }
        with httpx.Client(timeout=SCRAPE_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        if selector:
            elements = soup.select(selector)
            data = [el.get_text(strip=True) for el in elements]
            return {
                "ok": True,
                "type": "browser_scrape",
                "url": url,
                "selector": selector,
                "count": len(data),
                "data": data[:100],
            }

        title = soup.title.get_text(strip=True) if soup.title else ""
        meta_desc = ""
        meta_tag = soup.find("meta", attrs={"name": "description"})
        if meta_tag and meta_tag.get("content"):
            meta_desc = str(meta_tag.get("content", "")).strip()

        for tag in soup(["script", "style", "nav", "header", "footer"]):
            tag.decompose()

        text = soup.get_text(separator="\n", strip=True)
        lines = [line for line in text.split("\n") if line.strip()][:200]

        return {
            "ok": True,
            "type": "browser_scrape",
            "url": url,
            "title": title,
            "description": meta_desc,
            "text_lines": lines,
        }

    except Exception as e:
        return {"ok": False, "type": "browser_scrape", "error": str(e)}


# ---------------------------------------------------------------------------
# In-memory step recorder for "follow me" mode
# ---------------------------------------------------------------------------


class StepRecorder:
    """Records user actions for later automation creation."""

    def __init__(self) -> None:
        self.recording: bool = False
        self.steps: List[AutomationStep] = []
        self.session_id: Optional[str] = None

    def start(self, session_name: str = "") -> Dict[str, Any]:
        self.recording = True
        self.steps = []
        self.session_id = session_name or str(uuid.uuid4())[:8]
        return {"ok": True, "result": f"Recording started: {self.session_id}"}

    def stop(self) -> Dict[str, Any]:
        self.recording = False
        return {
            "ok": True,
            "result": f"Recording stopped. Captured {len(self.steps)} steps.",
            "steps": [s.to_dict() for s in self.steps],
            "session_id": self.session_id,
        }

    def record(
        self, step_type: str, content: str, metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        if not self.recording:
            return
        self.steps.append(
            AutomationStep(
                type=step_type,
                content=content,
                metadata=metadata or {},
            )
        )

    def is_recording(self) -> bool:
        return self.recording


# Global recorder instance
_recorder = StepRecorder()


def get_recorder() -> StepRecorder:
    return _recorder
