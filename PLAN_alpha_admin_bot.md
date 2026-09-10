# Plan: Alpha Admin Bot with Background Monitoring & Toast Progress

## Goal

Transform the Alpha Avatar (Lilly) into an **admin interface** backed by OpenCode/Kilo for building, creating, and implementing changes. Add a **background monitoring bot** that suggests improvements and upgrades via toast notifications with approve/dismiss actions. Approved tasks show progress in a toast bar at the top of the WebUI (port 8098).

---

## Current State

- **`alpha_popout.html`** — Browser-based Alpha chat UI, connects to `/api/vibecode/chat`
- **`lilly_ai.py`** — Main server with 100+ routes, vibecode chat endpoint, existing persona system
- **`phone_broker.py`** — WebSocket relay with AutomationEngine (rules-based actions)
- **`overlay_local.html`** — Android overlay with existing `Toast()` function and `localToast()` bridge
- **Background loops** — Already has `synaptic_learning_loop`, `notification_monitor_loop`, etc.
- **No existing** progress tracking, task approval system, or background suggestion bot

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    WEB UI (port 8098)                        │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  PROGRESS BAR (toast-style, top of page)            │    │
│  │  [⚡ Task name] [████████░░] [✓ Approve] [✕ Dismiss]│    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
│  ┌──────────────────────┐  ┌──────────────────────────┐    │
│  │  Alpha Chat          │  │  Task Queue Panel        │    │
│  │  (admin commands)    │  │  (approved/pending/done) │    │
│  └──────────────────────┘  └──────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
         │                        │
         ▼                        ▼
┌─────────────────┐    ┌─────────────────────┐
│  /api/admin/cmd │    │  /api/admin/tasks   │
│  (OpenCode/Kilo)│    │  (task management)  │
└─────────────────┘    └─────────────────────┘
         │                        │
         ▼                        ▼
┌─────────────────────────────────────────────────────────────┐
│                  BACKGROUND BOT                              │
│                                                             │
│  - monitoring_loop() — scans system health, code quality   │
│  - Suggests upgrades, improvements, fixes                  │
│  - Posts suggestions → /api/admin/suggestions              │
│  - Polls for approved tasks → executes via OpenCode/Kilo   │
│  - Updates task progress → WebSocket push to UI            │
└─────────────────────────────────────────────────────────────┘
```

---

## Implementation Plan

### Phase 1: Admin API Backend (lilly_ai.py additions)

**New routes to add:**

| Route | Method | Purpose |
|-------|--------|---------|
| `/api/admin/cmd` | POST | Admin commands via OpenCode/Kilo (build, create, implement) |
| `/api/admin/suggestions` | GET | List pending suggestions from background bot |
| `/api/admin/suggestions/{id}/approve` | POST | Approve a suggestion → creates task |
| `/api/admin/suggestions/{id}/dismiss` | POST | Dismiss a suggestion |
| `/api/admin/tasks` | GET | List all tasks (pending/running/done) |
| `/api/admin/tasks/{id}` | GET | Task detail + progress |
| `/api/admin/tasks/{id}/progress` | POST | Update task progress (called by bot) |
| `/api/admin/ws` | WS | Real-time push: new suggestions, task progress, toasts |

**Data models:**

```python
@dataclass
class AdminSuggestion:
    id: str
    title: str
    description: str
    category: str  # "upgrade", "fix", "optimization", "feature"
    priority: str  # "low", "medium", "high", "critical"
    created_at: float
    status: str  # "pending", "approved", "dismissed"
    metadata: dict  # files affected, estimated impact, etc.

@dataclass 
class AdminTask:
    id: str
    suggestion_id: str  # linked suggestion
    title: str
    status: str  # "queued", "running", "done", "failed"
    progress: float  # 0.0 - 1.0
    steps: list  # list of {name, status, progress}
    result: str  # final output
    created_at: float
    updated_at: float
```

### Phase 2: Background Monitoring Bot

**New background loop: `admin_monitoring_loop()`**

Runs every 5 minutes (configurable), performs:

1. **System Health Scan**
   - Check lilly_ai.py process health
   - Check Docker container status
   - Check phone sensor server reachability
   - Check disk space, memory usage
   - Check YOLO/vision status

2. **Code Quality Scan**
   - Look for TODO/FIXME/HACK comments
   - Check for unused imports
   - Detect deprecated patterns
   - Monitor error logs for recurring issues

3. **Improvement Suggestions**
   - Based on usage patterns (which avatars are used, which features are popular)
   - Security updates (check for outdated dependencies)
   - Performance optimizations (slow routes, memory leaks)
   - New feature opportunities based on user interactions

4. **Upgrade Detection**
   - Check for new versions of dependencies
   - Check for new YOLO models
   - Check for new Piper voices
   - Check for new skills in OpenHuman catalog

Each finding becomes an `AdminSuggestion` pushed via WebSocket.

### Phase 3: Toast Progress UI

**Add to the main WebUI (served at port 8098):**

```html
<!-- Toast Progress Bar - fixed at top of page -->
<div id="admin-toast-bar" style="display:none">
  <div class="toast-progress">
    <span class="toast-icon">⚡</span>
    <span class="toast-title">Task Title</span>
    <div class="toast-bar">
      <div class="toast-fill" style="width: 45%"></div>
    </div>
    <span class="toast-pct">45%</span>
    <button class="toast-btn approve">✓</button>
    <button class="toast-btn dismiss">✕</button>
  </div>
</div>
```

**Toast bar behaviors:**
- **New suggestion** → slides in from top with title, description, approve/dismiss buttons
- **Task approved** → shows progress bar with percentage
- **Task running** → animates progress bar, shows current step
- **Task done** → shows checkmark, auto-dismisses after 5s
- **Task failed** → shows error, offers retry

**WebSocket connection** for real-time updates:
```javascript
const adminWs = new WebSocket(`ws://${location.host}/api/admin/ws`);
adminWs.onmessage = (e) => {
  const msg = JSON.parse(e.data);
  switch(msg.type) {
    case 'suggestion': showSuggestionToast(msg.data); break;
    case 'progress':   updateProgressToast(msg.data); break;
    case 'complete':   showCompleteToast(msg.data); break;
    case 'error':      showErrorToast(msg.data); break;
  }
};
```

### Phase 4: Admin Chat Integration

**Modify `alpha_popout.html`** to support admin commands:

- When user types a command like "build X", "create Y", "deploy Z"
- Route to `/api/admin/cmd` instead of `/api/vibecode/chat`
- Show task creation confirmation in chat
- Link to toast progress for the created task

**Admin command patterns:**
- `build [project]` → scaffold + compile
- `create [thing]` → generate new code/component
- `deploy [project]` → push to production
- `fix [issue]` → auto-diagnose and fix
- `upgrade [component]` → update dependency
- `scan` → run immediate health check
- `tasks` → show active tasks
- `suggestions` → show pending suggestions

---

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `lilly_ai.py` | MODIFY | Add admin routes, suggestion/task models, background loop |
| `admin_bot.py` | CREATE | Background monitoring bot logic (separate module for clean separation) |
| `alpha_popout.html` | MODIFY | Admin command routing, task list panel |
| `admin_toast.js` | CREATE | Toast progress bar JavaScript (injected into WebUI) |
| `admin_toast.css` | CREATE | Toast progress bar styling |

---

## Toast UI Mockup

```
┌─────────────────────────────────────────────────────────────────────┐
│ ⚡ Auto-upgrade YOLOv8 to v8.2       [████████░░░░] 65%  ✓ ✕      │
├─────────────────────────────────────────────────────────────────────┤
│ 💡 Add Redis caching for sensor data  [Approve] [Dismiss]          │
├─────────────────────────────────────────────────────────────────────┤
│ 🔧 Fix memory leak in vision loop     [✓ Done - auto-dismiss]      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Open Questions

1. **Where should the toast bar appear?** Top of the existing chat UI, or as a separate overlay bar across the entire page?
2. **Should suggestions auto-approve for low-priority items?** Or always require manual approval?
3. **How aggressive should the monitoring be?** Every 5 minutes? On-demand? Both?
4. **Should the bot execute approved tasks immediately, or queue them?** 
5. **Integration with existing `phone_broker.py` AutomationEngine?** Should suggestions also feed into the automation rules system?

---

## Estimated Complexity

- **Phase 1 (Backend):** Medium — new routes + data models, ~300-500 lines
- **Phase 2 (Bot):** Medium — background loop + heuristics, ~400-600 lines  
- **Phase 3 (Toast UI):** Low-Medium — CSS + JS + WebSocket, ~200-300 lines
- **Phase 4 (Chat Integration):** Low — modify existing vibecode chat routing, ~100-150 lines

**Total estimated:** ~1000-1500 lines of new/modified code
