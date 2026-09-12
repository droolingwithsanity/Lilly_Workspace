import React, { useState, useRef, useEffect, useCallback } from 'react';

const API = import.meta.env.VITE_API_URL || (typeof window !== 'undefined' ? window.location.origin : '');

const DEFAULT_THEME = {
  colors: { primary: '#3B82F6', secondary: '#8B5CF6', accent: '#10B981', background: '#F5F5F4', text: '#2D2D2D', danger: '#EF4444', warning: '#F59E0B' },
  spacing: { xs: '4px', sm: '8px', md: '16px', lg: '24px', xl: '32px' },
  radii: { sm: '8px', md: '12px', lg: '16px', xl: '24px' },
  typography: { fontFamily: 'system-ui, -apple-system, sans-serif', fontMono: 'SFMono-Regular, monospace', h1: '24px', h2: '20px', h3: '16px', body: '14px', small: '12px' },
};

const COMPONENT_PALETTE = [
  { type: 'button', label: 'Button', icon: '▣' },
  { type: 'card', label: 'Card', icon: '▢' },
  { type: 'input', label: 'Input', icon: '⌨' },
  { type: 'badge', label: 'Badge', icon: '⬡' },
  { type: 'alert', label: 'Alert', icon: '⚠' },
  { type: 'metric', label: 'Metric Card', icon: '📊' },
  { type: 'kanban', label: 'Kanban Col', icon: '⊞' },
  { type: 'hero', label: 'Hero', icon: '★' },
];

const PLACEHOLDER_IMG = 'data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iNDAwIiBoZWlnaHQ9IjMwMCIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48cmVjdCB3aWR0aD0iNDAwIiBoZWlnaHQ9IjMwMCIgZmlsbD0iI2VlZSIvPjx0ZXh0IHg9IjIwMCIgeT0iMTUwIiB0ZXh0LWFuY2hvcj0ibWlkZGxlIiBkeT0iLjNlbSIgZm9udC1mYW1pbHk9InNhbnMtc2VyaWYiIGZvbnQtc2l6ZT0iMTgiIGZpbGw9IiM5OTkiPkxpdmUgUHJldmlldzwvdGV4dD48L3N2Zz4=';

function renderComponent(comp, theme, selected, onClick) {
  const c = theme.colors;
  const s = theme.spacing;
  const r = theme.radii;
  const t = theme.typography;
  const sel = selected === comp.id;
  const border = sel ? `2px solid ${c.primary}` : '1px solid #eee';

  switch (comp.type) {
    case 'button':
      return (
        <div key={comp.id} onClick={() => onClick(comp.id)} style={{ padding: s.xs, border, borderRadius: r.sm, cursor: 'pointer', margin: s.xs }}>
          <button style={{ background: c.primary, color: 'white', border: 'none', borderRadius: r.sm, padding: `${s.sm} ${s.md}`, fontSize: t.body, cursor: 'pointer', fontFamily: t.fontFamily }}>
            {comp.props?.text || 'Button'}
          </button>
        </div>
      );
    case 'card':
      return (
        <div key={comp.id} onClick={() => onClick(comp.id)} style={{ padding: s.xs, border, borderRadius: r.md, cursor: 'pointer', margin: s.xs }}>
          <div style={{ background: 'white', borderRadius: r.md, border: '1px solid #eee', padding: s.md, boxShadow: '0 1px 3px rgba(0,0,0,0.04)' }}>
            <div style={{ fontSize: t.h3, fontWeight: 700, color: c.text, marginBottom: s.sm }}>{comp.props?.title || 'Card Title'}</div>
            <div style={{ fontSize: t.body, color: '#666' }}>{comp.props?.body || 'Card content goes here. This is a preview of the card component.'}</div>
          </div>
        </div>
      );
    case 'input':
      return (
        <div key={comp.id} onClick={() => onClick(comp.id)} style={{ padding: s.xs, border, borderRadius: r.sm, cursor: 'pointer', margin: s.xs }}>
          <div style={{ fontSize: t.small, color: '#666', marginBottom: s.xs, fontWeight: 600 }}>{comp.props?.label || 'Label'}</div>
          <input placeholder={comp.props?.placeholder || 'Enter text...'} style={{ width: '100%', padding: s.sm, border: '1px solid #ddd', borderRadius: r.sm, fontSize: t.body, outline: 'none', boxSizing: 'border-box', fontFamily: t.fontFamily }} />
        </div>
      );
    case 'badge':
      return (
        <div key={comp.id} onClick={() => onClick(comp.id)} style={{ padding: s.xs, border, borderRadius: r.sm, cursor: 'pointer', margin: s.xs }}>
          <span style={{ background: c.secondary + '20', color: c.secondary, padding: `2px ${s.sm}`, borderRadius: '10px', fontSize: t.small, fontWeight: 600 }}>
            {comp.props?.text || 'Badge'}
          </span>
        </div>
      );
    case 'alert':
      return (
        <div key={comp.id} onClick={() => onClick(comp.id)} style={{ padding: s.xs, border, borderRadius: r.sm, cursor: 'pointer', margin: s.xs }}>
          <div style={{ background: comp.props?.variant === 'error' ? '#FEE2E2' : '#FEF3C7', borderRadius: r.sm, padding: s.sm, border: `1px solid ${comp.props?.variant === 'error' ? '#FCA5A5' : '#FCD34D'}` }}>
            <div style={{ fontSize: t.small, fontWeight: 700, color: comp.props?.variant === 'error' ? '#991B1B' : '#92400E', marginBottom: 2 }}>{comp.props?.title || 'Alert'}</div>
            <div style={{ fontSize: t.small, color: comp.props?.variant === 'error' ? '#991B1B' : '#92400E' }}>{comp.props?.body || 'This is an alert message.'}</div>
          </div>
        </div>
      );
    case 'metric':
      return (
        <div key={comp.id} onClick={() => onClick(comp.id)} style={{ padding: s.xs, border, borderRadius: r.md, cursor: 'pointer', margin: s.xs }}>
          <div style={{ background: 'white', borderRadius: r.md, border: '1px solid #eee', padding: s.md, boxShadow: '0 1px 2px rgba(0,0,0,0.03)' }}>
            <div style={{ fontSize: t.small, fontWeight: 600, color: '#999', textTransform: 'uppercase', letterSpacing: '0.3px' }}>{comp.props?.label || 'Metric'}</div>
            <div style={{ fontSize: t.h1, fontWeight: 700, color: c.primary }}>{comp.props?.value || '42'}</div>
          </div>
        </div>
      );
    case 'kanban':
      return (
        <div key={comp.id} onClick={() => onClick(comp.id)} style={{ padding: s.xs, border, borderRadius: r.md, cursor: 'pointer', margin: s.xs }}>
          <div style={{ flex: '0 0 180px', background: '#FAFAFA', borderRadius: r.md, border: '1px solid #eee', padding: s.sm }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: s.sm }}>
              <span style={{ fontSize: t.small, fontWeight: 700, color: c.text }}>{comp.props?.title || 'Column'}</span>
              <span style={{ background: c.primary, color: 'white', borderRadius: 10, padding: '0 7px', fontSize: t.small, fontWeight: 700 }}>{comp.props?.count || '3'}</span>
            </div>
            <div style={{ height: 60, background: '#F0F0F0', borderRadius: r.sm, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: t.small, color: '#bbb' }}>Items</div>
          </div>
        </div>
      );
    case 'hero':
      return (
        <div key={comp.id} onClick={() => onClick(comp.id)} style={{ padding: s.xs, border, borderRadius: r.md, cursor: 'pointer', margin: s.xs }}>
          <div style={{ background: `linear-gradient(135deg, ${c.primary}, ${c.secondary})`, borderRadius: r.lg, padding: s.xl, textAlign: 'center' }}>
            <div style={{ fontSize: t.h1, fontWeight: 700, color: 'white', marginBottom: s.sm }}>{comp.props?.title || 'Hero Title'}</div>
            <div style={{ fontSize: t.body, color: 'rgba(255,255,255,0.8)', marginBottom: s.md }}>{comp.props?.subtitle || 'Subtitle goes here'}</div>
            <button style={{ background: 'white', color: c.primary, border: 'none', borderRadius: r.sm, padding: `${s.sm} ${s.lg}`, fontWeight: 600, cursor: 'pointer', fontSize: t.body }}>
              {comp.props?.cta || 'Get Started'}
            </button>
          </div>
        </div>
      );
    default:
      return <div key={comp.id} style={{ padding: s.md, border, borderRadius: r.sm }}>Unknown: {comp.type}</div>;
  }
}

function toCssVars(theme) {
  let css = ':root {\n';
  for (const [cat, vals] of Object.entries(theme)) {
    for (const [key, val] of Object.entries(vals)) {
      css += `  --${cat}-${key}: ${val};\n`;
    }
  }
  css += '}';
  return css;
}

function toJsx(components, theme) {
  if (components.length === 0) return '<!-- Drop components on the canvas to generate JSX -->';
  return components.map(c => {
    const p = c.props || {};
    switch (c.type) {
      case 'button': return `<button styleName="primary">${p.text || 'Button'}</button>`;
      case 'card': return `<Card title="${p.title || ''}" body="${p.body || ''}" />`;
      case 'input': return `<Input label="${p.label || ''}" placeholder="${p.placeholder || ''}" />`;
      case 'badge': return `<Badge>${p.text || 'Badge'}</Badge>`;
      case 'alert': return `<Alert variant="${p.variant || 'warning'}" title="${p.title || ''}" />`;
      case 'metric': return `<MetricCard label="${p.label || ''}" value="${p.value || '42'}" />`;
      case 'hero': return `<Hero title="${p.title || ''}" subtitle="${p.subtitle || ''}" cta="${p.cta || ''}" />`;
      default: return `<${c.type} />`;
    }
  }).join('\n');
}

export default function LillyEditor({ onClose }) {
  const [theme, setTheme] = useState(DEFAULT_THEME);
  const [canvasComponents, setCanvasComponents] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [activeTab, setActiveTab] = useState('theme');
  const [showCode, setShowCode] = useState(true);
  const [layoutName, setLayoutName] = useState('');
  const [savedLayouts, setSavedLayouts] = useState([]);
  const [dragOver, setDragOver] = useState(false);
  const canvasRef = useRef(null);
  const [collapsed, setCollapsed] = useState({ left: false, right: false });

  useEffect(() => {
    fetch(`${API}/api/editor/theme`).then(r => r.json()).then(t => { if (t?.colors) setTheme(t); }).catch(() => {});
    fetch(`${API}/api/editor/layouts`).then(r => r.json()).then(setSavedLayouts).catch(() => {});
  }, []);

  const saveTheme = useCallback(() => {
    fetch(`${API}/api/editor/theme`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(theme),
    }).catch(() => {});
  }, [theme]);

  const saveLayout = useCallback(() => {
    const name = layoutName || `Layout ${canvasComponents.length}c`;
    fetch(`${API}/api/editor/layouts`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, components: canvasComponents }),
    }).then(r => r.json()).then(l => {
      setSavedLayouts(prev => [l, ...prev]);
      setLayoutName('');
    }).catch(() => {});
  }, [canvasComponents, layoutName]);

  const updateColor = (key, val) => {
    setTheme(prev => ({ ...prev, colors: { ...prev.colors, [key]: val } }));
  };
  const updateSpacing = (key, val) => {
    setTheme(prev => ({ ...prev, spacing: { ...prev.spacing, [key]: val } }));
  };
  const updateRadius = (key, val) => {
    setTheme(prev => ({ ...prev, radii: { ...prev.radii, [key]: val } }));
  };
  const updateTypography = (key, val) => {
    setTheme(prev => ({ ...prev, typography: { ...prev.typography, [key]: val } }));
  };

  const handleDragStart = (e, compType) => {
    e.dataTransfer.setData('text/plain', compType);
    e.dataTransfer.effectAllowed = 'copy';
  };

  const handleDrop = (e) => {
    e.preventDefault();
    setDragOver(false);
    const type = e.dataTransfer.getData('text/plain');
    if (!type) return;
    const newComp = { id: `comp-${Date.now()}`, type, props: {} };
    setCanvasComponents(prev => [...prev, newComp]);
    setSelectedId(newComp.id);
  };

  const handleDragOver = (e) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
    setDragOver(true);
  };

  const updateSelectedProp = (key, val) => {
    setCanvasComponents(prev => prev.map(c => c.id === selectedId ? { ...c, props: { ...c.props, [key]: val } } : c));
  };

  const removeSelected = () => {
    setCanvasComponents(prev => prev.filter(c => c.id !== selectedId));
    setSelectedId(null);
  };

  const clearCanvas = () => {
    setCanvasComponents([]);
    setSelectedId(null);
  };

  const loadLayout = (layout) => {
    setCanvasComponents(layout.components || []);
    setSelectedId(null);
  };

  const selectedComp = canvasComponents.find(c => c.id === selectedId);
  const cssVars = toCssVars(theme);
  const jsxCode = toJsx(canvasComponents, theme);

  const inputStyle = {
    padding: '6px 10px', border: '1px solid #ddd', borderRadius: 8, fontSize: 12,
    outline: 'none', width: '100%', boxSizing: 'border-box',
  };

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', height: '100vh',
      fontFamily: theme.typography.fontFamily, background: theme.colors.background,
    }}>
      {/* Header */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '12px 20px', background: 'white', borderBottom: '1px solid #eee',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div style={{
            width: 32, height: 32, borderRadius: 8,
            background: theme.colors.primary, color: 'white',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontWeight: 700, fontSize: 14,
          }}>L</div>
          <div>
            <div style={{ fontSize: 16, fontWeight: 700, color: theme.colors.text }}>Lilly Editor</div>
            <div style={{ fontSize: 11, color: '#999' }}>Theme & Component Designer</div>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <button onClick={saveTheme} style={{
            padding: '6px 14px', background: theme.colors.primary, color: 'white',
            border: 'none', borderRadius: 8, fontSize: 12, fontWeight: 600, cursor: 'pointer',
          }}>Save Theme</button>
          <button onClick={clearCanvas} style={{
            padding: '6px 14px', background: '#F5F5F4', color: '#666',
            border: '1px solid #ddd', borderRadius: 8, fontSize: 12, cursor: 'pointer',
          }}>Clear ♻</button>
          <button onClick={onClose} style={{
            padding: '6px 10px', background: 'none', border: 'none',
            fontSize: 16, cursor: 'pointer', color: '#999',
          }}>✕</button>
        </div>
      </div>

      {/* Toolbar tabs */}
      <div style={{
        display: 'flex', gap: 0, background: 'white',
        borderBottom: '1px solid #eee', paddingLeft: 16,
      }}>
        {[
          { key: 'theme', label: '🎨 Theme' },
          { key: 'components', label: '🧩 Components' },
          { key: 'layouts', label: '💾 Layouts' },
        ].map(tab => (
          <button key={tab.key} onClick={() => setActiveTab(tab.key)} style={{
            padding: '8px 16px', border: 'none', background: 'none',
            cursor: 'pointer', fontSize: 12, fontWeight: activeTab === tab.key ? 600 : 400,
            color: activeTab === tab.key ? theme.colors.text : '#999',
            borderBottom: activeTab === tab.key ? `2px solid ${theme.colors.primary}` : '2px solid transparent',
          }}>
            {tab.label}
          </button>
        ))}
        <div style={{ flex: 1 }} />
        <button onClick={() => setShowCode(!showCode)} style={{
          padding: '8px 16px', border: 'none', background: 'none',
          cursor: 'pointer', fontSize: 12, color: '#999',
        }}>
          {showCode ? 'Hide Code' : 'Show Code'} ⌨
        </button>
      </div>

      {/* Main area */}
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>

        {/* Left panel */}
        <div style={{
          width: collapsed.left ? 0 : 280, overflow: 'auto',
          background: 'white', borderRight: '1px solid #eee',
          transition: 'width 0.2s', flexShrink: 0,
        }}>
          {activeTab === 'theme' && (
            <div style={{ padding: 16 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                <h3 style={{ fontSize: 13, fontWeight: 700, margin: 0, color: theme.colors.text }}>Colors</h3>
              </div>
              {Object.entries(theme.colors).map(([key, val]) => (
                <div key={key} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                  <div style={{
                    width: 24, height: 24, borderRadius: 6,
                    background: val, border: '1px solid #ddd', flexShrink: 0,
                  }} />
                  <span style={{ flex: 1, fontSize: 12, color: '#666', textTransform: 'capitalize' }}>{key}</span>
                  <input type="color" value={val}
                    onChange={e => updateColor(key, e.target.value)}
                    style={{ width: 28, height: 28, border: 'none', cursor: 'pointer', padding: 0 }} />
                </div>
              ))}

              <h3 style={{ fontSize: 13, fontWeight: 700, margin: '16px 0 8px', color: theme.colors.text }}>Spacing</h3>
              {Object.entries(theme.spacing).map(([key, val]) => (
                <div key={key} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                  <span style={{ width: 24, fontSize: 11, color: '#666' }}>{key}</span>
                  <input type="range" min="2" max="48" value={parseInt(val)}
                    onChange={e => updateSpacing(key, e.target.value + 'px')}
                    style={{ flex: 1 }} />
                  <span style={{ fontSize: 11, color: '#999', width: 32, textAlign: 'right' }}>{val}</span>
                </div>
              ))}

              <h3 style={{ fontSize: 13, fontWeight: 700, margin: '16px 0 8px', color: theme.colors.text }}>Border Radius</h3>
              {Object.entries(theme.radii).map(([key, val]) => (
                <div key={key} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                  <span style={{ width: 24, fontSize: 11, color: '#666' }}>{key}</span>
                  <input type="range" min="0" max="32" value={parseInt(val)}
                    onChange={e => updateRadius(key, e.target.value + 'px')}
                    style={{ flex: 1 }} />
                  <span style={{ fontSize: 11, color: '#999', width: 32, textAlign: 'right' }}>{val}</span>
                </div>
              ))}

              <h3 style={{ fontSize: 13, fontWeight: 700, margin: '16px 0 8px', color: theme.colors.text }}>Typography</h3>
              {Object.entries(theme.typography).map(([key, val]) => (
                <div key={key} style={{ marginBottom: 4 }}>
                  <div style={{ fontSize: 11, color: '#666', marginBottom: 2, textTransform: 'capitalize' }}>{key}</div>
                  {key === 'fontFamily' || key === 'fontMono' ? (
                    <input value={val} onChange={e => updateTypography(key, e.target.value)}
                      style={inputStyle} />
                  ) : (
                    <div style={{ display: 'flex', gap: 4 }}>
                      <input type="range" min="10" max="48" value={parseInt(val)}
                        onChange={e => updateTypography(key, e.target.value + 'px')}
                        style={{ flex: 1 }} />
                      <span style={{ fontSize: 11, color: '#999', width: 32, textAlign: 'right' }}>{val}</span>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          {activeTab === 'components' && (
            <div style={{ padding: 16 }}>
              <h3 style={{ fontSize: 13, fontWeight: 700, margin: '0 0 12px', color: theme.colors.text }}>Component Palette</h3>
              <p style={{ fontSize: 11, color: '#999', marginBottom: 12 }}>Drag components onto the canvas</p>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
                {COMPONENT_PALETTE.map(comp => (
                  <div key={comp.type}
                    draggable
                    onDragStart={e => handleDragStart(e, comp.type)}
                    style={{
                      padding: '10px 8px', border: '1px solid #eee', borderRadius: 8,
                      cursor: 'grab', textAlign: 'center', fontSize: 11, color: '#666',
                      background: '#FAFAFA', transition: 'all 0.1s',
                    }}
                    onMouseEnter={e => e.target.style.background = '#F0F0F0'}
                    onMouseLeave={e => e.target.style.background = '#FAFAFA'}
                  >
                    <div style={{ fontSize: 20, marginBottom: 4 }}>{comp.icon}</div>
                    <div style={{ fontWeight: 600 }}>{comp.label}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {activeTab === 'layouts' && (
            <div style={{ padding: 16 }}>
              <h3 style={{ fontSize: 13, fontWeight: 700, margin: '0 0 12px', color: theme.colors.text }}>Saved Layouts</h3>
              {savedLayouts.length === 0 && (
                <div style={{ fontSize: 12, color: '#999', textAlign: 'center', padding: 20 }}>No saved layouts yet</div>
              )}
              {savedLayouts.map(layout => (
                <div key={layout.id} style={{
                  padding: 10, border: '1px solid #eee', borderRadius: 8, marginBottom: 6,
                  cursor: 'pointer', display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                }}
                  onClick={() => loadLayout(layout)}
                >
                  <div>
                    <div style={{ fontSize: 12, fontWeight: 600, color: theme.colors.text }}>{layout.name}</div>
                    <div style={{ fontSize: 10, color: '#999' }}>{layout.components?.length || 0} components</div>
                  </div>
                  <span style={{ fontSize: 10, color: '#999' }}>Load</span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Canvas */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'auto' }}
          ref={canvasRef}
          onDrop={handleDrop}
          onDragOver={handleDragOver}
          onDragLeave={() => setDragOver(false)}
        >
          <div style={{
            flex: 1, margin: 16, borderRadius: theme.radii.lg,
            border: dragOver ? `2px dashed ${theme.colors.primary}` : '1px dashed #ddd',
            background: dragOver ? theme.colors.primary + '08' : 'transparent',
            minHeight: 400, padding: theme.spacing.md,
            transition: 'all 0.15s',
          }}>
            {canvasComponents.length === 0 && !dragOver && (
              <div style={{
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                height: '100%', minHeight: 360, color: '#ccc', fontSize: 14, flexDirection: 'column', gap: 8,
              }}>
                <div style={{ fontSize: 32 }}>🎨</div>
                <div>Drag components here from the palette</div>
                <div style={{ fontSize: 12, color: '#ddd' }}>or click "Components" tab to start</div>
              </div>
            )}
            {canvasComponents.length > 0 && (
              <div style={{
                display: 'flex', flexWrap: 'wrap', gap: theme.spacing.sm,
                alignItems: 'flex-start', alignContent: 'flex-start',
              }}>
                {canvasComponents.map(comp => renderComponent(comp, theme, selectedId, setSelectedId))}
              </div>
            )}
          </div>

          {/* Selected component editor */}
          {selectedComp && (
            <div style={{
              borderTop: '1px solid #eee', background: 'white', padding: 12,
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                <div style={{ fontSize: 12, fontWeight: 700, color: theme.colors.text }}>
                  Editing: {selectedComp.type}
                </div>
                <button onClick={removeSelected} style={{
                  padding: '4px 10px', border: '1px solid #FCA5A5', borderRadius: 6,
                  background: '#FEE2E2', color: '#991B1B', fontSize: 11, cursor: 'pointer',
                }}>Remove</button>
              </div>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                {selectedComp.type === 'button' && (
                  <input placeholder="Button text" value={selectedComp.props?.text || ''}
                    onChange={e => updateSelectedProp('text', e.target.value)} style={inputStyle} />
                )}
                {selectedComp.type === 'card' && (
                  <>
                    <input placeholder="Title" value={selectedComp.props?.title || ''}
                      onChange={e => updateSelectedProp('title', e.target.value)} style={{ ...inputStyle, flex: 1 }} />
                    <input placeholder="Body" value={selectedComp.props?.body || ''}
                      onChange={e => updateSelectedProp('body', e.target.value)} style={{ ...inputStyle, flex: 2 }} />
                  </>
                )}
                {selectedComp.type === 'input' && (
                  <>
                    <input placeholder="Label" value={selectedComp.props?.label || ''}
                      onChange={e => updateSelectedProp('label', e.target.value)} style={{ ...inputStyle, flex: 1 }} />
                    <input placeholder="Placeholder" value={selectedComp.props?.placeholder || ''}
                      onChange={e => updateSelectedProp('placeholder', e.target.value)} style={{ ...inputStyle, flex: 2 }} />
                  </>
                )}
                {selectedComp.type === 'badge' && (
                  <input placeholder="Badge text" value={selectedComp.props?.text || ''}
                    onChange={e => updateSelectedProp('text', e.target.value)} style={inputStyle} />
                )}
                {selectedComp.type === 'alert' && (
                  <>
                    <input placeholder="Title" value={selectedComp.props?.title || ''}
                      onChange={e => updateSelectedProp('title', e.target.value)} style={{ ...inputStyle, flex: 1 }} />
                    <input placeholder="Body" value={selectedComp.props?.body || ''}
                      onChange={e => updateSelectedProp('body', e.target.value)} style={{ ...inputStyle, flex: 2 }} />
                    <select value={selectedComp.props?.variant || 'warning'}
                      onChange={e => updateSelectedProp('variant', e.target.value)}
                      style={inputStyle}>
                      <option value="warning">Warning</option>
                      <option value="error">Error</option>
                    </select>
                  </>
                )}
                {selectedComp.type === 'metric' && (
                  <>
                    <input placeholder="Label" value={selectedComp.props?.label || ''}
                      onChange={e => updateSelectedProp('label', e.target.value)} style={{ ...inputStyle, flex: 1 }} />
                    <input placeholder="Value" value={selectedComp.props?.value || ''}
                      onChange={e => updateSelectedProp('value', e.target.value)} style={{ ...inputStyle, flex: 1 }} />
                  </>
                )}
                {selectedComp.type === 'hero' && (
                  <>
                    <input placeholder="Title" value={selectedComp.props?.title || ''}
                      onChange={e => updateSelectedProp('title', e.target.value)} style={{ ...inputStyle, flex: 1 }} />
                    <input placeholder="Subtitle" value={selectedComp.props?.subtitle || ''}
                      onChange={e => updateSelectedProp('subtitle', e.target.value)} style={{ ...inputStyle, flex: 2 }} />
                    <input placeholder="CTA Text" value={selectedComp.props?.cta || ''}
                      onChange={e => updateSelectedProp('cta', e.target.value)} style={{ ...inputStyle, flex: 1 }} />
                  </>
                )}
              </div>
              <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                <input placeholder="Layout name..." value={layoutName}
                  onChange={e => setLayoutName(e.target.value)}
                  style={{ ...inputStyle, flex: 1 }} />
                <button onClick={saveLayout} style={{
                  padding: '6px 14px', background: theme.colors.primary, color: 'white',
                  border: 'none', borderRadius: 8, fontSize: 11, fontWeight: 600, cursor: 'pointer',
                }}>Save Layout</button>
              </div>
            </div>
          )}
        </div>

        {/* Right panel - Code */}
        {showCode && (
          <div style={{
            width: collapsed.right ? 0 : 320, overflow: 'auto',
            background: '#1A1A2E', color: '#E4E4E7',
            borderLeft: '1px solid #eee', fontSize: 12, fontFamily: theme.typography.fontMono,
            transition: 'width 0.2s', flexShrink: 0,
          }}>
            <div style={{ padding: 16 }}>
              <div style={{ fontSize: 11, fontWeight: 600, color: '#818CF8', marginBottom: 8, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                CSS Variables
              </div>
              <pre style={{ whiteSpace: 'pre-wrap', lineHeight: 1.6, margin: 0, fontSize: 11 }}>
                {cssVars}
              </pre>
            </div>
            <div style={{ borderTop: '1px solid rgba(255,255,255,0.08)', padding: 16 }}>
              <div style={{ fontSize: 11, fontWeight: 600, color: '#818CF8', marginBottom: 8, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                JSX Output
              </div>
              <pre style={{ whiteSpace: 'pre-wrap', lineHeight: 1.6, margin: 0, fontSize: 11, color: '#E4E4E7' }}>
                {jsxCode}
              </pre>
            </div>
            {canvasComponents.length > 0 && (
              <div style={{ borderTop: '1px solid rgba(255,255,255,0.08)', padding: 16 }}>
                <div style={{ fontSize: 11, fontWeight: 600, color: '#818CF8', marginBottom: 8, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                  Theme JSON
                </div>
                <pre style={{ whiteSpace: 'pre-wrap', lineHeight: 1.6, margin: 0, fontSize: 11 }}>
                  {JSON.stringify(theme, null, 2)}
                </pre>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
