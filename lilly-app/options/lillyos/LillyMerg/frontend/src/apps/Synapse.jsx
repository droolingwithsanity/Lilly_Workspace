import React, { useState, useEffect, useRef } from 'react';

const API = import.meta.env.VITE_API_URL || (typeof window !== 'undefined' ? window.location.origin : '');

const HEALTH_COLORS = {
  healthy: { bg: '#D1FAE5', text: '#065F46', dot: '#10B981', glow: 'rgba(16,185,129,0.3)' },
  warning: { bg: '#FEF3C7', text: '#92400E', dot: '#F59E0B', glow: 'rgba(245,158,11,0.3)' },
  critical: { bg: '#FEE2E2', text: '#991B1B', dot: '#EF4444', glow: 'rgba(239,68,68,0.3)' },
};

const SEVERITY_COLORS = {
  critical: { bg: '#FEE2E2', text: '#991B1B', dot: '#EF4444', icon: '🚨' },
  high: { bg: '#FFF7ED', text: '#9A3412', dot: '#F97316', icon: '⚠️' },
  medium: { bg: '#FEF3C7', text: '#92400E', dot: '#F59E0B', icon: '⚡' },
  low: { bg: '#EFF6FF', text: '#1E40AF', dot: '#3B82F6', icon: 'ℹ️' },
};

function ConnectionGraph({ connections, departments }) {
  const svgRef = useRef(null);
  const [dimensions, setDimensions] = useState({ w: 600, h: 300 });

  useEffect(() => {
    const update = () => {
      if (svgRef.current?.parentElement) {
        setDimensions({ w: svgRef.current.parentElement.clientWidth, h: 280 });
      }
    };
    update();
    window.addEventListener('resize', update);
    return () => window.removeEventListener('resize', update);
  }, []);

  const { w, h } = dimensions;
  const cx = w / 2;
  const cy = h / 2;
  const radius = Math.min(w, h) * 0.3;

  const deptPositions = {};
  departments.forEach((d, i) => {
    const angle = (i / departments.length) * Math.PI * 2 - Math.PI / 2;
    deptPositions[d.id] = {
      x: cx + radius * Math.cos(angle),
      y: cy + radius * Math.sin(angle),
    };
  });

  if (departments.length === 0) return null;

  return (
    <svg ref={svgRef} width={w} height={h} style={{ display: 'block' }}>
      <defs>
        {departments.map(d => (
          <radialGradient key={d.id} id={`glow-${d.id}`}>
            <stop offset="0%" stopColor={HEALTH_COLORS[d.health]?.glow || 'rgba(99,102,241,0.2)'} />
            <stop offset="100%" stopColor="transparent" />
          </radialGradient>
        ))}
      </defs>

      {/* Connection lines */}
      {connections.map((c, i) => {
        const src = deptPositions[c.source];
        const tgt = deptPositions[c.target];
        if (!src || !tgt) return null;
        return (
          <line key={i}
            x1={src.x} y1={src.y} x2={tgt.x} y2={tgt.y}
            stroke="#C4B5FD" strokeWidth={1.5} strokeDasharray="4,3"
            opacity={0.6}
          />
        );
      })}

      {/* Department nodes */}
      {departments.map(d => {
        const pos = deptPositions[d.id];
        if (!pos) return null;
        const hc = HEALTH_COLORS[d.health] || HEALTH_COLORS.healthy;
        return (
          <g key={d.id}>
            <circle cx={pos.x} cy={pos.y} r={32}
              fill={`url(#glow-${d.id})`} opacity={0.5}
            />
            <circle cx={pos.x} cy={pos.y} r={22}
              fill="white" stroke={hc.dot} strokeWidth={2}
            />
            <text x={pos.x} y={pos.y + 1} textAnchor="middle"
              dominantBaseline="central" fontSize={16}>
              {d.icon || '📁'}
            </text>
            <text x={pos.x} y={pos.y + 36} textAnchor="middle"
              fontSize={10} fill="#666" fontWeight={500}>
              {d.name.length > 12 ? d.name.slice(0, 10) + '…' : d.name}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

function DepartmentCard({ dept }) {
  const hc = HEALTH_COLORS[dept.health] || HEALTH_COLORS.healthy;
  return (
    <div style={{
      background: 'white', borderRadius: 14, border: `1px solid ${hc.dot}30`,
      padding: 16, position: 'relative', overflow: 'hidden',
      boxShadow: `0 2px 8px ${hc.glow}`,
    }}>
      <div style={{
        position: 'absolute', top: 0, right: 0, width: 80, height: 80,
        borderRadius: '0 0 0 80', background: hc.dot + '08',
      }} />
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
        <div style={{
          width: 36, height: 36, borderRadius: 10,
          background: dept.color + '20', display: 'flex',
          alignItems: 'center', justifyContent: 'center', fontSize: 18,
        }}>{dept.icon || '📁'}</div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: '#2D2D2D' }}>{dept.name}</div>
          <div style={{ fontSize: 10, color: '#999' }}>{dept.description?.slice(0, 40) || ''}</div>
        </div>
        <div style={{
          width: 10, height: 10, borderRadius: 5, flexShrink: 0,
          background: hc.dot, boxShadow: `0 0 8px ${hc.glow}`,
        }} />
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
        <Metric label="Open" value={dept.tickets.open} color="#3B82F6" />
        <Metric label="Critical" value={dept.tickets.critical} color="#EF4444" />
        <Metric label="Agents" value={dept.agents.total} color="#8B5CF6" />
        <Metric label="Success" value={`${dept.agents.avg_success}%`} color={dept.agents.avg_success > 70 ? '#10B981' : '#F59E0B'} />
      </div>
    </div>
  );
}

function Metric({ label, value, color }) {
  return (
    <div style={{ textAlign: 'center', padding: '6px 8px', background: '#FAFAFA', borderRadius: 8 }}>
      <div style={{ fontSize: 16, fontWeight: 700, color }}>{value}</div>
      <div style={{ fontSize: 9, color: '#999', textTransform: 'uppercase', letterSpacing: '0.3px' }}>{label}</div>
    </div>
  );
}

export default function Synapse() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [filter, setFilter] = useState('all');
  const [insightIndex, setInsightIndex] = useState(0);

  const fetchData = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API}/api/apps/synapse/dashboard`);
      // Note: backend mounts at /api/apps/synapse via router.include_router
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const d = await res.json();
      setData(d);
    } catch (e) {
      setError(e.message);
    }
    setLoading(false);
  };

  useEffect(() => { fetchData(); }, []);

  useEffect(() => {
    if (!data?.insights?.length) return;
    const timer = setInterval(() => {
      setInsightIndex(i => (i + 1) % data.insights.length);
    }, 6000);
    return () => clearInterval(timer);
  }, [data?.insights?.length]);

  const filteredDepts = data?.departments?.filter(d => {
    if (filter === 'healthy') return d.health === 'healthy';
    if (filter === 'warning') return d.health === 'warning';
    if (filter === 'critical') return d.health === 'critical';
    return true;
  }) || [];

  if (loading && !data) {
    return (
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        padding: 80, color: '#999', fontSize: 14,
      }}>
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: 32, marginBottom: 12, opacity: 0.5 }}>🧠</div>
          <div>Analyzing organizational intelligence...</div>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div style={{
        background: '#FEE2E2', color: '#991B1B', padding: '20px 24px',
        borderRadius: 12, margin: 24, fontSize: 13, textAlign: 'center',
      }}>
        Failed to load Synapse: {error}
        <button onClick={fetchData} style={{
          display: 'block', margin: '12px auto 0', padding: '8px 20px',
          background: '#991B1B', color: 'white', border: 'none', borderRadius: 8,
          cursor: 'pointer', fontSize: 12, fontFamily: 'inherit',
        }}>Retry</button>
      </div>
    );
  }

  return (
    <div style={{ padding: '24px 32px' }}>
      {/* Header */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div style={{
            width: 36, height: 36, borderRadius: 10,
            background: 'linear-gradient(135deg, #8B5CF6, #6366F1)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            color: 'white', fontWeight: 700, fontSize: 16,
          }}>S</div>
          <div>
            <h1 style={{ fontSize: 20, fontWeight: 700, margin: 0, color: '#2D2D2D' }}>
              Synapse
            </h1>
            <span style={{ fontSize: 12, color: '#999' }}>
              Organizational Intelligence Mapper
            </span>
          </div>
        </div>
        <button onClick={fetchData} style={{
          padding: '8px 16px', background: 'white', border: '1px solid #ddd',
          borderRadius: 8, fontSize: 12, cursor: 'pointer', color: '#666',
          fontFamily: 'inherit', display: 'flex', alignItems: 'center', gap: 6,
        }}>
          🔄 Refresh
        </button>
      </div>

      {/* Summary bar */}
      {data?.total && (
        <div style={{
          display: 'flex', gap: 12, marginBottom: 20, flexWrap: 'wrap',
        }}>
          {[
            { label: 'Departments', value: data.total.departments, color: '#8B5CF6' },
            { label: 'Open Tickets', value: data.total.open_tickets, color: '#3B82F6' },
            { label: 'Critical', value: data.total.critical_tickets, color: '#EF4444' },
            { label: 'Overdue', value: data.total.overdue_tickets, color: '#F97316' },
            { label: 'Agents', value: data.total.total_agents, color: '#10B981' },
            { label: 'Connections', value: data.total.connections, color: '#8B5CF6' },
          ].map((s, i) => (
            <div key={i} style={{
              flex: 1, minWidth: 100, background: 'white', borderRadius: 12,
              border: '1px solid #eee', padding: '12px 16px', textAlign: 'center',
            }}>
              <div style={{ fontSize: 22, fontWeight: 700, color: s.color }}>{s.value}</div>
              <div style={{ fontSize: 10, color: '#999', textTransform: 'uppercase', letterSpacing: '0.3px', marginTop: 2 }}>{s.label}</div>
            </div>
          ))}
        </div>
      )}

      {/* Insights carousel */}
      {data?.insights?.length > 0 && (
        <div style={{
          background: 'linear-gradient(135deg, #F5F3FF 0%, #EDE9FE 100%)',
          borderRadius: 14, padding: '14px 20px', marginBottom: 20,
          border: '1px solid #DDD6FE',
          minHeight: 48, display: 'flex', alignItems: 'center',
        }}>
          <span style={{ fontSize: 16, marginRight: 10, flexShrink: 0 }}>💡</span>
          <span style={{
            fontSize: 13, color: '#5B21B6', lineHeight: 1.5,
            animation: 'fadeIn 0.4s ease',
          }} key={insightIndex}>
            {data.insights[insightIndex]}
          </span>
        </div>
      )}

      {/* Connection map */}
      {data?.departments?.length > 0 && (
        <div style={{
          background: 'white', borderRadius: 16, border: '1px solid #eee',
          padding: 20, marginBottom: 20,
        }}>
          <h3 style={{
            fontSize: 13, fontWeight: 700, margin: '0 0 12px 0',
            textTransform: 'uppercase', letterSpacing: '0.5px', color: '#2D2D2D',
          }}>
            Department Network
            <span style={{
              fontSize: 10, color: '#999', fontWeight: 400, marginLeft: 8, textTransform: 'none',
            }}>
              ({data.connections.length} connections)
            </span>
          </h3>
          <ConnectionGraph connections={data.connections} departments={data.departments} />
        </div>
      )}

      {/* Risks */}
      {data?.risks?.length > 0 && (
        <div style={{
          background: 'white', borderRadius: 16, border: '1px solid #eee',
          padding: 20, marginBottom: 20,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
            <h3 style={{
              fontSize: 13, fontWeight: 700, margin: 0,
              textTransform: 'uppercase', letterSpacing: '0.5px', color: '#2D2D2D',
            }}>
              Risk Alerts
            </h3>
            <span style={{
              fontSize: 10, padding: '2px 8px', borderRadius: 8,
              background: '#FEE2E2', color: '#EF4444', fontWeight: 600,
            }}>
              {data.risks.length}
            </span>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {data.risks.map((r, i) => {
              const sc = SEVERITY_COLORS[r.severity] || SEVERITY_COLORS.low;
              return (
                <div key={i} style={{
                  display: 'flex', alignItems: 'center', gap: 10,
                  padding: '10px 14px', background: sc.bg, borderRadius: 10,
                  borderLeft: `3px solid ${sc.dot}`,
                }}>
                  <span style={{ fontSize: 16 }}>{sc.icon}</span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <span style={{ fontSize: 11, fontWeight: 700, color: sc.text, textTransform: 'uppercase' }}>
                      {r.department}
                    </span>
                    <div style={{ fontSize: 12, color: '#555', marginTop: 2 }}>{r.message}</div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Filter tabs */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 14 }}>
        {[
          { key: 'all', label: `All (${data?.departments?.length || 0})` },
          { key: 'healthy', label: `Healthy (${data?.departments?.filter(d => d.health === 'healthy').length || 0})` },
          { key: 'warning', label: `Warning (${data?.departments?.filter(d => d.health === 'warning').length || 0})` },
          { key: 'critical', label: `Critical (${data?.departments?.filter(d => d.health === 'critical').length || 0})` },
        ].map(f => (
          <button key={f.key} onClick={() => setFilter(f.key)}
            style={{
              padding: '6px 14px', borderRadius: 8, border: '1px solid #ddd',
              background: filter === f.key ? '#2D2D2D' : 'white',
              color: filter === f.key ? 'white' : '#666',
              fontSize: 11, fontWeight: 500, cursor: 'pointer',
              fontFamily: 'inherit', transition: 'all 0.15s',
            }}
          >{f.label}</button>
        ))}
      </div>

      {/* Department cards grid */}
      <div style={{
        display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))',
        gap: 14, marginBottom: 24,
      }}>
        {filteredDepts.map(d => (
          <DepartmentCard key={d.id} dept={d} />
        ))}
      </div>

      <style>{`
        @keyframes fadeIn {
          from { opacity: 0; transform: translateY(4px); }
          to { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </div>
  );
}
