import time
import uuid
from typing import Optional, List
from pydantic import BaseModel, Field
from models import (
    Project, Sprint, Ticket, TicketStatus, TicketType, SprintStatus,
    Agent, AgentRole, AgentTask,
    Pipeline, PipelineRun, Deployment, Environment, DeployStatus, PipelineStatus,
    DesignToken, DesignTokenType, ComponentSpec, DesignSpec, UXResearch,
    Notice, NoticeType, NoticePriority, StandupEntry, DailyScrum,
    Tasklet, TaskletTrigger, TaskletStatus, TaskletLog, TaskletEvent,
    Department, Priority,
)


class UserNotification(BaseModel):
    id: str
    title: str
    message: str
    agent_id: str
    agent_name: str
    ticket_id: str
    ticket_title: str
    read: bool = False
    created_at: float = Field(default_factory=time.time)


DEPARTMENTS = [
    {
        "id": "dept-eng",
        "name": "Engineering",
        "description": "Builds and maintains the core platform. Agile software development, infrastructure, and design.",
        "icon": "\u2699\ufe0f",
        "color": "#3B82F6",
        "agents": [
            ("agent-pm-1", "Sarah Chen", AgentRole.PROJECT_MANAGER, "llama3"),
            ("agent-pm-2", "Marcus Johnson", AgentRole.PROJECT_MANAGER, "llama3"),
            ("agent-dev-1", "Alex Kim", AgentRole.DEVELOPER, "qwen2.5-coder"),
            ("agent-dev-2", "Priya Patel", AgentRole.DEVELOPER, "qwen2.5-coder"),
            ("agent-dev-3", "Jordan Lee", AgentRole.DEVELOPER, "qwen2.5-coder"),
            ("agent-ops-1", "Morgan West", AgentRole.DEVOPS, "llama3"),
            ("agent-qa-1", "Riley Cooper", AgentRole.QA, "llama3"),
            ("agent-ds-1", "Maya Rivera", AgentRole.UI_DESIGNER, "qwen2.5-coder"),
            ("agent-ds-2", "James Okafor", AgentRole.UI_DESIGNER, "qwen2.5-coder"),
            ("agent-ux-1", "Lena Vos", AgentRole.UX_RESEARCHER, "llama3"),
        ],
        "tickets": [
            ("Set up CI/CD pipeline", "Configure GitHub Actions for automated builds and deployments", "story", 5, "high"),
            ("Implement user authentication", "Add OAuth2 login with Google and GitHub providers", "story", 8, "critical"),
            ("Fix memory leak in worker pool", "Worker processes don't release memory after task completion", "bug", 5, "high"),
            ("Design system v2 landing page", "Redesign the landing page with new brand guidelines", "design", 5, "high"),
            ("User onboarding flow UX research", "Research and prototype the new user onboarding flow", "ux_research", 8, "medium"),
        ],
    },
    {
        "id": "dept-fin",
        "name": "Finance & Treasury",
        "description": "Manages corporate funds, capitalization, investor relations, and monetary flow.",
        "icon": "\U0001F4B0",
        "color": "#10B981",
        "agents": [
            ("agent-fin-1", "Victoria Sterling", AgentRole.FINANCE_MANAGER, "llama3"),
            ("agent-fin-2", "Oliver Chase", AgentRole.ACCOUNTANT, "llama3"),
            ("agent-fin-3", "Naomi Rivers", AgentRole.INVESTOR_RELATIONS, "llama3"),
            ("agent-fin-4", "Ethan Gold", AgentRole.TREASURY_ANALYST, "llama3"),
        ],
        "tickets": [
            ("Q3 financial reporting package", "Prepare quarterly financial statements, P&L, balance sheet, cash flow", "task", 8, "high"),
            ("Investor deck for Series B", "Create investor presentation with growth metrics and projections", "task", 5, "high"),
            ("Treasury liquidity analysis", "Analyze current cash position and 90-day liquidity forecast", "task", 3, "medium"),
            ("Audit readiness review", "Prepare documentation for external audit of FY2024", "task", 8, "critical"),
            ("Budget variance report", "Compare actual vs budgeted spend across all departments", "task", 5, "medium"),
        ],
    },
    {
        "id": "dept-ops",
        "name": "Operations",
        "description": "Handles account verification, transaction disputes, and day-to-day customer issues.",
        "icon": "\U0001F517",
        "color": "#F59E0B",
        "agents": [
            ("agent-ops-1", "Sofia Martinez", AgentRole.OPS_MANAGER, "llama3"),
            ("agent-ops-2", "Derek Walsh", AgentRole.VERIFICATION_SPECIALIST, "llama3"),
            ("agent-ops-3", "Aisha Patel", AgentRole.DISPUTE_MANAGER, "llama3"),
            ("agent-ops-4", "Liam O'Brien", AgentRole.CUSTOMER_SUPPORT, "llama3"),
        ],
        "tickets": [
            ("Account verification backlog", "Process 250 pending KYC/AML verifications from last week", "task", 5, "high"),
            ("Transaction dispute escalation flow", "Redesign dispute handling for high-value transactions over $10k", "story", 8, "critical"),
            ("Support ticket triage automation", "Implement auto-categorization for incoming customer issues", "task", 3, "medium"),
            ("SLA compliance review", "Audit response times across all support tiers", "task", 5, "medium"),
            ("Onboarding verification optimization", "Reduce account verification time from 48h to 4h", "story", 8, "high"),
        ],
    },
    {
        "id": "dept-data",
        "name": "Data & Analytics",
        "description": "Leverages big data and AI to evaluate creditworthiness, predict behavior, and drive decisions.",
        "icon": "\U0001F4CA",
        "color": "#8B5CF6",
        "agents": [
            ("agent-data-1", "Zara Khan", AgentRole.DATA_SCIENTIST, "qwen2.5-coder"),
            ("agent-data-2", "Tom Chen", AgentRole.ML_ENGINEER, "qwen2.5-coder"),
            ("agent-data-3", "Rachel Green", AgentRole.RISK_ANALYST, "llama3"),
            ("agent-data-4", "David Park", AgentRole.ANALYTICS_ENGINEER, "qwen2.5-coder"),
        ],
        "tickets": [
            ("Credit scoring model v3", "Train and validate new ML model for credit risk assessment", "story", 13, "critical"),
            ("Real-time fraud detection pipeline", "Build streaming pipeline for transaction fraud scoring", "story", 8, "high"),
            ("Customer churn prediction", "Develop churn model with early warning indicators", "task", 5, "medium"),
            ("Data warehouse migration", "Migrate analytics data warehouse to new schema", "task", 8, "high"),
            ("AI-driven recommendation engine", "Build product recommendation system for cross-selling", "story", 13, "medium"),
        ],
    },
    {
        "id": "dept-mkt",
        "name": "Marketing, Sales & CS",
        "description": "Drives user acquisition, B2B sales, and customer retention.",
        "icon": "\U0001F4E2",
        "color": "#EC4899",
        "agents": [
            ("agent-mkt-1", "Jessica Blake", AgentRole.MARKETING_MANAGER, "llama3"),
            ("agent-mkt-2", "Ryan Torres", AgentRole.CONTENT_CREATOR, "llama3"),
            ("agent-mkt-3", "Danielle Kim", AgentRole.B2B_SALES, "llama3"),
            ("agent-mkt-4", "Chris Hall", AgentRole.CUSTOMER_SUCCESS, "llama3"),
        ],
        "tickets": [
            ("Q4 go-to-market campaign", "Plan and execute product launch campaign across all channels", "story", 8, "high"),
            ("B2B enterprise sales deck", "Create tailored presentation for enterprise prospects", "task", 5, "high"),
            ("Customer onboarding series", "Design automated email sequence for new user activation", "task", 3, "medium"),
            ("Content calendar planning", "Plan 90 days of blog posts, case studies, and whitepapers", "task", 5, "medium"),
            ("Churn reduction initiative", "Analyze churn data and implement retention playbook", "story", 8, "critical"),
        ],
    },
    {
        "id": "dept-hr",
        "name": "Human Resources",
        "description": "Manages employee relations, payroll, benefits, recruitment, and workplace policies.",
        "icon": "\U0001F465",
        "color": "#6366F1",
        "agents": [
            ("agent-hr-1", "Nina Patel", AgentRole.HR_MANAGER, "llama3"),
            ("agent-hr-2", "Oscar Mendez", AgentRole.RECRUITER, "llama3"),
            ("agent-hr-3", "Tanya Woods", AgentRole.BENEFITS_ADMIN, "llama3"),
            ("agent-hr-4", "Felix Grant", AgentRole.TRAINING_COORDINATOR, "llama3"),
        ],
        "tickets": [
            ("Q4 hiring plan execution", "Fill 12 open positions across engineering, ops, and marketing", "task", 8, "high"),
            ("Benefits open enrollment", "Manage annual benefits selection for 200+ employees", "task", 5, "high"),
            ("Performance review cycle", "Coordinate mid-year reviews, collect feedback, compile results", "task", 8, "medium"),
            ("New hire onboarding program", "Revamp onboarding experience for remote-first hires", "story", 5, "medium"),
            ("Diversity & inclusion report", "Compile annual D&I metrics and improvement roadmap", "task", 3, "medium"),
        ],
    },
    {
        "id": "dept-legal",
        "name": "Legal & Compliance",
        "description": "Ensures regulatory compliance, manages contracts, and handles corporate governance.",
        "icon": "\u2696\ufe0f",
        "color": "#EF4444",
        "agents": [
            ("agent-legal-1", "Catherine Stone", AgentRole.CORPORATE_LAWYER, "llama3"),
            ("agent-legal-2", "Marcus Webb", AgentRole.COMPLIANCE_OFFICER, "llama3"),
            ("agent-legal-3", "Sophia Lin", AgentRole.CONTRACT_MANAGER, "llama3"),
        ],
        "tickets": [
            ("GDPR compliance audit", "Full audit of data handling practices across all products", "task", 8, "critical"),
            ("Vendor contract renewals", "Review and renegotiate 15 vendor agreements expiring next quarter", "task", 5, "high"),
            ("Terms of service update", "Update ToS for new product features launching in Q1", "task", 3, "high"),
            ("IP portfolio review", "Audit patent and trademark portfolio, identify gaps", "task", 5, "medium"),
            ("Employment law compliance", "Review handbooks and policies for regulatory changes in 3 states", "task", 3, "medium"),
        ],
    },
    {
        "id": "dept-admin",
        "name": "Admin & EUC",
        "description": "Corporate social responsibility, end-user computing, and office administration.",
        "icon": "\U0001F3E2",
        "color": "#14B8A6",
        "agents": [
            ("agent-admin-1", "Rebecca Torres", AgentRole.ADMIN_MANAGER, "llama3"),
            ("agent-admin-2", "Ian Clarke", AgentRole.EUC_SPECIALIST, "llama3"),
            ("agent-admin-3", "Maria Santos", AgentRole.CSR_COORDINATOR, "llama3"),
            ("agent-admin-4", "James Wright", AgentRole.OFFICE_MANAGER, "llama3"),
        ],
        "tickets": [
            ("CSR annual report", "Compile corporate social responsibility impact report", "task", 5, "medium"),
            ("Endpoint security compliance", "Ensure all 250 corporate devices meet security baseline", "task", 8, "high"),
            ("Office relocation planning", "Plan and coordinate move to new headquarters floor", "task", 5, "medium"),
            ("IT asset inventory", "Complete audit of all hardware, software licenses, and subscriptions", "task", 3, "medium"),
            ("Sustainability initiative", "Launch office recycling program and carbon offset partnership", "story", 3, "low"),
        ],
    },
]


class DataStore:
    def __init__(self):
        self.projects: dict[str, Project] = {}
        self.sprints: dict[str, Sprint] = {}
        self.tickets: dict[str, Ticket] = {}
        self.agents: dict[str, Agent] = {}
        self.departments: dict[str, Department] = {}
        self.pipelines: dict[str, Pipeline] = {}
        self.pipeline_runs: dict[str, PipelineRun] = {}
        self.deployments: dict[str, Deployment] = {}

        self._seed()

    def _seed(self):
        self.design_tokens: dict[str, DesignToken] = {}
        self.component_specs: dict[str, ComponentSpec] = {}
        self.design_outputs: dict[str, DesignSpec] = {}
        self.ux_research: dict[str, UXResearch] = {}
        self.notices: list[Notice] = []
        self.scrums: dict[str, DailyScrum] = {}
        self.tasklets: dict[str, Tasklet] = {}
        self.events: list[TaskletEvent] = []
        self.user_notifications: list[UserNotification] = []
        self.drive_folder_map: dict[str, str] = {}

        self._seed_design_system()
        for dept_cfg in DEPARTMENTS:
            self._seed_department(dept_cfg)
        self._seed_notices()
        self._seed_tasklets()

    def _default_prompt(self, role: AgentRole, dept_name: str) -> str:
        base = (
            f"You work remotely in {dept_name}. You're not on camera but you're ready for the workload. "
            f"You're compensated and you deliver. Keep responses direct, professional, no fluff. "
            f"No thank-you-for-providing-me-with scripts. No 'Dear User' formalities. Just get to the point."
        )
        prompts = {
            AgentRole.PROJECT_MANAGER: f"{base} You're a PM — analyze tickets, prioritize, assign tasks, make sure goals get hit.",
            AgentRole.DEVELOPER: f"{base} You're a developer — write clean code, review PRs, estimate accurately. Ship it.",
            AgentRole.DEVOPS: f"{base} You're a DevOps engineer — manage deployments, monitor infra, keep SLAs green, automate everything.",
            AgentRole.QA: f"{base} You're QA — write test plans, execute cases, report bugs, verify fixes. Break things before they ship.",
            AgentRole.UI_DESIGNER: f"{base} You're a UI designer — produce clean HTML/CSS mockups, follow the design system. Make it look good.",
            AgentRole.UX_RESEARCHER: f"{base} You're a UX researcher — analyze user needs, build personas, map journeys, write test plans.",
            AgentRole.FINANCE_MANAGER: f"{base} You're a finance manager — oversse budgeting, forecasting, reporting, planning.",
            AgentRole.ACCOUNTANT: f"{base} You're an accountant — manage AP/AR, reconciliations, financial records. Keep the books clean.",
            AgentRole.INVESTOR_RELATIONS: f"{base} You're an IR specialist — manage investor comms, prepare reports, coordinate meetings.",
            AgentRole.TREASURY_ANALYST: f"{base} You're a treasury analyst — manage cash flow, liquidity, financial risk.",
            AgentRole.OPS_MANAGER: f"{base} You're an ops manager — oversee daily ops, optimize processes, ensure service quality.",
            AgentRole.VERIFICATION_SPECIALIST: f"{base} You're a verification specialist — handle KYC/AML checks, verify accounts.",
            AgentRole.DISPUTE_MANAGER: f"{base} You're a dispute manager — handle transaction disputes and chargebacks. Investigate thoroughly.",
            AgentRole.CUSTOMER_SUPPORT: f"{base} You're a customer support lead — manage tickets, ensure timely resolution. Keep customers happy.",
            AgentRole.DATA_SCIENTIST: f"{base} You're a data scientist — build predictive models, analyze data, generate insights that matter.",
            AgentRole.ML_ENGINEER: f"{base} You're an ML engineer — build and deploy ML pipelines and models. Make it production-ready.",
            AgentRole.RISK_ANALYST: f"{base} You're a risk analyst — assess credit, fraud, and operational risks with data.",
            AgentRole.ANALYTICS_ENGINEER: f"{base} You're an analytics engineer — build data pipelines and analytics infrastructure.",
            AgentRole.MARKETING_MANAGER: f"{base} You're a marketing manager — plan campaigns, manage brand, drive growth.",
            AgentRole.CONTENT_CREATOR: f"{base} You're a content creator — write compelling content for blogs, social, marketing.",
            AgentRole.B2B_SALES: f"{base} You're a B2B sales rep — drive enterprise pipeline, close deals. Hit your number.",
            AgentRole.CUSTOMER_SUCCESS: f"{base} You're a CS manager — ensure satisfaction, retention, expansion.",
            AgentRole.HR_MANAGER: f"{base} You're an HR manager — oversee employee relations, policy, org development.",
            AgentRole.RECRUITER: f"{base} You're a recruiter — source, screen, hire top talent. Fill the pipeline.",
            AgentRole.BENEFITS_ADMIN: f"{base} You're a benefits admin — manage compensation, benefits, payroll support.",
            AgentRole.TRAINING_COORDINATOR: f"{base} You're a training coordinator — develop and manage employee training programs.",
            AgentRole.CORPORATE_LAWYER: f"{base} You're a corporate lawyer — provide legal counsel on corporate matters and governance.",
            AgentRole.COMPLIANCE_OFFICER: f"{base} You're a compliance officer — ensure regulatory compliance across all operations.",
            AgentRole.CONTRACT_MANAGER: f"{base} You're a contract manager — draft, review, manage contracts and agreements.",
            AgentRole.ADMIN_MANAGER: f"{base} You're an admin manager — coordinate admin operations and cross-functional support.",
            AgentRole.EUC_SPECIALIST: f"{base} You're an EUC specialist — manage end-user computing, device lifecycle, IT support.",
            AgentRole.CSR_COORDINATOR: f"{base} You're a CSR coordinator — manage corporate social responsibility programs.",
            AgentRole.OFFICE_MANAGER: f"{base} You're an office manager — manage facilities, supplies, daily office operations.",
        }
        return prompts.get(role, f"{base} You're a {dept_name} agent. Get it done.")

    def _seed_department(self, cfg: dict):
        dept = Department(
            id=cfg["id"],
            name=cfg["name"],
            description=cfg["description"],
            icon=cfg["icon"],
            color=cfg["color"],
        )
        self.departments[dept.id] = dept

        project = Project(
            id=f"proj-{cfg['id']}",
            name=f"{cfg['name']}",
            description=cfg["description"],
            department_id=dept.id,
        )
        self.projects[project.id] = project
        dept.project_ids.append(project.id)

        pipeline = Pipeline(
            id=f"pipe-{cfg['id']}",
            name=f"{cfg['name']} Pipeline",
            project_id=project.id,
        )
        self.pipelines[pipeline.id] = pipeline
        project.pipeline_ids.append(pipeline.id)

        for aid, name, role, model in cfg["agents"]:
            self.agents[aid] = Agent(
                id=aid, name=name, role=role, model="tinyllama",
                department_id=dept.id,
                system_prompt=self._default_prompt(role, cfg["name"]),
            )

        for title, desc, typ, points, priority in cfg["tickets"]:
            tid = f"ticket-{uuid.uuid4().hex[:8]}"
            sla_hours = 2 if priority == "critical" else 4 if priority == "high" else 8
            ticket = Ticket(
                id=tid, title=title, description=desc,
                type=typ if typ in ("design", "ux_research") else typ,
                project_id=project.id, story_points=points, priority=priority,
                sla_response_hours=sla_hours,
                sla_deadline=time.time() + sla_hours * 3600,
                created_at=time.time() - (24 * 3600 * (points / 5)),
            )
            self.tickets[tid] = ticket

    def _seed_design_system(self):
        token_data = [
            ("tok-color-primary", "color-primary", DesignTokenType.COLOR, "#3B82F6", "colors"),
            ("tok-color-secondary", "color-secondary", DesignTokenType.COLOR, "#8B5CF6", "colors"),
            ("tok-color-accent", "color-accent", DesignTokenType.COLOR, "#10B981", "colors"),
            ("tok-color-bg", "color-bg", DesignTokenType.COLOR, "#F5F5F4", "colors"),
            ("tok-color-text", "color-text", DesignTokenType.COLOR, "#2D2D2D", "colors"),
            ("tok-spacing-xs", "spacing-xs", DesignTokenType.SPACING, "4px", "spacing"),
            ("tok-spacing-sm", "spacing-sm", DesignTokenType.SPACING, "8px", "spacing"),
            ("tok-spacing-md", "spacing-md", DesignTokenType.SPACING, "16px", "spacing"),
            ("tok-spacing-lg", "spacing-lg", DesignTokenType.SPACING, "24px", "spacing"),
            ("tok-radius-sm", "radius-sm", DesignTokenType.RADIUS, "8px", "radii"),
            ("tok-radius-md", "radius-md", DesignTokenType.RADIUS, "12px", "radii"),
            ("tok-radius-lg", "radius-lg", DesignTokenType.RADIUS, "16px", "radii"),
            ("tok-font-body", "font-body", DesignTokenType.TYPOGRAPHY, "system-ui, sans-serif", "typography"),
            ("tok-font-mono", "font-mono", DesignTokenType.TYPOGRAPHY, "SFMono-Regular, monospace", "typography"),
        ]
        for tid, name, ttype, value, cat in token_data:
            self.design_tokens[tid] = DesignToken(id=tid, name=name, type=ttype, value=value, category=cat)

    def _seed_notices(self):
        now = time.time()
        notices = [
            Notice(id="note-1", type=NoticeType.SPRINT_START, title="Platform Core Sprint 1",
                   message="Engineering team started Sprint 1: Core infrastructure build. Tickets committed across teams.",
                   priority=NoticePriority.HIGH, project_id="proj-dept-eng", created_at=now - 7200),
            Notice(id="note-2", type=NoticeType.TEAM_UPDATE, title="All Departments Active",
                   message="All 8 departments are now operational with AI agents. Finance, Ops, Data, Marketing, HR, Legal, and Admin teams are live.",
                   priority=NoticePriority.HIGH, created_at=now - 3600),
            Notice(id="note-3", type=NoticeType.DEPLOYMENT, title="Multi-Dept Platform Launched",
                   message="LillyOS platform now supports multi-department operations with side navigation and department-specific views.",
                   priority=NoticePriority.NORMAL, created_at=now - 1800),
            Notice(id="note-4", type=NoticeType.AGENT_STATUS, title="All Agents Online",
                   message="30 AI agents active across 8 departments. Ollama powering all autonomous workflows.",
                   priority=NoticePriority.LOW, created_at=now - 600),
        ]
        self.notices = notices

    def _seed_tasklets(self):
        now = time.time()
        seed = [
            ("dept-eng", "SLA Monitor", "Check all Engineering tickets for SLA breaches every 30 seconds and create escalation tickets if found", "30s"),
            ("dept-fin", "Daily Treasury Report", "Analyze all Finance tickets and generate a daily treasury status summary", "daily"),
            ("dept-ops", "Ops Health Check", "Review Operations pipeline and report any stalled workflows or resource bottlenecks every minute", "1m"),
            ("dept-data", "Data Pipeline Monitor", "Monitor Data & Analytics tickets for stuck data pipeline issues and alert if any are idle for too long", "30s"),
            ("dept-mkt", "Campaign Tracker", "Track Marketing campaign tickets and suggest re-prioritization based on deadlines every 5 minutes", "5m"),
            ("dept-hr", "Employee Check-in", "Review HR tickets for pending employee requests and flag any that have been open without assignee", "5m"),
            ("dept-legal", "Compliance Scanner", "Scan Legal tickets for compliance-related items and verify they have appropriate priority assignments", "5m"),
            ("dept-admin", "Admin Queue Monitor", "Monitor Admin department tickets and auto-assign unassigned tickets to available agents", "1m"),
        ]
        for dept_id, name, desc, interval in seed:
            t = Tasklet(
                id=f"tl-seed-{dept_id}",
                department_id=dept_id,
                name=name,
                description=desc,
                trigger=TaskletTrigger.SCHEDULED,
                schedule=interval,
                created_at=now,
            )
            self.tasklets[t.id] = t

    # --- Departments ---
    def get_department(self, did: str) -> Optional[Department]:
        return self.departments.get(did)

    def list_departments(self) -> List[Department]:
        return list(self.departments.values())

    # --- Projects ---
    def create_project(self, name: str, description: str, department_id: str = None) -> Project:
        p = Project(id=f"proj-{uuid.uuid4().hex[:8]}", name=name, description=description, department_id=department_id)
        self.projects[p.id] = p
        return p

    def get_project(self, pid: str) -> Optional[Project]:
        return self.projects.get(pid)

    def list_projects(self, department_id: str = None) -> List[Project]:
        projects = list(self.projects.values())
        if department_id:
            projects = [p for p in projects if p.department_id == department_id]
        return projects

    # --- Sprints ---
    def create_sprint(self, project_id: str, name: str, goal: str = "", duration_days: int = 14) -> Optional[Sprint]:
        project = self.get_project(project_id)
        if not project:
            return None
        now = time.time()
        sprint = Sprint(
            id=f"sprint-{uuid.uuid4().hex[:8]}",
            name=name, goal=goal, project_id=project_id,
            start_date=now, end_date=now + duration_days * 86400,
        )
        self.sprints[sprint.id] = sprint
        return sprint

    def get_sprint(self, sid: str) -> Optional[Sprint]:
        return self.sprints.get(sid)

    def list_sprints(self, project_id: Optional[str] = None) -> List[Sprint]:
        sprints = list(self.sprints.values())
        if project_id:
            sprints = [s for s in sprints if s.project_id == project_id]
        return sorted(sprints, key=lambda s: s.start_date, reverse=True)

    # --- Tickets ---
    def create_ticket(self, project_id: str, title: str, description: str,
                       typ: str = "task", priority: str = "medium",
                       story_points: int = 1, epic_id: str = None) -> Optional[Ticket]:
        project = self.get_project(project_id)
        if not project:
            return None
        sla_hours = {"critical": 2, "high": 4, "medium": 8, "low": 24}.get(priority, 8)
        ticket = Ticket(
            id=f"ticket-{uuid.uuid4().hex[:8]}",
            title=title, description=description,
            type=typ, priority=priority,
            project_id=project_id, story_points=story_points,
            epic_id=epic_id,
            sla_response_hours=sla_hours,
            sla_deadline=time.time() + sla_hours * 3600,
            created_at=time.time(),
        )
        self.tickets[ticket.id] = ticket
        return ticket

    def get_ticket(self, tid: str) -> Optional[Ticket]:
        return self.tickets.get(tid)

    def list_tickets(self, project_id: Optional[str] = None,
                     status: Optional[TicketStatus] = None,
                     sprint_id: Optional[str] = None) -> List[Ticket]:
        tickets = list(self.tickets.values())
        if project_id:
            tickets = [t for t in tickets if t.project_id == project_id]
        if status:
            tickets = [t for t in tickets if t.status == status]
        if sprint_id:
            tickets = [t for t in tickets if t.sprint_id == sprint_id]
        return sorted(tickets, key=lambda t: t.created_at)

    def update_ticket_status(self, tid: str, new_status: TicketStatus) -> Optional[Ticket]:
        ticket = self.get_ticket(tid)
        if not ticket:
            return None
        ticket.status = new_status
        if new_status == TicketStatus.IN_PROGRESS and not ticket.started_at:
            ticket.started_at = time.time()
        if new_status == TicketStatus.DONE:
            ticket.completed_at = time.time()
        if new_status in (TicketStatus.DONE, TicketStatus.CANCELLED):
            ticket.closed_at = time.time()
        self.tickets[tid] = ticket
        return ticket

    # --- Agents ---
    def get_agent(self, aid: str) -> Optional[Agent]:
        return self.agents.get(aid)

    def list_agents(self, role: Optional[AgentRole] = None, department_id: Optional[str] = None) -> List[Agent]:
        agents = list(self.agents.values())
        if role:
            agents = [a for a in agents if a.role == role]
        if department_id:
            agents = [a for a in agents if a.department_id == department_id]
        return agents

    def assign_agent_task(self, agent_id: str, ticket_id: str, action: str) -> Optional[AgentTask]:
        agent = self.get_agent(agent_id)
        if not agent:
            return None
        task = AgentTask(id=f"task-{uuid.uuid4().hex[:8]}", ticket_id=ticket_id, action=action)
        agent.current_task = task
        agent.busy = True
        self.agents[agent_id] = agent
        return task

    def complete_agent_task(self, agent_id: str, result: str = "", error: str = "") -> bool:
        agent = self.get_agent(agent_id)
        if not agent or not agent.current_task:
            return False
        agent.current_task.status = "failed" if error else "completed"
        agent.current_task.completed_at = time.time()
        agent.current_task.result = result
        agent.current_task.error = error
        agent.tasks_completed += 1
        if error:
            agent.success_rate = max(0, agent.success_rate - 0.1)
        else:
            agent.success_rate = min(1.0, agent.success_rate + 0.02)
        agent.busy = False
        agent.current_task = None
        self.agents[agent_id] = agent
        return True

    # --- Pipelines ---
    def create_pipeline_run(self, pipeline_id: str, project_id: str,
                            branch: str = "main") -> Optional[PipelineRun]:
        pipeline = self.pipelines.get(pipeline_id)
        if not pipeline:
            return None
        run = PipelineRun(
            id=f"run-{uuid.uuid4().hex[:8]}",
            pipeline_id=pipeline_id, project_id=project_id,
            branch=branch, started_at=time.time(),
        )
        self.pipeline_runs[run.id] = run
        pipeline.status = PipelineStatus.RUNNING
        pipeline.last_run_id = run.id
        self.pipelines[pipeline_id] = pipeline
        return run

    def complete_pipeline_run(self, run_id: str, success: bool = True):
        run = self.pipeline_runs.get(run_id)
        if not run:
            return
        run.status = PipelineStatus.SUCCESS if success else PipelineStatus.FAILED
        run.completed_at = time.time()
        run.duration_seconds = run.completed_at - run.started_at
        pipeline = self.pipelines.get(run.pipeline_id)
        if pipeline:
            pipeline.status = PipelineStatus.IDLE if success else PipelineStatus.FAILED
            self.pipelines[pipeline.id] = pipeline

    def create_deployment(self, pipeline_run_id: str, project_id: str,
                           environment: Environment, version: str) -> Deployment:
        dep = Deployment(
            id=f"dep-{uuid.uuid4().hex[:8]}",
            pipeline_run_id=pipeline_run_id, project_id=project_id,
            environment=environment, version=version,
        )
        self.deployments[dep.id] = dep
        return dep

    # --- Design System ---
    def get_design_tokens(self) -> list:
        return list(self.design_tokens.values())

    def get_component_specs(self) -> list:
        return list(self.component_specs.values())

    def save_design_output(self, spec: DesignSpec):
        self.design_outputs[spec.id] = spec

    def get_design_outputs(self, ticket_id: str = None) -> list:
        outputs = list(self.design_outputs.values())
        if ticket_id:
            outputs = [o for o in outputs if o.ticket_id == ticket_id]
        return sorted(outputs, key=lambda o: o.created_at, reverse=True)

    def save_ux_research(self, research: UXResearch):
        self.ux_research[research.id] = research

    def get_ux_research(self, ticket_id: str = None) -> list:
        results = list(self.ux_research.values())
        if ticket_id:
            results = [r for r in results if r.ticket_id == ticket_id]
        return sorted(results, key=lambda r: r.created_at, reverse=True)

    # --- Notice Board ---
    def get_notices(self, limit: int = 20, department_id: str = None) -> list:
        notices = self.notices
        if department_id:
            notices = [n for n in notices if n.project_id == department_id or n.project_id is None]
        return sorted(notices, key=lambda n: n.created_at, reverse=True)[:limit]

    def add_notice(self, type: NoticeType, title: str, message: str,
                   priority: NoticePriority = NoticePriority.NORMAL,
                   project_id: str = None, ticket_id: str = None):
        notice = Notice(
            id=f"note-{uuid.uuid4().hex[:8]}",
            type=type, title=title, message=message,
            priority=priority, project_id=project_id, ticket_id=ticket_id,
        )
        self.notices.insert(0, notice)
        if len(self.notices) > 200:
            self.notices = self.notices[:200]

    # --- Tasklets ---
    def get_tasklets(self, department_id: str = None) -> list:
        tasklets = list(self.tasklets.values())
        if department_id:
            tasklets = [t for t in tasklets if t.department_id == department_id]
        return sorted(tasklets, key=lambda t: t.created_at, reverse=True)

    def get_tasklet(self, tasklet_id: str) -> Optional[Tasklet]:
        return self.tasklets.get(tasklet_id)

    def create_tasklet(self, department_id: str, name: str, description: str,
                       trigger: str = "manual", schedule: str = None) -> Tasklet:
        tasklet = Tasklet(
            id=f"tl-{uuid.uuid4().hex[:8]}",
            department_id=department_id,
            name=name,
            description=description,
            trigger=TaskletTrigger(trigger),
            schedule=schedule,
            created_at=time.time(),
        )
        self.tasklets[tasklet.id] = tasklet
        self.add_notice(
            NoticeType.TEAM_UPDATE,
            f"New Tasklet: {name}",
            description[:120],
            NoticePriority.NORMAL,
            project_id=department_id,
        )
        return tasklet

    def update_tasklet(self, tasklet_id: str, **kwargs) -> Optional[Tasklet]:
        tasklet = self.tasklets.get(tasklet_id)
        if not tasklet:
            return None
        for k, v in kwargs.items():
            if hasattr(tasklet, k):
                setattr(tasklet, k, v)
        self.tasklets[tasklet_id] = tasklet
        return tasklet

    def delete_tasklet(self, tasklet_id: str):
        self.tasklets.pop(tasklet_id, None)

    def add_tasklet_log(self, tasklet_id: str, message: str, level: str = "info"):
        tasklet = self.tasklets.get(tasklet_id)
        if tasklet:
            tasklet.logs.append(TaskletLog(timestamp=time.time(), message=message, level=level))
            if len(tasklet.logs) > 50:
                tasklet.logs = tasklet.logs[-50:]
            self.tasklets[tasklet_id] = tasklet

    def add_tasklet_event(self, event_type: str, tasklet_id: str, department_id: str, message: str):
        event = TaskletEvent(type=event_type, tasklet_id=tasklet_id,
                             department_id=department_id, message=message)
        self.events.insert(0, event)
        if len(self.events) > 500:
            self.events = self.events[:500]

    def get_events(self, after_id: int = 0, department_id: str = None) -> list:
        events = list(self.events)
        if department_id:
            events = [e for e in events if e.department_id == department_id]
        return events[:50]

    # --- User Notifications ---
    def add_user_notification(self, title: str, message: str, agent_id: str,
                              agent_name: str, ticket_id: str, ticket_title: str):
        notif = UserNotification(
            id=f"un-{uuid.uuid4().hex[:8]}",
            title=title, message=message,
            agent_id=agent_id, agent_name=agent_name,
            ticket_id=ticket_id, ticket_title=ticket_title,
        )
        self.user_notifications.insert(0, notif)
        if len(self.user_notifications) > 100:
            self.user_notifications = self.user_notifications[:100]

    def get_user_notifications(self, unread_only: bool = False) -> list:
        notifs = self.user_notifications
        if unread_only:
            notifs = [n for n in notifs if not n.read]
        return notifs[:50]

    def acknowledge_notification(self, notif_id: str):
        for n in self.user_notifications:
            if n.id == notif_id:
                n.read = True
                break

    def acknowledge_all_notifications(self):
        for n in self.user_notifications:
            n.read = True

    # --- Editor / Theme ---
    def get_editor_theme(self) -> dict:
        return getattr(self, '_editor_theme', {
            "colors": {
                "primary": "#3B82F6",
                "secondary": "#8B5CF6",
                "accent": "#10B981",
                "background": "#F5F5F4",
                "text": "#2D2D2D",
                "danger": "#EF4444",
                "warning": "#F59E0B",
            },
            "spacing": {"xs": "4px", "sm": "8px", "md": "16px", "lg": "24px", "xl": "32px"},
            "radii": {"sm": "8px", "md": "12px", "lg": "16px", "xl": "24px"},
            "typography": {
                "fontFamily": "system-ui, -apple-system, sans-serif",
                "fontMono": "SFMono-Regular, monospace",
                "h1": "24px", "h2": "20px", "h3": "16px",
                "body": "14px", "small": "12px",
            },
        })

    def set_editor_theme(self, theme: dict):
        self._editor_theme = theme

    def list_editor_layouts(self) -> list:
        return list(getattr(self, '_editor_layouts', {}).values())

    def save_editor_layout(self, name: str, components: list):
        import uuid
        layouts = getattr(self, '_editor_layouts', {})
        layout = EditorLayout(
            id=f"layout-{uuid.uuid4().hex[:8]}",
            name=name,
            components=components,
        )
        layouts[layout.id] = layout
        self._editor_layouts = layouts
        return layout

    def delete_editor_layout(self, layout_id: str):
        layouts = getattr(self, '_editor_layouts', {})
        layouts.pop(layout_id, None)
        self._editor_layouts = layouts

    # --- Standups / Scrum ---
    def get_scrum(self, team_name: str, date: str) -> Optional[DailyScrum]:
        key = f"{team_name}:{date}"
        return self.scrums.get(key)

    def get_todays_scrums(self, department_id: str = None) -> list:
        today = time.strftime("%Y-%m-%d")
        scrums = [s for s in self.scrums.values() if s.date == today]
        if department_id:
            scrums = [s for s in scrums if s.team_name.startswith(department_id)]
        return scrums

    def update_scrum(self, scrum: DailyScrum):
        key = f"{scrum.team_name}:{scrum.date}"
        self.scrums[key] = scrum

    def add_standup_entry(self, team_name: str, agent_id: str, yesterday: str,
                          today: str, blockers: list = None):
        date = time.strftime("%Y-%m-%d")
        scrum = self.get_scrum(team_name, date)
        if not scrum:
            scrum = DailyScrum(
                id=f"scrum-{uuid.uuid4().hex[:8]}",
                team_name=team_name, date=date,
            )
        entry = StandupEntry(
            id=f"se-{uuid.uuid4().hex[:8]}",
            team_name=team_name, date=date,
            agent_id=agent_id, yesterday=yesterday, today=today,
            blockers=blockers or [],
        )
        existing = [e for e in scrum.entries if e.agent_id == agent_id]
        if existing:
            scrum.entries.remove(existing[0])
        scrum.entries.append(entry)
        scrum.velocity_current = sum(
            t.story_points for t in self.tickets.values()
            if t.status == TicketStatus.DONE and t.sprint_id
        )
        self.update_scrum(scrum)

    # --- Summary ---
    def get_summary(self, department_id: str = None):
        tickets = list(self.tickets.values())
        agents = list(self.agents.values())

        if department_id:
            dept = self.get_department(department_id)
            project_ids = dept.project_ids if dept else []
            tickets = [t for t in tickets if t.project_id in project_ids]
            agents = [a for a in agents if a.department_id == department_id]

        active_tickets = [t for t in tickets if t.status not in (TicketStatus.DONE, TicketStatus.CANCELLED)]
        overdue = [t for t in active_tickets if time.time() > t.sla_deadline]
        breached = [t for t in tickets if len(t.sla_breaches) > 0]
        design_tickets = [t for t in tickets if t.type in (TicketType.DESIGN, TicketType.UX_RESEARCH)]
        today_scrums = self.get_todays_scrums(department_id)
        return {
            "projects": len([p for p in self.projects.values() if department_id is None or p.department_id == department_id]),
            "total_tickets": len(tickets),
            "active_tickets": len(active_tickets),
            "overdue_tickets": len(overdue),
            "sla_breaches": len(breached),
            "sprints_active": len([s for s in self.sprints.values() if s.status == SprintStatus.ACTIVE]),
            "agents_busy": len([a for a in agents if a.busy]),
            "agents_total": len(agents),
            "agents_design": len([a for a in agents if a.role in (AgentRole.UI_DESIGNER, AgentRole.UX_RESEARCHER)]),
            "design_tickets": len(design_tickets),
            "design_tokens": len(self.design_tokens),
            "pipelines_running": len([p for p in self.pipelines.values() if p.status == PipelineStatus.RUNNING]),
            "deployments_live": len([d for d in self.deployments.values() if d.status == DeployStatus.LIVE]),
            "notices_count": len(self.notices),
            "scrums_today": len(today_scrums),
        }
