import React, { useState, useEffect, useCallback } from 'react';
import { useDashboard } from './hooks/useDashboard';
import Sidebar from './ui/Sidebar';
import MetricsBar from './ui/MetricsBar';
import AgentPanel from './ui/AgentPanel';
import PipelineView from './ui/PipelineView';
import NoticeBoard from './ui/NoticeBoard';
import TicketList from './ui/TicketList';
import SakuraPetals from './ui/SakuraPetals';
import LillyChat from './ui/LillyChat';
import LillyEditor from './ui/LillyEditor';
import Synapse from './apps/Synapse';
import AgentChat from './ui/AgentChat';
import TaskletPanel from './ui/TaskletPanel';
import Portfolio from './apps/Portfolio';
import WebsiteAnalysis from './ui/WebsiteAnalysis';
import NotificationsBell from './ui/NotificationsBell';

const API = import.meta.env.VITE_API_URL || '';

function App() {
  const [deptId, setDeptId] = useState(null);
  const [deptInfo, setDeptInfo] = useState(null);
  const { data, loading, error, refetch } = useDashboard(deptId);
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState({ title: '', description: '', type: 'task', priority: 'medium', story_points: 1 });
  const [view, setView] = useState('dashboard');
  const [chatOpen, setChatOpen] = useState(false);
  const [chatDeptId, setChatDeptId] = useState(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const [agentChat, setAgentChat] = useState(null);
  const [analysisOpen, setAnalysisOpen] = useState(false);
  const [analysisUrl, setAnalysisUrl] = useState('');

  useEffect(() => {
    window.__openWebsiteAnalysis = (url) => {
      setAnalysisUrl(url || '');
      setAnalysisOpen(true);
    };
    window.__toggleLillyChat = (deptId) => {
      setChatDeptId(deptId);
      setChatOpen(prev => deptId ? true : !prev);
    };
    window.__openEditor = () => setEditorOpen(true);
    window.__handleLillyAction = handleLillyAction;
    return () => {
      delete window.__toggleLillyChat;
      delete window.__openEditor;
      delete window.__handleLillyAction;
    };
  }, []);

  const handleLillyAction = useCallback((action) => {
    if (!action) return;
    switch (action.type) {
      case 'ui_action':
        switch (action.ui_action) {
          case 'switch_dept':
            setDeptId(action.ui_param);
            break;
          case 'open_app':
            setView(action.ui_param || 'dashboard');
            break;
          case 'open_ticket':
            setForm(prev => ({ ...prev, title: `Viewing ticket ${action.ui_param}` }));
            break;
          case 'open_chat':
            setChatDeptId(action.ui_param);
            if (!chatOpen) setChatOpen(true);
            break;
          case 'website_analysis':
            setAnalysisUrl(action.ui_param || '');
            setAnalysisOpen(true);
            break;
          default:
            break;
        }
        break;
      case 'drive_sync':
        if (action.status === 'needs_auth') {
          fetch(`${API}/api/drive/auth-url`)
            .then(r => r.json())
            .then(d => { if (d.url) window.open(d.url, '_blank'); });
        }
        break;
      default:
        break;
    }
  }, [chatOpen]);

  useEffect(() => {
    if (!deptId) {
      setDeptInfo(null);
      return;
    }
    fetch(`${API}/api/departments/${deptId}`)
      .then(r => r.json())
      .then(setDeptInfo)
      .catch(() => setDeptInfo(null));
  }, [deptId]);

  const handleTransition = async (ticketId, newStatus) => {
    try {
      await fetch(`${API}/api/tickets/${ticketId}/transition`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: newStatus }),
      });
      refetch();
    } catch (e) {
      console.error(e);
    }
  };

  const handleCreateTicket = async (e) => {
    e.preventDefault();
    const projectId = deptId ? `proj-${deptId}` : 'proj-1';
    try {
      await fetch(`${API}/api/tickets`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...form, project_id: projectId }),
      });
      setShowCreate(false);
      setForm({ title: '', description: '', type: 'task', priority: 'medium', story_points: 1 });
      refetch();
    } catch (e) {
      console.error(e);
    }
  };

  if (loading) {
    return (
      <div style={{
        minHeight: '100vh',
        background: '#F5F5F4',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontFamily: 'system-ui, sans-serif',
        color: '#999',
        fontSize: 14,
        flexDirection: 'column',
        gap: 8,
      }}>
        Connecting to LillyOS...
      </div>
    );
  }

  if (editorOpen) {
    return <LillyEditor onClose={() => setEditorOpen(false)} />;
  }

  return (
    <>
      <SakuraPetals />
      <div style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100vh',
        overflow: 'hidden',
        fontFamily: 'system-ui, -apple-system, sans-serif',
      }}>
        <Sidebar activeDept={deptId} onSelect={setDeptId} activeApp={view} onAppSelect={setView} />

        <div style={{
          flex: 1,
          background: '#F5F5F4',
          padding: 0,
          position: 'relative',
          zIndex: 1,
          overflowY: 'auto',
        }}>
          {view === 'synapse' ? (
            <Synapse />
          ) : view === 'portfolio' ? (
            <Portfolio onChat={(project) => {
              setChatDeptId(null);
              setChatOpen(true);
              setTimeout(() => {
                window.__lillyChatSend && window.__lillyChatSend(`Tell me about the project "${project.name}" — ${project.description.slice(0, 100)}`);
              }, 500);
            }} />
          ) : (
          <>
          {error && (
            <div style={{
              background: '#FEE2E2', color: '#991B1B', padding: '10px 16px',
              fontFamily: 'monospace', fontSize: 13, textAlign: 'center',
              borderBottom: '1px solid #FCA5A5', marginBottom: 16, borderRadius: 8,
            }}>
              API Error: {error}
            </div>
          )}

          {/* Header */}
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24,
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <div style={{
                width: 36, height: 36, borderRadius: 10,
                background: deptInfo?.color || '#2D2D2D',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                color: 'white', fontWeight: 700, fontSize: 16,
              }}>
                {deptInfo?.icon || 'L'}
              </div>
              <div>
                <h1 style={{ fontSize: 20, fontWeight: 700, margin: 0, color: '#2D2D2D' }}>
                  {deptInfo ? deptInfo.name : 'All Departments'}
                </h1>
                <span style={{ fontSize: 12, color: '#999' }}>
                  {deptInfo ? deptInfo.description : 'Corporate Overview'}
                </span>
              </div>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <NotificationsBell />
              <button
                onClick={() => setShowCreate(true)}
                style={{
                  padding: '8px 18px', background: deptInfo?.color || '#2D2D2D',
                  color: 'white', border: 'none', borderRadius: 10, fontWeight: 600,
                  fontSize: 13, cursor: 'pointer',
                }}
              >
                + New Ticket
              </button>
            </div>
          </div>

          {/* Tickets */}
          {data?.recent_tickets?.length > 0 && (
            <TicketList
              tickets={data.recent_tickets}
              deptColor={deptInfo?.color}
              agents={data.agents || []}
            />
          )}

          {/* Notice Board */}
          <NoticeBoard notices={data?.notices} />

          {/* Scrum Standups */}
          {data?.scrums?.length > 0 && (
            <div style={{
              background: 'white', borderRadius: 16, border: '1px solid #eee',
              padding: 20, marginBottom: 20,
            }}>
              <h3 style={{
                fontSize: 14, fontWeight: 700, margin: '0 0 12px 0',
                textTransform: 'uppercase', letterSpacing: '0.5px', color: '#2D2D2D',
              }}>
                Today's Standups
              </h3>
              <div style={{ display: 'flex', gap: 12, overflow: 'auto' }}>
                {data.scrums.map(s => (
                  <div key={s.id} style={{
                    flex: '0 0 280px', padding: 12, background: '#FAFAFA',
                    borderRadius: 10, border: '1px solid #f0f0f0',
                  }}>
                    <div style={{
                      fontSize: 13, fontWeight: 700, color: '#2D2D2D', marginBottom: 8,
                    }}>{s.team_name}</div>
                    {s.entries?.map(e => (
                      <div key={e.id} style={{
                        fontSize: 12, color: '#666', marginBottom: 6,
                        paddingLeft: 8, borderLeft: '2px solid #ddd',
                      }}>
                        <div><strong>Yesterday:</strong> {e.yesterday || '—'}</div>
                        <div><strong>Today:</strong> {e.today || '—'}</div>
                        {e.blockers?.length > 0 && (
                          <div style={{ color: '#EF4444' }}>
                            <strong>Blockers:</strong> {e.blockers.join(', ')}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Metrics */}
          <MetricsBar summary={data?.summary} />

          {/* Main area */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 20, marginBottom: 24 }}>
            <TaskletPanel departmentId={deptId} deptColor={deptInfo?.color || '#3B82F6'} />
            <AgentPanel agents={data?.agents || []} onAgentClick={setAgentChat} />
            <PipelineView deployments={data?.deployments || []} />
          </div>

          {/* Sprints */}
          {data?.sprints?.length > 0 && (
            <div style={{
              background: 'white', borderRadius: 16, border: '1px solid #eee', padding: 20,
            }}>
              <h3 style={{
                fontSize: 14, fontWeight: 700, margin: '0 0 12px 0',
                textTransform: 'uppercase', letterSpacing: '0.5px', color: '#2D2D2D',
              }}>
                Sprints
              </h3>
              <div style={{ display: 'flex', gap: 12 }}>
                {data.sprints.map(s => (
                  <div key={s.id} style={{
                    flex: 1, padding: 14,
                    background: s.status === 'active' ? '#F0F9FF' : '#FAFAFA',
                    borderRadius: 10, border: '1px solid #f0f0f0',
                  }}>
                    <div style={{
                      fontSize: 13, fontWeight: 700, color: '#2D2D2D', marginBottom: 4,
                    }}>{s.name}</div>
                    <div style={{ fontSize: 11, color: '#999', marginBottom: 4 }}>
                      {s.goal || 'No goal set'}
                    </div>
                    <div style={{ fontSize: 11, color: '#666' }}>
                      <strong>Velocity:</strong> {s.velocity_actual || 0}/{s.velocity_planned || 0}
                      {' | '}<strong>Status:</strong> {s.status}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Create Ticket Modal */}
          {showCreate && (
            <div style={{
              position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              zIndex: 1000, padding: 20,
            }}>
              <div style={{
                background: 'white', borderRadius: 16, padding: 24,
                width: '100%', maxWidth: 480, boxShadow: '0 20px 60px rgba(0,0,0,0.15)',
              }}>
                <h2 style={{ fontSize: 16, fontWeight: 700, margin: '0 0 16px', color: '#2D2D2D' }}>
                  New Ticket
                </h2>
                <form onSubmit={handleCreateTicket}>
                  <div style={{ marginBottom: 12 }}>
                    <input
                      placeholder="Title"
                      value={form.title}
                      onChange={e => setForm({ ...form, title: e.target.value })}
                      required
                      style={{
                        width: '100%', padding: '10px 12px', border: '1px solid #ddd',
                        borderRadius: 8, fontSize: 13, outline: 'none', boxSizing: 'border-box',
                      }}
                    />
                  </div>
                  <div style={{ marginBottom: 12 }}>
                    <textarea
                      placeholder="Description"
                      value={form.description}
                      onChange={e => setForm({ ...form, description: e.target.value })}
                      rows={3}
                      style={{
                        width: '100%', padding: '10px 12px', border: '1px solid #ddd',
                        borderRadius: 8, fontSize: 13, outline: 'none', resize: 'vertical',
                        fontFamily: 'inherit', boxSizing: 'border-box',
                      }}
                    />
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8, marginBottom: 16 }}>
                    <select
                      value={form.type}
                      onChange={e => setForm({ ...form, type: e.target.value })}
                      style={{ padding: '8px 10px', border: '1px solid #ddd', borderRadius: 8, fontSize: 12, outline: 'none' }}
                    >
                      <option value="task">Task</option>
                      <option value="story">Story</option>
                      <option value="bug">Bug</option>
                      <option value="design">Design</option>
                      <option value="ux_research">UX Research</option>
                    </select>
                    <select
                      value={form.priority}
                      onChange={e => setForm({ ...form, priority: e.target.value })}
                      style={{ padding: '8px 10px', border: '1px solid #ddd', borderRadius: 8, fontSize: 12, outline: 'none' }}
                    >
                      <option value="critical">Critical</option>
                      <option value="high">High</option>
                      <option value="medium">Medium</option>
                      <option value="low">Low</option>
                    </select>
                    <input
                      type="number"
                      placeholder="Points"
                      value={form.story_points}
                      onChange={e => setForm({ ...form, story_points: parseInt(e.target.value) || 1 })}
                      min={1}
                      max={21}
                      style={{ padding: '8px 10px', border: '1px solid #ddd', borderRadius: 8, fontSize: 12, outline: 'none', width: '100%', boxSizing: 'border-box' }}
                    />
                  </div>
                  <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                    <button
                      type="button"
                      onClick={() => setShowCreate(false)}
                      style={{
                        padding: '8px 18px', border: '1px solid #ddd', borderRadius: 10,
                        background: 'white', fontSize: 13, cursor: 'pointer', color: '#666',
                      }}
                    >
                      Cancel
                    </button>
                    <button
                      type="submit"
                      style={{
                        padding: '8px 18px', border: 'none', borderRadius: 10,
                        background: '#2D2D2D', color: 'white', fontSize: 13, fontWeight: 600, cursor: 'pointer',
                      }}
                    >
                      Create
                    </button>
                  </div>
                </form>
              </div>
            </div>
          )}
          {/* Dept chat button */}
          {deptId && (
            <button
              onClick={() => { setChatDeptId(deptId); setChatOpen(true); }}
              style={{
                position: 'fixed', bottom: 20, right: 20, zIndex: 100,
                width: 48, height: 48, borderRadius: 14,
                background: deptInfo?.color || '#6366F1', color: 'white',
                border: 'none', cursor: 'pointer', fontSize: 20,
                boxShadow: '0 4px 16px rgba(0,0,0,0.15)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}
              title={`Ask Lilly about ${deptInfo?.name || 'this department'}`}
            >
              🧠
            </button>
          )}
          </>
          )}
        </div>

        {/* Chat Panel */}
        {chatOpen && (
          <div style={{
            position: 'fixed', top: 0, right: 0, bottom: 0, zIndex: 999,
            width: 400, maxWidth: '100vw',
            boxShadow: '-4px 0 24px rgba(0,0,0,0.1)',
            display: 'flex', flexDirection: 'column',
          }}>
            <LillyChat
              departmentId={chatDeptId}
              departmentInfo={chatDeptId ? deptInfo : null}
              onClose={() => setChatOpen(false)}
              onAction={handleLillyAction}
            />
          </div>
        )}

        {/* Agent Chat Panel */}
        {agentChat && (
          <div style={{
            position: 'fixed', top: 0, right: 0, bottom: 0, zIndex: 999,
            width: 400, maxWidth: '100vw',
            boxShadow: '-4px 0 24px rgba(0,0,0,0.1)',
            display: 'flex', flexDirection: 'column',
          }}>
            <AgentChat
              agent={agentChat}
              onClose={() => setAgentChat(null)}
            />
          </div>
        )}

        {/* Website Analysis Panel */}
        {analysisOpen && (
          <WebsiteAnalysis
            url={analysisUrl}
            onClose={() => setAnalysisOpen(false)}
          />
        )}
      </div>
    </>
  );
}

export default App;
