from pydantic import BaseModel
from typing import Optional, List
from enum import Enum
import time


class SprintStatus(str, Enum):
    PLANNING = "planning"
    ACTIVE = "active"
    REVIEW = "review"
    CLOSED = "closed"


class Sprint(BaseModel):
    id: str
    name: str
    goal: Optional[str] = None
    project_id: str
    status: SprintStatus = SprintStatus.PLANNING
    ticket_ids: List[str] = []
    start_date: float
    end_date: float
    velocity_planned: int = 0
    velocity_actual: int = 0
    created_at: float = time.time()
