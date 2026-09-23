/**
 * Admin Toast Progress Bar — JavaScript companion
 *
 * Connects to /api/admin/ws for real-time suggestions + task progress.
 * Renders toast notifications at top of page with approve/dismiss buttons
 * and animated progress bars.
 *
 * Usage: include this script after admin_toast.css in any WebUI page.
 *   <link rel="stylesheet" href="/static/admin_toast.css">
 *   <script src="/static/admin_toast.js"></script>
 */

(function () {
  'use strict';

  // ── Create the toast container ──
  let container = document.getElementById('admin-toast-bar');
  if (!container) {
    container = document.createElement('div');
    container.id = 'admin-toast-bar';
    document.body.prepend(container);
  }

  // ── State ──
  let ws = null;
  let reconnectTimer = null;
  let toasts = new Map(); // toastId → DOM element

  // ── Toast templates ──

  const ICONS = {
    suggestion: '💡',
    progress:   '⚡',
    complete:   '✅',
    error:      '❌',
    init:       '🔄',
    pong:       '💓',
    scan_started: '🔍',
    suggestion_update: '💡',
    task_created: '📋',
  };

  function toastIcon(type) {
    return ICONS[type] || '📌';
  }

  // ── Create a toast element ──

  function createToast(id, opts) {
    const el = document.createElement('div');
    el.className = `admin-toast ${opts.priority ? 'priority-' + opts.priority : ''}`;
    el.dataset.toastId = id;

    const iconClass = opts.iconClass || '';
    el.innerHTML = `
      <div class="admin-toast-icon ${iconClass}">${toastIcon(opts.type || 'suggestion')}</div>
      <div class="admin-toast-content">
        <div class="admin-toast-title">${escapeHtml(opts.title || '')}</div>
        ${opts.description ? `<div class="admin-toast-desc">${escapeHtml(opts.description)}</div>` : ''}
        ${opts.showBar ? `
          <div class="admin-toast-bar-track">
            <div class="admin-toast-bar-fill ${opts.barClass || ''}" style="width:${opts.progress || 0}%"></div>
          </div>
        ` : ''}
      </div>
      ${opts.badge ? `<span class="admin-toast-badge ${opts.badgeClass || ''}">${escapeHtml(opts.badge)}</span>` : ''}
      ${opts.showPct ? `<span class="admin-toast-pct">${Math.round(opts.progress || 0)}%</span>` : ''}
      <div class="admin-toast-btns">
        ${opts.actions || ''}
      </div>
    `;
    return el;
  }

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  // ── Show a suggestion toast ──

  function showSuggestionToast(data) {
    const id = 'sug_' + data.id;
    if (toasts.has(id)) return; // dedupe

    const actions = data.status === 'pending' ? `
      <button class="admin-toast-btn approve" title="Approve" onclick="AdminToast.approve('${data.id}')">✓</button>
      <button class="admin-toast-btn dismiss" title="Dismiss" onclick="AdminToast.dismiss('${data.id}')">✕</button>
    ` : '';

    const el = createToast(id, {
      type: 'suggestion',
      title: data.title,
      description: data.description,
      priority: data.priority,
      badge: data.category,
      badgeClass: data.category,
      actions: actions,
      iconClass: 'suggestion',
    });

    prependToast(el, id);
    autoRemoveToast(el, id, data.status === 'pending' ? 15000 : 8000);
  }

  // ── Show a progress toast ──

  function showProgressToast(data) {
    const id = 'task_' + data.task_id;
    let el = toasts.get(id);

    if (el) {
      // Update existing toast
      const fill = el.querySelector('.admin-toast-bar-fill');
      const pct = el.querySelector('.admin-toast-pct');
      const title = el.querySelector('.admin-toast-title');
      if (fill) {
        fill.style.width = (data.progress * 100) + '%';
        fill.className = 'admin-toast-bar-fill animated';
      }
      if (pct) pct.textContent = Math.round(data.progress * 100) + '%';
      if (title && data.title) title.textContent = data.title;
    } else {
      // Create new progress toast
      el = createToast(id, {
        type: 'progress',
        title: data.title || 'Task running...',
        progress: (data.progress || 0) * 100,
        showBar: true,
        showPct: true,
        barClass: 'animated',
        iconClass: 'progress',
      });
      prependToast(el, id);
    }
  }

  // ── Show a completion toast ──

  function showCompleteToast(data) {
    const id = 'task_' + data.task_id;
    let el = toasts.get(id);

    if (el) {
      // Update to complete state
      const fill = el.querySelector('.admin-toast-bar-fill');
      const pct = el.querySelector('.admin-toast-pct');
      const title = el.querySelector('.admin-toast-title');
      const btns = el.querySelector('.admin-toast-btns');
      if (fill) {
        fill.style.width = '100%';
        fill.className = 'admin-toast-bar-fill done';
      }
      if (pct) pct.textContent = '100%';
      if (title) title.textContent = (data.status === 'done' ? '✅ ' : '❌ ') + (data.title || 'Task finished');
      if (btns) btns.innerHTML = '';
    } else {
      // Create fresh completion toast
      el = createToast(id, {
        type: data.status === 'done' ? 'complete' : 'error',
        title: (data.status === 'done' ? '✅ ' : '❌ ') + (data.title || 'Task finished'),
        description: data.result || '',
        progress: 100,
        showBar: true,
        showPct: true,
        barClass: data.status === 'done' ? 'done' : 'error',
        iconClass: data.status === 'done' ? 'complete' : 'error',
      });
      prependToast(el, id);
    }

    autoRemoveToast(el, id, 6000);
  }

  // ── Show an init toast (brief) ──

  function showInitToast(data) {
    const pending = (data.pending_suggestions || []).length;
    const running = (data.running_tasks || []).length;
    if (pending === 0 && running === 0) return; // nothing to show

    const id = 'init_' + Date.now();
    const el = createToast(id, {
      type: 'init',
      title: `📡 Admin Bot connected`,
      description: `${pending} pending suggestion${pending !== 1 ? 's' : ''}, ${running} running task${running !== 1 ? 's' : ''}`,
      iconClass: 'progress',
    });
    prependToast(el, id);
    autoRemoveToast(el, id, 4000);

    // Show pending suggestions individually
    (data.pending_suggestions || []).slice(0, 5).forEach((s, i) => {
      setTimeout(() => showSuggestionToast(s), 500 + i * 300);
    });
  }

  // ── Toast lifecycle helpers ──

  function prependToast(el, id) {
    toasts.set(id, el);
    container.prepend(el);
    // Limit visible toasts
    while (container.children.length > 5) {
      const last = container.lastChild;
      if (last && last.dataset.toastId) {
        toasts.delete(last.dataset.toastId);
      }
      container.removeChild(last);
    }
  }

  function autoRemoveToast(el, id, delay) {
    setTimeout(() => {
      el.classList.add('dismissing');
      setTimeout(() => {
        el.remove();
        toasts.delete(id);
      }, 300);
    }, delay);
  }

  function dismissToast(id) {
    const el = toasts.get(id);
    if (el) {
      el.classList.add('dismissing');
      setTimeout(() => {
        el.remove();
        toasts.delete(id);
      }, 300);
    }
  }

  // ── WebSocket connection ──

  function connectWS() {
    if (ws && ws.readyState <= 1) return; // already open or opening

    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${protocol}//${location.host}/api/admin/ws`;

    try {
      ws = new WebSocket(url);
    } catch (e) {
      console.warn('[AdminToast] WS connect failed:', e);
      scheduleReconnect();
      return;
    }

    ws.onopen = () => {
      console.log('[AdminToast] WebSocket connected');
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
    };

    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        switch (msg.type) {
          case 'init':
            showInitToast(msg.data);
            break;
          case 'suggestion':
            showSuggestionToast(msg.data);
            break;
          case 'suggestion_update':
            // Update existing suggestion toast if visible
            {
              const sid = 'sug_' + msg.data.id;
              if (msg.data.status !== 'pending') dismissToast(sid);
            }
            break;
          case 'progress':
            showProgressToast(msg.data);
            break;
          case 'complete':
            showCompleteToast(msg.data);
            break;
          case 'task_created':
            showProgressToast({
              task_id: msg.data.id,
              title: msg.data.title,
              progress: 0,
            });
            break;
          case 'pong':
            break; // keepalive response
          case 'ping':
            // Server ping — respond
            if (ws && ws.readyState === 1) {
              ws.send(JSON.stringify({ cmd: 'ping' }));
            }
            break;
          case 'scan_started':
            break;
          default:
            console.log('[AdminToast] unknown msg type:', msg.type);
        }
      } catch (err) {
        console.warn('[AdminToast] message parse error:', err);
      }
    };

    ws.onclose = () => {
      console.log('[AdminToast] WebSocket closed');
      ws = null;
      scheduleReconnect();
    };

    ws.onerror = (err) => {
      console.warn('[AdminToast] WebSocket error:', err);
    };
  }

  function scheduleReconnect() {
    if (reconnectTimer) return;
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      connectWS();
    }, 5000);
  }

  // ── Approve / Dismiss API calls ──

  async function approveSuggestion(suggestionId) {
    try {
      const r = await fetch(`/api/admin/suggestions/${suggestionId}/approve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ auto_run: false }),
      });
      const d = await r.json();
      if (d.ok) {
        dismissToast('sug_' + suggestionId);
        // Show task progress toast
        if (d.task) {
          showProgressToast({
            task_id: d.task.id,
            title: d.task.title,
            progress: 0,
          });
        }
      }
    } catch (e) {
      console.warn('[AdminToast] approve failed:', e);
    }
  }

  async function dismissSuggestion(suggestionId) {
    try {
      const r = await fetch(`/api/admin/suggestions/${suggestionId}/dismiss`, {
        method: 'POST',
      });
      const d = await r.json();
      if (d.ok) {
        dismissToast('sug_' + suggestionId);
      }
    } catch (e) {
      console.warn('[AdminToast] dismiss failed:', e);
    }
  }

  async function triggerScan() {
    if (ws && ws.readyState === 1) {
      ws.send(JSON.stringify({ cmd: 'scan' }));
    }
  }

  // ── Public API ──

  window.AdminToast = {
    connect: connectWS,
    approve: approveSuggestion,
    dismiss: dismissSuggestion,
    scan: triggerScan,
    showSuggestion: showSuggestionToast,
    showProgress: showProgressToast,
    showComplete: showCompleteToast,
  };

  // ── Auto-connect on load ──
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', connectWS);
  } else {
    connectWS();
  }
})();
