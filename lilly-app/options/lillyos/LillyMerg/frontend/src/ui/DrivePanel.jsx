import React, { useState, useEffect } from 'react';

const API = import.meta.env.VITE_API_URL || (typeof window !== 'undefined' ? window.location.origin : '');

export default function DrivePanel({ onClose }) {
  const [status, setStatus] = useState(null);
  const [folders, setFolders] = useState([]);
  const [syncing, setSyncing] = useState(false);

  useEffect(() => {
    fetch(`${API}/api/drive/status`)
      .then(r => r.json())
      .then(d => setStatus(d.authenticated))
      .catch(() => setStatus(false));
    fetch(`${API}/api/drive/folders`)
      .then(r => r.json())
      .then(d => setFolders(d.folders || []))
      .catch(() => {});
  }, []);

  async function handleAuth() {
    try {
      const r = await fetch(`${API}/api/drive/auth-url`);
      const d = await r.json();
      if (d.url) window.open(d.url, '_blank');
    } catch {}
  }

  async function handleSync() {
    setSyncing(true);
    try {
      const r = await fetch(`${API}/api/drive/sync`, { method: 'POST' });
      const d = await r.json();
      setFolders(d.folders || []);
      setStatus(true);
    } catch {}
    setSyncing(false);
  }

  return (
    <div style={{
      position: 'fixed', top: 60, right: 16, zIndex: 999,
      width: 360, background: 'white', borderRadius: 14,
      boxShadow: '0 10px 40px rgba(0,0,0,0.12)',
      border: '1px solid #eee', overflow: 'hidden',
    }}>
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        padding: '14px 16px', borderBottom: '1px solid #f0f0f0',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 18 }}>📁</span>
          <span style={{ fontSize: 14, fontWeight: 700, color: '#2D2D2D' }}>Google Drive</span>
        </div>
        <button onClick={onClose} style={{
          background: 'none', border: 'none', cursor: 'pointer', fontSize: 16, color: '#999',
        }}>✕</button>
      </div>

      <div style={{ padding: 16 }}>
        {status === null && (
          <div style={{ fontSize: 13, color: '#999', textAlign: 'center', padding: 12 }}>
            Checking connection...
          </div>
        )}

        {status === false && (
          <div style={{ textAlign: 'center', padding: 12 }}>
            <div style={{ fontSize: 13, color: '#666', marginBottom: 10 }}>
              Connect your Google account to create department folders in Drive.
            </div>
            <button onClick={handleAuth} style={{
              padding: '8px 20px', borderRadius: 8, border: 'none',
              background: '#4285F4', color: 'white', fontSize: 12, fontWeight: 600,
              cursor: 'pointer', fontFamily: 'inherit',
            }}>
              🔗 Sign in with Google
            </button>
            <div style={{ fontSize: 10, color: '#999', marginTop: 8 }}>
              Requires OAuth setup — see .env for GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET
            </div>
          </div>
        )}

        {status === true && (
          <>
            <button onClick={handleSync} disabled={syncing} style={{
              width: '100%', padding: '8px 0', borderRadius: 8, border: 'none',
              background: syncing ? '#eee' : '#E8F5E9', color: syncing ? '#999' : '#2E7D32',
              fontSize: 12, fontWeight: 600, cursor: syncing ? 'default' : 'pointer',
              marginBottom: 12, fontFamily: 'inherit',
            }}>
              {syncing ? '⏳ Syncing...' : '🔄 Create/Refresh Department Folders'}
            </button>

            {folders.length === 0 && !syncing && (
              <div style={{ fontSize: 12, color: '#999', textAlign: 'center', padding: 8 }}>
                No folders yet. Click sync to create them.
              </div>
            )}

            {folders.map(f => (
              <div key={f.dept_id} style={{
                display: 'flex', alignItems: 'center', gap: 10,
                padding: '8px 10px', borderRadius: 8,
                border: '1px solid #f0f0f0', marginBottom: 6,
              }}>
                <span style={{ fontSize: 16, flexShrink: 0 }}>📂</span>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 12, fontWeight: 600, color: '#2D2D2D' }}>{f.name}</div>
                  {f.url ? (
                    <a href={f.url} target="_blank" rel="noopener noreferrer" style={{
                      fontSize: 10, color: '#4285F4', textDecoration: 'none',
                    }}>
                      Open in Drive ↗
                    </a>
                  ) : (
                    <span style={{ fontSize: 10, color: '#999' }}>Not created</span>
                  )}
                </div>
                {f.folder_id && <span style={{
                  fontSize: 9, color: '#999', fontFamily: 'monospace',
                }}>{f.folder_id.slice(0, 8)}</span>}
              </div>
            ))}
          </>
        )}
      </div>
    </div>
  );
}
