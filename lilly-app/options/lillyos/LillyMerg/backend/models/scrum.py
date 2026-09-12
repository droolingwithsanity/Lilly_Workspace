from pydantic import BaseModel
from typing import Optional, List
from enum import Enum
import time


class NoticeType(str, Enum):
    SPRINT_START = "sprint_start"
    SPRINT_COMPLETE = "sprint_complete"
    DEPLOYMENT = "deployment"
    SLA_BREACH = "sla_breach"
    MILESTONE = "milestone"
    DESIGN_REVIEW = "design_review"
    UX_REVIEW = "ux_review"
    AGENT_STATUS = "agent_status"
    TEAM_UPDATE = "team_update"
    SYSTEM_ALERT = "system_alert"


class NoticePriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class Notice(BaseModel):
    id: str
    type: NoticeType
    title: str
    message: str
    priority: NoticePriority = NoticePriority.NORMAL
    source: str = "system"
    project_id: Optional[str] = None
    sprint_id: Optional[str] = None
    ticket_id: Optional[str] = None
    created_at: float = time.time()
    acknowledged: bool = False


class StandupEntry(BaseModel):
    id: str
    team_name: str
    date: str
    agent_id: Optional[str] = None
    yesterday: str = ""
    today: str = ""
    blockers: List[str] = []
    created_at: float = time.time()


class DailyScrum(BaseModel):
    id: str
    team_name: str  # e.g. "Platform Core", "Design Team", "DevOps"
    date: str
    entries: List[StandupEntry] = []
    summary: str = ""
    velocity_current: int = 0
    velocity_target: int = 0
    created_at: float = time.time()
