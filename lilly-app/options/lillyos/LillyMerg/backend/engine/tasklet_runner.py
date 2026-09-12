import threading
import time
import os
import requests
import random
from models import TaskletStatus, TaskletTrigger, Ticket, TicketType, TicketStatus, Priority


OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://host.docker.internal:11434")
OLLAMA_MODEL = os.getenv("LILLY_MODEL", "tinyllama")


class TaskletRunner:
    def __init__(self, store, workflow):
        self.store = store
        self.workflow = workflow
        self._running = False
        self._thread = None

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
            time.sleep(5)

    def _tick(self):
        for tasklet in list(self.store.tasklets.values()):
            if tasklet.status == TaskletStatus.RUNNING:
                continue
            if tasklet.trigger == TaskletTrigger.MANUAL and tasklet.status != TaskletStatus.IDLE:
                continue
            if tasklet.trigger == TaskletTrigger.SCHEDULED and tasklet.schedule:
                if not self._should_run(tasklet):
                    continue
            else:
                continue

            self._execute(tasklet)

    def _should_run(self, tasklet) -> bool:
        now = time.time()
        interval = self._parse_schedule(tasklet.schedule)
        if interval is None:
            return False
        if tasklet.last_run is None:
            return True
        return (now - tasklet.last_run) >= interval

    def _parse_schedule(self, schedule: str) -> float:
        mapping = {"5s": 5, "30s": 30, "1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "6h": 21600, "12h": 43200, "24h": 86400, "daily": 86400, "hourly": 3600}
        return mapping.get(schedule)

    def trigger(self, tasklet_id: str):
        tasklet = self.store.get_tasklet(tasklet_id)
        if not tasklet or tasklet.status == TaskletStatus.RUNNING:
            return False
        threading.Thread(target=self._execute, args=(tasklet,), daemon=True).start()
        return True

    def _execute(self, tasklet):
        self.store.update_tasklet(tasklet.id, status=TaskletStatus.RUNNING)
        self.store.add_tasklet_log(tasklet.id, f"Starting execution: {tasklet.name}")
        self.store.add_tasklet_event("tasklet_status", tasklet.id, tasklet.department_id, f"Started: {tasklet.name}")

        dept = self.store.get_department(tasklet.department_id)
        dept_name = dept.name if dept else "Unknown"
        agents = self.store.list_agents(department_id=tasklet.department_id)
        agent = random.choice(agents) if agents else None

        system = f"You are an autonomous AI tasklet agent for {dept_name} department. Execute the assigned task autonomously."
        prompt = (
            f"Task: {tasklet.name}\n"
            f"Description: {tasklet.description}\n\n"
            f"Decide what action to take. You can:\n"
            f"1. Create a high-priority ticket for this task\n"
            f"2. Perform analysis and return findings\n"
            f"3. Delegate to a specific agent\n\n"
            f"Respond with a JSON object:\n"
            f"{{\"action\": \"create_ticket|respond|delegate\", \"title\": \"...\", "
            f"\"description\": \"...\", \"details\": \"...\", \"assignee\": \"agent_id_or_null\"}}"
        )

        result_text = ""
        try:
            resp = requests.post(
                f"{OLLAMA_HOST}/api/generate",
                json={"model": OLLAMA_MODEL, "prompt": prompt, "system": system,
                       "stream": False, "options": {"num_predict": 512}},
                timeout=30,
            )
            if resp.status_code == 200:
                result_text = resp.json().get("response", "")
        except Exception:
            result_text = ""

        action_data = self._parse_action(result_text, tasklet)
        ticket_created = None

        if action_data.get("action") == "create_ticket":
            dept = self.store.get_department(tasklet.department_id)
            pids = dept.project_ids if dept else []
            pid = pids[0] if pids else "proj-1"
            ticket = Ticket(
                id=f"ticket-{os.urandom(4).hex()}",
                title=action_data.get("title", tasklet.name)[:100],
                description=action_data.get("description", tasklet.description)[:500],
                type=TicketType.TASK,
                status=TicketStatus.BACKLOG,
                priority=Priority.HIGH,
                project_id=pid,
                assignee=action_data.get("assignee"),
                story_points=2,
                sla_deadline=time.time() + 7200,
                sla_response_hours=2,
                created_at=time.time(),
                metadata={"source": "tasklet", "tasklet_id": tasklet.id},
            )
            self.store.tickets[ticket.id] = ticket
            if action_data.get("assignee"):
                self.store.assign_agent_task(action_data["assignee"], ticket.id, "implement")
            self.store.add_tasklet_log(tasklet.id, f"Created ticket: {ticket.title}")
            ticket_created = ticket

        elif action_data.get("action") == "delegate" and action_data.get("assignee"):
            target_agent = self.store.get_agent(action_data["assignee"])
            if target_agent:
                self.store.add_tasklet_log(tasklet.id, f"Delegated to {target_agent.name}")
                self.store.add_notice(
                    type=NoticeType.AGENT_STATUS,
                    title=f"Tasklet delegation: {tasklet.name}",
                    message=f"Delegated to {target_agent.name} by autonomous tasklet",
                    priority=NoticePriority.NORMAL,
                    project_id=tasklet.department_id,
                )

        summary = action_data.get("details", result_text[:200] if result_text else "Completed")
        self.store.update_tasklet(
            tasklet.id,
            status=TaskletStatus.COMPLETED,
            last_run=time.time(),
            run_count=tasklet.run_count + 1,
            last_result=summary[:500],
        )
        self.store.add_tasklet_log(tasklet.id, f"Completed: {summary[:100]}", "info")
        self.store.add_tasklet_event(
            "tasklet_complete", tasklet.id, tasklet.department_id,
            f"Completed: {tasklet.name} — {summary[:100]}",
        )

    def _parse_action(self, text: str, tasklet) -> dict:
        try:
            import json
            start = text.index("{")
            end = text.rindex("}") + 1
            return json.loads(text[start:end])
        except Exception:
            return {"action": "respond", "details": text[:200], "title": tasklet.name}
