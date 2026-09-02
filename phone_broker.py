#!/usr/bin/env python3
"""
Lilly Phone Broker — tiny WebSocket relay between paired phones and web UIs.

Architecture:
  Phone (overlay app) ──WS──> Broker ──WS──> Web UI (browser)

Topics:
  sensor   — live sensor readings (light, accel, gyro, mag, proximity, steps)
  battery  — battery level, temperature, charging
  location — GPS coordinates
  notification — Android notifications
  bluetooth — nearby BT devices
  wifi     — WiFi scan results
  chat     — bidirectional chat messages
  state    — UI state (mood, avatar, mic, speaking)

Automation:
  Rules fire when incoming messages match conditions.
  Actions: notify, chat, shell, webhook, tts

Usage:
  python phone_broker.py --port 8077

  Or import into lilly_ai.py:
    from phone_broker import PhoneBroker
    broker = PhoneBroker(app)  # mounts WS routes on existing FastAPI app
"""

import asyncio
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import uvicorn

logger = logging.getLogger("PhoneBroker")

AUTOMATION_RULES_FILE = os.environ.get(
    "BROKER_AUTOMATION_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "broker_automations.json"),
)


# ─── Data structures ──────────────────────────────────────────────────────────


@dataclass
class PhoneConnection:
    """A connected phone (overlay app)."""

    ws: WebSocket
    device_id: str
    device_token: str
    model: str = ""
    connected_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    subscriptions: set[str] = field(
        default_factory=lambda: {"sensor", "battery", "notification", "chat", "state"}
    )


@dataclass
class WebUIConnection:
    """A connected web UI client."""

    ws: WebSocket
    client_id: str
    connected_at: float = field(default_factory=time.time)
    subscriptions: set[str] = field(
        default_factory=lambda: {"sensor", "battery", "notification", "chat", "state"}
    )


@dataclass
class AutomationRule:
    """An automation rule: IF topic+condition THEN fire action."""

    id: str
    name: str
    enabled: bool = True
    # Trigger: which topic and what condition
    topic: str = ""  # e.g. "sensor", "battery", "notification", "*" (any)
    # Condition: JSONPath-style field checks
    # e.g. {"field": "data.battery_level", "op": "<", "value": 15}
    # e.g. {"field": "data.notification.title", "op": "contains", "value": "Missed call"}
    condition: dict = field(default_factory=dict)
    # Action: what to do
    # action_type: "notify" | "chat" | "shell" | "webhook" | "tts"
    action_type: str = ""
    action_payload: dict = field(default_factory=dict)
    # Rate-limiting: don't fire more than once per cooldown_seconds
    cooldown_seconds: float = 60.0
    _last_fired: float = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "enabled": self.enabled,
            "topic": self.topic,
            "condition": self.condition,
            "action_type": self.action_type,
            "action_payload": self.action_payload,
            "cooldown_seconds": self.cooldown_seconds,
        }


# ─── Automation engine ────────────────────────────────────────────────────────


class AutomationEngine:
    """
    Evaluates incoming messages against registered rules and fires actions.

    Condition format:
      {"field": "data.battery_level", "op": "<", "value": 15}
      {"field": "data.light", "op": ">", "value": 1000}
      {"field": "data.notification.title", "op": "contains", "value": "Missed call"}

    Supported ops: ==, !=, <, <=, >, >=, contains, not_contains, regex

    Action types:
      "notify"  — send push notification to all phones  (payload: {title, body})
      "chat"    — send chat message to web UIs           (payload: {text})
      "shell"   — run shell command on phone             (payload: {command})
      "webhook" — HTTP POST to URL                       (payload: {url, headers?, body?})
      "tts"     — text-to-speech on phone                (payload: {text})
    """

    def __init__(self):
        self.rules: dict[str, AutomationRule] = {}
        self._action_handlers: dict[str, Callable[..., Awaitable[None]]] = {}
        self._load_rules()

    def _load_rules(self):
        """Load rules from disk."""
        if not os.path.exists(AUTOMATION_RULES_FILE):
            return
        try:
            with open(AUTOMATION_RULES_FILE) as f:
                data = json.load(f)
            for r in data.get("rules", []):
                rule = AutomationRule(
                    id=r["id"],
                    name=r.get("name", ""),
                    enabled=r.get("enabled", True),
                    topic=r.get("topic", ""),
                    condition=r.get("condition", {}),
                    action_type=r.get("action_type", ""),
                    action_payload=r.get("action_payload", {}),
                    cooldown_seconds=r.get("cooldown_seconds", 60.0),
                )
                self.rules[rule.id] = rule
            logger.info(f"Loaded {len(self.rules)} automation rules")
        except Exception as e:
            logger.warning(f"Failed to load automation rules: {e}")

    def _save_rules(self):
        """Persist rules to disk."""
        try:
            data = {"rules": [r.to_dict() for r in self.rules.values()]}
            with open(AUTOMATION_RULES_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save automation rules: {e}")

    def register_action_handler(
        self, action_type: str, handler: Callable[..., Awaitable[None]]
    ):
        """Register a handler for an action type (e.g. 'notify', 'chat', 'shell')."""
        self._action_handlers[action_type] = handler

    def add_rule(self, rule: AutomationRule) -> AutomationRule:
        """Add or update a rule."""
        self.rules[rule.id] = rule
        self._save_rules()
        return rule

    def remove_rule(self, rule_id: str) -> bool:
        """Remove a rule by ID."""
        if rule_id in self.rules:
            del self.rules[rule_id]
            self._save_rules()
            return True
        return False

    def get_rules(self) -> list[dict]:
        """Return all rules as dicts."""
        return [r.to_dict() for r in self.rules.values()]

    def _evaluate_condition(self, msg: dict, condition: dict) -> bool:
        """Check if a message satisfies a condition."""
        if not condition:
            return True  # no condition = always fires

        field_path = condition.get("field", "")
        op = condition.get("op", "==")
        expected = condition.get("value")

        # Resolve field path (e.g. "data.battery_level" → msg["data"]["battery_level"])
        actual = msg
        for part in field_path.split("."):
            if isinstance(actual, dict):
                actual = actual.get(part)
            else:
                return False

        if actual is None:
            return False

        # Type coerce for comparison
        try:
            if isinstance(expected, (int, float)):
                actual = float(actual)
            elif isinstance(expected, str):
                actual = str(actual)
        except (ValueError, TypeError):
            return False

        # Evaluate operator
        if op == "==":
            return actual == expected
        elif op == "!=":
            return actual != expected
        elif op == "<":
            return actual < expected
        elif op == "<=":
            return actual <= expected
        elif op == ">":
            return actual > expected
        elif op == ">=":
            return actual >= expected
        elif op == "contains":
            return str(expected).lower() in str(actual).lower()
        elif op == "not_contains":
            return str(expected).lower() not in str(actual).lower()
        elif op == "regex":
            return bool(re.search(str(expected), str(actual)))
        else:
            return False

    async def evaluate(self, msg: dict) -> list[str]:
        """
        Evaluate all enabled rules against a message.
        Returns list of rule IDs that fired.
        """
        fired = []
        topic = msg.get("type", "").replace("data_", "").replace("update_", "")

        for rule in self.rules.values():
            if not rule.enabled:
                continue

            # Topic match (empty topic or * matches anything)
            if rule.topic and rule.topic != "*" and rule.topic != topic:
                continue

            # Cooldown check
            now = time.time()
            if now - rule._last_fired < rule.cooldown_seconds:
                continue

            # Evaluate condition
            if not self._evaluate_condition(msg, rule.condition):
                continue

            # Fire!
            rule._last_fired = now
            fired.append(rule.id)
            logger.info(f"Automation rule fired: {rule.name} ({rule.id})")

            # Dispatch action
            handler = self._action_handlers.get(rule.action_type)
            if handler:
                try:
                    await handler(rule.action_payload, msg)
                except Exception as e:
                    logger.warning(f"Action handler error ({rule.action_type}): {e}")
            else:
                logger.warning(f"No handler for action type: {rule.action_type}")

        return fired


# ─── Broker core ──────────────────────────────────────────────────────────────


class PhoneBroker:
    """
    Tiny pub/sub WebSocket broker.

    - Phones push sensor/state data → broker fans out to subscribed web UIs
    - Web UIs subscribe to topics → broker forwards matching data from phones
    - Web UIs can send commands back to phones
    - Supports multiple paired phones (by device_id)
    - Automation engine fires rules on incoming messages
    """

    def __init__(self, app: FastAPI | None = None, valid_tokens: dict | None = None):
        self.phones: dict[str, PhoneConnection] = {}  # device_id → PhoneConnection
        self.webuis: dict[str, WebUIConnection] = {}  # client_id → WebUIConnection
        self.valid_tokens = valid_tokens or {}  # device_token → metadata
        self._last_broadcast: dict[str, float] = {}  # topic → timestamp (rate limiting)
        self._broadcast_interval = 1.0  # min seconds between same-topic broadcasts
        self.automation = AutomationEngine()

        if app:
            self.mount(app)

    def mount(self, app: FastAPI):
        """Mount WebSocket routes on an existing FastAPI app."""

        @app.websocket("/ws/phone")
        async def ws_phone(websocket: WebSocket):
            await self._handle_phone(websocket)

        @app.websocket("/ws/webui")
        async def ws_webui(websocket: WebSocket):
            await self._handle_webui(websocket)

        @app.get("/api/broker/status")
        async def broker_status():
            return {
                "phones": len(self.phones),
                "webuis": len(self.webuis),
                "phone_ids": list(self.phones.keys()),
                "uptime": time.time(),
            }

        logger.info("PhoneBroker mounted on FastAPI app")

    # ─── Phone connection handler ──────────────────────────────────────────

    async def _handle_phone(self, ws: WebSocket):
        await ws.accept()

        # Auth handshake: first message must be {"type":"auth", "device_id":"...", "token":"..."}
        try:
            raw = await asyncio.wait_for(ws.receive_text(), timeout=10)
            auth = json.loads(raw)
        except Exception:
            await ws.close(code=4001, reason="auth timeout")
            return

        if auth.get("type") != "auth":
            await ws.close(code=4002, reason="expected auth message")
            return

        device_id = auth.get("device_id", "")
        token = auth.get("token", "")
        model = auth.get("model", "")

        # Validate token (if valid_tokens is empty, accept all — open pairing mode)
        if self.valid_tokens and token not in self.valid_tokens:
            await ws.close(code=4003, reason="invalid token")
            return

        if not device_id:
            device_id = str(uuid.uuid4())[:8]

        # Register phone
        phone = PhoneConnection(
            ws=ws, device_id=device_id, device_token=token, model=model
        )
        self.phones[device_id] = phone
        logger.info(f"Phone connected: {device_id} ({model})")

        # Ack
        await ws.send_json({"type": "auth_ok", "device_id": device_id})

        # Notify all web UIs
        await self._broadcast_to_webuis(
            {
                "type": "phone_connected",
                "device_id": device_id,
                "model": model,
            }
        )

        # Message loop
        try:
            while True:
                raw = await ws.receive_text()
                msg = json.loads(raw)
                await self._handle_phone_message(phone, msg)
        except WebSocketDisconnect:
            logger.info(f"Phone disconnected: {device_id}")
        except Exception as e:
            logger.warning(f"Phone error ({device_id}): {e}")
        finally:
            self.phones.pop(device_id, None)
            await self._broadcast_to_webuis(
                {
                    "type": "phone_disconnected",
                    "device_id": device_id,
                }
            )

    async def _handle_phone_message(self, phone: PhoneConnection, msg: dict):
        """Process a message from a phone and fan out to web UIs."""
        msg_type = msg.get("type", "")
        phone.last_seen = time.time()

        # Rate-limit high-frequency topics
        topic = msg_type.replace("data_", "").replace("update_", "")
        if not self._should_broadcast(topic):
            return

        # Forward to all subscribed web UIs
        forward = {
            "type": msg_type,
            "device_id": phone.device_id,
            "data": msg.get("data", msg),
            "ts": time.time(),
        }

        await self._broadcast_to_webuis(forward, topic)

    # ─── Web UI connection handler ─────────────────────────────────────────

    async def _handle_webui(self, ws: WebSocket):
        await ws.accept()

        # Auth handshake
        try:
            raw = await asyncio.wait_for(ws.receive_text(), timeout=10)
            auth = json.loads(raw)
        except Exception:
            await ws.close(code=4001, reason="auth timeout")
            return

        if auth.get("type") != "auth":
            await ws.close(code=4002, reason="expected auth message")
            return

        client_id = auth.get("client_id", str(uuid.uuid4())[:8])
        token = auth.get("token", "")

        # Validate token
        if self.valid_tokens and token not in self.valid_tokens:
            await ws.close(code=4003, reason="invalid token")
            return

        # Register web UI
        webui = WebUIConnection(ws=ws, client_id=client_id)
        self.webuis[client_id] = webui
        logger.info(f"Web UI connected: {client_id}")

        # Send current state
        await ws.send_json(
            {
                "type": "auth_ok",
                "client_id": client_id,
                "phones": [
                    {
                        "device_id": p.device_id,
                        "model": p.model,
                        "last_seen": p.last_seen,
                    }
                    for p in self.phones.values()
                ],
            }
        )

        # Message loop
        try:
            while True:
                raw = await ws.receive_text()
                msg = json.loads(raw)
                await self._handle_webui_message(webui, msg)
        except WebSocketDisconnect:
            logger.info(f"Web UI disconnected: {client_id}")
        except Exception as e:
            logger.warning(f"Web UI error ({client_id}): {e}")
        finally:
            self.webuis.pop(client_id, None)

    async def _handle_webui_message(self, webui: WebUIConnection, msg: dict):
        """Process a message from a web UI and route to phones."""
        msg_type = msg.get("type", "")

        if msg_type == "subscribe":
            topic = msg.get("topic", "")
            if topic:
                webui.subscriptions.add(topic)
                await webui.ws.send_json({"type": "subscribed", "topic": topic})

        elif msg_type == "unsubscribe":
            topic = msg.get("topic", "")
            webui.subscriptions.discard(topic)

        elif msg_type in ("command", "chat", "tts", "shell"):
            # Forward to a specific phone or all phones
            target = msg.get("device_id", "")
            payload = {
                "type": msg_type,
                "data": msg.get("data", msg),
                "from": webui.client_id,
            }
            if target and target in self.phones:
                await self.phones[target].ws.send_json(payload)
            else:
                for phone in self.phones.values():
                    try:
                        await phone.ws.send_json(payload)
                    except Exception:
                        pass

    # ─── Fan-out helpers ───────────────────────────────────────────────────

    async def _broadcast_to_webuis(self, msg: dict, topic: str = ""):
        """Send a message to all web UIs subscribed to the topic."""
        if not self.webuis:
            return

        dead = []
        for client_id, webui in self.webuis.items():
            if topic and topic not in webui.subscriptions:
                continue
            try:
                await webui.ws.send_json(msg)
            except Exception:
                dead.append(client_id)

        for cid in dead:
            self.webuis.pop(cid, None)

    def _should_broadcast(self, topic: str) -> bool:
        """Rate-limit broadcasts per topic (1 Hz default)."""
        now = time.time()
        last = self._last_broadcast.get(topic, 0)
        if now - last < self._broadcast_interval:
            return False
        self._last_broadcast[topic] = now
        return True

    # ─── Public API for pushing data programmatically ──────────────────────

    async def push_sensor(self, device_id: str, data: dict):
        """Push sensor data from a phone (or from code emulating a phone)."""
        await self._broadcast_to_webuis(
            {
                "type": "sensor",
                "device_id": device_id,
                "data": data,
                "ts": time.time(),
            },
            "sensor",
        )

    async def push_notification(self, device_id: str, data: dict):
        await self._broadcast_to_webuis(
            {
                "type": "notification",
                "device_id": device_id,
                "data": data,
                "ts": time.time(),
            },
            "notification",
        )

    async def push_battery(self, device_id: str, data: dict):
        await self._broadcast_to_webuis(
            {
                "type": "battery",
                "device_id": device_id,
                "data": data,
                "ts": time.time(),
            },
            "battery",
        )

    async def push_chat(self, device_id: str, data: dict):
        await self._broadcast_to_webuis(
            {
                "type": "chat",
                "device_id": device_id,
                "data": data,
                "ts": time.time(),
            },
            "chat",
        )

    def get_phone_ids(self) -> list[str]:
        return list(self.phones.keys())

    def is_phone_connected(self, device_id: str = "") -> bool:
        if device_id:
            return device_id in self.phones
        return len(self.phones) > 0


# ─── Standalone mode ──────────────────────────────────────────────────────────


def create_app():
    app = FastAPI(title="Lilly Phone Broker")
    broker = PhoneBroker(app)
    return app, broker


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Lilly Phone Broker")
    parser.add_argument("--port", type=int, default=8077)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

    app, broker = create_app()
    logger.info(f"Phone Broker starting on {args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port)
