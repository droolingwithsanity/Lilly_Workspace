from pydantic import BaseModel
from typing import Optional, List
from enum import Enum
import time


class TaskletTrigger(str, Enum):
    MANUAL = "manual"
    SCHEDULED = "scheduled"
    EVENT = "event"


class TaskletStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskletLog(BaseModel):
    timestamp: float
    message: str
    level: str = "info"


class TaskletEvent(BaseModel):
    type: str  # "tasklet_status", "tasklet_log", "tasklet_complete", "tasklet_failed"
    tasklet_id: str
    department_id: str
    message: str
    timestamp: float = time.time()


class Tasklet(BaseModel):
    id: str
    department_id: str
    name: str
    description: str
    prompt: str = ""
    status: TaskletStatus = TaskletStatus.IDLE
    trigger: TaskletTrigger = TaskletTrigger.MANUAL
    schedule: Optional[str] = None
    last_run: Optional[float] = None
    next_run: Optional[float] = None
    run_count: int = 0
    last_result: Optional[str] = None
    logs: List[TaskletLog] = []
    agent_id: Optional[str] = None
    created_at: float
