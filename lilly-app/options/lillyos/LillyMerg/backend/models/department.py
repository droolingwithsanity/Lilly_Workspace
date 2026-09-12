from pydantic import BaseModel
from typing import Optional, List
from enum import Enum
import time


class Department(BaseModel):
    id: str
    name: str
    description: str
    icon: str = ""
    color: str = "#6366F1"
    project_ids: List[str] = []
    created_at: float = time.time()
