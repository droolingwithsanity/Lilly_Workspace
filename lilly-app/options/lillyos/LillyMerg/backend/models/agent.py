from pydantic import BaseModel
from typing import Optional, List
from enum import Enum
import time


class AgentRole(str, Enum):
    # Engineering
    PROJECT_MANAGER = "pm"
    DEVELOPER = "developer"
    DEVOPS = "devops"
    QA = "qa"
    UI_DESIGNER = "designer"
    UX_RESEARCHER = "ux"
    # Finance
    FINANCE_MANAGER = "finance_manager"
    ACCOUNTANT = "accountant"
    INVESTOR_RELATIONS = "investor_relations"
    TREASURY_ANALYST = "treasury_analyst"
    # Operations
    OPS_MANAGER = "ops_manager"
    VERIFICATION_SPECIALIST = "verification_specialist"
    DISPUTE_MANAGER = "dispute_manager"
    CUSTOMER_SUPPORT = "customer_support"
    # Data & Analytics
    DATA_SCIENTIST = "data_scientist"
    ML_ENGINEER = "ml_engineer"
    RISK_ANALYST = "risk_analyst"
    ANALYTICS_ENGINEER = "analytics_engineer"
    # Marketing
    MARKETING_MANAGER = "marketing_manager"
    CONTENT_CREATOR = "content_creator"
    B2B_SALES = "b2b_sales"
    CUSTOMER_SUCCESS = "customer_success"
    # HR
    HR_MANAGER = "hr_manager"
    RECRUITER = "recruiter"
    BENEFITS_ADMIN = "benefits_admin"
    TRAINING_COORDINATOR = "training_coordinator"
    # Legal
    CORPORATE_LAWYER = "corporate_lawyer"
    COMPLIANCE_OFFICER = "compliance_officer"
    CONTRACT_MANAGER = "contract_manager"
    # Admin
    ADMIN_MANAGER = "admin_manager"
    EUC_SPECIALIST = "euc_specialist"
    CSR_COORDINATOR = "csr_coordinator"
    OFFICE_MANAGER = "office_manager"


class AgentTask(BaseModel):
    id: str
    ticket_id: str
    action: str
    status: str = "pending"
    result: Optional[str] = None
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    error: Optional[str] = None


class Agent(BaseModel):
    id: str
    name: str
    role: AgentRole
    model: str = "tinyllama"
    system_prompt: str = ""
    department_id: Optional[str] = None
    enabled: bool = True
    busy: bool = False
    current_task: Optional[AgentTask] = None
    tasks_completed: int = 0
    success_rate: float = 1.0
    created_at: float = time.time()
