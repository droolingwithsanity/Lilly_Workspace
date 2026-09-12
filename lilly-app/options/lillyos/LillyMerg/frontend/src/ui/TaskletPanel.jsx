import React, { useState, useEffect, useCallback } from 'react';
import { useNotifications } from '../hooks/useNotifications';

const API = import.meta.env.VITE_API_URL || '';

const STATUS_COLORS = {
  idle: '#94A3B8',
  running: '#3B82F6',
  completed: '#10B981',
  failed: '#EF4444',
};

const STATUS_LABELS = {
  idle: 'Idle',
  running: 'Running...',
  completed: 'Done',
  failed: 'Failed',
};

const SUGGESTIONS = [
  'Monitor all open tickets and report blockers',
  'Generate a daily status summary for this department',
  'Check for SLA breaches and create escalation tickets',
  'Review agent workload and redistribute tasks',
  'Analyze sprint progress and suggest improvements',
];

export default function TaskletPanel({ departmentId, deptColor = '#3B82F6' }) {
  const [tasklets, setTasklets] = useState([]);
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [schedule, setSchedule] = useState('');
  const [expandedId, setExpandedId] = useState(null);
  const [loading, setLoading] = useState(false);
  const { notify } = useNotifications(departmentId);

  const fetchTasklets = useCallback(async () => {
    try {
      const params = departmentId ? `?department_id=${departmentId}` : '';
      const res = await fetch(`${API}/api/tasklets${params}`);
      const data = await res.json();
      setTasklets(data);
    } catch (e) {
      // ignore
    }
  }, [departmentId]);

  useEffect(() => {
    fetchTasklets();
    const interval = setInterval(fetchTasklets, 5000);
    return () => clearInterval(interval);
  }, [fetchTasklets]);

  const createTasklet = async () => {
    if (!name.trim() || !description.trim()) return;
    setLoading(true);
    try {
      const res = await fetch(`${API}/api/tasklets`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          department_id: departmentId,
          name: name.trim(),
          description: description.trim(),
          trigger: schedule ? 'scheduled' : 'manual',
          schedule: schedule || null,
        }),
      });
      const tasklet = await res.json();
      setTasklets(prev => [tasklet, ...prev]);
      setName('');
      setDescription('');
      setShowForm(false);
    } catch (e) {
      // ignore
    }
    setLoading(false);
  };

  const triggerTasklet = async (id) => {
    try {
      const res = await fetch(`${API}/api/tasklets/${id}/trigger`, { method: 'POST' });
      const updated = await res.json();
      setTasklets(prev => prev.map(t => t.id === id ? updated : t));
    } catch (e) {
      // ignore
    }
  };

  const deleteTasklet = async (id) => {
    try {
      await fetch(`${API}/api/tasklets/${id}`, { method: 'DELETE' });
      setTasklets(prev => prev.filter(t => t.id !== id));
    } catch (e) {
      // ignore
    }
  };

  const applySuggestion = (s) => {
    setName(s.length > 40 ? s.slice(0, 40) + '...' : s);
    setDescription(s);
    setShowForm(true);
  };

  const formatTime = (ts) => {
    if (!ts) return '';
    const d = new Date(ts * 1000);
    const now = new Date();
    const diff = now - d;
    if (diff < 60000) return 'just now';
    if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
    if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
    return d.toLocaleDateString();
  };

  return (
    <div style={{ background: 'white', borderRadius: 16, border: '1px solid #eee', padding: 20 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
        <h3 style={{ fontSize: 14, fontWeight: 700, margin: 0, textTransform: 'uppercase', letterSpacing: '0.5px', color: '#2D2D2D' }}>
          🧿 Tasklets
        </h3>
        <div style={{ display: 'flex', gap: 6 }}>
          <span style={{ fontSize: 11, color: '#999', alignSelf: 'center' }}>
            {tasklets.filter(t => t.status === 'running').length} running
          </span>
          <button onClick={() => setShowForm(!showForm)}
            style={{
              padding: '5px 12px', borderRadius: 8, border: 'none',
              background: deptColor, color: 'white', fontSize: 11, fontWeight: 600,
              cursor: 'pointer', fontFamily: 'inherit',
            }}
          >+ New Tasklet</button>
        </div>
      </div>

      {showForm && (
        <div style={{
          marginBottom: 16, padding: 14, borderRadius: 12,
          border: '2px solid ' + deptColor + '30', background: '#FAFAFA',
        }}>
          <input
            value={name} onChange={e => setName(e.target.value)}
            placeholder="Tasklet name (e.g. Daily SLA Monitor)"
            style={{
              width: '100%', padding: '9px 12px', border: '1px solid #ddd', borderRadius: 8,
              fontSize: 12, marginBottom: 8, outline: 'none', fontFamily: 'inherit',
              boxSizing: 'border-box',
            }}
          />
          <textarea
            value={description} onChange={e => setDescription(e.target.value)}
            placeholder="Describe what this tasklet should do in plain English..."
            rows={3}
            style={{
              width: '100%', padding: '9px 12px', border: '1px solid #ddd', borderRadius: 8,
              fontSize: 12, marginBottom: 8, outline: 'none', resize: 'vertical',
              fontFamily: 'inherit', boxSizing: 'border-box',
            }}
          />
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8 }}>
            <select
              value={schedule} onChange={e => setSchedule(e.target.value)}
              style={{
                padding: '7px 10px', border: '1px solid #ddd', borderRadius: 8,
                fontSize: 11, fontFamily: 'inherit', background: 'white',
              }}
            >
              <option value="">Manual (run once)</option>
              <option value="5s">Every 5 seconds</option>
              <option value="30s">Every 30 seconds</option>
              <option value="1m">Every minute</option>
              <option value="5m">Every 5 minutes</option>
              <option value="15m">Every 15 minutes</option>
              <option value="1h">Hourly</option>
              <option value="daily">Daily</option>
            </select>
          </div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 8 }}>
            {SUGGESTIONS.map((s, i) => (
              <button key={i} onClick={() => applySuggestion(s)}
                style={{
                  padding: '4px 10px', borderRadius: 6, border: '1px solid #E0E7FF',
                  background: '#F5F3FF', fontSize: 10, color: '#6366F1', cursor: 'pointer',
                  fontFamily: 'inherit',
                }}
              >{s.slice(0, 35)}...</button>
            ))}
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            <button onClick={createTasklet} disabled={!name.trim() || !description.trim() || loading}
              style={{
                padding: '7px 16px', borderRadius: 8, border: 'none',
                background: name.trim() && description.trim() && !loading ? deptColor : '#ddd',
                color: name.trim() && description.trim() && !loading ? 'white' : '#999',
                fontSize: 12, fontWeight: 600, cursor: name.trim() && description.trim() && !loading ? 'pointer' : 'default',
                fontFamily: 'inherit',
              }}
            >{loading ? 'Creating...' : 'Create Tasklet'}</button>
            <button onClick={() => { setShowForm(false); setName(''); setDescription(''); }}
              style={{ padding: '7px 16px', borderRadius: 8, border: '1px solid #ddd',
                background: 'white', fontSize: 12, cursor: 'pointer', fontFamily: 'inherit',
              }}
            >Cancel</button>
          </div>
        </div>
      )}

      {tasklets.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '24px 12px', color: '#999', fontSize: 12, lineHeight: 1.6 }}>
          <div style={{ fontSize: 24, marginBottom: 6 }}>🤖</div>
          <div>No tasklets yet. Create one to give this department an autonomous AI worker.</div>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {tasklets.map(t => {
            const isExpanded = expandedId === t.id;
            return (
              <div key={t.id} style={{
                borderRadius: 10, border: '1px solid',
                borderColor: t.status === 'running' ? deptColor + '50' : '#eee',
                background: t.status === 'running' ? '#FAFBFF' : 'white',
                overflow: 'hidden',
              }}>
                <div
                  onClick={() => setExpandedId(isExpanded ? null : t.id)}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 10,
                    padding: '10px 12px', cursor: 'pointer',
                  }}
                >
                  <div style={{
                    width: 10, height: 10, borderRadius: 5, flexShrink: 0,
                    background: STATUS_COLORS[t.status] || '#94A3B8',
                    animation: t.status === 'running' ? 'tpulse 1.5s infinite' : 'none',
                  }} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 13, fontWeight: 600, color: '#2D2D2D' }}>{t.name}</div>
                    <div style={{ fontSize: 10, color: '#999', display: 'flex', gap: 8 }}>
                      <span>{STATUS_LABELS[t.status] || t.status}</span>
                      {t.run_count > 0 && <span>{t.run_count} runs</span>}
                      {t.last_run && <span>{formatTime(t.last_run)}</span>}
                      {t.schedule && <span>🔄 {t.schedule}</span>}
                    </div>
                  </div>
                  <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                    {t.status === 'idle' && (
                      <button onClick={e => { e.stopPropagation(); triggerTasklet(t.id); }}
                        style={{
                          padding: '4px 10px', borderRadius: 6, border: 'none',
                          background: deptColor + '20', color: deptColor,
                          fontSize: 10, fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit',
                        }}
                      >▶ Run</button>
                    )}
                    {t.status !== 'running' && (
                      <button onClick={e => { e.stopPropagation(); deleteTasklet(t.id); }}
                        style={{
                          padding: 4, borderRadius: 6, border: 'none',
                          background: 'transparent', color: '#ccc', cursor: 'pointer',
                          fontSize: 12, fontFamily: 'inherit',
                        }}
                      >✕</button>
                    )}
                  </div>
                </div>
                {isExpanded && (
                  <div style={{ padding: '0 12px 12px 12px', borderTop: '1px solid #f0f0f0', marginTop: 0 }}>
                    <div style={{ fontSize: 11, color: '#666', marginTop: 8, lineHeight: 1.5 }}>
                      {t.description}
                    </div>
                    {t.last_result && (
                      <div style={{
                        marginTop: 6, padding: 8, borderRadius: 6, background: '#F8FAFB',
                        fontSize: 11, color: '#555', lineHeight: 1.5,
                      }}>
                        <strong style={{ color: '#999' }}>Last result:</strong> {t.last_result}
                      </div>
                    )}
                    {t.logs?.length > 0 && (
                      <div style={{ marginTop: 6 }}>
                        <div style={{ fontSize: 10, fontWeight: 600, color: '#999', marginBottom: 4 }}>Logs</div>
                        {t.logs.slice(-5).map((log, i) => (
                          <div key={i} style={{
                            display: 'flex', gap: 6, fontSize: 10, color: '#777', padding: '2px 0',
                          }}>
                            <span style={{ color: log.level === 'error' ? '#EF4444' : log.level === 'warning' ? '#F59E0B' : '#999', flexShrink: 0 }}>
                              {new Date(log.timestamp * 1000).toLocaleTimeString()}
                            </span>
                            <span>{log.message}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      <style>{`
        @keyframes tpulse {
          0%, 100% { opacity: 1; transform: scale(1); }
          50% { opacity: 0.5; transform: scale(0.8); }
        }
      `}</style>
    </div>
  );
}
