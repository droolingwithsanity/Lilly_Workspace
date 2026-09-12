import re
import os
import requests
from typing import Optional
from fastapi import APIRouter, Request, HTTPException

router = APIRouter(tags=["synapse"])

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://host.docker.internal:11434")
OLLAMA_MODEL = os.getenv("LILLY_MODEL", "tinyllama")


def _call_ollama(prompt: str, system: str = "") -> Optional[str]:
    try:
        resp = requests.post(
            f"{OLLAMA_HOST}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "system": system,
                "stream": False,
                "options": {"num_predict": 512, "temperature": 0.3},
            },
            timeout=30,
        )
        if resp.status_code == 200:
            return resp.json().get("response", "")
        return None
    except Exception:
        return None


def _gather_dept_data(orch) -> list:
    departments = orch.store.list_departments()
    result = []
    for dept in departments:
        project_ids = dept.project_ids or []
        tickets = [t for t in orch.store.list_tickets() if t.project_id in project_ids]
        agents = orch.store.list_agents(department_id=dept.id)
        sprints = [s for s in orch.store.list_sprints() if s.project_id in project_ids]

        open_tickets = [t for t in tickets if t.status not in ("done", "cancelled", "closed")]
        critical = [t for t in open_tickets if t.priority.value == "critical"]
        overdue = [t for t in tickets if t.sla_deadline and t.created_at + t.sla_deadline < __import__("time").time() and t.status not in ("done", "cancelled")]

        busy_count = len([a for a in agents if a.busy])
        avg_success = round(sum(a.success_rate for a in agents) / len(agents) * 100, 1) if agents else 0
        avg_energy = round(avg_success * (1 - busy_count / max(len(agents), 1) * 0.3), 1)

        result.append({
            "id": dept.id,
            "name": dept.name,
            "icon": dept.icon or "📁",
            "color": dept.color or "#6366F1",
            "description": dept.description,
            "tickets": {
                "total": len(tickets),
                "open": len(open_tickets),
                "critical": len(critical),
                "overdue": len(overdue),
            },
            "agents": {
                "total": len(agents),
                "busy": busy_count,
                "avg_energy": avg_energy,
                "avg_success": avg_success,
            },
            "sprints": len(sprints),
            "health": _calc_health(len(open_tickets), len(critical), avg_energy, busy_count, len(agents)),
        })
    return result


def _calc_health(open_tickets: int, critical: int, energy: float, busy: int, total_agents: int) -> str:
    score = 100
    score -= open_tickets * 2
    score -= critical * 10
    score -= max(0, 50 - energy) * 0.5
    score -= (busy / max(total_agents, 1)) * 20
    if score >= 70:
        return "healthy"
    if score >= 40:
        return "warning"
    return "critical"


def _find_connections(orch, dept_data) -> list:
    departments = {d.id: d for d in orch.store.list_departments()}
    connections = []
    seen_pairs = set()

    all_tickets = orch.store.list_tickets()
    for ticket in all_tickets:
        text = f"{ticket.title} {ticket.description}".lower()
        for dept in departments.values():
            if not dept.project_ids or ticket.project_id in dept.project_ids:
                continue
            name_lower = dept.name.lower()
            if name_lower in text:
                # Find which department this ticket belongs to
                for source_dept in departments.values():
                    if source_dept.id != dept.id and ticket.project_id in (source_dept.project_ids or []):
                        pair = tuple(sorted([source_dept.id, dept.id]))
                        if pair not in seen_pairs:
                            seen_pairs.add(pair)
                            connections.append({
                                "source": source_dept.id,
                                "target": dept.id,
                                "source_name": source_dept.name,
                                "target_name": dept.name,
                                "type": "cross_ref",
                                "strength": 1,
                            })
                        break

    return connections


def _generate_insights(dept_data, connections) -> list:
    insights = []
    total_open = sum(d["tickets"]["open"] for d in dept_data)
    total_critical = sum(d["tickets"]["critical"] for d in dept_data)
    total_agents = sum(d["agents"]["total"] for d in dept_data)

    prompt_parts = []
    for d in dept_data:
        prompt_parts.append(
            f"{d['name']}: {d['tickets']['open']} open, {d['tickets']['critical']} critical, "
            f"{d['tickets']['overdue']} overdue, {d['agents']['total']} agents "
            f"({d['agents']['busy']} busy, energy {d['agents']['avg_energy']}%), health={d['health']}"
        )

    analysis_prompt = (
        f"Analyze this organization's departments and generate 3 brief, actionable insights. "
        f"Format each as: '**Title** - Description'\n\n"
        + "\n".join(prompt_parts) +
        f"\n\nTotal open tickets: {total_open}, Critical: {total_critical}, Total agents: {total_agents}"
        f"\nConnections: {len(connections)}"
    )

    ai_result = _call_ollama(analysis_prompt, "You are an organizational intelligence analyst. Be concise and specific.")
    if ai_result:
        for line in ai_result.strip().split("\n"):
            line = line.strip()
            if line and len(line) > 10:
                insights.append(line)

    if not insights:
        insights.append(f"📊 **Workload** — {total_open} open tickets across {len(dept_data)} departments")
        if total_critical > 0:
            insights.append(f"🚨 **Critical items** — {total_critical} critical tickets need immediate attention")
        if connections:
            insights.append(f"🔗 **Collaboration** — {len(connections)} cross-department links detected")

    return insights[:5]


def _detect_risks(dept_data) -> list:
    risks = []
    for d in dept_data:
        if d["tickets"]["critical"] > 3:
            risks.append({
                "department": d["name"],
                "dept_id": d["id"],
                "severity": "critical",
                "message": f"{d['tickets']['critical']} critical tickets — crisis threshold exceeded",
            })
        if d["tickets"]["overdue"] > 5:
            risks.append({
                "department": d["name"],
                "dept_id": d["id"],
                "severity": "high",
                "message": f"{d['tickets']['overdue']} overdue tickets — SLA breaches mounting",
            })
        if d["agents"]["avg_energy"] < 25 and d["agents"]["total"] > 0:
            risks.append({
                "department": d["name"],
                "dept_id": d["id"],
                "severity": "high",
                "message": f"Team energy at {d['agents']['avg_energy']}% — burnout risk imminent",
            })
        if d["agents"]["total"] == 0:
            risks.append({
                "department": d["name"],
                "dept_id": d["id"],
                "severity": "medium",
                "message": "No agents assigned — department is unstaffed",
            })
        if d["tickets"]["open"] > 30:
            risks.append({
                "department": d["name"],
                "dept_id": d["id"],
                "severity": "medium",
                "message": f"{d['tickets']['open']} open tickets — backlog needs grooming",
            })
    return sorted(risks, key=lambda r: {"critical": 0, "high": 1, "medium": 2, "low": 3}[r["severity"]])


@router.get("/dashboard")
def get_synapse_dashboard(request: Request):
    orch = getattr(request.app.state, "orchestrator", None)
    if not orch:
        raise HTTPException(503, "Orchestrator not available")

    dept_data = _gather_dept_data(orch)
    connections = _find_connections(orch, dept_data)
    insights = _generate_insights(dept_data, connections)
    risks = _detect_risks(dept_data)

    total = {
        "departments": len(dept_data),
        "total_tickets": sum(d["tickets"]["total"] for d in dept_data),
        "open_tickets": sum(d["tickets"]["open"] for d in dept_data),
        "critical_tickets": sum(d["tickets"]["critical"] for d in dept_data),
        "overdue_tickets": sum(d["tickets"]["overdue"] for d in dept_data),
        "total_agents": sum(d["agents"]["total"] for d in dept_data),
        "busy_agents": sum(d["agents"]["busy"] for d in dept_data),
        "connections": len(connections),
        "risks": len(risks),
    }

    return {
        "departments": dept_data,
        "connections": connections,
        "insights": insights,
        "risks": risks,
        "total": total,
    }
