"""
TencentDB Agent Memory integration for Lilly AI.

Maps Lilly's 7 senses to the 4-layer TencentDB memory pyramid:
  - Eyes 👁️ (vision) → L2 Scenario: "at desk", "in car", "dark room"
  - Ears 👂 (audio) → L1 Atom: "user said they like jazz"
  - Nose 👃 (air quality) → L2 Scenario: "high humidity", "temperature rising"
  - Tongue 👅 (proximity/light) → L1 Atom: "phone on table", "phone in pocket"
  - Skin ✋ (touch/motion) → L2 Scenario: "walking", "standing", "still"
  - Heart ❤️ (battery) → L1 Atom: "battery at 20%", "charging"
  - Brain 🧠 (cognitive) → L0 Conversation + L1/L2/L3: full memory stack

The memory service runs as a Docker container (hermes-memory) on port 8420.
API: REST at http://localhost:8420/v3/* with Bearer auth.

Layers:
  L0 Conversation  — raw dialogue (offloaded chat history)
  L1 Atom          — atomic facts (preferences, names, learned patterns)
  L2 Scenario      — sensor context scenes ("driving", "at home")
  L3 Core/Persona  — shared 9-avatar hive mind personality profile

Usage:
  from tencentdb_memory import LillyMemory
  mem = LillyMemory(team_id="lilly", agent_id="puppy", user_id="alex")
  mem.write_atom("user_prefers_bear_at_night", "Alex prefers Bear avatar after 8pm")
  mem.read_core()  # get persona layer
  mem.write_scenario("driving", "# Driving\\n## Sensors\\n- sig_motion: active\\n- location: highway")
"""

import asyncio
import httpx
import json
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────

TENCENTDB_GATEWAY_URL = os.environ.get("TENCENTDB_GATEWAY_URL", "http://localhost:8420")
TENCENTDB_API_KEY = os.environ.get("TENCENTDB_API_KEY", "lilly-memory-key")
TENCENTDB_SERVICE_ID = os.environ.get("TENCENTDB_SERVICE_ID", "lilly-memory")


class TDAMError(Exception):
    """TencentDB Agent Memory error."""

    def __init__(self, code: int, message: str, request_id: str = ""):
        self.code = code
        self.message = message
        self.request_id = request_id
        super().__init__(f"[{code}] {message} (req: {request_id})")


class TencentDBMemoryClient:
    """
    Python client for the TencentDB Agent Memory Gateway REST API.

    Mirrors the TypeScript SDK's MemoryClient:
      L0: addConversation / queryConversation / searchConversation / deleteConversation
      L1: updateAtomic / queryAtomic / searchAtomic / deleteAtomic
      L2: listScenarios / readScenario / writeScenario / rmScenario
      L3: readCore / writeCore
    """

    def __init__(
        self,
        endpoint: str = TENCENTDB_GATEWAY_URL,
        api_key: str = TENCENTDB_API_KEY,
        service_id: str = TENCENTDB_SERVICE_ID,
        team_id: str = "default",
        agent_id: str = "default",
        user_id: str = "alex",
        session_id: Optional[str] = None,
    ):
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.service_id = service_id
        self.team_id = team_id
        self.agent_id = agent_id
        self.user_id = user_id
        self.session_id = session_id
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def headers(self) -> dict:
        h = {
            "Content-Type": "application/json",
            "x-tdai-service-id": self.service_id,
        }
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.endpoint,
                headers=self.headers,
                timeout=30.0,
            )
        return self._client

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    def _isolation(self) -> dict:
        """Build the v3 isolation context for all API calls.

        The gateway stores records under the isolation triple (team_id, agent_id, user_id).
        By default, the gateway uses "default" for team_id and agent_id, so we match that.
        """
        return {
            "team_id": self.team_id,
            "agent_id": self.agent_id,
            "user_id": self.user_id,
            **({"session_id": self.session_id} if self.session_id else {}),
        }

    async def _post(self, path: str, payload: dict) -> dict:
        try:
            resp = await self.client.post(path, json=payload)
            if resp.status_code != 200:
                data = resp.json()
                raise TDAMError(
                    data.get("code", resp.status_code),
                    data.get("message", resp.text),
                    data.get("request_id", ""),
                )
            data = resp.json()
            if data.get("code", 0) != 0:
                raise TDAMError(
                    data.get("code"),
                    data.get("message", ""),
                    data.get("request_id", ""),
                )
            return data
        except httpx.HTTPError as e:
            logger.warning(f"TencentDB memory request failed: {e}")
            raise

    async def _post_v1(self, path: str, payload: dict) -> dict:
        """POST to v1 endpoint (uses session_key-based isolation instead of team/agent/user)."""
        resp = await self.client.post(path, json=payload)
        if resp.status_code != 200:
            data = resp.json()
            raise TDAMError(
                data.get("code", resp.status_code),
                data.get("message", resp.text),
                data.get("request_id", ""),
            )
        return resp.json()

    # ── v1 L0: Conversation (session_key-based) ────────────────────────────────

    async def capture(
        self, user_content: str, assistant_content: str, session_key: str
    ) -> dict:
        """Capture a conversation pair to L0 (v1 API, uses session_key)."""
        return await self._post_v1(
            "/capture",
            {
                "user_content": user_content,
                "assistant_content": assistant_content,
                "session_key": session_key,
            },
        )

    async def reclaim(self, query: str, session_key: str, top_k: int = 10) -> dict:
        """Recall conversation memories (v1 API, uses session_key)."""
        return await self._post_v1(
            "/recall",
            {
                "query": query,
                "session_key": session_key,
                "top_k": top_k,
            },
        )

    # ── L0: Conversation ────────────────────────────────────────────────────

    async def add_conversation(
        self, messages: list[dict], session_id: Optional[str] = None
    ) -> dict:
        """Write a conversation turn to L0."""
        return await self._post(
            "/v3/conversation/add",
            {
                **self._isolation(),
                "session_id": session_id or self.session_id,
                "messages": [
                    {"role": m["role"], "content": m["content"]} for m in messages
                ],
            },
        )

    async def query_conversation(
        self, limit: int = 20, offset: int = 0, session_id: Optional[str] = None
    ) -> dict:
        """Query L0 conversation history."""
        return await self._post(
            "/v3/conversation/query",
            {
                **self._isolation(),
                "session_id": session_id or self.session_id,
                "limit": limit,
                "offset": offset,
            },
        )

    async def search_conversation(self, query: str, limit: int = 10) -> dict:
        """Search L0 conversations."""
        return await self._post(
            "/v3/conversation/search",
            {
                **self._isolation(),
                "query": query,
                "limit": limit,
            },
        )

    async def count_conversation(self) -> dict:
        """Count total conversation entries."""
        return await self._post(
            "/v3/conversation/count",
            {
                **self._isolation(),
            },
        )

    async def delete_conversation(self, session_id: Optional[str] = None) -> dict:
        """Delete conversation entries for a session."""
        return await self._post(
            "/v3/conversation/delete",
            {
                **self._isolation(),
                "session_id": session_id or self.session_id,
            },
        )

    # ── L1: Atomic Facts ───────────────────────────────────────────────────

    async def update_atomic(
        self, key: str, value: str, tags: list[str] | None = None
    ) -> dict:
        """Update an atomic fact (L1). Key becomes the 'id', value becomes 'content'.

        The gateway's /v3/atomic/update endpoint is update-only (returns 404 for
        new atoms). This method raises TDAMError(404, ...) for new atoms.
        Callers should catch this and use v1 capture as a fallback.
        """
        return await self._post(
            "/v3/atomic/update",
            {
                **self._isolation(),
                "id": key,
                "content": value,
                "tags": tags or [],
            },
        )

    async def query_atomic(self, keys: list[str] | None = None) -> dict:
        """Query atomic facts by id."""
        return await self._post(
            "/v3/atomic/query",
            {
                **self._isolation(),
                "ids": keys or [],
            },
        )

    async def search_atomic(self, query: str, limit: int = 10) -> dict:
        """Search atomic facts by query text."""
        return await self._post(
            "/v3/atomic/search",
            {
                **self._isolation(),
                "query": query,
                "limit": limit,
            },
        )

    async def delete_atomic(self, keys: list[str]) -> dict:
        """Delete atomic facts by key."""
        return await self._post(
            "/v3/atomic/delete",
            {
                **self._isolation(),
                "keys": keys,
            },
        )

    async def count_atomic(self) -> dict:
        """Count total atomic facts."""
        return await self._post(
            "/v3/atomic/count",
            {
                **self._isolation(),
            },
        )

    # ── L2: Scenarios ───────────────────────────────────────────────────────

    async def list_scenarios(self) -> dict:
        """List all scenario files."""
        return await self._post(
            "/v3/scenario/ls",
            {
                **self._isolation(),
            },
        )

    async def read_scenario(self, path: str) -> dict:
        """Read a scenario by path (e.g. 'driving.md')."""
        return await self._post(
            "/v3/scenario/read",
            {
                **self._isolation(),
                "path": path,
            },
        )

    async def write_scenario(self, path: str, content: str) -> dict:
        """Write/update a scenario file.

        The gateway's /v3/scenario/write is update-only (returns 404 for new files).
        Callers should catch this and use v1 capture as a fallback.
        """
        return await self._post(
            "/v3/scenario/write",
            {
                **self._isolation(),
                "path": path,
                "content": content,
            },
        )

    async def rm_scenario(self, path: str) -> dict:
        """Delete a scenario file."""
        return await self._post(
            "/v3/scenario/rm",
            {
                **self._isolation(),
                "path": path,
            },
        )

    async def count_scenario(self) -> dict:
        """Count total scenarios."""
        return await self._post(
            "/v3/scenario/count",
            {
                **self._isolation(),
            },
        )

    # ── L3: Core / Persona ──────────────────────────────────────────────────

    async def read_core(self) -> dict:
        """Read the L3 core persona / team profile."""
        return await self._post(
            "/v3/core/read",
            {
                **self._isolation(),
            },
        )

    async def write_core(self, content: str) -> dict:
        """Write/update the L3 core persona."""
        return await self._post(
            "/v3/core/write",
            {
                **self._isolation(),
                "content": content,
            },
        )

    async def count_core(self) -> dict:
        """Count core entries."""
        return await self._post(
            "/v3/core/count",
            {
                **self._isolation(),
            },
        )

    # ── withIsolation: cross-session query ──────────────────────────────────

    async def query_conversation_all_sessions(
        self, limit: int = 20, offset: int = 0
    ) -> dict:
        """Query L0 across all sessions (cross-session aggregation)."""
        return await self._post(
            "/v3/conversation/query",
            {
                **{k: v for k, v in self._isolation().items() if k != "session_id"},
                "session_id": None,
                "limit": limit,
                "offset": offset,
            },
        )


# ── Lilly's Senses → Memory Layer Mapping ───────────────────────────────────


class LillyMemory:
    """
    Maps Lilly's 7 senses to TencentDB's 4-layer memory pyramid.

    Each sense writes to specific layers:

    Eyes 👁️ (vision + light sensors):
      L2: Scenarios — "bright_room", "dark_room", "sunny_outside"
      L1: Atoms — "last_vision_time", "last_light_level"

    Ears 👂 (audio/mic):
      L0: Conversations — full audio transcripts
      L1: Atoms — "user_mentioned_topic", "last_speech_time"

    Nose 👃 (temperature + pressure):
      L2: Scenarios — "storm_approaching", "indoor", "outdoor"
      L1: Atoms — "last_baro_temp", "pressure_trend"

    Tongue 👅 (proximity + light):
      L2: Scenarios — "phone_down", "phone_up", "in_pocket"
      L1: Atoms — "proximity_state", "ambient_brightness"

    Skin ✋ (motion/accelerometer):
      L2: Scenarios — "walking", "running", "still", "vehicle"
      L1: Atoms — "last_motion", "step_count"

    Heart ❤️ (battery):
      L1: Atoms — "battery_level", "charging_status"
      L3: Core — battery preferences ("low power mode enabled at 20%")

    Brain 🧠 (cognitive/cross-sense):
      L3: Core — persona profile, user preferences, avatar preferences
      L1: Atoms — "user_name", "favorite_avatar", "preferred_time_for_bear"
      L0: Conversations — offloaded chat history
    """

    # Sense → layer mapping
    SENSE_LAYERS = {
        "EYES": {"primary": "L2", "secondary": "L1"},
        "EARS": {"primary": "L0", "secondary": "L1"},
        "NOSE": {"primary": "L2", "secondary": "L1"},
        "TONGUE": {"primary": "L2", "secondary": "L1"},
        "SKIN": {"primary": "L2", "secondary": "L1"},
        "HEART": {"primary": "L1", "secondary": "L3"},
        "BRAIN": {"primary": "L3", "secondary": "L1"},
    }

    def __init__(self, **client_kwargs):
        self.client = TencentDBMemoryClient(**client_kwargs)

    # ── Eyes (Vision) ───────────────────────────────────────────────────────

    async def record_vision(self, avatar: str, light_level: float, description: str):
        """Record what the eyes (camera + light sensor) perceive."""
        await self.client.update_atomic(
            key=f"senses.eyes.{avatar}.last_light",
            value=str(light_level),
            tags=["eyes", avatar, "vision"],
        )
        if light_level < 10:
            await self.client.write_scenario(
                "dark_room.md",
                f"""# Dark Room
## Sensors
- light: {light_level} lux
- avatar: {avatar}
## Context
Light level dropped below 10 lux.
""",
            )
        elif light_level > 1000:
            await self.client.write_scenario(
                "bright_light.md",
                f"""# Bright Light
## Sensors
- light: {light_level} lux
- avatar: {avatar}
## Context
Light level exceeded 1000 lux.
""",
            )

    # ── Ears (Audio) ────────────────────────────────────────────────────────

    async def record_speech(self, speaker: str, text: str, avatar: str = "hive-mind"):
        """Record audio from the ears (microphone)."""
        await self.client.add_conversation(
            messages=[
                {"role": "user", "content": text},
            ],
            session_id=f"voice_{avatar}",
        )
        await self.client.update_atomic(
            key="senses.ears.last_speech_time",
            value=str(asyncio.get_event_loop().time()),
            tags=["ears", avatar, "audio"],
        )

    # ── Nose (Air Quality) ──────────────────────────────────────────────────

    async def record_air_quality(
        self, temperature: float, pressure: float, humidity: float | None = None
    ):
        """Record air quality from the nose (temperature + pressure sensors)."""
        await self.client.update_atomic(
            key="senses.nose.last_reading",
            value=json.dumps(
                {"temp": temperature, "pressure": pressure, "humidity": humidity}
            ),
            tags=["nose", "environment"],
        )
        if pressure < 1000:
            scenario = "# Low Pressure\n\nPressure dropped below 1000 hPa — weather may be changing.\n"
            await self.client.write_scenario("low_pressure.md", scenario)

    # ── Tongue (Proximity/Light) ────────────────────────────────────────────

    async def record_proximity(self, prox_value: float, light_level: float):
        """Record proximity + light from the tongue."""
        state = "near" if prox_value < 5 else "far"
        await self.client.update_atomic(
            key="senses.tongue.proximity",
            value=state,
            tags=["tongue", "proximity"],
        )
        await self.client.update_atomic(
            key="senses.tongue.ambient_light",
            value=str(light_level),
            tags=["tongue", "light"],
        )

    # ── Skin (Touch/Motion) ─────────────────────────────────────────────────

    async def record_motion(self, avatar: str, signature: dict):
        """Record motion from the skin (accelerometer/gyro)."""
        await self.client.update_atomic(
            key=f"senses.skin.{avatar}.last_motion",
            value=json.dumps(signature),
            tags=["skin", avatar, "motion"],
        )
        motion_intensity = signature.get("intensity", 0)
        if motion_intensity > 2.0:
            await self.client.write_scenario(
                "moving.md",
                f"""# Moving
## Avatar: {avatar}
## Intensity: {motion_intensity}
## Context
Significant motion detected — user is likely in transit.
""",
            )

    # ── Heart (Battery) ─────────────────────────────────────────────────────

    async def record_battery(self, level: int, charging: bool):
        """Record battery from the heart."""
        await self.client.update_atomic(
            key="senses.heart.battery_level",
            value=str(level),
            tags=["heart", "battery"],
        )
        await self.client.update_atomic(
            key="senses.heart.charging",
            value=str(charging),
            tags=["heart", "battery"],
        )
        if level < 20 and not charging:
            core = await self.client.read_core()
            core_content = core.get("data", {}).get("content", "")
            if "low_power_mode" not in core_content:
                await self.client.write_core(
                    f"{core_content}\n\n## Low Power Mode\nActivated at 20% battery."
                )

    # ── Brain (Cognitive) ───────────────────────────────────────────────────

    async def record_preference(self, key: str, value: str):
        """Record a user preference in L3 Core (brain layer)."""
        core = await self.client.read_core()
        content = core.get("data", {}).get("content", "")
        await self.client.write_core(f"{content}\n## {key}\n{value}\n")

    async def recall_preference(self, key: str) -> Optional[str]:
        """Recall a user preference from L3 Core."""
        core = await self.client.read_core()
        content = core.get("data", {}).get("content", "")
        marker = f"## {key}"
        if marker in content:
            idx = content.index(marker) + len(marker)
            # Read until next ## section
            rest = content[idx:]
            next_section = rest.find("\n## ")
            if next_section >= 0:
                return rest[:next_section].strip()
            return rest.strip()
        return None

    async def record_user_fact(
        self, key: str, value: str, tags: list[str] | None = None
    ):
        """Record an atomic fact in L1 (brain layer).

        Falls back to v1 capture if the atom doesn't exist yet (404 from update_atomic).
        """
        try:
            await self.client.update_atomic(
                key=f"brain.fact.{key}",
                value=value,
                tags=["brain", "fact"] + (tags or []),
            )
        except TDAMError as e:
            if e.code == 404:
                # New atom — create via v1 capture flow
                await self.client.capture(
                    user_content=f"[ATOM] brain.fact.{key}: {value}",
                    assistant_content=f"Recorded fact: {key}={value}",
                    session_key=self.client.session_id or "lilly-chat",
                )

    async def search_facts(self, query: str, limit: int = 10) -> list[dict]:
        """Search for atomic facts (L1).

        Uses v1 reclaim API first (which works reliably with session-based isolation),
        then falls back to v3 atomic/search.
        """
        # Try v1 reclaim first — it works with session_key and returns context
        try:
            result = await self.client.reclaim(
                query, session_key=self.client.session_id or "lilly-chat", top_k=limit
            )
            context = result.get("context", "")
            if context:
                # Return as a list with the context
                return [{"id": "reclaim", "content": context}]
        except Exception:
            pass
        # Fall back to v3 atomic/search
        result = await self.client.search_atomic(query, limit=limit)
        return result.get("data", {}).get("items", [])

    async def record_sensor_snapshot(self, snapshot: dict):
        """Record a sensor snapshot via v1 capture, triggering LLM pipeline.

        The LLM will extract relevant L1 atoms and L2 scenarios from the sensor data.
        """
        # Build a natural description of the sensor snapshot
        desc_parts = []
        if "light" in snapshot:
            lux = (
                snapshot["light"].get("raw", [0])[0]
                if snapshot["light"].get("raw")
                else 0
            )
            desc_parts.append(f"Light: {lux} lux")
        if "battery" in snapshot:
            pct = (
                snapshot["battery"].get("raw", [0])[0]
                if snapshot["battery"].get("raw")
                else 0
            )
            desc_parts.append(f"Battery: {pct}%")
        if "motion" in snapshot:
            desc_parts.append("Motion detected")
        if "proximity" in snapshot:
            prox = (
                snapshot["proximity"].get("raw", [0])[0]
                if snapshot["proximity"].get("raw")
                else 0
            )
            desc_parts.append(f"Proximity: {prox}")

        desc = "; ".join(desc_parts) if desc_parts else "Sensor data snapshot"
        await self.client.capture(
            user_content=f"Sensor snapshot: {desc}",
            assistant_content="Recording sensor context for memory.",
            session_key=self.client.session_id or "lilly-chat",
        )

    # ── Cross-avatar awareness ───────────────────────────────────────────────

    async def broadcast_to_avatars(self, message: str, category: str = "announcement"):
        """Write a message visible to all avatars (shared L1 atom).

        Falls back to v1 capture if the atom doesn't exist yet.
        """
        atom_key = f"broadcast.{int(asyncio.get_event_loop().time())}"
        try:
            await self.client.update_atomic(
                key=atom_key,
                value=message,
                tags=["broadcast", category, "hive-mind"],
            )
        except TDAMError as e:
            if e.code == 404:
                await self.client.capture(
                    user_content=f"[BROADCAST:{category}] {message}",
                    assistant_content=f"Broadcast: {message}",
                    session_key=self.client.session_id or "lilly-chat",
                )
            else:
                raise

    async def recall_broadcasts(
        self, category: str | None = None, limit: int = 10
    ) -> list[dict]:
        """Recall recent broadcasts for cross-avatar awareness."""
        query = f"broadcast {category}" if category else "broadcast"
        result = await self.client.search_atomic(query, limit=limit)
        return result.get("data", {}).get("items", [])


# ── FastAPI integration for the memory API ──────────────────────────────────


async def tencentdb_memory_available() -> bool:
    """Check if the TencentDB memory gateway is running."""
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(f"{TENCENTDB_GATEWAY_URL}/health")
            return resp.status_code == 200
    except Exception:
        return False
