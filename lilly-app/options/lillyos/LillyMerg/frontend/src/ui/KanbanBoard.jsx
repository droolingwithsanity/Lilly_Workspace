const COLUMNS = [
  { key: 'backlog', label: 'Backlog', color: '#94A3B8' },
  { key: 'groomed', label: 'Groomed', color: '#64748B' },
  { key: 'sprint_ready', label: 'Sprint Ready', color: '#6366F1' },
  { key: 'in_sprint', label: 'In Sprint', color: '#8B5CF6' },
  { key: 'in_progress', label: 'In Progress', color: '#3B82F6' },
  { key: 'in_review', label: 'Review', color: '#F59E0B' },
  { key: 'done', label: 'Done', color: '#10B981' },
];

const PRIORITY_COLORS = {
  critical: '#EF4444',
  high: '#F97316',
  medium: '#EAB308',
  low: '#94A3B8',
};

export default function KanbanBoard({ tickets = [], onTransition }) {
  const grouped = {};
  for (const col of COLUMNS) {
    grouped[col.key] = tickets.filter(t => t.status === col.key);
  }

  return (
    <div style={{ background: 'white', borderRadius: 16, border: '1px solid #eee', padding: 20 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
        <h3 style={{ fontSize: 14, fontWeight: 700, margin: 0, textTransform: 'uppercase', letterSpacing: '0.5px', color: '#2D2D2D' }}>
          Board
        </h3>
      </div>
      <div style={{ display: 'flex', gap: 8, overflow: 'auto', paddingBottom: 8 }}>
        {COLUMNS.map(col => {
          const items = grouped[col.key] || [];
          return (
            <div key={col.key} style={{ flex: '0 0 200px', minWidth: 200 }}>
              <div style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '6px 10px',
                marginBottom: 8,
                background: col.color + '15',
                borderRadius: 8,
              }}>
                <span style={{ fontSize: 12, fontWeight: 700, color: col.color }}>{col.label}</span>
                <span style={{
                  background: col.color,
                  color: 'white',
                  borderRadius: 10,
                  padding: '0 7px',
                  fontSize: 11,
                  fontWeight: 700,
                }}>{items.length}</span>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6, maxHeight: 500, overflow: 'auto' }}>
                {items.map(t => (
                  <div key={t.id} style={{
                    background: '#FAFAFA',
                    border: '1px solid #f0f0f0',
                    borderRadius: 10,
                    padding: 12,
                    cursor: 'pointer',
                  }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
                      <span style={{
                        width: 8,
                        height: 8,
                        borderRadius: 4,
                        background: PRIORITY_COLORS[t.priority] || '#94A3B8',
                        flexShrink: 0,
                      }} />
                      <span style={{ fontSize: 12, fontWeight: 600, color: '#2D2D2D', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {t.title}
                      </span>
                    </div>
                    <div style={{ display: 'flex', gap: 8, fontSize: 10, color: '#999' }}>
                      {t.story_points && <span>{t.story_points}pts</span>}
                      {t.assignee && <span>{t.assignee}</span>}
                      {t.type && <span>{t.type}</span>}
                    </div>
                    {col.key !== 'done' && col.key !== 'cancelled' && onTransition && (
                      <div style={{ marginTop: 8, display: 'flex', gap: 4 }}>
                        {(() => {
                          const next = {
                            backlog: 'groomed',
                            groomed: 'sprint_ready',
                            sprint_ready: 'in_sprint',
                            in_sprint: 'in_progress',
                            in_progress: 'in_review',
                            in_review: 'done',
                          }[t.status];
                          return next ? (
                            <button
                              onClick={() => onTransition(t.id, next)}
                              style={{
                                padding: '4px 10px',
                                fontSize: 10,
                                fontWeight: 600,
                                border: 'none',
                                borderRadius: 6,
                                background: col.color + '20',
                                color: col.color,
                                cursor: 'pointer',
                              }}
                            >
                              Move → {COLUMNS.find(c => c.key === next)?.label}
                            </button>
                          ) : null;
                        })()}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
