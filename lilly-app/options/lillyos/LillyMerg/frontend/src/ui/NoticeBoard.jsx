const PRIORITY_COLORS = {
  critical: { dot: '#EF4444', bg: '#FEF2F2' },
  high: { dot: '#F97316', bg: '#FFF7ED' },
  normal: { dot: '#3B82F6', bg: '#EFF6FF' },
  low: { dot: '#94A3B8', bg: '#F8FAFC' },
};

const TYPE_ICONS = {
  sprint_start: 'S',
  sprint_complete: 'S',
  deployment: 'D',
  sla_breach: '!',
  milestone: 'M',
  design_review: 'D',
  ux_review: 'U',
  agent_status: 'A',
  team_update: 'T',
};

export default function NoticeBoard({ notices = [] }) {
  if (notices.length === 0) return null;

  return (
    <div style={{
      background: 'white',
      borderRadius: 16,
      border: '1px solid #eee',
      padding: 20,
      marginBottom: 20,
    }}>
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        marginBottom: 14,
      }}>
        <h3 style={{
          fontSize: 14,
          fontWeight: 700,
          margin: 0,
          textTransform: 'uppercase',
          letterSpacing: '0.5px',
          color: '#2D2D2D',
        }}>
          Notice Board
        </h3>
        <span style={{
          fontSize: 11,
          color: '#999',
          background: '#F5F5F4',
          padding: '2px 8px',
          borderRadius: 8,
        }}>
          {notices.length} updates
        </span>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {notices.map(n => {
          const pc = PRIORITY_COLORS[n.priority] || PRIORITY_COLORS.normal;
          const icon = TYPE_ICONS[n.type] || '•';
          return (
            <div key={n.id} style={{
              display: 'flex',
              gap: 10,
              padding: '10px 12px',
              background: pc.bg,
              borderRadius: 10,
              borderLeft: `3px solid ${pc.dot}`,
            }}>
              <div style={{
                width: 24,
                height: 24,
                borderRadius: 6,
                background: pc.dot + '20',
                color: pc.dot,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: 11,
                fontWeight: 700,
                flexShrink: 0,
              }}>
                {icon}
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: '#2D2D2D' }}>
                  {n.title}
                </div>
                <div style={{ fontSize: 12, color: '#666', marginTop: 2, lineHeight: 1.4 }}>
                  {n.message}
                </div>
                <div style={{ fontSize: 10, color: '#999', marginTop: 4 }}>
                  {new Date(n.created_at * 1000).toLocaleTimeString()}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
