from pydantic import BaseModel
from typing import Optional, List
import time


class EditorLayout(BaseModel):
    id: str
    name: str
    components: list = []
    created_at: float = time.time()
