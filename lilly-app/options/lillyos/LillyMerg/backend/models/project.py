from pydantic import BaseModel
from typing import Optional, List
from enum import Enum
import time


class ProjectStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class Project(BaseModel):
    id: str
    name: str
    description: str
    department_id: Optional[str] = None
    status: ProjectStatus = ProjectStatus.ACTIVE
    repo_url: Optional[str] = None
    pipeline_ids: List[str] = []
    current_sprint_id: Optional[str] = None
    sla_uptime_target: float = 99.5
    sla_response_target_hours: float = 4.0
    created_at: float = time.time()
    metadata: dict = {}
