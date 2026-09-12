import React, { useState, useEffect, useRef } from 'react';

const API = import.meta.env.VITE_API_URL || (typeof window !== 'undefined' ? window.location.origin : '');

export default function NotificationsBell({ onOpenTicket }) {
  const [open, setOpen] = useState(false);
  const [notifs, setNotifs] = useState([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [dropdownPos, setDropdownPos] = useState({ top: 60, right: 16 });
  const [selectedNotif, setSelectedNotif] = useState(null);
  const ref = useRef(null);
  const btnRef = useRef(null);

  useEffect(() => {
    fetchNotifs();
    const iv = setInterval(fetchNotifs, 5000);
    return () => clearInterval(iv);
  }, []);

  async function fetchNotifs() {
    try {
      const r = await fetch(`${API}/api/notifications?unread_only=false`);
      if (r.ok) {
        const data = await r.json();
        setNotifs(data);
        setUnreadCount(data.filter(n => !n.read).length);
      }
    } catch {}
  }

  function handleClickOutside(e) {
    if (ref.current && !ref.current.contains(e.target)) setOpen(false);
  }
  useEffect(() => {
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  async function ack(id) {
    try {
      await fetch(`${API}/api/notifications/${id}/ack`, { method: 'POST' });
      fetchNotifs();
    } catch {}
  }

  async function ackAll() {
    try {
      await fetch(`${API}/api/notifications/ack-all`, { method: 'POST' });
      fetchNotifs();
    } catch {}
  }

  function toggleDropdown() {
    if (!open && btnRef.current) {
      const rect = btnRef.current.getBoundingClientRect();
      setDropdownPos({ top: rect.bottom + 4, right: window.innerWidth - rect.right });
    }
    setOpen(!open);
  }

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button
        ref={btnRef}
        onClick={toggleDropdown}
        style={{
          background: 'none', border: 'none', cursor: 'pointer',
          fontSize: 18, padding: '4px 6px', position: 'relative',
          color: open ? '#6366F1' : '#888',
        }}
        title="Notifications"
      >
        🔔
        {unreadCount > 0 && (
          <span style={{
            position: 'absolute', top: 0, right: 0,
            background: '#EF4444', color: 'white', fontSize: 9,
            fontWeight: 700, minWidth: 16, height: 16,
            borderRadius: 8, display: 'flex', alignItems: 'center',
            justifyContent: 'center', lineHeight: 1,
          }}>
            {unreadCount > 9 ? '9+' : unreadCount}
          </span>
        )}
      </button>

      {open && (
        <div style={{
          position: 'fixed', top: dropdownPos.top, right: dropdownPos.right,
          width: 360, maxHeight: 420, background: 'white', borderRadius: 14,
          boxShadow: '0 10px 40px rgba(0,0,0,0.12)', overflow: 'hidden',
          zIndex: 1001, border: '1px solid #eee',
        }}>
          <div style={{
            display: 'flex', justifyContent: 'space-between',
            alignItems: 'center', padding: '12px 16px',
            borderBottom: '1px solid #f0f0f0',
          }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: '#2D2D2D' }}>
              Notifications
            </div>
            <div style={{ display: 'flex', gap: 8, fontSize: 11 }}>
              {unreadCount > 0 && (
                <button onClick={ackAll} style={{
                  background: 'none', border: 'none', cursor: 'pointer',
                  color: '#6366F1', fontWeight: 600, fontFamily: 'inherit',
                  fontSize: 11,
                }}>
                  Mark all read
                </button>
              )}
              <button onClick={() => setOpen(false)} style={{
                background: 'none', border: 'none', cursor: 'pointer',
                color: '#999', fontSize: 14,
              }}>✕</button>
            </div>
          </div>

          <div style={{ overflowY: 'auto', maxHeight: 360 }}>
            {notifs.length === 0 && (
              <div style={{ padding: 24, textAlign: 'center', color: '#bbb', fontSize: 13 }}>
                No notifications yet
              </div>
            )}
            {notifs.map(n => (
              <div key={n.id}>
                <div
                  onClick={() => setSelectedNotif(selectedNotif?.id === n.id ? null : n)}
                  style={{
                    display: 'flex', gap: 10, padding: '10px 16px',
                    borderBottom: '1px solid #f5f5f5',
                    cursor: 'pointer',
                    background: selectedNotif?.id === n.id ? '#EEF2FF' : (n.read ? 'white' : '#F8FAFF'),
                    transition: 'background 0.15s',
                  }}
                  onMouseEnter={e => { if (selectedNotif?.id !== n.id) e.currentTarget.style.background = '#F0F0FF'; }}
                  onMouseLeave={e => { if (selectedNotif?.id !== n.id) e.currentTarget.style.background = n.read ? 'white' : '#F8FAFF'; }}
                >
                  <div style={{ flexShrink: 0, fontSize: 20, marginTop: 2 }}>
                    {n.read ? '💬' : '🆕'}
                  </div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{
                      fontSize: 12, fontWeight: 600, color: '#2D2D2D',
                      marginBottom: 2,
                    }}>
                      {n.title}
                    </div>
                    <div style={{
                      fontSize: 12, color: '#666', marginBottom: 3,
                      overflow: 'hidden', textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap', maxWidth: 260,
                    }}>
                      {n.message}
                    </div>
                    <div style={{
                      fontSize: 11, color: '#999',
                      display: 'flex', gap: 6, alignItems: 'center',
                    }}>
                      {n.ticket_id && <span>#{n.ticket_id?.slice(0, 8)}</span>}
                      {n.ticket_id && <span>·</span>}
                      <span>{n.agent_name}</span>
                      <span>·</span>
                      <span style={{ fontSize: 10 }}>
                        {timeAgo(n.created_at)}
                      </span>
                    </div>
                  </div>
                  {!n.read && (
                    <div style={{
                      width: 8, height: 8, borderRadius: 4,
                      background: '#6366F1', flexShrink: 0, marginTop: 6,
                    }} />
                  )}
                </div>
                {selectedNotif?.id === n.id && (
                  <div style={{
                    padding: '12px 16px 12px 46px', background: '#F8FAFF',
                    borderBottom: '1px solid #E0E7FF', fontSize: 13, color: '#2D2D2D',
                    lineHeight: 1.6, whiteSpace: 'pre-wrap',
                  }}>
                    {n.message}
                    <div style={{ marginTop: 8, display: 'flex', gap: 6 }}>
                      <button onClick={() => { if (!n.read) ack(n.id); setSelectedNotif(null); }}
                        style={{
                          padding: '4px 12px', borderRadius: 6, border: 'none',
                          background: '#6366F1', color: 'white', fontSize: 11,
                          fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit',
                        }}
                      >{n.read ? 'Done' : 'Mark read'}</button>
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function timeAgo(ts) {
  const diff = Date.now() - (ts * 1000 || 0);
  const mins = Math.round(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}
