"""Blink Camera Connector for Lilly AI.

Connects to Blink home security cameras via the blinkpy library.
Provides snap_picture, get_latest_image, and list_cameras functionality.

Install:
  pip install blinkpy

Environment variables:
  BLINK_USERNAME   — Blink account email
  BLINK_PASSWORD   — Blink account password
  BLINK_MANAGED    — Set 'true' if using a managed/2FA account (default: false)

Usage:
  connector = BlinkConnector()
  await connector.start()
  cameras = await connector.list_cameras()
  image_bytes = await connector.get_snapshot("Front Door")
  await connector.stop()
"""

import os
import asyncio
import logging
import json
import time
import base64
import tempfile
from typing import Optional
from pathlib import Path

logger = logging.getLogger("blink-connector")

BLINK_USERNAME = os.environ.get("BLINK_USERNAME", "")
BLINK_PASSWORD = os.environ.get("BLINK_PASSWORD", "")
BLINK_MANAGED = os.environ.get("BLINK_MANAGED", "false").lower() == "true"

# Cooldown: prevent repeated start() calls that trigger multiple SMS codes
_RETRY_COOLDOWN_SECONDS = 120  # wait 120s between login attempts
_RATE_LIMIT_COOLDOWN_SECONDS = (
    300  # 5min cooldown after HTTP 429 from Blink (they block longer)
)
_TOKEN_CACHE_PATH = Path("/tmp/blink_auth_tokens.json")
_2FA_STATE_PATH = Path("/tmp/blink_2fa_state.json")

# Optional import — blinkpy may not be installed in all environments
try:
    from blinkpy.blinkpy import BlinkTwoFARequiredError as _BlinkTwoFARequiredError
except ImportError:  # pragma: no cover
    _BlinkTwoFARequiredError = Exception


class BlinkConnector:
    """Manages connection to Blink camera system."""

    def __init__(self):
        self._blink = None
        self._session = None
        self._started = False
        self._cameras = {}
        self._pending_2fa = False
        self._last_attempt: float = 0  # timestamp of last start() attempt
        self._cooldown_remaining: float = 0  # seconds left on cooldown
        self._rate_limited: bool = False  # True if Blink returned 429
        # Restore persisted 2FA state from disk (survives container restarts)
        self._restore_2fa_state()

    def _in_cooldown(self) -> bool:
        """Check if we're in the retry cooldown window."""
        if self._last_attempt == 0:
            return False
        # Use longer cooldown if rate-limited (429)
        cooldown = (
            _RATE_LIMIT_COOLDOWN_SECONDS
            if self._rate_limited
            else _RETRY_COOLDOWN_SECONDS
        )
        elapsed = time.time() - self._last_attempt
        if elapsed < cooldown:
            self._cooldown_remaining = round(cooldown - elapsed, 1)
            return True
        self._cooldown_remaining = 0
        self._rate_limited = False  # clear rate-limit flag after cooldown expires
        return False

    def _save_tokens(self):
        """Persist auth tokens to disk so container restarts don't need new 2FA."""
        if not self._blink or not self._blink.auth:
            return
        try:
            auth = self._blink.auth
            token_data = {
                "token": getattr(auth, "token", None),
                "refresh_token": getattr(auth, "refresh_token", None),
                "expiration_date": getattr(auth, "expiration_date", None),
                "client_id": getattr(auth, "client_id", None),
                "account_id": getattr(auth, "account_id", None),
                "user_id": getattr(auth, "user_id", None),
                "host": getattr(auth, "host", None),
                "region_id": getattr(auth, "region_id", None),
                "client_id": getattr(auth, "client_id", None),
            }
            _TOKEN_CACHE_PATH.write_text(json.dumps(token_data))
            logger.info("Blink: Auth tokens saved to disk")
        except Exception as e:
            logger.debug(f"Blink: Could not save tokens — {e}")

    def _load_tokens(self) -> bool:
        """Load cached auth tokens from disk."""
        if not _TOKEN_CACHE_PATH.exists():
            return False
        try:
            data = json.loads(_TOKEN_CACHE_PATH.read_text())
            if not data.get("token"):
                return False
            return data
        except Exception:
            return False

    def _save_2fa_state(self):
        """Persist the full Blink auth state when 2FA is required.

        This saves the OAuth login session data so that after a container
        restart, complete_2fa_login() can resume the same session instead
        of needing a fresh SMS code.
        """
        if not self._blink or not self._blink.auth:
            return
        try:
            auth = self._blink.auth
            state = {
                "auth_data": dict(auth.data) if hasattr(auth, "data") else {},
                "token": getattr(auth, "token", None),
                "refresh_token": getattr(auth, "refresh_token", None),
                "expiration_date": getattr(auth, "expiration_date", None),
                "client_id": getattr(auth, "client_id", None),
                "account_id": getattr(auth, "account_id", None),
                "user_id": getattr(auth, "user_id", None),
                "host": getattr(auth, "host", None),
                "region_id": getattr(auth, "region_id", None),
                "hardware_id": getattr(auth, "hardware_id", None),
            }
            _2FA_STATE_PATH.write_text(json.dumps(state))
            logger.info("Blink: 2FA state saved to disk")
        except Exception as e:
            logger.debug(f"Blink: Could not save 2FA state — {e}")

    def _restore_2fa_state(self):
        """Restore persisted 2FA state from disk after container restart.

        If a 2FA session was in progress when the container stopped, this
        sets _pending_2fa=True so the UI shows the code input, and stores
        the auth data so complete_2fa_login() can resume the session.
        """
        if not _2FA_STATE_PATH.exists():
            return
        try:
            state = json.loads(_2FA_STATE_PATH.read_text())
            if state.get("auth_data") or state.get("token"):
                self._pending_2fa = True
                self._persisted_2fa_state = state
                logger.info("Blink: Restored pending 2FA state from disk")
            else:
                self._persisted_2fa_state = None
        except Exception:
            self._persisted_2fa_state = None

    def _clear_2fa_state(self):
        """Remove persisted 2FA state from disk."""
        try:
            if _2FA_STATE_PATH.exists():
                _2FA_STATE_PATH.unlink()
        except Exception:
            pass
        self._persisted_2fa_state = None

    async def start(self) -> bool:
        """Initialize Blink connection. Returns True if successful."""
        if self._started:
            return True

        # Enforce cooldown — don't hammer the Blink API
        if self._in_cooldown():
            logger.info(
                f"Blink: Cooldown active — {self._cooldown_remaining}s remaining. "
                "Waiting before retry."
            )
            return False

        if not BLINK_USERNAME or not BLINK_PASSWORD:
            logger.warning(
                "Blink: No credentials configured (set BLINK_USERNAME + BLINK_PASSWORD)"
            )
            return False

        try:
            from blinkpy.blinkpy import Blink
            from blinkpy.auth import Auth
            from blinkpy.blinkpy import BlinkTwoFARequiredError
            from aiohttp import ClientSession
            import logging as _log

            # Record this attempt for cooldown tracking
            self._last_attempt = time.time()

            self._session = ClientSession()
            self._blink = Blink(session=self._session)

            # Try loading cached tokens first to skip 2FA
            cached = self._load_tokens()
            if cached:
                logger.info("Blink: Restoring cached auth tokens...")
                self._blink.auth.data["username"] = BLINK_USERNAME
                self._blink.auth.data["password"] = BLINK_PASSWORD
                self._blink.auth.token = cached.get("token")
                self._blink.auth.refresh_token = cached.get("refresh_token")
                self._blink.auth.expiration_date = cached.get("expiration_date")
                self._blink.auth.client_id = cached.get("client_id")
                self._blink.auth.account_id = cached.get("account_id")
                self._blink.auth.user_id = cached.get("user_id")
                self._blink.auth.host = cached.get("host")
                self._blink.auth.region_id = cached.get("region_id")
                self._blink.auth.no_prompt = True
            else:
                # Provide credentials directly on the Auth object and set
                # no_prompt=True to prevent the library from calling input() /
                # getpass() on stdin (which fails with EOF in a headless server).
                self._blink.auth.data["username"] = BLINK_USERNAME
                self._blink.auth.data["password"] = BLINK_PASSWORD
                self._blink.auth.no_prompt = True

            # Capture blinkpy log output during start() to detect 429
            _blink_log_capture = []
            _blink_handler = _log.Handler()
            _blink_handler.emit = lambda record: _blink_log_capture.append(
                record.getMessage()
            )
            blinkpy_logger = _log.getLogger("blinkpy")
            blinkpy_logger.addHandler(_blink_handler)

            logger.info(f"Blink: Connecting as {BLINK_USERNAME}...")
            await self._blink.start()

            # Remove capture handler
            blinkpy_logger.removeHandler(_blink_handler)

            # Check if blinkpy logged a 429 rate limit
            _all_log = " ".join(_blink_log_capture).lower()
            _saw_429 = "429" in _all_log or "rate" in _all_log or "too many" in _all_log

            # Verify we actually got sync modules — blinkpy may "start"
            # but return empty sync if auth silently failed (e.g. HTTP 429).
            if not self._blink.sync:
                if _saw_429:
                    self._rate_limited = True
                    self._last_attempt = time.time()
                    logger.error(
                        f"Blink: Rate limited (429) — waiting "
                        f"{_RATE_LIMIT_COOLDOWN_SECONDS}s before retry"
                    )
                else:
                    self._last_attempt = time.time()
                    logger.warning(
                        "Blink: Login succeeded but no sync modules found — "
                        "auth may have failed. Retry later."
                    )
                return False

            self._started = True
            self._pending_2fa = False
            self._cameras = {}
            for name, module in self._blink.sync.items():
                for cam_name, cam in module.cameras.items():
                    self._cameras[cam_name] = {
                        "name": cam_name,
                        "sync_module": name,
                        "camera": cam,
                        "online": getattr(cam, "online", True),
                        "battery": getattr(cam, "battery_level", "unknown"),
                        "motion": getattr(cam, "motion_detected", False),
                    }
            logger.info(f"Blink: Connected — {len(self._cameras)} camera(s) found")

            # Persist tokens so next restart doesn't need 2FA again
            self._save_tokens()
            return True

        except ImportError:
            logger.error("Blink: blinkpy not installed — run: pip install blinkpy")
            return False
        except _BlinkTwoFARequiredError:
            # Two-factor authentication is required.  Store the OAuth state
            # so a 2FA code can be supplied later via complete_2fa_login().
            self._pending_2fa = True
            self._last_attempt = time.time()  # enforce cooldown
            # Persist the auth state so complete_2fa_login() works after restart
            self._save_2fa_state()
            logger.warning(
                "Blink: 2FA required. Call complete_2fa_login(<code>) with "
                "the SMS/email code to finish setup."
            )
            return False
        except Exception as e:
            err_str = str(e).lower()
            if "429" in err_str or "rate" in err_str or "too many" in err_str:
                # HTTP 429 from Blink — use the longer rate-limit cooldown
                self._last_attempt = time.time()
                self._rate_limited = True
                logger.error(
                    f"Blink: Rate limited (429) — waiting {_RATE_LIMIT_COOLDOWN_SECONDS}s"
                )
            else:
                self._last_attempt = time.time()
                logger.error(f"Blink: Connection failed — {e}")
            return False

    async def complete_2fa_login(self, twofa_code: str) -> bool:
        """Complete Blink login after a 2FA code has been provided.

        Call this when start() returns False due to 2FA requirement.
        The code is typically sent via SMS or email by Blink.
        """
        if not self._pending_2fa:
            return False

        # If the Blink object is missing (container restart), recreate it
        # from the persisted 2FA state so complete_2fa_login() can work.
        if not self._blink:
            persisted = getattr(self, "_persisted_2fa_state", None)
            if not persisted:
                logger.error("Blink: No persisted 2FA state — cannot complete login")
                return False
            try:
                from blinkpy.blinkpy import Blink
                from aiohttp import ClientSession

                self._session = ClientSession()
                self._blink = Blink(session=self._session)
                # Restore auth data from the persisted state
                auth_data = persisted.get("auth_data", {})
                self._blink.auth.data.update(auth_data)
                if persisted.get("token"):
                    self._blink.auth.token = persisted["token"]
                if persisted.get("refresh_token"):
                    self._blink.auth.refresh_token = persisted["refresh_token"]
                if persisted.get("expiration_date"):
                    self._blink.auth.expiration_date = persisted["expiration_date"]
                if persisted.get("client_id"):
                    self._blink.auth.client_id = persisted["client_id"]
                if persisted.get("account_id"):
                    self._blink.auth.account_id = persisted["account_id"]
                if persisted.get("user_id"):
                    self._blink.auth.user_id = persisted["user_id"]
                if persisted.get("host"):
                    self._blink.auth.host = persisted["host"]
                if persisted.get("region_id"):
                    self._blink.auth.region_id = persisted["region_id"]
                if persisted.get("hardware_id"):
                    self._blink.auth.hardware_id = persisted["hardware_id"]
                self._blink.auth.no_prompt = True
                logger.info("Blink: Restored Blink auth from persisted 2FA state")
            except Exception as e:
                logger.error(f"Blink: Could not restore Blink auth — {e}")
                return False

        try:
            ok = await self._blink.auth.complete_2fa_login(twofa_code)
            if ok:
                self._pending_2fa = False
                self._last_attempt = 0  # clear cooldown on success
                self._clear_2fa_state()  # remove persisted 2FA state
                # Re-run setup now that auth is complete
                await self._blink.setup_post_verify()
                self._started = True
                self._cameras = {}
                for name, module in self._blink.sync.items():
                    for cam_name, cam in module.cameras.items():
                        self._cameras[cam_name] = {
                            "name": cam_name,
                            "sync_module": name,
                            "camera": cam,
                            "online": getattr(cam, "online", True),
                            "battery": getattr(cam, "battery_level", "unknown"),
                            "motion": getattr(cam, "motion_detected", False),
                        }
                logger.info(
                    f"Blink: 2FA complete — {len(self._cameras)} camera(s) found"
                )
                # Persist tokens so next restart doesn't need 2FA again
                self._save_tokens()
                return True
            logger.error("Blink: 2FA verification failed")
            self._last_attempt = time.time()  # cooldown on failure
            return False
        except Exception as e:
            self._last_attempt = time.time()
            logger.error(f"Blink: 2FA completion failed — {e}")
            return False

    async def stop(self):
        """Close Blink connection."""
        if self._session:
            await self._session.close()
            self._session = None
        self._started = False
        self._blink = None

    async def list_cameras(self) -> list[dict]:
        """Return list of available cameras with status."""
        if not self._started:
            # Don't auto-retry — let the status endpoint handle retries
            # This prevents _cwStartBlink from triggering extra SMS codes
            return []
        result = []
        for name, info in self._cameras.items():
            result.append(
                {
                    "name": name,
                    "sync_module": info["sync_module"],
                    "online": info["online"],
                    "battery": info["battery"],
                    "motion": info["motion"],
                }
            )
        return result

    async def get_snapshot(self, camera_name: Optional[str] = None) -> Optional[bytes]:
        """Snap a new picture and return JPEG bytes.
        If camera_name is None, returns snapshot from first available camera."""
        if not self._started:
            if not await self.start():
                return None

        try:
            # Refresh to get latest state
            await self._blink.refresh()

            if camera_name:
                cam_info = self._cameras.get(camera_name)
                if not cam_info:
                    logger.warning(f"Blink: Camera '{camera_name}' not found")
                    return None
                cam = cam_info["camera"]
            else:
                # Use first available camera
                if not self._cameras:
                    logger.warning("Blink: No cameras available")
                    return None
                cam = list(self._cameras.values())[0]["camera"]
                camera_name = list(self._cameras.keys())[0]

            # Snap a new picture
            await cam.snap_picture()
            await asyncio.sleep(2)  # Wait for Blink servers to process
            await self._blink.refresh()

            # Get the image URL
            image_url = cam.image_url
            if not image_url:
                logger.warning(f"Blink: No image URL for {camera_name}")
                return None

            # Download the image
            async with self._session.get(image_url) as resp:
                if resp.status == 200:
                    return await resp.read()
                else:
                    logger.warning(f"Blink: Image download failed ({resp.status})")
                    return None

        except Exception as e:
            logger.error(f"Blink: Snapshot failed — {e}")
            return None

    async def get_latest_image(
        self, camera_name: Optional[str] = None
    ) -> Optional[bytes]:
        """Get the most recent image from a camera (no new snap)."""
        if not self._started:
            if not await self.start():
                return None

        try:
            await self._blink.refresh()

            if camera_name:
                cam_info = self._cameras.get(camera_name)
                if not cam_info:
                    return None
                cam = cam_info["camera"]
            else:
                if not self._cameras:
                    return None
                cam = list(self._cameras.values())[0]["camera"]

            image_url = cam.image_url
            if not image_url:
                return None

            async with self._session.get(image_url) as resp:
                if resp.status == 200:
                    return await resp.read()
            return None

        except Exception as e:
            logger.error(f"Blink: Get image failed — {e}")
            return None

    async def get_all_snapshots(self) -> dict[str, bytes]:
        """Get snapshots from all cameras. Returns {name: jpeg_bytes}."""
        if not self._started:
            if not await self.start():
                return {}

        results = {}
        for name, info in self._cameras.items():
            try:
                image_url = info["camera"].image_url
                if image_url:
                    async with self._session.get(image_url) as resp:
                        if resp.status == 200:
                            results[name] = await resp.read()
            except Exception as e:
                logger.debug(f"Blink: Failed to get image from {name}: {e}")
        return results

    async def get_snapshot_base64(
        self, camera_name: Optional[str] = None
    ) -> Optional[str]:
        """Snap and return as base64 string."""
        img = await self.get_snapshot(camera_name)
        if img:
            return base64.b64encode(img).decode()
        return None

    async def get_all_snapshots_base64(self) -> dict[str, str]:
        """Get all snapshots as base64. Returns {name: base64_str}."""
        raw = await self.get_all_snapshots()
        return {name: base64.b64encode(img).decode() for name, img in raw.items()}


# Singleton
_blink_connector: Optional[BlinkConnector] = None


def get_blink_connector() -> BlinkConnector:
    global _blink_connector
    if _blink_connector is None:
        _blink_connector = BlinkConnector()
    return _blink_connector
