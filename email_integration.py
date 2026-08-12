"""
Open Connector Email/Calendar Integration for Lilly AI
Connects to Oomol Open Connector for Gmail/Outlook email and calendar operations.
Uses SSO for user authentication and token management.
"""

import os
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any
import httpx

logger = logging.getLogger(__name__)

OPENCONNECTOR_URL = os.environ.get("OPENCONNECTOR_URL", "http://connector:3000")
OPENCONNECTOR_RUNTIME_TOKEN = os.environ.get("OPENCONNECTOR_RUNTIME_TOKEN", "")


class OpenConnectorClient:
    """HTTP client for Open Connector API with SSO support."""

    def __init__(
        self,
        base_url: str = OPENCONNECTOR_URL,
        token: str = OPENCONNECTOR_RUNTIME_TOKEN,
    ):
        self.base_url = base_url.rstrip("/")
        self.default_token = token

    def _get_headers(self, user_id: Optional[str] = None) -> Dict[str, str]:
        """Get headers with appropriate authentication."""
        headers = {"Content-Type": "application/json"}

        # Try to get user-specific token from SSO
        if user_id:
            try:
                from sso_auth import sso_manager

                if sso_manager:
                    tokens = sso_manager.get_oauth_tokens(user_id)
                    if tokens and "openconnector" in tokens:
                        headers["Authorization"] = (
                            f"Bearer {tokens['openconnector']['access_token']}"
                        )
                        return headers
            except ImportError:
                pass

        # Fall back to default token
        if self.default_token:
            headers["Authorization"] = f"Bearer {self.default_token}"

        return headers

    async def execute_action(
        self, action: str, input_data: Dict[str, Any], user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Execute an Open Connector action via HTTP API."""
        url = f"{self.base_url}/v1/actions/{action}"
        headers = self._get_headers(user_id)

        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post(
                    url, json={"input": input_data}, headers=headers, timeout=30.0
                )
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as e:
                logger.error(
                    f"Open Connector API error: {e.response.status_code} - {e.response.text}"
                )
                raise
            except Exception as e:
                logger.error(f"Open Connector connection error: {e}")
                raise

    async def health_check(self) -> bool:
        """Check if Open Connector is reachable."""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{self.base_url}/health", timeout=5.0)
                return resp.status_code == 200
        except:
            return False


class GmailIntegration:
    """Gmail operations via Open Connector with SSO support."""

    def __init__(self, client: OpenConnectorClient):
        self.client = client

    async def search_emails(
        self, query: str, max_results: int = 10, user_id: Optional[str] = None
    ) -> List[Dict]:
        """Search Gmail threads by query."""
        result = await self.client.execute_action(
            "gmail.search_threads",
            {"query": query, "maxResults": max_results},
            user_id=user_id,
        )
        return result.get("threads", [])

    async def get_recent_emails(
        self, days: int = 7, max_results: int = 20, user_id: Optional[str] = None
    ) -> List[Dict]:
        """Get emails from the last N days."""
        query = f"newer_than:{days}d"
        return await self.search_emails(query, max_results, user_id=user_id)

    async def get_unread_emails(
        self, max_results: int = 20, user_id: Optional[str] = None
    ) -> List[Dict]:
        """Get unread emails."""
        query = "is:unread"
        return await self.search_emails(query, max_results, user_id=user_id)

    async def get_message(self, message_id: str, user_id: Optional[str] = None) -> Dict:
        """Get a specific email message."""
        return await self.client.execute_action(
            "gmail.get_message", {"messageId": message_id}, user_id=user_id
        )

    async def send_email(
        self,
        to: str,
        subject: str,
        body: str,
        is_html: bool = False,
        user_id: Optional[str] = None,
    ) -> Dict:
        """Send an email."""
        return await self.client.execute_action(
            "gmail.send_email",
            {"to": to, "subject": subject, "body": body, "isHtml": is_html},
            user_id=user_id,
        )

    async def reply_to_email(
        self, message_id: str, thread_id: str, body: str, user_id: Optional[str] = None
    ) -> Dict:
        """Reply to an email thread."""
        return await self.client.execute_action(
            "gmail.reply_email",
            {"messageId": message_id, "threadId": thread_id, "body": body},
            user_id=user_id,
        )

    async def create_draft(
        self, to: str, subject: str, body: str, user_id: Optional[str] = None
    ) -> Dict:
        """Create an email draft."""
        return await self.client.execute_action(
            "gmail.create_draft",
            {"to": to, "subject": subject, "body": body},
            user_id=user_id,
        )

    async def get_profile(self, user_id: Optional[str] = None) -> Dict:
        """Get Gmail profile info."""
        return await self.client.execute_action(
            "gmail.get_profile", {}, user_id=user_id
        )

    async def list_labels(self, user_id: Optional[str] = None) -> List[Dict]:
        """List all Gmail labels."""
        result = await self.client.execute_action(
            "gmail.list_labels", {}, user_id=user_id
        )
        return result.get("labels", [])

    async def add_label_to_email(
        self,
        message_id: str,
        add_label_ids: Optional[List[str]] = None,
        remove_label_ids: Optional[List[str]] = None,
        user_id: Optional[str] = None,
    ) -> Dict:
        """Add/remove labels from an email."""
        input_data: Dict[str, Any] = {"messageId": message_id}
        if add_label_ids:
            input_data["addLabelIds"] = add_label_ids
        if remove_label_ids:
            input_data["removeLabelIds"] = remove_label_ids
        return await self.client.execute_action(
            "gmail.add_label_to_email", input_data, user_id=user_id
        )


class OutlookIntegration:
    """Outlook operations via Open Connector with SSO support."""

    def __init__(self, client: OpenConnectorClient):
        self.client = client

    async def search_emails(
        self, query: str, max_results: int = 10, user_id: Optional[str] = None
    ) -> List[Dict]:
        """Search Outlook emails."""
        result = await self.client.execute_action(
            "outlook.search_messages",
            {"query": query, "top": max_results},
            user_id=user_id,
        )
        return result.get("messages", [])

    async def get_recent_emails(
        self, days: int = 7, max_results: int = 20, user_id: Optional[str] = None
    ) -> List[Dict]:
        """Get recent Outlook emails."""
        query = f"received ge {datetime.now() - timedelta(days=days)}"
        return await self.search_emails(query, max_results, user_id=user_id)

    async def send_email(
        self,
        to: str,
        subject: str,
        body: str,
        is_html: bool = False,
        user_id: Optional[str] = None,
    ) -> Dict:
        """Send an Outlook email."""
        return await self.client.execute_action(
            "outlook.send_mail",
            {"to": to, "subject": subject, "body": body, "isHtml": is_html},
            user_id=user_id,
        )


class EmailTriageSystem:
    """Email triage and prioritization."""

    PRIORITY_LABELS = {
        "urgent": ["URGENT", "HIGH_PRIORITY"],
        "important": ["IMPORTANT"],
        "fyi": ["FYI"],
        "spam": ["SPAM"],
    }

    def __init__(
        self, gmail: GmailIntegration, outlook: Optional[OutlookIntegration] = None
    ):
        self.gmail = gmail
        self.outlook = outlook

    async def triage_recent_emails(
        self, days: int = 1, user_id: Optional[str] = None
    ) -> Dict[str, List[Dict]]:
        """Triage emails from the last N days by priority."""
        emails = await self.gmail.get_recent_emails(days=days, user_id=user_id)

        triaged = {"urgent": [], "important": [], "normal": [], "low": []}

        for thread in emails:
            messages = thread.get("messages", [])
            if not messages:
                continue

            latest = messages[-1]
            priority = self._classify_priority(latest)
            triaged[priority].append(
                {
                    "thread_id": thread.get("threadId"),
                    "message_id": latest.get("messageId"),
                    "subject": latest.get("subject", "No subject"),
                    "sender": latest.get("sender", "Unknown"),
                    "timestamp": latest.get("messageTimestamp"),
                    "snippet": thread.get("snippet", ""),
                    "labels": latest.get("labelIds", []),
                }
            )

        return triaged

    def _classify_priority(self, message: Dict) -> str:
        """Classify email priority based on content and metadata."""
        subject = (message.get("subject", "") or "").lower()
        sender = (message.get("sender", "") or "").lower()
        labels = message.get("labelIds", [])

        # Check for urgent indicators
        urgent_keywords = ["urgent", "asap", "emergency", "immediate", "deadline"]
        if any(kw in subject for kw in urgent_keywords):
            return "urgent"

        # Check for important labels
        if "IMPORTANT" in labels or "HIGH_PRIORITY" in labels:
            return "important"

        # Check sender importance (could be expanded with user preferences)
        # For now, just classify as normal

        return "normal"

    async def generate_daily_digest(
        self, user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Generate a daily email digest."""
        triaged = await self.triage_recent_emails(days=1, user_id=user_id)

        return {
            "date": datetime.now().isoformat(),
            "urgent_count": len(triaged.get("urgent", [])),
            "important_count": len(triaged.get("important", [])),
            "normal_count": len(triaged.get("normal", [])),
            "low_count": len(triaged.get("low", [])),
            "urgent_emails": triaged.get("urgent", [])[:5],
            "important_emails": triaged.get("important", [])[:5],
            "summary": self._generate_summary(triaged),
        }

    def _generate_summary(self, triaged: Dict) -> str:
        """Generate a text summary of triaged emails."""
        urgent = len(triaged.get("urgent", []))
        important = len(triaged.get("important", []))
        total = sum(len(v) for v in triaged.values())

        parts = []
        if urgent > 0:
            parts.append(f"{urgent} urgent")
        if important > 0:
            parts.append(f"{important} important")

        normal = len(triaged.get("normal", []))
        low = len(triaged.get("low", []))

        if parts:
            return f"Email summary: {', '.join(parts)} out of {total} total emails."
        else:
            return f"You have {total} new emails."


class MeetingIntelligence:
    """Meeting intelligence and preparation."""

    def __init__(self, gmail: GmailIntegration, llm_callback=None):
        self.gmail = gmail
        self.llm_callback = llm_callback

    async def get_meeting_prep(
        self,
        meeting_subject: str,
        meeting_time: datetime,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Prepare for a meeting by gathering relevant information."""
        # Search for emails related to the meeting
        search_query = f"subject:({meeting_subject}) newer_than:7d"
        related_emails = await self.gmail.search_emails(
            search_query, max_results=10, user_id=user_id
        )

        # Extract key information
        prep = {
            "meeting_subject": meeting_subject,
            "meeting_time": meeting_time.isoformat(),
            "related_emails_count": len(related_emails),
            "related_emails": [],
            "key_points": [],
            "action_items": [],
        }

        for thread in related_emails[:5]:  # Limit to 5 most relevant
            messages = thread.get("messages", [])
            if messages:
                latest = messages[-1]
                prep["related_emails"].append(
                    {
                        "subject": latest.get("subject"),
                        "sender": latest.get("sender"),
                        "snippet": thread.get("snippet", ""),
                    }
                )

        # Generate key points if LLM available
        if self.llm_callback and related_emails:
            # Build context from emails
            email_context = "\n".join(
                [
                    f"From: {e.get('sender')}\nSubject: {e.get('subject')}\nSnippet: {e.get('snippet')}"
                    for e in prep["related_emails"]
                ]
            )

            prompt = f"""Analyze these emails related to a meeting about "{meeting_subject}" and extract:
1. Key discussion points
2. Any action items or decisions needed
3. Important context for the meeting

Emails:
{email_context}

Provide a concise summary."""

            try:
                response = await self.llm_callback(prompt, max_tokens=500)
                prep["key_points"] = response.split("\n") if response else []
            except Exception as e:
                logger.error(f"LLM processing error: {e}")

        return prep


class MorningBriefing:
    """Morning briefing generator combining email, calendar, and notifications."""

    def __init__(
        self, email_triage: EmailTriageSystem, meeting_intel: MeetingIntelligence
    ):
        self.email_triage = email_triage
        self.meeting_intel = meeting_intel

    async def generate_briefing(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Generate a morning briefing."""
        # Get email digest
        email_digest = await self.email_triage.generate_daily_digest(user_id=user_id)

        # Prepare meeting info (would need calendar integration)
        meetings = []  # Placeholder - would come from calendar

        briefing = {
            "generated_at": datetime.now().isoformat(),
            "email_summary": email_digest,
            "upcoming_meetings": meetings,
            "priority_items": [],
            "suggestions": [],
        }

        # Combine priority items
        if email_digest.get("urgent_count", 0) > 0:
            briefing["priority_items"].append(
                f"{email_digest['urgent_count']} urgent emails require attention"
            )

        if email_digest.get("important_count", 0) > 0:
            briefing["priority_items"].append(
                f"{email_digest['important_count']} important emails to review"
            )

        # Generate suggestions
        briefing["suggestions"] = self._generate_suggestions(email_digest, meetings)

        return briefing

    def _generate_suggestions(self, email_digest: Dict, meetings: List) -> List[str]:
        """Generate actionable suggestions."""
        suggestions = []

        if email_digest.get("urgent_count", 0) > 0:
            suggestions.append("Review urgent emails first")

        if email_digest.get("normal_count", 0) > 10:
            suggestions.append("Consider batch processing normal emails")

        return suggestions


class ReplyDraftingSystem:
    """Email reply drafting using Lilly's LLM."""

    def __init__(self, gmail: GmailIntegration, llm_callback=None):
        self.gmail = gmail
        self.llm_callback = llm_callback

    async def draft_reply(
        self,
        message_id: str,
        thread_id: str,
        context: str = "",
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Draft a reply to an email."""
        # Get the original message
        original = await self.gmail.get_message(message_id, user_id=user_id)

        if not self.llm_callback:
            return {
                "error": "LLM not available for drafting",
                "original_subject": original.get("subject"),
                "original_sender": original.get("from"),
            }

        # Build prompt for drafting
        prompt = f"""Draft a professional email reply to:

From: {original.get("from")}
Subject: {original.get("subject")}
Date: {original.get("date")}

Original message:
{original.get("body", "No content available")}

Additional context: {context}

Draft a concise, professional reply:"""

        try:
            draft_text = await self.llm_callback(prompt, max_tokens=500)

            # Optionally create draft in Gmail
            draft_result = await self.gmail.create_draft(
                to=original.get("from") or "",
                subject=f"Re: {original.get('subject')}",
                body=draft_text,
                user_id=user_id,
            )

            return {
                "draft_text": draft_text,
                "draft_id": draft_result.get("draftId"),
                "original_subject": original.get("subject"),
                "original_sender": original.get("from"),
            }
        except Exception as e:
            logger.error(f"Draft generation error: {e}")
            return {"error": str(e)}

    async def draft_simple_reply(
        self,
        message_id: str,
        thread_id: str,
        tone: str = "professional",
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Draft a simple acknowledgment reply."""
        return await self.draft_reply(
            message_id,
            thread_id,
            context=f"Write a {tone} acknowledgment/reply",
            user_id=user_id,
        )


# Global instances (initialized in lilly_ai.py)
openconnector_client = None
gmail_integration = None
outlook_integration = None
email_triage = None
meeting_intelligence = None
morning_briefing = None
reply_drafting = None


def init_email_integration(llm_callback=None):
    """Initialize email integration components."""
    global openconnector_client, gmail_integration, outlook_integration
    global email_triage, meeting_intelligence, morning_briefing, reply_drafting

    openconnector_client = OpenConnectorClient()
    gmail_integration = GmailIntegration(openconnector_client)

    # Outlook integration (optional - depends on configuration)
    outlook_integration = None  # Would need separate OAuth setup

    email_triage = EmailTriageSystem(gmail_integration, outlook_integration)
    meeting_intelligence = MeetingIntelligence(gmail_integration, llm_callback)
    morning_briefing = MorningBriefing(email_triage, meeting_intelligence)
    reply_drafting = ReplyDraftingSystem(gmail_integration, llm_callback)

    logger.info("Email integration initialized")


# Example usage in lilly_ai.py:
#
# from email_integration import init_email_integration, gmail_integration, email_triage, morning_briefing
#
# # In lifespan startup:
# init_email_integration(llm_callback=your_llm_function)
#
# # Add background task:
# asyncio.create_task(email_monitoring_loop())
#
# # Add API endpoints:
# @app.get("/api/email/digest")
# async def get_email_digest():
#     return await email_triage.generate_daily_digest()
#
# @app.get("/api/email/briefing")
# async def get_morning_briefing():
#     return await morning_briefing.generate_briefing()
#
# @app.post("/api/email/reply/{message_id}")
# async def draft_email_reply(message_id: str, thread_id: str, context: str = ""):
#     return await reply_drafting.draft_reply(message_id, thread_id, context)
