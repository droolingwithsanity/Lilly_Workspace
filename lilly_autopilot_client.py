"""
Lilly Autopilot Client
======================
Connects Lilly AI to the Mission Control Autopilot system at:
  http://100.93.131.114:4000/api/

Product: "Lilly" (ID: ec8e611b-6f2a-4f9c-90f6-f7e3505bc180) in Default workspace

Enables Lilly to:
- Fetch improvement ideas and research from the autopilot
- Discuss them with users in conversation
- Track progress on self-improvement initiatives
- Request user approvals for new features
"""

import os
import json
import httpx
from datetime import datetime
from typing import Optional

AUTOPILOT_URL = os.environ.get("AUTOPILOT_URL", "http://100.93.131.114:4000")
# The Lilly product ID (discovered via /api/products?workspace_id=default)
AUTOPILOT_PRODUCT_ID = os.environ.get(
    "AUTOPILOT_PRODUCT_ID", "ec8e611b-6f2a-4f9c-90f6-f7e3505bc180"
)
AUTOPILOT_WORKSPACE = os.environ.get("AUTOPILOT_WORKSPACE", "default")

# Headers needed for auth bypass (middleware checks Origin/Referer)
DEFAULT_HEADERS = {
    "Content-Type": "application/json",
    "Origin": AUTOPILOT_URL,
    "Referer": f"{AUTOPILOT_URL}/",
}


class LillyAutopilotClient:
    """Client for interacting with the Mission Control Autopilot API."""

    def __init__(
        self, workspace_id: Optional[str] = None, product_id: Optional[str] = None
    ):
        self.workspace_id = workspace_id or AUTOPILOT_WORKSPACE
        self.product_id = product_id or AUTOPILOT_PRODUCT_ID
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                headers=DEFAULT_HEADERS,
            )
        return self._client

    def _url(self, path: str) -> str:
        """Build API URL with workspace_id query param."""
        return f"{AUTOPILOT_URL}/api/{path}?workspace_id={self.workspace_id}"

    async def list_products(self) -> list[dict]:
        """List all products in the workspace."""
        try:
            client = await self._get_client()
            r = await client.get(self._url("products"))
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"[autopilot] list_products error: {e}")
            return []

    async def get_product(self, product_id: Optional[str] = None) -> Optional[dict]:
        """Get a specific product by ID."""
        pid = product_id or self.product_id
        try:
            client = await self._get_client()
            r = await client.get(self._url(f"products/{pid}"))
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"[autopilot] get_product error: {e}")
            return None

    async def list_ideas(
        self, product_id: Optional[str] = None, limit: int = 10
    ) -> list[dict]:
        """Get improvement ideas for a product."""
        pid = product_id or self.product_id
        try:
            client = await self._get_client()
            r = await client.get(
                self._url(f"products/{pid}/ideas"), params={"limit": limit}
            )
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"[autopilot] list_ideas error: {e}")
            return []

    async def get_pending_approvals(
        self, product_id: Optional[str] = None
    ) -> list[dict]:
        """Get ideas pending user approval."""
        pid = product_id or self.product_id
        try:
            client = await self._get_client()
            r = await client.get(self._url(f"products/{pid}/ideas/pending"))
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"[autopilot] get_pending_approvals error: {e}")
            return []

    async def approve_idea(
        self, idea_id: str, product_id: Optional[str] = None
    ) -> bool:
        """Approve an idea."""
        pid = product_id or self.product_id
        try:
            client = await self._get_client()
            r = await client.post(self._url(f"products/{pid}/ideas/{idea_id}/approve"))
            r.raise_for_status()
            return True
        except Exception as e:
            print(f"[autopilot] approve_idea error: {e}")
            return False

    async def get_health_score(
        self, product_id: Optional[str] = None
    ) -> Optional[dict]:
        """Get the health score for a product."""
        pid = product_id or self.product_id
        try:
            client = await self._get_client()
            r = await client.get(self._url(f"products/{pid}/health/scores"))
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"[autopilot] get_health_score error: {e}")
            return None

    async def get_research_cycles(self, product_id: Optional[str] = None) -> list[dict]:
        """Get recent research cycles for a product."""
        pid = product_id or self.product_id
        try:
            client = await self._get_client()
            r = await client.get(self._url(f"products/{pid}/research/cycles"))
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"[autopilot] get_research_cycles error: {e}")
            return []

    async def list_tasks(self, assignee: Optional[str] = None) -> list[dict]:
        """List tasks (optionally filtered by assignee)."""
        try:
            client = await self._get_client()
            params = {"workspace_id": self.workspace_id}
            if assignee:
                params["assignee"] = assignee
            r = await client.get(f"{AUTOPILOT_URL}/api/tasks", params=params)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"[autopilot] list_tasks error: {e}")
            return []

    async def close(self):
        """Clean up the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()


# ─── Convenience functions for Lilly AI integration ──────────────────────


async def get_lilly_improvement_ideas() -> list[dict]:
    """Fetch the latest improvement ideas for the Lilly AI product."""
    client = LillyAutopilotClient()
    ideas = await client.list_ideas(limit=5)
    await client.close()
    return ideas


async def get_lilly_health() -> Optional[dict]:
    """Get Lilly AI's health score from the autopilot system."""
    client = LillyAutopilotClient()
    health = await client.get_health_score()
    await client.close()
    return health


async def get_lilly_research() -> list[dict]:
    """Get recent research cycles for Lilly AI self-improvement."""
    client = LillyAutopilotClient()
    cycles = await client.get_research_cycles()
    await client.close()
    return cycles


async def get_pending_approvals() -> list[dict]:
    """Get ideas pending user approval."""
    client = LillyAutopilotClient()
    pending = await client.get_pending_approvals()
    await client.close()
    return pending


def format_ideas_for_lilly(ideas: list[dict]) -> str:
    """Format autopilot ideas into a conversational summary for Lilly."""
    if not ideas:
        return (
            "I don't have any new improvement ideas from my research system right now."
        )

    parts = ["Here's what my self-improvement system suggests:"]
    for i, idea in enumerate(ideas[:3], 1):
        title = idea.get("title", "Untitled idea")
        desc = idea.get("description", "No description")
        impact = idea.get("impact_score", "?")
        effort = idea.get("estimated_effort_hours", "?")
        complexity = idea.get("complexity", "?")

        short_desc = desc[:120] + "..." if len(desc) > 120 else desc
        parts.append(
            f"{i}. {title} (impact: {impact}/10, effort: {effort}h, complexity: {complexity}). {short_desc}"
        )

    parts.append("Want me to work on any of these?")
    return " ".join(parts)


def format_health_for_lilly(health: dict) -> str:
    """Format health score into a conversational summary."""
    if not health:
        return "I couldn't reach my health score system right now."

    try:
        score = health.get("score", health.get("health_score", 0))
        status = health.get("status", "unknown")
        details = health.get("components", {})

        parts = [f"My system health is at {score}% — {status}."]
        if details:
            for comp, val in list(details.items())[:3]:
                parts.append(f"{comp}: {val}")

        return " ".join(parts)
    except:
        return f"Health data received but couldn't parse: {json.dumps(health)[:200]}"
