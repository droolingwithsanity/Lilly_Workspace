import requests
import json
import time
import os
import threading
import random
from typing import Optional
from models import Agent, AgentTask, Ticket, TicketStatus, TicketType, AgentRole
from models import DesignSpec, UXResearch


OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://host.docker.internal:11434")
OLLAMA_MODEL = os.getenv("LILLY_MODEL", "tinyllama")


class OllamaRunner:
    def __init__(self, store, workflow):
        self.store = store
        self.workflow = workflow
        self._running = False
        self._thread = None
        self._help_log = []

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _loop(self):
        while self._running:
            try:
                self._tick()
            except Exception:
                pass
            time.sleep(3)

    def _tick(self):
        agents = self.store.list_agents()
        processed = 0
        for agent in agents:
            if not agent.enabled or processed >= 8:
                continue
            try:
                if self._process_agent(agent):
                    processed += 1
            except Exception:
                pass

    def _process_agent(self, agent: Agent) -> bool:
        dept_tickets = self._dept_tickets(agent)

        if not agent.busy:
            task = self._find_task(agent, dept_tickets)
            if task:
                self.store.assign_agent_task(agent.id, task.ticket_id, task.action)
                refreshed = self.store.get_agent(agent.id)
                if refreshed and refreshed.current_task:
                    threading.Thread(
                        target=self._execute_task,
                        args=(agent.id, refreshed.current_task),
                        daemon=True,
                    ).start()
                return True

        elif agent.current_task and agent.current_task.status == "pending":
            now = time.time()
            started = agent.current_task.started_at or 0
            if now - started > 30:
                self.store.complete_agent_task(agent.id, error="timeout")
                agent.busy = False
                return True

        return False

    def _dept_tickets(self, agent: Agent) -> list:
        tickets = self.store.list_tickets()
        if agent.department_id:
            dept = self.store.get_department(agent.department_id)
            if dept:
                tickets = [t for t in tickets if t.project_id in dept.project_ids]
        return tickets

    def _find_task(self, agent: Agent, dept_tickets: list) -> Optional[AgentTask]:
        role = agent.role
        now = time.time()

        # Priority 1: Critical/overdue tickets in department
        urgent = [t for t in dept_tickets
                  if t.status == TicketStatus.IN_PROGRESS
                  and t.priority.value in ("critical", "high")
                  and (t.sla_deadline and now > t.sla_deadline * 0.8)
                  and (not t.assignee or t.assignee == agent.id)]
        if urgent:
            return AgentTask(id="", ticket_id=urgent[0].id, action="implement")

        # Priority 2: Unassigned in-progress tickets
        unassigned = [t for t in dept_tickets
                      if t.status == TicketStatus.IN_PROGRESS and not t.assignee]
        if unassigned:
            return AgentTask(id="", ticket_id=unassigned[0].id, action="implement")

        # Priority 3: Tickets assigned to this agent
        mine = [t for t in dept_tickets
                if t.assignee == agent.id and t.status == TicketStatus.IN_PROGRESS]
        if mine:
            return AgentTask(id="", ticket_id=mine[0].id, action="implement")

        # Priority 4: Tickets in sprint not yet in progress
        sprint_ready = [t for t in dept_tickets if t.status == TicketStatus.IN_SPRINT]
        if sprint_ready:
            return AgentTask(id="", ticket_id=sprint_ready[0].id, action="implement")

        # Priority 5: Cross-department help (if this agent is free and other depts need help)
        if agent.department_id:
            other_ticket = self._find_help_request(agent)
            if other_ticket:
                self._help_log.append(f"{agent.name} helping {other_ticket.project_id}")
                return AgentTask(id="", ticket_id=other_ticket.id, action="implement")

        return None

    def _find_help_request(self, agent: Agent) -> Optional[Ticket]:
        departments = self.store.list_departments()
        helper_depts = [d for d in departments if d.id != agent.department_id]
        random.shuffle(helper_depts)

        for dept in helper_depts:
            project_ids = dept.project_ids or []
            for pid in project_ids:
                dept_agents = [a for a in self.store.list_agents(department_id=dept.id) if not a.busy]
                if len(dept_agents) == 0:
                    tickets = [t for t in self.store.list_tickets(pid)
                               if t.status == TicketStatus.IN_PROGRESS and not t.assignee]
                    if tickets:
                        return tickets[0]
        return None

    def _call_ollama(self, model: str, prompt: str, system: str) -> Optional[str]:
        try:
            resp = requests.post(
                f"{OLLAMA_HOST}/api/generate",
                json={
                    "model": model or OLLAMA_MODEL,
                    "prompt": prompt,
                    "system": system,
                    "stream": False,
                    "options": {"num_predict": 256, "temperature": 0.2},
                },
                timeout=15,
            )
            if resp.status_code == 200:
                return resp.json().get("response", "")
            return None
        except Exception:
            return None

    def _execute_task(self, agent_id: str, task: AgentTask):
        agent = self.store.get_agent(agent_id)
        if not agent:
            return

        now = time.time()
        task.started_at = now

        ticket = self.store.get_ticket(task.ticket_id)
        if not ticket:
            self.store.complete_agent_task(agent_id, error="ticket not found")
            return

        prompt = self._build_prompt(agent, task, ticket)
        result = self._call_ollama(agent.model, prompt, agent.system_prompt)

        if result is None:
            result = f"Processed ticket {ticket.title} — completed task: {task.action}"

        self._handle_result(agent, task, ticket, result)

    def _build_prompt(self, agent: Agent, task: AgentTask, ticket: Ticket) -> str:
        base = f"Ticket: {ticket.title}\nDescription: {ticket.description}\nType: {ticket.type.value}\nPriority: {ticket.priority.value}\n\n"

        action_prompts = {
            "implement": f"Work on this ticket and provide a brief status update. {base}",
            "groom_and_prioritize": f"Groom this ticket. Is it clear and actionable? {base}",
            "plan_and_assign": f"Plan this ticket. Estimate effort and suggest who should handle it. {base}",
        }
        return action_prompts.get(task.action, f"Handle this ticket: {base}")

    def _handle_result(self, agent: Agent, task: AgentTask, ticket: Ticket, result: str):
        now = time.time()
        tid = ticket.id
        status = ticket.status
        next_map = {
            TicketStatus.IN_SPRINT: TicketStatus.IN_PROGRESS,
            TicketStatus.IN_PROGRESS: TicketStatus.IN_REVIEW,
            TicketStatus.IN_REVIEW: TicketStatus.DONE,
        }
        next_s = next_map.get(status)
        if next_s:
            self.workflow.transition(tid, next_s)
            updated = self.store.get_ticket(tid)
            if updated and next_s == TicketStatus.IN_PROGRESS:
                updated.assignee = agent.id
                updated.started_at = now
                self.store.tickets[tid] = updated
            elif updated and next_s == TicketStatus.DONE:
                updated.completed_at = now
                self.store.tickets[tid] = updated

        self.store.complete_agent_task(agent.id, result=result)
        agent.busy = False
