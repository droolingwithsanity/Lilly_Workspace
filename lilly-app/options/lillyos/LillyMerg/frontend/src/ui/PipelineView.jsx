export default function PipelineView({ deployments = [] }) {
  return (
    <div style={{ background: 'white', borderRadius: 16, border: '1px solid #eee', padding: 20 }}>
      <h3 style={{ fontSize: 14, fontWeight: 700, margin: '0 0 16px 0', textTransform: 'uppercase', letterSpacing: '0.5px', color: '#2D2D2D' }}>
        Deployments
      </h3>
      {deployments.length === 0 ? (
        <div style={{ fontSize: 13, color: '#999', textAlign: 'center', padding: 20 }}>
          No deployments yet
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {deployments.map(d => (
            <div key={d.id} style={{
              display: 'flex',
              alignItems: 'center',
              gap: 12,
              padding: '10px 12px',
              background: '#FAFAFA',
              borderRadius: 10,
              border: '1px solid #f0f0f0',
            }}>
              <div style={{
                width: 8,
                height: 8,
                borderRadius: 4,
                background: d.status === 'live' ? '#10B981' : d.status === 'failed' ? '#EF4444' : '#F59E0B',
                flexShrink: 0,
              }} />
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: '#2D2D2D' }}>
                  {d.version}
                  <span style={{
                    fontSize: 10,
                    fontWeight: 600,
                    marginLeft: 8,
                    padding: '1px 6px',
                    borderRadius: 4,
                    background: d.status === 'live' ? '#DCFCE7' : '#FEF3C7',
                    color: d.status === 'live' ? '#059669' : '#92400E',
                    textTransform: 'uppercase',
                  }}>
                    {d.environment || 'dev'}
                  </span>
                </div>
                <div style={{ fontSize: 11, color: '#999' }}>
                  {d.status} {d.uptime_check ? '✓ uptime ok' : ''}
                </div>
              </div>
              <div style={{ fontSize: 11, color: '#999', textAlign: 'right' }}>
                <div>{d.deployed_at ? new Date(d.deployed_at * 1000).toLocaleTimeString() : '—'}</div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
