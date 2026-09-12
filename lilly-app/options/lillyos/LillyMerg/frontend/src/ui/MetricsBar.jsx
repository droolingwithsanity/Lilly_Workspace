export default function MetricsBar({ summary }) {
  if (!summary) return null;

  const metrics = [
    { label: 'Tickets', value: summary.total_tickets, color: '#3B82F6' },
    { label: 'Active', value: summary.active_tickets, color: '#F59E0B' },
    { label: 'Overdue', value: summary.overdue_tickets, color: '#EF4444' },
    { label: 'SLA Breaches', value: summary.sla_breaches, color: '#DC2626' },
    { label: 'Active Sprints', value: summary.sprints_active, color: '#8B5CF6' },
    { label: 'Agents Busy', value: `${summary.agents_busy}/${summary.agents_total}`, color: '#10B981' },
    { label: 'Pipeline Runs', value: summary.pipelines_running, color: '#06B6D4' },
    { label: 'Live Deployments', value: summary.deployments_live, color: '#059669' },
  ];

  return (
    <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 24 }}>
      {metrics.map(m => (
        <div key={m.label} style={{
          background: 'white',
          borderRadius: 12,
          padding: '14px 18px',
          border: '1px solid #eee',
          boxShadow: '0 1px 2px rgba(0,0,0,0.03)',
          flex: 1,
          minWidth: 120,
        }}>
          <div style={{ fontSize: 11, fontWeight: 600, color: '#999', textTransform: 'uppercase', letterSpacing: '0.3px', marginBottom: 4 }}>
            {m.label}
          </div>
          <div style={{ fontSize: 24, fontWeight: 700, color: m.color }}>
            {m.value}
          </div>
        </div>
      ))}
    </div>
  );
}
