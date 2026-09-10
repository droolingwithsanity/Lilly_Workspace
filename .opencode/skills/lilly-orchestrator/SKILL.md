---
name: lilly-orchestrator
description: Use when delegating tasks to multiple coding agents, managing agent fallback chains, checking agent quotas, or coordinating work across OpenCode, Kilo, Aider, Codex, Kiro, and OpenClaw. Front-load keywords like delegate, orchestrate, agent, fallback, quota, coding agent, handoff, coordinate, multi-agent.
version: 0.1.0
metadata:
  homepage: https://github.com/lilly-ai/orchestrator
  requirements: python3
---

# Lilly Orchestrator Skill

Use this skill when the user wants to delegate tasks across multiple coding agents,
manage agent availability and quotas, or coordinate complex work across the agent pool.

## When to Use This Skill

- User says "delegate", "orchestrate", "hand this off to [agent]"
- User asks about agent status, quotas, or availability
- A coding task should be routed to the best available agent
- User wants to check which agents are available or what their limits are
- Complex task that spans multiple agents needs coordination
- Agent failed and needs fallback activation

## Quick Reference

### Check Agent Status
```python
from lilly_orchestrator import Orchestrator
orch = Orchestrator()
status = orch.status()
print(status["pool"])     # All agent status
print(status["quotas"])   # Quota usage
print(orch.render_chat()) # Group chat log
```

### Delegate a Task
```python
result = orch.delegate("Fix the memory leak in lilly_ai.py")
# Returns: task_id, assigned agent, complexity, reasoning, fallback chain
```

### Complete a Task
```python
orch.complete(task_id="task-a1b2c3d4", result="Fixed leak", tokens_used=1500)
```

### Report Failure + Auto-Fallback
```python
orch.fail(task_id="task-a1b2c3d4", error="Rate limited")
# Automatically delegates to next agent in fallback chain
```

## Agent Fallback Chains

```
OpenCode → Kilo → Aider → OpenCode (cycle)
Codex → OpenCode → Kilo → Aider
Kiro → OpenCode → Kilo
OpenClaw → Aider → OpenCode
Fox → Lilly
Cat → Lilly
Deer → Owl → Lilly
```

## Routing Logic

| Complexity | Example | Routes To |
|------------|---------|-----------|
| TRIVIAL | "status check", "rename x to y" | Free agent (Aider/OpenClaw) |
| SIMPLE | "fix this typo", "add import" | Cheapest available coder |
| MODERATE | "implement feature X" | Best avatar or coder for category |
| COMPLEX | "refactor auth system" | Highest thinking budget coder |
| CRITICAL | "fix production security bug" | Lilly oversees + best coder |

## CLI Usage

You can also run tasks via CLI without Python:

```bash
# Check orchestrator status
python3 -c "from lilly_orchestrator import Orchestrator; print(Orchestrator().status())"

# Delegate a task
python3 -c "
from lilly_orchestrator import Orchestrator
import json
orch = Orchestrator()
result = orch.delegate('Fix the bug in phone_broker.py')
print(json.dumps(result, indent=2))
"
```

## Running Coding Agents Directly

When Lilly delegates to a coding agent, you execute via bash:

```bash
# OpenCode (primary coder)
cd /home/labhrasd/Lilly_Workspace && opencode

# Aider (free, local)
cd /home/labhrasd/Lilly_Workspace && aider --model ollama/qwen2.5-coder:14b

# Codex CLI (GPT-powered)
cd /home/labhrasd/Lilly_Workspace && codex
```

## Group Chat Output Format

All agent communications follow this format:
```
🌟 **Lilly** [14:32:01]
  Delegating to ⚡ **OpenCode**: _Fix the login bug_

⚡ **OpenCode** [14:32:03]
  Got it. Fallback chain: Kilo → Aider if I can't finish.

⚡ **OpenCode** [14:32:15]
  ✅ Task `task-a1b2c3d4` complete: Fixed null pointer in auth.py

🌟 **Lilly** [14:32:16]
  📊 **Quota Alert** — codex: RATE_LIMITED. Hourly: 85%.
```

## Token Efficiency Principles (from Glean research)

1. **Context shrinks between steps** — pass only what the next agent needs
2. **Structured state replaces transcripts** — persist decisions, not raw messages
3. **Bounded loops** — max 3 retries before escalating to user
4. **Cost awareness** — prefer free local agents for simple tasks
5. **Measure by workflow** — track success rate per agent, not just tokens

## Files

| File | Purpose |
|------|---------|
| `lilly_orchestrator/__init__.py` | Package exports |
| `lilly_orchestrator/agent_pool.py` | 15-agent registry with capabilities |
| `lilly_orchestrator/quota_tracker.py` | Per-agent quota/limit tracking |
| `lilly_orchestrator/router.py` | Complexity classification + routing |
| `lilly_orchestrator/orchestrator.py` | Main entry point |
| `lilly_orchestrator/group_chat.py` | Chat output formatting |
| `.opencode/agents/lilly-orchestrator.md` | OpenCode agent definition |
