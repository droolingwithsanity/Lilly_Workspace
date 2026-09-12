from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel
from typing import Optional
import time, uuid, os
from api.portfolio_data import PORTFOLIO
from api.analyzer import analyze_website, generate_ai_report
from models import (
    TicketStatus, TicketType, SprintStatus, AgentRole, Environment,
    NoticeType, NoticePriority, Ticket, AgentTask, Priority,
    TaskletStatus, TaskletTrigger,
)

router = APIRouter(prefix="/api")


def get_orch(request: Request):
    return request.app.state.orchestrator


# ---- Department endpoints ----

@router.get("/departments")
def list_departments(request: Request):
    orch = get_orch(request)
    return [d.model_dump(mode="json") for d in orch.store.list_departments()]


@router.get("/departments/{dept_id}")
def get_department(dept_id: str, request: Request):
    orch = get_orch(request)
    dept = orch.store.get_department(dept_id)
    if not dept:
        raise HTTPException(404, "Department not found")
    return dept.model_dump(mode="json")


# ---- Ticket endpoints ----

@router.get("/tickets")
def list_tickets(request: Request, project_id: str = None, status: str = None, sprint_id: str = None):
    orch = get_orch(request)
    status_enum = TicketStatus(status) if status else None
    return [t.model_dump(mode="json") for t in orch.store.list_tickets(project_id, status_enum, sprint_id)]


@router.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: str, request: Request):
    orch = get_orch(request)
    ticket = orch.store.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    return ticket.model_dump(mode="json")


class CreateTicketBody(BaseModel):
    project_id: str
    title: str
    description: str = ""
    type: str = "task"
    priority: str = "medium"
    story_points: int = 1


@router.post("/tickets")
def create_ticket(body: CreateTicketBody, request: Request):
    orch = get_orch(request)
    ticket = orch.store.create_ticket(
        body.project_id, body.title, body.description,
        body.type, body.priority, body.story_points,
    )
    if not ticket:
        raise HTTPException(400, "Failed to create ticket")
    return ticket.model_dump(mode="json")


class TransitionBody(BaseModel):
    status: str


@router.post("/tickets/{ticket_id}/transition")
def transition_ticket(ticket_id: str, body: TransitionBody, request: Request):
    orch = get_orch(request)
    try:
        new_status = TicketStatus(body.status)
    except ValueError:
        raise HTTPException(400, f"Invalid status: {body.status}")
    ticket = orch.workflow.transition(ticket_id, new_status)
    if not ticket:
        raise HTTPException(400, "Transition not allowed")
    return ticket.model_dump(mode="json")


@router.post("/tickets/{ticket_id}/assign")
def assign_ticket(ticket_id: str, agent_id: str, request: Request):
    orch = get_orch(request)
    ticket = orch.store.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    agent = orch.store.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    ticket.assignee = agent_id
    orch.store.tickets[ticket_id] = ticket
    orch.store.assign_agent_task(agent_id, ticket_id, "implement")
    return ticket.model_dump(mode="json")


# ---- Sprint endpoints ----

@router.get("/sprints")
def list_sprints(request: Request, project_id: str = None):
    orch = get_orch(request)
    return [s.model_dump(mode="json") for s in orch.store.list_sprints(project_id)]


class CreateSprintBody(BaseModel):
    project_id: str
    name: str
    goal: str = ""
    duration_days: int = 14


@router.post("/sprints")
def create_sprint(body: CreateSprintBody, request: Request):
    orch = get_orch(request)
    sprint = orch.store.create_sprint(body.project_id, body.name, body.goal, body.duration_days)
    if not sprint:
        raise HTTPException(400, "Failed to create sprint")
    return sprint.model_dump(mode="json")


@router.post("/sprints/{sprint_id}/start")
def start_sprint(sprint_id: str, request: Request):
    orch = get_orch(request)
    sprint = orch.store.get_sprint(sprint_id)
    if not sprint:
        raise HTTPException(404, "Sprint not found")
    sprint.status = SprintStatus.ACTIVE
    return sprint.model_dump(mode="json")


@router.post("/sprints/{sprint_id}/add-ticket")
def add_ticket_to_sprint(sprint_id: str, ticket_id: str, request: Request):
    orch = get_orch(request)
    sprint = orch.store.get_sprint(sprint_id)
    if not sprint:
        raise HTTPException(404, "Sprint not found")
    ticket = orch.store.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    if ticket.id not in sprint.ticket_ids:
        sprint.ticket_ids.append(ticket.id)
        sprint.velocity_planned += ticket.story_points
    ticket.sprint_id = sprint_id
    orch.store.tickets[ticket_id] = ticket
    return sprint.model_dump(mode="json")


# ---- Project endpoints ----

@router.get("/projects")
def list_projects(request: Request, department_id: str = None):
    orch = get_orch(request)
    return [p.model_dump(mode="json") for p in orch.store.list_projects(department_id)]


class CreateProjectBody(BaseModel):
    name: str
    description: str = ""
    department_id: str = None


@router.post("/projects")
def create_project(body: CreateProjectBody, request: Request):
    orch = get_orch(request)
    project = orch.store.create_project(body.name, body.description, body.department_id)
    return project.model_dump(mode="json")


# ---- Agent endpoints ----

@router.get("/agents")
def list_agents(request: Request, role: str = None, department_id: str = None):
    orch = get_orch(request)
    role_enum = AgentRole(role) if role else None
    return [a.model_dump(mode="json") for a in orch.store.list_agents(role_enum, department_id)]


@router.get("/agents/{agent_id}")
def get_agent(agent_id: str, request: Request):
    orch = get_orch(request)
    agent = orch.store.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    return agent.model_dump(mode="json")


# ---- Pipeline endpoints ----

@router.get("/pipelines")
def list_pipelines(request: Request):
    orch = get_orch(request)
    return [p.model_dump(mode="json") for p in orch.store.pipelines.values()]


@router.post("/pipelines/{pipeline_id}/run")
def trigger_pipeline(pipeline_id: str, request: Request):
    orch = get_orch(request)
    pipeline = orch.store.pipelines.get(pipeline_id)
    if not pipeline:
        raise HTTPException(404, "Pipeline not found")
    run = orch.store.create_pipeline_run(pipeline_id, pipeline.project_id)
    if not run:
        raise HTTPException(400, "Failed to start pipeline")
    return run.model_dump(mode="json")


@router.get("/deployments")
def list_deployments(request: Request):
    orch = get_orch(request)
    return [d.model_dump(mode="json") for d in orch.store.deployments.values()]


# ---- Notice Board ----

@router.get("/notices")
def get_notices(request: Request, limit: int = 20):
    orch = get_orch(request)
    return [n.model_dump(mode="json") for n in orch.store.get_notices(limit)]


class CreateNoticeBody(BaseModel):
    type: str
    title: str
    message: str
    priority: str = "normal"


@router.post("/notices")
def create_notice(body: CreateNoticeBody, request: Request):
    orch = get_orch(request)
    try:
        ntype = NoticeType(body.type)
        nprio = NoticePriority(body.priority)
    except ValueError:
        raise HTTPException(400, "Invalid notice type or priority")
    orch.store.add_notice(ntype, body.title, body.message, nprio)
    return {"status": "ok"}


# ---- Scrum / Standups ----

@router.get("/scrums")
def get_scrums(request: Request, date: str = None):
    orch = get_orch(request)
    if date:
        return [s.model_dump(mode="json") for s in orch.store.scrums.values()
                if s.date == date]
    return [s.model_dump(mode="json") for s in orch.store.get_todays_scrums()]


class StandupBody(BaseModel):
    team_name: str
    agent_id: str
    yesterday: str = ""
    today: str = ""
    blockers: list[str] = []


@router.post("/standup")
def post_standup(body: StandupBody, request: Request):
    orch = get_orch(request)
    orch.store.add_standup_entry(
        body.team_name, body.agent_id,
        body.yesterday, body.today, body.blockers,
    )
    orch.store.add_notice(
        NoticeType.AGENT_STATUS,
        f"Standup: {body.agent_id}",
        f"Today: {body.today[:100]}" if body.today else "Check-in recorded",
        NoticePriority.LOW,
    )
    date = time.strftime("%Y-%m-%d")
    scrum = orch.store.get_scrum(body.team_name, date)
    return scrum.model_dump(mode="json") if scrum else {"status": "ok"}


# ---- Design System ----

@router.get("/design/tokens")
def get_design_tokens(request: Request):
    orch = get_orch(request)
    return [t.model_dump(mode="json") for t in orch.store.get_design_tokens()]


@router.get("/design/outputs")
def get_design_outputs(request: Request, ticket_id: str = None):
    orch = get_orch(request)
    return [o.model_dump(mode="json") for o in orch.store.get_design_outputs(ticket_id)]


@router.get("/design/ux-research")
def get_ux_research(request: Request, ticket_id: str = None):
    orch = get_orch(request)
    return [r.model_dump(mode="json") for r in orch.store.get_ux_research(ticket_id)]


# ---- Dashboard ----

@router.get("/dashboard")
def get_dashboard(request: Request, department_id: str = None):
    orch = get_orch(request)
    dept = orch.store.get_department(department_id) if department_id else None
    project_ids = dept.project_ids if dept else None

    tickets = orch.store.list_tickets()
    if project_ids:
        tickets = [t for t in tickets if t.project_id in project_ids]

    return {
        "summary": orch.store.get_summary(department_id),
        "recent_tickets": [t.model_dump(mode="json") for t in
                          sorted(tickets, key=lambda t: t.created_at, reverse=True)[:10]],
        "agents": [a.model_dump(mode="json") for a in orch.store.list_agents(department_id=department_id)],
        "sprints": [s.model_dump(mode="json") for s in orch.store.list_sprints()[:3]],
        "deployments": [d.model_dump(mode="json") for d in
                       sorted(orch.store.deployments.values(), key=lambda d: d.deployed_at or 0, reverse=True)[:5]],
        "notices": [n.model_dump(mode="json") for n in orch.store.get_notices(10)],
        "design_tokens": [t.model_dump(mode="json") for t in orch.store.get_design_tokens()],
        "scrums": [s.model_dump(mode="json") for s in orch.store.get_todays_scrums(department_id)],
        "department_id": department_id,
    }


# ---- Portfolio ----

@router.get("/portfolio")
def list_portfolio(request: Request):
    return PORTFOLIO


class AnalyzeBody(BaseModel):
    url: str
    has_root_access: bool = True


@router.post("/analyze-website")
def analyze_website_endpoint(body: AnalyzeBody, request: Request):
    raw = analyze_website(body.url)
    report = generate_ai_report(body.url, raw, body.has_root_access)
    return {
        "url": body.url,
        "grade": raw.get("grade", "N/A"),
        "status_code": raw.get("status_code"),
        "response_time_ms": raw.get("response_time_ms"),
        "tech_stack": raw.get("tech_stack", []),
        "security_headers": raw.get("security_headers", {}),
        "ssl_info": raw.get("ssl_info", {}),
        "issues": raw.get("issues", []),
        "report": report,
    }


# ---- Google Drive ----

from services.drive import get_auth_url, exchange_code, is_authenticated, ensure_department_folders, get_folder_links


@router.get("/drive/auth-url")
def drive_auth_url():
    url = get_auth_url()
    return {"url": url} if url else {"error": "Google Drive not configured — set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET"}


@router.get("/drive/callback")
def drive_callback(code: str):
    exchange_code(code)
    return {"status": "authenticated"}


@router.get("/drive/status")
def drive_status():
    return {"authenticated": is_authenticated()}


@router.post("/drive/sync")
def drive_sync(request: Request):
    orch = get_orch(request)
    depts = orch.store.list_departments()
    dept_dicts = [{"id": d.id, "name": d.name} for d in depts]
    folder_map = ensure_department_folders(dept_dicts)
    orch.store.drive_folder_map = folder_map
    links = get_folder_links(folder_map, dept_dicts)
    return {"folders": links, "count": len(links)}


@router.get("/drive/folders")
def drive_folders(request: Request):
    orch = get_orch(request)
    depts = orch.store.list_departments()
    dept_dicts = [{"id": d.id, "name": d.name} for d in depts]
    links = get_folder_links(getattr(orch.store, 'drive_folder_map', {}), dept_dicts)
    return {"folders": links}

# ---- User Notifications ----

@router.get("/notifications")
def get_notifications(request: Request, unread_only: bool = False):
    orch = get_orch(request)
    return [n.model_dump(mode="json") for n in orch.store.get_user_notifications(unread_only)]


@router.post("/notifications/{notif_id}/ack")
def ack_notification(notif_id: str, request: Request):
    orch = get_orch(request)
    orch.store.acknowledge_notification(notif_id)
    return {"status": "ok"}


@router.post("/notifications/ack-all")
def ack_all_notifications(request: Request):
    orch = get_orch(request)
    orch.store.acknowledge_all_notifications()
    return {"status": "ok"}


# ---- Tasklets ----

class CreateTaskletBody(BaseModel):
    department_id: str
    name: str
    description: str
    trigger: str = "manual"
    schedule: Optional[str] = None


@router.get("/tasklets")
def list_tasklets(request: Request, department_id: str = None):
    orch = get_orch(request)
    return [t.model_dump(mode="json") for t in orch.store.get_tasklets(department_id)]


@router.post("/tasklets")
def create_tasklet(body: CreateTaskletBody, request: Request):
    orch = get_orch(request)
    tasklet = orch.store.create_tasklet(
        body.department_id, body.name, body.description,
        body.trigger, body.schedule,
    )
    return tasklet.model_dump(mode="json")


@router.get("/tasklets/{tasklet_id}")
def get_tasklet(tasklet_id: str, request: Request):
    orch = get_orch(request)
    t = orch.store.get_tasklet(tasklet_id)
    if not t:
        raise HTTPException(404, "Tasklet not found")
    return t.model_dump(mode="json")


@router.post("/tasklets/{tasklet_id}/trigger")
def trigger_tasklet(tasklet_id: str, request: Request):
    orch = get_orch(request)
    ok = orch.tasklets.trigger(tasklet_id)
    if not ok:
        raise HTTPException(400, "Tasklet not found or already running")
    t = orch.store.get_tasklet(tasklet_id)
    return t.model_dump(mode="json")


@router.delete("/tasklets/{tasklet_id}")
def delete_tasklet(tasklet_id: str, request: Request):
    orch = get_orch(request)
    orch.store.delete_tasklet(tasklet_id)
    return {"status": "ok"}


# ---- Events (SSE push for notifications) ----

@router.get("/events")
def stream_events(request: Request, department_id: str = None):
    from fastapi.responses import StreamingResponse
    import asyncio

    orch = get_orch(request)

    async def event_stream():
        sent = 0
        while True:
            try:
                events = orch.store.get_events(department_id=department_id)
                if len(events) > sent:
                    new_events = events[:len(events) - sent]
                    for ev in reversed(new_events):
                        yield f"data: {ev.model_dump_json()}\n\n"
                        sent += 1
            except Exception:
                pass
            await asyncio.sleep(2)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ---- Chat / Lilly ----

class ChatBody(BaseModel):
    message: str
    conversation_id: Optional[str] = None
    department_id: Optional[str] = None


@router.post("/chat")
def chat(body: ChatBody, request: Request):
    orch = get_orch(request)
    result = orch.lilly.process_message(
        body.conversation_id,
        body.message,
        body.department_id,
    )
    return result


@router.get("/chat/history")
def chat_history(request: Request, conversation_id: str, limit: int = 50):
    orch = get_orch(request)
    return {"conversation_id": conversation_id, "messages": orch.lilly.get_history(conversation_id, limit)}


@router.post("/chat/transcribe")
async def transcribe_audio(request: Request):
    import tempfile, os, aiofiles
    orch = get_orch(request)
    form = await request.form()
    audio_file = form.get("audio")
    if not audio_file:
        raise HTTPException(400, "No audio file provided")
    data = await audio_file.read()
    text = orch.lilly.transcribe_audio(data, audio_file.filename or "audio.webm")
    if not text:
        raise HTTPException(400, "Transcription failed")
    return {"text": text}


@router.post("/chat/speak")
async def speak_text(request: Request):
    try:
        from edge_tts import Communicate
        import tempfile, os, re
        body = await request.json()
        text = body.get("text", "")
        voice = body.get("voice", "en-US-JennyNeural")
        style = body.get("style", "friendly")
        rate = body.get("rate", "+15%")
        pitch = body.get("pitch", "+8Hz")
        if not text:
            raise HTTPException(400, "No text provided")

        # Escape XML special characters
        text = (text.replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;")
                    .replace('"', "&quot;")
                    .replace("'", "&apos;"))

        ssml = f"""<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis"
  xmlns:mstts="http://www.w3.org/2001/mstts" xml:lang="en-US">
  <voice name="{voice}">
    <mstts:express-as style="{style}">
      <prosody rate="{rate}" pitch="{pitch}">
        {text}
      </prosody>
    </mstts:express-as>
  </voice>
</speak>"""

        comm = Communicate(ssml, voice)
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            temp_path = f.name
        await comm.save(temp_path)

        from fastapi.responses import FileResponse
        return FileResponse(temp_path, media_type="audio/mpeg",
                           headers={"Content-Disposition": "inline"})

    except ImportError:
        raise HTTPException(503, "Edge TTS not available")
    except Exception as e:
        raise HTTPException(500, str(e))


# ---- Agent Chat ----
@router.post("/agents/{agent_id}/chat")
def agent_chat(agent_id: str, body: ChatBody, request: Request):
    orch = get_orch(request)
    agent = orch.store.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    message = body.message

    # Process via Ollama — agent responds to the user directly
    system = agent.system_prompt or f"You are {agent.name}, a {agent.role} agent. You work remote, not on camera, ready for the workload. Compensated and delivering."
    prompt = (
        f"[DIRECT MESSAGE from the user to you, {agent.name}]\n"
        f"User says: {message}\n\n"
        f"Respond to the user directly in first person. No scenario, no talking about yourself in third person. "
        f"Acknowledge the user's message and state what you will do. "
        f"If this should be delegated to another department, say which and why. "
        f"Keep it short, like a text message."
    )
    try:
        import requests
        resp = requests.post(
            f"{os.environ.get('OLLAMA_HOST', 'http://host.docker.internal:11434')}/api/generate",
            json={"model": agent.model, "prompt": prompt, "system": system,
                   "stream": False, "options": {"num_predict": 512}},
            timeout=30,
        )
        response_text = resp.json().get("response", "") if resp.status_code == 200 else ""
    except Exception:
        response_text = ""

    if not response_text:
        response_text = f""
    else:
        # Strip formal fluff and roleplay patterns from model responses
        import re as _re
        response_text = _re.sub(r'(?i)^(Dear\s+\w+[,:].*?)(\n|$)','', response_text).strip()
        response_text = _re.sub(r'(?i)^(I hope this (message|email|response).*?)(\n|$)','', response_text).strip()
        response_text = _re.sub(r'(?i)^(Thank you for (your |providing |contacting ).*?)(\n|$)','', response_text).strip()
        response_text = _re.sub(r'(?i)^(I am writing to .*?)(\n|$)','', response_text).strip()
        response_text = _re.sub(r'(?i)^(I wanted to .*?)(\n|$)','', response_text).strip()
        response_text = _re.sub(r'(?i)^(Please let me know if you have any (questions|concerns).*?)(\n|$)','', response_text).strip()
        response_text = _re.sub(r'(?i)(Please let me know if you have any (questions|concerns|further|other).*?)$','', response_text).strip()
        response_text = _re.sub(r'(?i)(Best regards[,:]?.*)$','', response_text).strip()
        response_text = _re.sub(r'(?i)(Sincerely[,:]?.*)$','', response_text).strip()
        response_text = _re.sub(r'(?i)(Feel free to (reach out|contact).*?)$','', response_text).strip()
        # Strip ALL bracketed stage directions like [User sends...], [Rachel Green], etc.
        response_text = _re.sub(r'\[.*?\]\s*\n*', '', response_text).strip()
        # Strip agent self-identification at start (e.g. "Rachel Green: ..." after stripping bracketed parts)
        agent_escaped = _re.escape(agent.name)
        for _ in range(3):
            response_text = _re.sub(r'^' + agent_escaped + r'\s*[:,]\s*', '', response_text).strip()
        # Strip any remaining Name: pattern at start
        response_text = _re.sub(r'^\w+\s+\w+\s*:\s*', '', response_text).strip()
        response_text = response_text.strip().strip(',').strip()

    if not response_text:
        response_text = f"On it. {message[:100]}"

    # Check for delegation request in response
    delegation = None
    dept_names = {d.name.lower(): d for d in orch.store.list_departments()}
    for dname_lower, d in dept_names.items():
        if d.id != agent.department_id and dname_lower in response_text.lower():
            target_agents = [a for a in orch.store.list_agents(department_id=d.id) if not a.busy]
            if target_agents:
                target = target_agents[0]
            else:
                target = None
            if target:
                delegation = {
                    "to_agent": target.name,
                    "to_agent_id": target.id,
                    "to_department": d.name,
                    "reason": f"{agent.name} delegated to {d.name}",
                }
                response_text += f"\n\n🔄 I've delegated this to **{target.name}** in {d.name} who will take it from here."
                break

    # Create user notification for this reply
    orch.store.add_user_notification(
        title=f"Reply from {agent.name}",
        message=response_text[:200],
        agent_id=agent.id,
        agent_name=agent.name,
        ticket_id="",
        ticket_title="",
    )

    return {
        "response": response_text,
        "agent_id": agent_id,
        "agent_name": agent.name,
        "delegation": delegation,
    }


# ---- Synapse App ----
from apps.synapse import router as synapse_router
router.include_router(synapse_router, prefix="/apps/synapse")

# ---- Editor / Theme ----

@router.get("/editor/theme")
def get_editor_theme(request: Request):
    orch = get_orch(request)
    return orch.store.get_editor_theme()


class EditorThemeBody(BaseModel):
    colors: dict = {}
    spacing: dict = {}
    radii: dict = {}
    typography: dict = {}


@router.put("/editor/theme")
def update_editor_theme(body: EditorThemeBody, request: Request):
    orch = get_orch(request)
    orch.store.set_editor_theme(body.model_dump())
    return {"status": "ok"}


@router.get("/editor/layouts")
def list_editor_layouts(request: Request):
    orch = get_orch(request)
    return [l.model_dump(mode="json") for l in orch.store.list_editor_layouts()]


class SaveLayoutBody(BaseModel):
    name: str
    components: list = []


@router.post("/editor/layouts")
def save_editor_layout(body: SaveLayoutBody, request: Request):
    orch = get_orch(request)
    layout = orch.store.save_editor_layout(body.name, body.components)
    return layout.model_dump(mode="json")


@router.delete("/editor/layouts/{layout_id}")
def delete_editor_layout(layout_id: str, request: Request):
    orch = get_orch(request)
    orch.store.delete_editor_layout(layout_id)
    return {"status": "ok"}


# ---- Summary (legacy) ----

@router.get("/summary")
def get_summary(request: Request):
    orch = get_orch(request)
    return orch.store.get_summary()


# ---- Self-Healing ----

@router.post("/self-heal")
def self_heal(request: Request):
    orch = get_orch(request)
    report = orch.lilly.get_self_healing_report()
    dept_eng = orch.store.get_department("dept-eng")
    actions = []

    if report["health"] != "healthy":
        for issue in report["issues"]:
            if dept_eng and dept_eng.project_ids:
                ticket = orch.store.create_ticket(
                    dept_eng.project_ids[0],
                    f"[Self-Heal] {issue}",
                    f"Auto-detected by Lilly Self-Healing System:\n\n{issue}",
                    "bug",
                    "high",
                    3,
                )
                if ticket:
                    actions.append({
                        "action": "ticket_created",
                        "ticket_id": ticket.id,
                        "issue": issue,
                    })
                    orch.store.add_notice(
                        type=NoticeType.SYSTEM_ALERT,
                        title=f"Self-Heal: {ticket.title}",
                        message=issue,
                        priority="high",
                        project_id=dept_eng.project_ids[0],
                        ticket_id=ticket.id,
                    )

    return {
        "health": report["health"],
        "issues": report["issues"],
        "actions": actions,
        "timestamp": report["timestamp"],
    }
