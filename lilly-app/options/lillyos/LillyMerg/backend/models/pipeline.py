from pydantic import BaseModel
from typing import Optional, List
from enum import Enum
import time


class PipelineStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    FAILED = "failed"
    SUCCESS = "success"


class DeployStatus(str, Enum):
    PENDING = "pending"
    DEPLOYING = "deploying"
    LIVE = "live"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


class Environment(str, Enum):
    DEV = "dev"
    STAGING = "staging"
    PRODUCTION = "production"


class Pipeline(BaseModel):
    id: str
    name: str
    project_id: str
    stages: List[str] = ["build", "test", "deploy"]
    status: PipelineStatus = PipelineStatus.IDLE
    last_run_id: Optional[str] = None
    created_at: float = time.time()


class PipelineRun(BaseModel):
    id: str
    pipeline_id: str
    project_id: str
    status: PipelineStatus = PipelineStatus.RUNNING
    commit_sha: Optional[str] = None
    branch: str = "main"
    started_at: float
    completed_at: Optional[float] = None
    duration_seconds: Optional[float] = None
    logs: List[str] = []
    triggered_by: str = "system"


class Deployment(BaseModel):
    id: str
    pipeline_run_id: str
    project_id: str
    environment: Environment
    status: DeployStatus = DeployStatus.PENDING
    version: str
    deployed_at: Optional[float] = None
    uptime_check: bool = False
    rollback_version: Optional[str] = None
