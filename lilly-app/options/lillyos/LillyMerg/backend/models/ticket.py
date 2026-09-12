from pydantic import BaseModel
from typing import Optional, List
from enum import Enum
import time


class TicketType(str, Enum):
    EPIC = "epic"
    STORY = "story"
    TASK = "task"
    BUG = "bug"
    DESIGN = "design"
    UX_RESEARCH = "ux_research"


class TicketStatus(str, Enum):
    BACKLOG = "backlog"
    GROOMED = "groomed"
    SPRINT_READY = "sprint_ready"
    IN_SPRINT = "in_sprint"
    IN_PROGRESS = "in_progress"
    DESIGN_REVIEW = "design_review"
    UX_REVIEW = "ux_review"
    READY_FOR_DEV = "ready_for_dev"
    IN_REVIEW = "in_review"
    DONE = "done"
    CANCELLED = "cancelled"


class Priority(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SLABreach(BaseModel):
    rule: str
    triggered_at: float
    ticket_id: str


class Ticket(BaseModel):
    id: str
    title: str
    description: str
    type: TicketType
    status: TicketStatus = TicketStatus.BACKLOG
    priority: Priority = Priority.MEDIUM
    project_id: str
    sprint_id: Optional[str] = None
    epic_id: Optional[str] = None
    assignee: Optional[str] = None  # agent id
    story_points: int = 1
    sla_deadline: float  # timestamp
    sla_response_hours: float = 4.0
    labels: List[str] = []
    created_at: float
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    closed_at: Optional[float] = None
    sla_breaches: List[SLABreach] = []
    metadata: dict = {}
