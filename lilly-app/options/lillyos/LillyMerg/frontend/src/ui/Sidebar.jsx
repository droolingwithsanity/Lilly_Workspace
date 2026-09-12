import React, { useState, useEffect } from 'react';
import NotificationsBell from './NotificationsBell.jsx';
import DrivePanel from './DrivePanel.jsx';

const API = import.meta.env.VITE_API_URL || (typeof window !== 'undefined' ? window.location.origin : '');

export default function Sidebar({ activeDept, onSelect, activeApp, onAppSelect }) {
  const [driveOpen, setDriveOpen] = useState(false);
  const [departments, setDepartments] = useState([]);

  useEffect(() => {
    fetch(`${API}/api/departments`)
      .then(r => r.json())
      .then(setDepartments)
      .catch(() => {});
  }, []);

  const allDept = { id: null, name: 'All Departments', icon: '🌐', color: '#6366F1' };

  return (
    <div style={{
      background: '#1A1A2E',
      padding: '0 16px',
      display: 'flex',
      alignItems: 'center',
      gap: 4,
      flexShrink: 0,
      height: 52,
      overflow: 'visible',
      zIndex: 50,
    }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8, marginRight: 16, flexShrink: 0,
      }}>
        <div style={{
          width: 28, height: 28, borderRadius: 6,
          background: 'white', color: '#1A1A2E',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontWeight: 700, fontSize: 12,
        }}>L</div>
        <span style={{ fontSize: 13, fontWeight: 700, color: 'white', whiteSpace: 'nowrap' }}>LillyOS</span>
      </div>

      {[allDept, ...departments].map(dept => (
        <button
          key={dept.id || 'all'}
          title={dept.name}
          onClick={() => onSelect(dept.id)}
          style={{
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            width: 34, height: 34, borderRadius: 8, border: 'none',
            background: activeDept === dept.id ? 'rgba(255,255,255,0.12)' : 'transparent',
            color: activeDept === dept.id ? 'white' : 'rgba(255,255,255,0.55)',
            cursor: 'pointer', fontSize: 16, flexShrink: 0,
            transition: 'all 0.15s',
          }}
          onMouseEnter={e => { if (activeDept !== dept.id) e.target.style.background = 'rgba(255,255,255,0.05)'; }}
          onMouseLeave={e => { if (activeDept !== dept.id) e.target.style.background = 'transparent'; }}
        >
          {dept.icon || '📁'}
        </button>
      ))}

      <div style={{ width: 1, height: 24, background: 'rgba(255,255,255,0.1)', margin: '0 8px', flexShrink: 0 }} />

      <button title="Synapse"
        onClick={() => onAppSelect(activeApp === 'synapse' ? 'dashboard' : 'synapse')}
        style={{
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          width: 34, height: 34, borderRadius: 8, border: 'none',
          background: activeApp === 'synapse' ? 'rgba(139,92,246,0.2)' : 'transparent',
          color: activeApp === 'synapse' ? '#A78BFA' : 'rgba(255,255,255,0.55)',
          cursor: 'pointer', fontSize: 16, flexShrink: 0, transition: 'all 0.15s',
        }}
      >🧠</button>

      <button title="Portfolio"
        onClick={() => onAppSelect(activeApp === 'portfolio' ? 'dashboard' : 'portfolio')}
        style={{
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          width: 34, height: 34, borderRadius: 8, border: 'none',
          background: activeApp === 'portfolio' ? 'rgba(16,185,129,0.2)' : 'transparent',
          color: activeApp === 'portfolio' ? '#34D399' : 'rgba(255,255,255,0.55)',
          cursor: 'pointer', fontSize: 16, flexShrink: 0, transition: 'all 0.15s',
        }}
      >🏆</button>

      <button title="Google Drive"
        onClick={() => setDriveOpen(!driveOpen)}
        style={{
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          width: 34, height: 34, borderRadius: 8, border: 'none',
          background: driveOpen ? 'rgba(66,133,244,0.2)' : 'transparent',
          color: driveOpen ? '#4285F4' : 'rgba(255,255,255,0.55)',
          cursor: 'pointer', fontSize: 16, flexShrink: 0, transition: 'all 0.15s',
        }}
      >📁</button>

      <div style={{ flex: 1 }} />

      <NotificationsBell />

      {driveOpen && <DrivePanel onClose={() => setDriveOpen(false)} />}

      <button title="Ask Lilly"
        onClick={() => window.__toggleLillyChat && window.__toggleLillyChat(null)}
        style={{
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          width: 34, height: 34, borderRadius: 8, border: 'none',
          background: 'rgba(99,102,241,0.15)', color: '#818CF8',
          cursor: 'pointer', fontSize: 16, flexShrink: 0,
        }}
      >🧠</button>
      <button title="Design Editor"
        onClick={() => window.__openEditor && window.__openEditor()}
        style={{
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          width: 34, height: 34, borderRadius: 8, border: 'none',
          background: 'rgba(16,185,129,0.15)', color: '#34D399',
          cursor: 'pointer', fontSize: 16, flexShrink: 0,
        }}
      >🎨</button>
    </div>
  );
}
