import { useEffect, useRef, useCallback } from 'react';

const API = import.meta.env.VITE_API_URL || '';
let permissionRequested = false;

export function useNotifications(departmentId) {
  const esRef = useRef(null);
  const lastNotifiedRef = useRef(0);

  const notify = useCallback((title, body) => {
    if (!('Notification' in window)) return;
    if (Notification.permission === 'granted') {
      try {
        const n = new Notification(title, {
          body: body?.slice(0, 120),
          icon: '/favicon.ico',
          tag: 'tasklet',
        });
        setTimeout(() => n.close(), 5000);
      } catch (e) {
        // ignore
      }
    }
  }, []);

  useEffect(() => {
    const params = departmentId ? `?department_id=${departmentId}` : '';
    const url = `${API}/api/events${params}`;

    let retryTimer;
    let currentEs;

    function connect() {
      if (currentEs) currentEs.close();
      const es = new EventSource(url);
      currentEs = es;
      esRef.current = es;

      es.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data);
          const ts = new Date(data.timestamp * 1000).getTime();
          if (ts <= lastNotifiedRef.current) return;
          lastNotifiedRef.current = ts;

          if (data.type === 'tasklet_started') {
            notify(`▶️ Tasklet Started`, data.message);
          } else if (data.type === 'tasklet_complete') {
            notify(`✅ Tasklet Complete`, data.message);
          } else if (data.type === 'tasklet_failed') {
            notify(`❌ Tasklet Failed`, data.message);
          } else if (data.type === 'tasklet_log' || data.type === 'tasklet_status') {
            notify(`📋 ${data.message?.split('—')[0] || 'Tasklet Update'}`, data.message);
          }
        } catch (e) {
          // ignore parse errors
        }
      };

      es.onerror = () => {
        es.close();
        retryTimer = setTimeout(connect, 3000);
      };
    }

    if (!permissionRequested && 'Notification' in window && Notification.permission === 'default') {
      permissionRequested = true;
      Notification.requestPermission();
    }

    connect();

    return () => {
      if (retryTimer) clearTimeout(retryTimer);
      if (currentEs) currentEs.close();
    };
  }, [departmentId, notify]);

  return { notify };
}
