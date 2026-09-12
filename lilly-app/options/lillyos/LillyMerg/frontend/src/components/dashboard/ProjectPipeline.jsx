const STAGES = [
  { key: 'backlog', label: 'Backlog', color: '#94A3B8' },
  { key: 'in_progress', label: 'In Progress', color: '#3B82F6' },
  { key: 'review', label: 'Review', color: '#F59E0B' },
  { key: 'staging', label: 'Staging', color: '#8B5CF6' },
  { key: 'deployed', label: 'Deployed', color: '#10B981' },
];

export default function ProjectPipeline({ projects = [] }) {
  const grouped = {};
  for (const stage of STAGES) {
    grouped[stage.key] = projects.filter(p => p.status === stage.key);
  }

  return (
    <div style={{ background: 'white', borderRadius: 16, border: '1px solid #eee', padding: 24 }}>
      <h3 style={{ fontSize: 14, fontWeight: 700, color: '#2D2D2D', margin: '0 0 20px 0', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
        Pipeline
      </h3>
      <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
        {STAGES.map(stage => {
          const items = grouped[stage.key] || [];
          return (
            <div key={stage.key} style={{ flex: 1, minWidth: 0 }}>
              <div style={{
                background: stage.color + '18',
                borderRadius: 8,
                padding: '6px 10px',
                marginBottom: 8,
                textAlign: 'center',
              }}>
                <span style={{ fontSize: 11, fontWeight: 700, color: stage.color }}>{stage.label}</span>
                <span style={{
                  background: stage.color,
                  color: 'white',
                  borderRadius: 10,
                  padding: '1px 7px',
                  fontSize: 10,
                  fontWeight: 700,
                  marginLeft: 6,
                }}>{items.length}</span>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {items.map(p => (
                  <div key={p.id} style={{
                    background: '#FAFAFA',
                    borderRadius: 8,
                    padding: '8px 10px',
                    border: '1px solid #f0f0f0',
                    fontSize: 11,
                  }}>
                    <div style={{ fontWeight: 600, color: '#2D2D2D', marginBottom: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {p.name}
                    </div>
                    {p.progress !== undefined && p.progress < 1 && (
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 4 }}>
                        <div style={{ flex: 1, height: 4, background: '#eee', borderRadius: 2 }}>
                          <div style={{
                            width: `${Math.round(p.progress * 100)}%`,
                            height: 4,
                            background: stage.color,
                            borderRadius: 2,
                            transition: 'width 0.5s',
                          }} />
                        </div>
                        <span style={{ fontSize: 10, color: '#999', fontWeight: 600 }}>{Math.round(p.progress * 100)}%</span>
                      </div>
                    )}
                    <div style={{ fontSize: 10, color: '#aaa', marginTop: 4 }}>
                      ${p.value?.toLocaleString()}
                    </div>
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
