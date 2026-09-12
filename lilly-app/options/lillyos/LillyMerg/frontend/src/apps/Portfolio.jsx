import React, { useState, useEffect } from 'react';

const API = import.meta.env.VITE_API_URL || '';

const TYPE_ICONS = {
  web_app: '🌐', shader: '✨', desktop: '🖥️', script: '📜', library: '📚', infra: '⚙️',
};

const TYPE_COLORS = {
  web_app: '#3B82F6', shader: '#8B5CF6', desktop: '#10B981', script: '#F59E0B', library: '#EC4899', infra: '#6366F1',
};

const FILTERS = ['all', 'web_app', 'shader', 'infra', 'desktop', 'script', 'library'];

export default function Portfolio({ onChat }) {
  const [projects, setProjects] = useState([]);
  const [filter, setFilter] = useState('all');
  const [selected, setSelected] = useState(null);
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(`${API}/api/portfolio`)
      .then(r => r.json())
      .then(data => { setProjects(data); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  const filtered = projects.filter(p => {
    if (filter !== 'all' && p.type !== filter) return false;
    if (search) {
      const q = search.toLowerCase();
      return p.name.toLowerCase().includes(q) ||
             p.description.toLowerCase().includes(q) ||
             (p.tech_stack || []).some(t => t.toLowerCase().includes(q));
    }
    return true;
  });

  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '60vh', color: '#999', fontSize: 14 }}>
        Loading portfolio...
      </div>
    );
  }

  return (
    <div style={{ padding: '24px 32px' }}>
      {/* Header */}
      <div style={{ marginBottom: 24 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: '0 0 4px 0', color: '#2D2D2D' }}>
          🏆 Portfolio
        </h1>
        <p style={{ fontSize: 13, color: '#888', margin: 0 }}>
          {projects.length} projects built, curated, and deployed on this server
        </p>
      </div>

      {/* Search + Filter */}
      <div style={{ display: 'flex', gap: 12, marginBottom: 20, alignItems: 'center', flexWrap: 'wrap' }}>
        <input
          value={search} onChange={e => setSearch(e.target.value)}
          placeholder="Search by name, tech, or description..."
          style={{
            flex: 1, minWidth: 200, padding: '9px 14px', borderRadius: 10,
            border: '1px solid #ddd', fontSize: 13, outline: 'none', fontFamily: 'inherit',
          }}
        />
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {FILTERS.map(f => (
            <button key={f} onClick={() => setFilter(f)}
              style={{
                padding: '6px 14px', borderRadius: 8, border: 'none',
                background: filter === f ? '#2D2D2D' : '#F0F0F0',
                color: filter === f ? 'white' : '#666',
                fontSize: 12, fontWeight: 500, cursor: 'pointer', fontFamily: 'inherit',
              }}
            >{f === 'all' ? 'All' : f.replace('_', ' ')}</button>
          ))}
        </div>
      </div>

      {/* Project Grid */}
      <div style={{
        display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))',
        gap: 16,
      }}>
        {filtered.map(p => (
          <ProjectCard key={p.id} project={p} onClick={() => setSelected(p)} />
        ))}
      </div>

      {filtered.length === 0 && (
        <div style={{ textAlign: 'center', padding: 60, color: '#999', fontSize: 13 }}>
          No projects match your search.
        </div>
      )}

      {/* Detail Modal */}
      {selected && (
        <ProjectDetail project={selected} onClose={() => setSelected(null)} onChat={onChat} />
      )}
    </div>
  );
}

function ProjectCard({ project, onClick }) {
  const base = TYPE_ICONS[project.type] || '📦';
  const color = TYPE_COLORS[project.type] || '#666';
  const isLive = project.status === 'live';

  return (
    <div onClick={onClick} style={{
      background: 'white', borderRadius: 14, border: '1px solid #eee',
      overflow: 'hidden', cursor: 'pointer',
      transition: 'all 0.15s',
      display: 'flex', flexDirection: 'column',
    }}
      onMouseEnter={e => e.currentTarget.style.borderColor = color + '60'}
      onMouseLeave={e => e.currentTarget.style.borderColor = '#eee'}
    >
      {/* Screenshot area */}
      <div style={{
        height: 160, background: '#F5F5F4', position: 'relative',
        overflow: 'hidden', display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}>
        <img
          src={project.screenshot ? `${API}${project.screenshot}` : null}
          alt={project.name}
          style={{ width: '100%', height: '100%', objectFit: 'cover' }}
          onError={e => { e.target.style.display = 'none'; }}
        />
        <div style={{
          position: 'absolute', top: 10, left: 10,
          padding: '3px 10px', borderRadius: 6,
          background: isLive ? 'rgba(16,185,129,0.9)' : 'rgba(148,163,184,0.9)',
          color: 'white', fontSize: 10, fontWeight: 600,
        }}>
          {isLive ? '● Live' : 'Archived'}
        </div>
        <div style={{
          position: 'absolute', bottom: 10, right: 10,
          width: 32, height: 32, borderRadius: 8,
          background: color + '20', display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 16,
        }}>{base}</div>
      </div>

      {/* Info */}
      <div style={{ padding: '14px 16px', flex: 1, display: 'flex', flexDirection: 'column' }}>
        <h3 style={{ fontSize: 14, fontWeight: 700, margin: '0 0 4px 0', color: '#2D2D2D' }}>{project.name}</h3>
        <p style={{ fontSize: 12, color: '#888', margin: '0 0 10px 0', lineHeight: 1.5, flex: 1 }}>
          {project.description.slice(0, 150)}{project.description.length > 150 ? '...' : ''}
        </p>
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
          {(project.tech_stack || []).slice(0, 5).map(t => (
            <span key={t} style={{
              padding: '2px 8px', borderRadius: 4, background: color + '15', color: color,
              fontSize: 10, fontWeight: 500,
            }}>{t}</span>
          ))}
          {(project.tech_stack || []).length > 5 && (
            <span style={{ fontSize: 10, color: '#999', padding: '2px 4px' }}>
              +{project.tech_stack.length - 5}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

function ProjectDetail({ project, onClose, onChat }) {
  const base = TYPE_ICONS[project.type] || '📦';
  const color = TYPE_COLORS[project.type] || '#666';

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 1000,
      background: 'rgba(0,0,0,0.4)', display: 'flex',
      alignItems: 'center', justifyContent: 'center',
      padding: 20,
    }} onClick={onClose}>
      <div style={{
        background: 'white', borderRadius: 18, maxWidth: 640, width: '100%',
        maxHeight: '90vh', overflow: 'auto', padding: 28,
      }} onClick={e => e.stopPropagation()}>
        {/* Screenshot */}
        {project.screenshot && (
          <div style={{
            borderRadius: 12, overflow: 'hidden', marginBottom: 20,
            background: '#F5F5F4', height: 200, display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            <img src={`${API}${project.screenshot}`} alt={project.name}
              style={{ width: '100%', height: '100%', objectFit: 'cover' }}
              onError={e => { e.target.style.display = 'none'; }}
            />
          </div>
        )}

        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, marginBottom: 12 }}>
          <div style={{
            width: 40, height: 40, borderRadius: 10,
            background: color + '20', display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 20, flexShrink: 0,
          }}>{base}</div>
          <div style={{ flex: 1 }}>
            <h2 style={{ fontSize: 18, fontWeight: 700, margin: 0, color: '#2D2D2D' }}>{project.name}</h2>
            <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
              <span style={{
                padding: '2px 10px', borderRadius: 6,
                background: project.status === 'live' ? '#DCFCE7' : '#F0F0F0',
                color: project.status === 'live' ? '#10B981' : '#999',
                fontSize: 11, fontWeight: 600,
              }}>
                {project.status === 'live' ? '● Live' : 'Archived'}
              </span>
              <span style={{ fontSize: 11, color: '#999', padding: '2px 0' }}>
                {project.type.replace('_', ' ')}
              </span>
              {project.created && (
                <span style={{ fontSize: 11, color: '#999', padding: '2px 0' }}>
                  {project.created}
                </span>
              )}
            </div>
          </div>
          <button onClick={onClose} style={{
            background: 'none', border: 'none', fontSize: 20, cursor: 'pointer',
            color: '#999', padding: 4, lineHeight: 1,
          }}>✕</button>
        </div>

        <p style={{ fontSize: 13, color: '#555', lineHeight: 1.7, margin: '0 0 16px 0' }}>
          {project.description}
        </p>

        {/* Tech Stack */}
        <div style={{ marginBottom: 16 }}>
          <h4 style={{ fontSize: 11, fontWeight: 600, color: '#999', textTransform: 'uppercase', margin: '0 0 8px 0', letterSpacing: '0.5px' }}>
            Tech Stack
          </h4>
          <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
            {(project.tech_stack || []).map(t => (
              <span key={t} style={{
                padding: '4px 10px', borderRadius: 6, background: color + '12', color: color,
                fontSize: 11, fontWeight: 500,
              }}>{t}</span>
            ))}
          </div>
        </div>

        {/* Features */}
        {(project.features || []).length > 0 && (
          <div style={{ marginBottom: 16 }}>
            <h4 style={{ fontSize: 11, fontWeight: 600, color: '#999', textTransform: 'uppercase', margin: '0 0 8px 0', letterSpacing: '0.5px' }}>
              Features
            </h4>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              {project.features.map((f, i) => (
                <div key={i} style={{ fontSize: 12, color: '#555', display: 'flex', gap: 8 }}>
                  <span style={{ color: '#10B981' }}>✓</span> {f}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Achievements */}
        {(project.achievements || []).length > 0 && (
          <div style={{ marginBottom: 16, padding: 12, borderRadius: 10, background: '#FFFBEB', border: '1px solid #FDE68A' }}>
            <h4 style={{ fontSize: 11, fontWeight: 600, color: '#92400E', textTransform: 'uppercase', margin: '0 0 8px 0', letterSpacing: '0.5px' }}>
              🏅 Achievements
            </h4>
            {project.achievements.map((a, i) => (
              <div key={i} style={{ fontSize: 12, color: '#92400E', marginBottom: 4, lineHeight: 1.5 }}>
                ✦ {a}
              </div>
            ))}
          </div>
        )}

        {/* Links */}
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {project.live_url && (
            <a href={project.live_url} target="_blank" rel="noopener noreferrer"
              style={{
                padding: '8px 18px', borderRadius: 10, border: 'none',
                background: '#10B981', color: 'white', fontSize: 12, fontWeight: 600,
                textDecoration: 'none', fontFamily: 'inherit',
              }}
            >🌐 Open Live</a>
          )}
          {onChat && (
            <button onClick={() => { onChat(project); onClose(); }}
              style={{
                padding: '8px 18px', borderRadius: 10, border: 'none',
                background: '#6366F1', color: 'white', fontSize: 12, fontWeight: 600,
                cursor: 'pointer', fontFamily: 'inherit',
              }}
            >💬 Chat about this</button>
          )}
          {project.container_name && (
            <span style={{
              padding: '8px 14px', borderRadius: 10, background: '#F0F0F0',
              color: '#666', fontSize: 11, display: 'flex', alignItems: 'center', gap: 4,
            }}>
              🐳 {project.container_name}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
