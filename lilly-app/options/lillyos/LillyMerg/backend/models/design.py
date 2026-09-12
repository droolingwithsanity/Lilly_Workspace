from pydantic import BaseModel
from typing import Optional, List
from enum import Enum
import time


class DesignTokenType(str, Enum):
    COLOR = "color"
    SPACING = "spacing"
    TYPOGRAPHY = "typography"
    SHADOW = "shadow"
    RADIUS = "radius"
    BREAKPOINT = "breakpoint"


class DesignToken(BaseModel):
    id: str
    name: str
    type: DesignTokenType
    value: str
    category: str = "global"
    description: Optional[str] = None


class ComponentSpec(BaseModel):
    id: str
    name: str
    description: str
    html_template: Optional[str] = None
    css_rules: Optional[str] = None
    props: dict = {}
    tokens_used: List[str] = []
    created_at: float = time.time()


class DesignSpec(BaseModel):
    id: str
    ticket_id: str
    agent_id: str
    type: str = "ui_mockup"  # ui_mockup, ux_research, design_system, prototype
    title: str
    description: str
    content: str  # HTML/CSS or markdown
    feedback: List[dict] = []
    approved: bool = False
    created_at: float = time.time()


class UXResearch(BaseModel):
    id: str
    ticket_id: str
    agent_id: str
    research_type: str  # user_story, persona, journey_map, usability_test
    title: str
    findings: str
    recommendations: List[str] = []
    created_at: float = time.time()
