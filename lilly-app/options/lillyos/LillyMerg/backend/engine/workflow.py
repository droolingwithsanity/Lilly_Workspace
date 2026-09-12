import time
import os
from typing import Optional
from models import (
    Ticket, TicketStatus, TicketType, SprintStatus, Sprint,
    SLABreach, Priority, AgentRole
)

SPRINT_DAYS = int(os.getenv("SPRINT_DAYS", "7"))


TRANSITIONS = {
    TicketStatus.BACKLOG: [TicketStatus.GROOMED, TicketStatus.CANCELLED],
    TicketStatus.GROOMED: [TicketStatus.SPRINT_READY, TicketStatus.BACKLOG, TicketStatus.CANCELLED],
    TicketStatus.SPRINT_READY: [TicketStatus.IN_SPRINT, TicketStatus.BACKLOG, TicketStatus.CANCELLED],
    TicketStatus.IN_SPRINT: [TicketStatus.IN_PROGRESS, TicketStatus.BACKLOG],
    TicketStatus.IN_PROGRESS: [
        TicketStatus.IN_REVIEW,
        TicketStatus.DESIGN_REVIEW,
        TicketStatus.UX_REVIEW,
        TicketStatus.BACKLOG,
    ],
    TicketStatus.DESIGN_REVIEW: [TicketStatus.READY_FOR_DEV, TicketStatus.IN_PROGRESS, TicketStatus.BACKLOG],
    TicketStatus.UX_REVIEW: [TicketStatus.DESIGN_REVIEW, TicketStatus.IN_PROGRESS, TicketStatus.BACKLOG],
    TicketStatus.READY_FOR_DEV: [TicketStatus.IN_SPRINT, TicketStatus.BACKLOG],
    TicketStatus.IN_REVIEW: [TicketStatus.DONE, TicketStatus.IN_PROGRESS, TicketStatus.BACKLOG],
    TicketStatus.DONE: [TicketStatus.CANCELLED],
    TicketStatus.CANCELLED: [],
}


class WorkflowEngine:
    def __init__(self, store):
        self.store = store

    def can_transition(self, ticket: Ticket, to_status: TicketStatus) -> bool:
        allowed = TRANSITIONS.get(ticket.status, [])
        return to_status in allowed

    def transition(self, ticket_id: str, to_status: TicketStatus) -> Optional[Ticket]:
        ticket = self.store.get_ticket(ticket_id)
        if not ticket:
            return None
        if not self.can_transition(ticket, to_status):
            return None
        ticket = self.store.update_ticket_status(ticket_id, to_status)
        if to_status == TicketStatus.IN_SPRINT:
            self._auto_assign(ticket)
        if to_status == TicketStatus.DONE:
            self._check_sprint_completion(ticket)
        return ticket

    def _auto_assign(self, ticket):
        if ticket.assignee:
            return
        project = self.store.get_project(ticket.project_id)
        dept_id = project.department_id if project else None

        if ticket.type in (TicketType.DESIGN,):
            designers = [a for a in self.store.list_agents(AgentRole.UI_DESIGNER, dept_id) if not a.busy]
            if designers:
                designers.sort(key=lambda a: a.tasks_completed)
                agent = designers[0]
                self.store.assign_agent_task(agent.id, ticket.id, "create_design")
                ticket.assignee = agent.id
                self.store.tickets[ticket.id] = ticket
                return
        elif ticket.type == TicketType.UX_RESEARCH:
            ux = [a for a in self.store.list_agents(AgentRole.UX_RESEARCHER, dept_id) if not a.busy]
            if ux:
                ux.sort(key=lambda a: a.tasks_completed)
                agent = ux[0]
                self.store.assign_agent_task(agent.id, ticket.id, "ux_research")
                ticket.assignee = agent.id
                self.store.tickets[ticket.id] = ticket
                return
        devs = [a for a in self.store.list_agents(department_id=dept_id) if not a.busy and a.role == AgentRole.DEVELOPER]
        if devs:
            devs.sort(key=lambda a: a.tasks_completed)
            agent = devs[0]
            self.store.assign_agent_task(agent.id, ticket.id, f"Implement: {ticket.title}")
            ticket.assignee = agent.id
            self.store.tickets[ticket.id] = ticket

    def _check_sprint_completion(self, ticket):
        sprint_id = ticket.sprint_id
        if not sprint_id:
            return
        sprint = self.store.get_sprint(sprint_id)
        if not sprint or sprint.status != SprintStatus.ACTIVE:
            return
        sprint_tickets = [t for t in self.store.list_tickets(sprint_id=sprint_id)
                          if t.status in (TicketStatus.DONE, TicketStatus.CANCELLED)]
        all_tickets = [t for t in self.store.list_tickets(sprint_id=sprint_id)]
        if len(sprint_tickets) == len(all_tickets):
            sprint.status = SprintStatus.REVIEW
            sprint.velocity_actual = sum(t.story_points for t in sprint_tickets if t.status == TicketStatus.DONE)

    def check_sla(self, ticket: Ticket):
        now = time.time()
        if ticket.status in (TicketStatus.DONE, TicketStatus.CANCELLED):
            return
        if now > ticket.sla_deadline:
            if not any(b.ticket_id == ticket.id for b in ticket.sla_breaches if b.rule == "deadline"):
                ticket.sla_breaches.append(SLABreach(
                    rule="deadline", triggered_at=now, ticket_id=ticket.id,
                ))

    def _auto_create_sprints(self):
        departments = self.store.list_departments()
        for dept in departments:
            project_ids = dept.project_ids or []
            for pid in project_ids:
                active = [s for s in self.store.list_sprints(pid) if s.status == SprintStatus.ACTIVE]
                if not active:
                    ready = [t for t in self.store.list_tickets(pid)
                             if t.status in (TicketStatus.SPRINT_READY, TicketStatus.BACKLOG, TicketStatus.GROOMED)]
                    if ready:
                        now = time.time()
                        sprint = self.store.create_sprint(
                            pid, f"Sprint {int(now)}", f"Auto-sprint for {dept.name}", SPRINT_DAYS
                        )
                        if sprint:
                            for t in ready[:8]:
                                t.status = TicketStatus.IN_SPRINT
                                t.sprint_id = sprint.id
                                self.store.tickets[t.id] = t
                                self._auto_assign(t)

    def _progress_tickets(self):
        tickets = self.store.list_tickets()
        for t in tickets:
            if t.status == TicketStatus.IN_REVIEW:
                t.status = TicketStatus.DONE
                t.completed_at = time.time()
                self.store.tickets[t.id] = t
                self._check_sprint_completion(t)

    def run_automation(self):
        tickets = self.store.list_tickets()
        now = time.time()

        for ticket in tickets:
            self.check_sla(ticket)
            if ticket.status == TicketStatus.BACKLOG:
                ticket.status = TicketStatus.GROOMED
                self.store.tickets[ticket.id] = ticket
            if ticket.status == TicketStatus.GROOMED:
                ticket.status = TicketStatus.SPRINT_READY
                self.store.tickets[ticket.id] = ticket

        self._auto_create_sprints()
        self._progress_tickets()

        sprints = [s for s in self.store.sprints.values()
                   if s.status == SprintStatus.ACTIVE and s.end_date < now]
        for sprint in sprints:
            sprint.status = SprintStatus.CLOSED
            uncompleted = [t for t in tickets if t.sprint_id == sprint.id
                           and t.status not in (TicketStatus.DONE, TicketStatus.CANCELLED)]
            for t in uncompleted:
                t.status = TicketStatus.BACKLOG
                t.sprint_id = None
                self.store.tickets[t.id] = t
