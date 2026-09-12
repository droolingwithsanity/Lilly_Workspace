const ROLE_COLORS = {
  pm: '#8B5CF6',
  developer: '#3B82F6',
  devops: '#10B981',
};

const ROLE_LABELS = {
  pm: 'PM',
  developer: 'Dev',
  devops: 'Ops',
};

export default function TeamBoard({ agents = [] }) {
  return (
    <div style={{ background: 'white', borderRadius: 16, border: '1px solid #eee', padding: 24 }}>
      <h3 style={{ fontSize: 14, fontWeight: 700, color: '#2D2D2D', margin: '0 0 16px 0', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
        Team
      </h3>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {agents.map(a => (
          <div key={a.id} style={{
            display: 'flex',
            alignItems: 'center',
            gap: 12,
            padding: '10px 12px',
            background: a.busy ? '#F0F9FF' : '#FAFAFA',
            borderRadius: 10,
            border: '1px solid',
            borderColor: a.busy ? '#BAE6FD' : '#f0f0f0',
            transition: 'all 0.3s',
          }}>
            <div style={{
              width: 32,
              height: 32,
              borderRadius: 8,
              background: ROLE_COLORS[a.role] + '20',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: 10,
              fontWeight: 700,
              color: ROLE_COLORS[a.role],
              flexShrink: 0,
            }}>
              {ROLE_LABELS[a.role] || a.role}
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: '#2D2D2D' }}>{a.name}</div>
              <div style={{ fontSize: 11, color: '#999' }}>{a.busy ? 'Working...' : 'Available'}</div>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <div style={{ textAlign: 'right' }}>
                <div style={{ fontSize: 10, color: '#999' }}>Skill</div>
                <div style={{ fontSize: 13, fontWeight: 700, color: '#2D2D2D' }}>{Math.round(a.skill * 100)}</div>
              </div>
              <div style={{ textAlign: 'right' }}>
                <div style={{ fontSize: 10, color: '#999' }}>Energy</div>
                <div style={{
                  fontSize: 13,
                  fontWeight: 700,
                  color: a.energy > 50 ? '#10B981' : a.energy > 25 ? '#F59E0B' : '#EF4444',
                }}>{Math.round(a.energy)}%</div>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
