import React, { useState } from 'react';

const API = import.meta.env.VITE_API_URL || (typeof window !== 'undefined' ? window.location.origin : '');

const PRIORITY_COLORS = {
  critical: '#EF4444', high: '#F97316', medium: '#3B82F6', low: '#94A3B8',
};

const PRIORITY_LABELS = {
  critical: 'Critical', high: 'High', medium: 'Medium', low: 'Low',
};

const STATUS_COLORS = {
  backlog: '#D1D5DB', groomed: '#93C5FD', sprint_ready: '#A78BFA',
  in_sprint: '#818CF8', in_progress: '#FCD34D',
  design_review: '#F472B6', ux_review: '#34D399', ready_for_dev: '#6EE7B7',
  in_review: '#A78BFA', done: '#6EE7B7', cancelled: '#FCA5A5',
};

const TYPE_ICONS = {
  bug: '🐛', task: '📋', story: '📖', epic: '🏗️', design: '🎨', ux_research: '🔍',
};

const ROLE_STYLES = {
  pm: { bg: '#F3E8FF', color: '#8B5CF6', label: 'PM' },
  developer: { bg: '#E0F2FE', color: '#3B82F6', label: 'Dev' },
  devops: { bg: '#DCFCE7', color: '#10B981', label: 'Ops' },
  qa: { bg: '#FEF3C7', color: '#F59E0B', label: 'QA' },
  designer: { bg: '#FCE7F3', color: '#EC4899', label: 'Design' },
  ux: { bg: '#D1FAE5', color: '#059669', label: 'UX' },
  finance_manager: { bg: '#D1FAE5', color: '#059669', label: 'Finance' },
  accountant: { bg: '#ECFDF5', color: '#10B981', label: 'Acct' },
  investor_relations: { bg: '#FEF3C7', color: '#D97706', label: 'IR' },
  treasury_analyst: { bg: '#FEF3C7', color: '#D97706', label: 'Treasury' },
  ops_manager: { bg: '#FFEDD5', color: '#EA580C', label: 'Ops' },
  verification_specialist: { bg: '#FFF7ED', color: '#C2410C', label: 'Verify' },
  dispute_manager: { bg: '#FEE2E2', color: '#DC2626', label: 'Dispute' },
  customer_support: { bg: '#E0E7FF', color: '#4F46E5', label: 'Support' },
  data_scientist: { bg: '#EDE9FE', color: '#7C3AED', label: 'Data Sci' },
  ml_engineer: { bg: '#F3E8FF', color: '#9333EA', label: 'ML' },
  risk_analyst: { bg: '#FEF2F2', color: '#B91C1C', label: 'Risk' },
  analytics_engineer: { bg: '#E0F2FE', color: '#0284C7', label: 'Analytics' },
  marketing_manager: { bg: '#FDF2F8', color: '#DB2777', label: 'Mktg' },
  content_creator: { bg: '#FFF1F2', color: '#E11D48', label: 'Content' },
  b2b_sales: { bg: '#F0FDF4', color: '#16A34A', label: 'Sales' },
  customer_success: { bg: '#ECFEFF', color: '#0891B2', label: 'CS' },
  hr_manager: { bg: '#F5F3FF', color: '#6D28D9', label: 'HR' },
  recruiter: { bg: '#F3E8FF', color: '#7C3AED', label: 'Recruit' },
  benefits_admin: { bg: '#F0F9FF', color: '#0369A1', label: 'Benefits' },
  training_coordinator: { bg: '#F0FDF4', color: '#15803D', label: 'Training' },
  corporate_lawyer: { bg: '#FEF2F2', color: '#991B1B', label: 'Legal' },
  compliance_officer: { bg: '#FFFBEB', color: '#B45309', label: 'Comply' },
  contract_manager: { bg: '#FEF3C7', color: '#B45309', label: 'Contracts' },
  admin_manager: { bg: '#F0F9FF', color: '#0E7490', label: 'Admin' },
  euc_specialist: { bg: '#ECFEFF', color: '#0891B2', label: 'EUC' },
  csr_coordinator: { bg: '#F0FDF4', color: '#15803D', label: 'CSR' },
  office_manager: { bg: '#F5F5F4', color: '#57534E', label: 'Office' },
};

export default function TicketList({ tickets, deptColor, agents = [] }) {
  const [selected, setSelected] = useState(null);
  const [message, setMessage] = useState('');
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(null);

  const agentMap = {};
  agents.forEach(a => { agentMap[a.id] = a; });

  const assignedAgent = selected ? agentMap[selected.assignee] : null;
  const aStyle = ROLE_STYLES[assignedAgent?.role] || ROLE_STYLES.developer;

  const sendMessage = async () => {
    if (!message.trim() || !selected?.assignee || sending) return;
    setSending(true);
    setSent(null);
    try {
      const res = await fetch(`${API}/api/agents/${selected.assignee}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: message.trim(),
          conversation_id: '',
          department_id: '',
        }),
      });
      const data = await res.json();
      setSent(data.response?.slice(0, 200) || 'Message sent');
      setMessage('');
    } catch (e) {
      setSent('Failed to send message');
    }
    setSending(false);
  };

  return (
    <>
      <div style={{
        background: 'white', borderRadius: 16, border: '1px solid #eee',
        padding: 20, marginBottom: 20,
      }}>
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14,
        }}>
          <h3 style={{
            fontSize: 14, fontWeight: 700, margin: 0,
            textTransform: 'uppercase', letterSpacing: '0.5px', color: '#2D2D2D',
          }}>
            Tickets
          </h3>
          <span style={{
            fontSize: 11, color: '#999', background: '#F5F5F4',
            padding: '2px 8px', borderRadius: 8,
          }}>
            {tickets.length} total
          </span>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {tickets.map(t => {
            const a = agentMap[t.assignee];
            return (
              <button
                key={t.id}
                onClick={() => setSelected(t)}
                style={{
                  display: 'flex', alignItems: 'center', gap: 10,
                  padding: '8px 12px', borderRadius: 10, border: '1px solid',
                  borderColor: selected?.id === t.id ? (PRIORITY_COLORS[t.priority] || '#94A3B8') + '40' : '#f0f0f0',
                  background: selected?.id === t.id ? '#F5F3FF' : '#FAFAFA',
                  cursor: 'pointer', textAlign: 'left', width: '100%',
                  transition: 'all 0.15s', fontFamily: 'inherit',
                }}
                onMouseEnter={e => { if (selected?.id !== t.id) e.target.style.background = '#F0F0F0'; }}
                onMouseLeave={e => { if (selected?.id !== t.id) e.target.style.background = '#FAFAFA'; }}
              >
                <span style={{ fontSize: 16, flexShrink: 0 }}>{TYPE_ICONS[t.type] || '📌'}</span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{
                    fontSize: 13, fontWeight: 600, color: '#2D2D2D',
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  }}>
                    {t.title}
                  </div>
                  <div style={{ display: 'flex', gap: 8, marginTop: 3, alignItems: 'center' }}>
                    <span style={{
                      fontSize: 10, padding: '1px 6px', borderRadius: 4,
                      background: (STATUS_COLORS[t.status] || '#D1D5DB') + '30',
                      color: '#2D2D2D', fontWeight: 500,
                    }}>
                      {t.status?.replace(/_/g, ' ')}
                    </span>
                    {a && (
                      <span style={{
                        fontSize: 10, color: aStyle.color, fontWeight: 500,
                        display: 'flex', alignItems: 'center', gap: 3,
                      }}>
                        <span style={{
                          width: 6, height: 6, borderRadius: 3,
                          background: a.busy ? '#10B981' : '#94A3B8',
                          display: 'inline-block',
                        }} />
                        {a.name}
                      </span>
                    )}
                    {!a && t.assignee && (
                      <span style={{ fontSize: 10, color: '#999' }}>
                        {t.assignee}
                      </span>
                    )}
                  </div>
                </div>
                <div style={{
                  width: 8, height: 8, borderRadius: 4, flexShrink: 0,
                  background: PRIORITY_COLORS[t.priority] || '#94A3B8',
                }} />
              </button>
            );
          })}
        </div>
      </div>

      {/* Ticket Detail Modal */}
      {selected && (
        <div style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          zIndex: 1000, padding: 20,
        }} onClick={() => { setSelected(null); setSent(null); }}>
          <div style={{
            background: 'white', borderRadius: 16, padding: 24,
            width: '100%', maxWidth: 540, boxShadow: '0 20px 60px rgba(0,0,0,0.15)',
            maxHeight: '85vh', overflow: 'auto',
          }} onClick={e => e.stopPropagation()}>
            {/* Header */}
            <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 14 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontSize: 24 }}>{TYPE_ICONS[selected.type] || '📌'}</span>
                <span style={{
                  fontSize: 11, color: '#999', background: '#F5F5F4',
                  padding: '2px 8px', borderRadius: 6, fontFamily: 'monospace',
                }}>
                  {selected.id}
                </span>
              </div>
              <button onClick={() => { setSelected(null); setSent(null); }} style={{
                background: 'none', border: 'none', fontSize: 18, cursor: 'pointer', color: '#999',
              }}>✕</button>
            </div>

            <h2 style={{ fontSize: 18, fontWeight: 700, margin: '0 0 8px', color: '#2D2D2D' }}>
              {selected.title}
            </h2>

            {/* Badge Row */}
            <div style={{ display: 'flex', gap: 6, marginBottom: 14, flexWrap: 'wrap' }}>
              <span style={{
                fontSize: 11, padding: '3px 10px', borderRadius: 6,
                background: (STATUS_COLORS[selected.status] || '#D1D5DB') + '30',
                color: '#2D2D2D', fontWeight: 600,
              }}>
                {selected.status?.replace(/_/g, ' ')}
              </span>
              <span style={{
                fontSize: 11, padding: '3px 10px', borderRadius: 6,
                background: (PRIORITY_COLORS[selected.priority] || '#94A3B8') + '20',
                color: PRIORITY_COLORS[selected.priority] || '#94A3B8', fontWeight: 700,
              }}>
                {PRIORITY_LABELS[selected.priority] || selected.priority} Priority
              </span>
              <span style={{ fontSize: 11, padding: '3px 10px', borderRadius: 6, background: '#F5F5F4', color: '#666' }}>
                {selected.story_points} pts
              </span>
              <span style={{ fontSize: 11, padding: '3px 10px', borderRadius: 6, background: '#F5F5F4', color: '#666' }}>
                {selected.type?.replace(/_/g, ' ')}
              </span>
            </div>

            {/* Description */}
            <div style={{ fontSize: 13, color: '#555', lineHeight: 1.7, marginBottom: 16, whiteSpace: 'pre-wrap' }}>
              {selected.description || 'No description'}
            </div>

            {/* Agent Card */}
            {assignedAgent && (
              <div style={{
                marginBottom: 16, padding: 14, borderRadius: 12,
                border: '1px solid ' + aStyle.color + '25',
                background: aStyle.bg + '60',
              }}>
                <div style={{ fontSize: 10, fontWeight: 600, color: '#999', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: 8 }}>
                  Assigned Agent
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <div style={{
                    width: 34, height: 34, borderRadius: 8,
                    background: aStyle.bg, display: 'flex',
                    alignItems: 'center', justifyContent: 'center',
                    fontSize: 11, fontWeight: 700, color: aStyle.color, flexShrink: 0,
                  }}>{aStyle.label}</div>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: 14, fontWeight: 600, color: '#2D2D2D' }}>
                      {assignedAgent.name}
                    </div>
                    <div style={{ fontSize: 11, color: '#888', display: 'flex', gap: 8 }}>
                      <span>{assignedAgent.role}</span>
                      <span>·</span>
                      <span style={{
                        display: 'flex', alignItems: 'center', gap: 3,
                        color: assignedAgent.busy ? '#10B981' : '#94A3B8',
                      }}>
                        <span style={{
                          width: 6, height: 6, borderRadius: 3,
                          background: assignedAgent.busy ? '#10B981' : '#94A3B8',
                          display: 'inline-block',
                        }} />
                        {assignedAgent.busy ? 'On task' : 'Available'}
                      </span>
                      <span>·</span>
                      <span>{Math.round(assignedAgent.success_rate * 100)}% success</span>
                    </div>
                  </div>
                  <div style={{ fontSize: 11, color: '#999', textAlign: 'right' }}>
                    <div>{assignedAgent.tasks_completed} done</div>
                  </div>
                </div>
              </div>
            )}

            {!assignedAgent && selected.assignee && (
              <div style={{ marginBottom: 16, padding: 12, borderRadius: 10, background: '#FFF7ED', border: '1px solid #FED7AA' }}>
                <div style={{ fontSize: 12, color: '#9A3412' }}>
                  👤 Assigned to: <strong>{selected.assignee}</strong>
                </div>
              </div>
            )}

            {!selected.assignee && (
              <div style={{ marginBottom: 16, padding: 12, borderRadius: 10, background: '#F0F0F0', fontSize: 12, color: '#888' }}>
                👤 Unassigned — no agent working on this ticket yet
              </div>
            )}

            {/* Message to Agent */}
            {selected.assignee && (
              <div style={{
                marginBottom: 12, padding: 14, borderRadius: 12,
                border: '1px solid #E0E7FF', background: '#F8FAFF',
              }}>
                <div style={{ fontSize: 10, fontWeight: 600, color: '#999', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: 8 }}>
                  💬 Leave a Message for {assignedAgent?.name || selected.assignee}
                </div>
                <textarea
                  value={message}
                  onChange={e => setMessage(e.target.value)}
                  placeholder="Ask for status update, give instructions, add context..."
                  rows={2}
                  style={{
                    width: '100%', padding: '8px 10px', border: '1px solid #ddd',
                    borderRadius: 8, fontSize: 12, outline: 'none', resize: 'vertical',
                    fontFamily: 'inherit', boxSizing: 'border-box', marginBottom: 8,
                  }}
                />
                <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                  <button
                    onClick={sendMessage}
                    disabled={!message.trim() || sending}
                    style={{
                      padding: '7px 16px', borderRadius: 8, border: 'none',
                      background: message.trim() && !sending ? '#6366F1' : '#eee',
                      color: message.trim() && !sending ? 'white' : '#ccc',
                      fontSize: 12, fontWeight: 600,
                      cursor: message.trim() && !sending ? 'pointer' : 'default', fontFamily: 'inherit',
                    }}
                  >{sending ? 'Sending...' : 'Send to Agent'}</button>
                  {sent && (
                    <span style={{ fontSize: 11, color: '#10B981', maxWidth: 250, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      ✓ {sent.slice(0, 80)}
                    </span>
                  )}
                </div>
              </div>
            )}

            {/* Meta */}
            <div style={{ fontSize: 12, color: '#999', borderTop: '1px solid #eee', paddingTop: 12 }}>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 4 }}>
                <div><strong>ID:</strong> {selected.id}</div>
                <div><strong>Priority:</strong> {PRIORITY_LABELS[selected.priority] || selected.priority}</div>
                <div><strong>Sprint:</strong> {selected.sprint_id || 'None'}</div>
                <div><strong>Points:</strong> {selected.story_points}</div>
                <div><strong>Created:</strong> {new Date(selected.created_at * 1000).toLocaleString()}</div>
                {selected.started_at && (
                  <div><strong>Started:</strong> {new Date(selected.started_at * 1000).toLocaleString()}</div>
                )}
              </div>
              {selected.labels?.length > 0 && (
                <div style={{ display: 'flex', gap: 4, marginTop: 8, flexWrap: 'wrap' }}>
                  {selected.labels.map((l, i) => (
                    <span key={i} style={{
                      fontSize: 10, padding: '2px 8px', borderRadius: 4,
                      background: '#EDE9FE', color: '#7C3AED',
                    }}>{l}</span>
                  ))}
                </div>
              )}
              {selected.sla_deadline && (
                <div style={{ marginTop: 6, fontSize: 11, color: timeToSla(selected.sla_deadline).color }}>
                  <strong>SLA:</strong> {timeToSla(selected.sla_deadline).text}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}

function timeToSla(deadline) {
  const diff = (deadline * 1000) - Date.now();
  if (diff < 0) return { text: '🔴 OVERDUE', color: '#EF4444' };
  const hours = diff / 3600000;
  if (hours < 1) return { text: `⚠️ ${Math.round(hours * 60)}m remaining`, color: '#F59E0B' };
  if (hours < 4) return { text: `⏳ ${Math.round(hours)}h remaining`, color: '#F59E0B' };
  return { text: `✅ ${Math.round(hours)}h remaining`, color: '#10B981' };
}
