import React, { useState, useRef, useEffect } from 'react';

const API = import.meta.env.VITE_API_URL || (typeof window !== 'undefined' ? window.location.origin : '');

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

export default function AgentChat({ agent, onClose }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const messagesEndRef = useRef(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, loading]);

  const sendMessage = async (text) => {
    if (!text.trim() || loading) return;
    setInput('');
    setLoading(true);

    const userMsg = { role: 'user', content: text, timestamp: Date.now() };
    setMessages(prev => [...prev, userMsg]);

    try {
      const res = await fetch(`${API}/api/agents/${agent.id}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: text,
          conversation_id: '',
          department_id: agent.department_id,
        }),
      });
      const data = await res.json();

      const msg = {
        role: 'assistant',
        content: data.response,
        delegation: data.delegation,
        timestamp: Date.now(),
      };
      setMessages(prev => [...prev, msg]);
    } catch (e) {
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: 'Connection error. Try again?',
        timestamp: Date.now(),
      }]);
    }
    setLoading(false);
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage(input);
    }
  };

  const s = ROLE_STYLES[agent.role] || ROLE_STYLES.developer;
  const formatTime = (ts) => new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', height: '100%',
      background: 'white', borderLeft: '1px solid #eee',
      overflow: 'hidden',
    }}>
      {/* Header */}
      <div style={{
        padding: '12px 16px', borderBottom: '1px solid #eee',
        display: 'flex', alignItems: 'center', gap: 10,
        background: s.bg,
      }}>
        <div style={{
          width: 30, height: 30, borderRadius: 8,
          background: s.bg, display: 'flex',
          alignItems: 'center', justifyContent: 'center',
          fontSize: 10, fontWeight: 700, color: s.color, flexShrink: 0,
        }}>{s.label}</div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: '#2D2D2D' }}>{agent.name}</div>
          <div style={{ fontSize: 10, color: '#999', display: 'flex', alignItems: 'center', gap: 4 }}>
            <span style={{ width: 6, height: 6, borderRadius: 3, background: agent.busy ? '#10B981' : '#94A3B8', display: 'inline-block' }} />
            {agent.busy ? 'Working' : 'Available'} · {agent.role} · {Math.round(agent.success_rate * 100)}%
          </div>
        </div>
        <button onClick={onClose} style={{
          background: 'none', border: 'none', fontSize: 18, cursor: 'pointer',
          color: '#999', padding: '4px 8px', borderRadius: 6,
        }}>✕</button>
      </div>

      {/* Messages */}
      <div style={{
        flex: 1, overflow: 'auto', padding: '12px 16px',
        display: 'flex', flexDirection: 'column', gap: 10,
      }}>
        {messages.length === 0 && (
          <div style={{ textAlign: 'center', color: '#999', fontSize: 12, padding: '40px 20px', lineHeight: 1.6 }}>
            <div style={{ fontSize: 28, marginBottom: 8 }}>💬</div>
            <div style={{ fontWeight: 600, color: '#666', marginBottom: 4 }}>Chat with {agent.name}</div>
            <div style={{ color: '#888' }}>
              Give them a task, ask them to delegate, or check their progress.
            </div>
            <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 4, alignItems: 'center' }}>
              {[
                `"Create a report on current sprint progress"`,
                `"Delegate a task to Engineering"`,
                `"What are you working on?"`,
              ].map((s, i) => (
                <button key={i} onClick={() => sendMessage(s.replace(/^"|"$/g, ''))}
                  style={{
                    background: '#F5F3FF', border: '1px solid #E0E7FF', borderRadius: 8,
                    padding: '5px 12px', fontSize: 11, color: '#6366F1', cursor: 'pointer',
                    fontFamily: 'inherit', width: '100%', textAlign: 'left',
                  }}
                >{s}</button>
              ))}
            </div>
          </div>
        )}
        {messages.map((msg, i) => (
          <div key={i} style={{
            display: 'flex', flexDirection: 'column',
            alignItems: msg.role === 'user' ? 'flex-end' : 'flex-start',
          }}>
            <div style={{
              maxWidth: '92%', padding: '10px 14px',
              borderRadius: msg.role === 'user' ? '14px 14px 4px 14px' : '14px 14px 14px 4px',
              background: msg.role === 'user' ? '#2D2D2D' : s.bg,
              color: msg.role === 'user' ? 'white' : '#2D2D2D',
              fontSize: 12, lineHeight: 1.6, wordBreak: 'break-word',
            }}>
              <div style={{ whiteSpace: 'pre-wrap' }}>{msg.content}</div>
              {msg.ticket_id && (
                <div style={{
                  marginTop: 8, padding: '6px 10px', borderRadius: 6,
                  background: msg.role === 'user' ? 'rgba(255,255,255,0.1)' : 'rgba(255,255,255,0.6)',
                  fontSize: 10, color: msg.role === 'user' ? 'rgba(255,255,255,0.7)' : '#666',
                }}>
                  🎫 Ticket created: <strong>{msg.ticket_id}</strong>
                </div>
              )}
              {msg.delegation && (
                <div style={{
                  marginTop: 6, padding: '6px 10px', borderRadius: 6,
                  background: '#FEF3C7', fontSize: 10, color: '#92400E',
                }}>
                  🔄 Delegated to <strong>{msg.delegation.to_agent}</strong> ({msg.delegation.to_department})
                </div>
              )}
              {msg.brief?.length > 1 && (
                <details style={{ marginTop: 6 }}>
                  <summary style={{ fontSize: 10, color: '#888', cursor: 'pointer' }}>📋 View brief</summary>
                  <div style={{ fontSize: 10, color: '#666', marginTop: 4, lineHeight: 1.5 }}>
                    {msg.brief.map((b, j) => (
                      <div key={j}>
                        {b.action === 'task_created' && '📋 '}
                        {b.action === 'agent_response' && '💬 '}
                        {b.action === 'delegated' && '🔄 '}
                        {b.detail?.slice(0, 120)}
                      </div>
                    ))}
                  </div>
                </details>
              )}
            </div>
            <div style={{ fontSize: 9, color: '#ccc', marginTop: 2 }}>
              {formatTime(msg.timestamp)}
            </div>
          </div>
        ))}
        {loading && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '4px 0' }}>
            <div style={{ background: s.bg, borderRadius: 10, padding: '6px 10px', display: 'flex', gap: 3 }}>
              <span style={{ width: 6, height: 6, borderRadius: 3, background: s.color, animation: 'ab 1.2s infinite', display: 'inline-block' }} />
              <span style={{ width: 6, height: 6, borderRadius: 3, background: s.color, animation: 'ab 1.2s infinite 0.2s', display: 'inline-block' }} />
              <span style={{ width: 6, height: 6, borderRadius: 3, background: s.color, animation: 'ab 1.2s infinite 0.4s', display: 'inline-block' }} />
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <div style={{ padding: '10px 12px', borderTop: '1px solid #eee', display: 'flex', gap: 8 }}>
        <input
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={`Message ${agent.name}...`}
          style={{
            flex: 1, padding: '9px 12px', border: '1px solid #ddd', borderRadius: 10,
            fontSize: 12, outline: 'none', fontFamily: 'inherit',
          }}
        />
        <button
          onClick={() => sendMessage(input)}
          disabled={!input.trim() || loading}
          style={{
            width: 34, height: 34, borderRadius: 10, border: 'none',
            cursor: input.trim() && !loading ? 'pointer' : 'default',
            background: input.trim() && !loading ? s.color : '#eee',
            color: input.trim() && !loading ? 'white' : '#ccc',
            fontSize: 14, display: 'flex', alignItems: 'center', justifyContent: 'center',
            flexShrink: 0,
          }}
        >➤</button>
      </div>

      <style>{`
        @keyframes ab {
          0%, 80%, 100% { transform: scale(0.6); opacity: 0.4; }
          40% { transform: scale(1); opacity: 1; }
        }
      `}</style>
    </div>
  );
}
