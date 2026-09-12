import React, { useState, useRef, useEffect } from 'react';

const API = import.meta.env.VITE_API_URL || (typeof window !== 'undefined' ? window.location.origin : '');

function parseMarkdown(text) {
  if (!text) return '';
  let html = text
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.*?)\*/g, '<em>$1</em>')
    .replace(/`([^`]+)`/g, '<code style="background:#f0f0f0;padding:1px 4px;border-radius:3px;font-size:12px;">$1</code>')
    .replace(/🔗\s*(https?:\/\/\S+)/g, '<a href="$1" target="_blank" style="color:#6366F1;text-decoration:underline;">$1</a>')
    .replace(/\n/g, '<br/>');
  return html;
}

function stripMarkdown(text) {
  return text
    .replace(/\*\*(.*?)\*\*/g, '$1')
    .replace(/\*(.*?)\*/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/🔗\s*\S+/g, '')
    .replace(/\s+/g, ' ').trim();
}

export default function LillyChat({ departmentId, departmentInfo, onClose, onAction }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [convId, setConvId] = useState(null);
  const [recording, setRecording] = useState(false);
  const [speakingId, setSpeakingId] = useState(null);
  const [autoPlay, setAutoPlay] = useState(false);
  const messagesEndRef = useRef(null);
  const mediaRecorder = useRef(null);
  const audioChunks = useRef([]);
  const audioRef = useRef(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, loading]);

  useEffect(() => {
    if (!autoPlay || messages.length === 0) return;
    const last = messages[messages.length - 1];
    if (last.role === 'assistant' && !last._spoken) {
      last._spoken = true;
      speakText(last.content, messages.length - 1);
    }
  }, [messages, autoPlay]);

  useEffect(() => {
    window.__lillyChatSend = (text) => {
      if (text) sendMessage(text);
    };
    return () => { delete window.__lillyChatSend; };
  }, []);

  const detectStyle = (text) => {
    const t = text.toLowerCase();
    if (/^(hey|hi|hello|yo|sup|howdy|hey there|good morning|good afternoon|good evening)/.test(t)) return 'cheerful';
    if (/(?:\?|\b(?:what|how|why|when|where|who|can|could|would|should)\b)/.test(t)) return 'friendly';
    if (/\b(?:sorry|apolog|unfortunately|oops|my bad)\b/.test(t)) return 'sympathetic';
    if (/\b(?:great|awesome|nice|amazing|fantastic|wonderful|excellent)\b/.test(t)) return 'excited';
    if (/\b(?:important|urgent|critical|warning|alert|attention)\b/.test(t)) return 'professional';
    if (/\b(?:news|update|announce|status|report|brief)\b/.test(t)) return 'newscast';
    return 'friendly';
  };

  const speakText = async (text, idx) => {
    const clean = stripMarkdown(text);
    if (!clean) return;
    const style = detectStyle(clean);
    try {
      const res = await fetch(`${API}/api/chat/speak`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: clean, voice: 'en-US-JennyNeural', style }),
      });
      if (!res.ok) throw new Error('TTS failed');
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current.src = '';
      }
      const audio = new Audio(url);
      audioRef.current = audio;
      setSpeakingId(idx);
      audio.onended = () => { setSpeakingId(null); URL.revokeObjectURL(url); };
      audio.onerror = () => { setSpeakingId(null); URL.revokeObjectURL(url); };
      audio.play();
    } catch (e) {
      console.error('TTS error:', e);
      setSpeakingId(null);
    }
  };

  const stopSpeech = () => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.src = '';
    }
    setSpeakingId(null);
  };

  const sendMessage = async (text) => {
    if (!text.trim() || loading) return;
    if (speakingId !== null) stopSpeech();
    setInput('');
    setLoading(true);

    const userMsg = { role: 'user', content: text, timestamp: Date.now() };
    setMessages(prev => [...prev, userMsg]);

    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 180000);
      const res = await fetch(`${API}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: text,
          conversation_id: convId,
          department_id: departmentId,
        }),
        signal: controller.signal,
      });
      clearTimeout(timer);
      const data = await res.json();
      setConvId(data.conversation_id);

      const msg = {
        role: 'assistant',
        content: data.response,
        actions: data.actions,
        images: data.images,
        timestamp: Date.now(),
      };
      setMessages(prev => [...prev, msg]);

      // Execute actions via callback
      if (data.actions?.length > 0) {
        setTimeout(() => {
          data.actions.forEach(a => {
            if (onAction) onAction(a);
          });
        }, 100);
      }

      // Open website analysis panel if triggered
      if (data.open_analysis) {
        setTimeout(() => {
          window.__openWebsiteAnalysis && window.__openWebsiteAnalysis(data.open_analysis);
        }, 500);
      }
    } catch (e) {
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: 'Oops, I lost my connection for a sec. Want to try again? 🔄',
        timestamp: Date.now(),
      }]);
    }
    setLoading(false);
  };

  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      mediaRecorder.current = recorder;
      audioChunks.current = [];

      recorder.ondataavailable = e => audioChunks.current.push(e.data);
      recorder.onstop = async () => {
        stream.getTracks().forEach(t => t.stop());
        const blob = new Blob(audioChunks.current, { type: 'audio/webm' });
        setRecording(false);

        const form = new FormData();
        form.append('audio', blob, 'voice.webm');
        try {
          const res = await fetch(`${API}/api/chat/transcribe`, {
            method: 'POST',
            body: form,
          });
          const data = await res.json();
          if (data.text) {
            setInput(data.text);
            setTimeout(() => sendMessage(data.text), 300);
          } else {
            setMessages(prev => [...prev, { role: 'assistant', content: "Sorry, I couldn't hear that clearly. Try again? 🎙️", timestamp: Date.now() }]);
          }
        } catch (e) {
          setMessages(prev => [...prev, { role: 'assistant', content: "Mic transcription failed — check that the backend is running. 🎙️", timestamp: Date.now() }]);
        }
      };

      recorder.start();
      setRecording(true);
      setTimeout(() => {
        if (recorder.state === 'recording') recorder.stop();
      }, 8000);
    } catch (e) {
      console.error('Microphone access denied');
    }
  };

  const stopRecording = () => {
    if (mediaRecorder.current?.state === 'recording') {
      mediaRecorder.current.stop();
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage(input);
    }
  };

  const formatTime = (ts) => {
    const d = new Date(ts);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  };

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', height: '100%',
      background: 'white', borderRadius: 16, border: '1px solid #eee',
      overflow: 'hidden', boxShadow: '0 8px 32px rgba(0,0,0,0.08)',
    }}>
      {/* Header */}
      <div style={{
        padding: '12px 16px', borderBottom: '1px solid #eee',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        background: 'linear-gradient(135deg, #FAFAFA 0%, #F5F3FF 100%)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{
            width: 32, height: 32, borderRadius: 10, overflow: 'hidden',
            background: departmentInfo?.color || 'linear-gradient(135deg, #6366F1, #8B5CF6)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 15, color: 'white', fontWeight: 700,
          }}>
            <img src="/lilly-avatar.png" alt="Lilly"
              style={{ width: '100%', height: '100%', objectFit: 'cover' }}
              onError={(e) => { e.target.style.display = 'none'; e.target.parentNode.textContent = 'L'; }}
            />
          </div>
          <div>
            <div style={{ fontSize: 13, fontWeight: 700, color: '#2D2D2D' }}>
              Lilly{departmentInfo ? ` — ${departmentInfo.name}` : ''}
            </div>
            <div style={{ fontSize: 10, color: '#999', display: 'flex', alignItems: 'center', gap: 4 }}>
              <span style={{ width: 6, height: 6, borderRadius: 3, background: loading ? '#F59E0B' : '#10B981', display: 'inline-block' }} />
              {loading ? 'Thinking...' : recording ? '🔴 Recording...' : 'Online'}
            </div>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
          <button
            onClick={() => setAutoPlay(!autoPlay)}
            style={{
              background: 'none', border: 'none', fontSize: 14, cursor: 'pointer',
              color: autoPlay ? '#6366F1' : '#999', padding: '4px 6px', borderRadius: 6,
              position: 'relative',
            }}
            title={autoPlay ? 'Auto-speak on' : 'Auto-speak off'}
          >
            🔊
            {autoPlay && <span style={{ position: 'absolute', top: 0, right: 2, fontSize: 8, color: '#10B981' }}>✓</span>}
          </button>
          <button onClick={onClose} style={{
            background: 'none', border: 'none', fontSize: 18, cursor: 'pointer',
            color: '#999', padding: '4px 8px', borderRadius: 6,
          }}>✕</button>
        </div>
      </div>

      {/* Messages */}
      <div style={{
        flex: 1, overflow: 'auto', padding: '12px 16px',
        display: 'flex', flexDirection: 'column', gap: 10,
      }}>
        {messages.length === 0 && (
          <div style={{ textAlign: 'center', color: '#999', fontSize: 13, padding: '40px 20px' }}>
            <div style={{ marginBottom: 12 }}>
              <img src="/lilly-avatar.png" alt="Lilly"
                style={{ width: 72, height: 72, borderRadius: 16, objectFit: 'cover', boxShadow: '0 4px 16px rgba(99,102,241,0.2)' }}
                onError={(e) => { e.target.outerHTML = '<div style="font-size:36px;opacity:0.9">🌸</div>'; }}
              />
            </div>
            <div style={{ fontWeight: 600, color: '#666', marginBottom: 6, fontSize: 14 }}>Hey! I'm Lilly 🌸</div>
            <div style={{ lineHeight: 1.6, color: '#888' }}>
              I'm your platform AI — I can chat about any department, pull standup briefs,<br/>
              search the web, check the weather, find images, create tickets, or even<br/>
              spin up a new AI agent with its own persona. Try something like:
            </div>
            <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 6, alignItems: 'center' }}>
              {[
                '"What are the standups today?"', '"Show me engineering stats"',
                '"What\'s the weather in London?"', '"Create a new designer agent"',
                '"Search for latest AI news"', '"🌐 Analyze a website"',
              ].map((s, i) => (
                <button key={i} onClick={() => sendMessage(s.replace(/^"|"$/g, ''))}
                  style={{
                    background: '#F5F3FF', border: '1px solid #E0E7FF', borderRadius: 8,
                    padding: '6px 14px', fontSize: 12, color: '#6366F1', cursor: 'pointer',
                    fontFamily: 'inherit',
                  }}
                >{s}</button>
              ))}
            </div>
          </div>
        )}
        {messages.map((msg, i) => (
          <div key={i} style={{
            display: 'flex', flexDirection: 'column',
            alignItems: msg.role === 'user' ? 'flex-end' : 'flex-start',
          }}>
            <div style={{
              maxWidth: '88%', padding: '10px 16px',
              borderRadius: msg.role === 'user' ? '16px 16px 4px 16px' : '16px 16px 16px 4px',
              background: msg.role === 'user' ? '#2D2D2D' : '#F5F3FF',
              color: msg.role === 'user' ? 'white' : '#2D2D2D',
              fontSize: 13, lineHeight: 1.6, wordBreak: 'break-word',
            }}>
              <div dangerouslySetInnerHTML={{ __html: parseMarkdown(msg.content) }} />
              {msg.images && msg.images.length > 0 && (
                <div style={{ marginTop: 10, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                  {msg.images.map((url, j) => (
                    <a key={j} href={url} target="_blank" rel="noopener noreferrer" style={{ textDecoration: 'none' }}>
                      <img src={url} alt=""
                        style={{ width: 120, height: 90, borderRadius: 8, objectFit: 'cover', border: '1px solid #E0E7FF', cursor: 'pointer' }}
                        onError={(e) => { e.target.style.display = 'none' }}
                      />
                    </a>
                  ))}
                </div>
              )}
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 6 }}>
                <div style={{ fontSize: 10, color: msg.role === 'user' ? 'rgba(255,255,255,0.4)' : '#bbb' }}>
                  {formatTime(msg.timestamp)}
                </div>
                {msg.role === 'assistant' && (
                  <button
                    onClick={() => speakingId === i ? stopSpeech() : speakText(msg.content, i)}
                    style={{
                      background: 'none', border: 'none', cursor: 'pointer',
                      fontSize: 13, padding: '2px 4px', borderRadius: 4,
                      color: speakingId === i ? '#6366F1' : '#bbb',
                    }}
                    title={speakingId === i ? 'Stop' : 'Read aloud'}
                  >
                    {speakingId === i ? '🔊' : '🔈'}
                  </button>
                )}
              </div>
            </div>
            {msg.actions?.length > 0 && (
              <div style={{ marginTop: 4, display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                {msg.actions.map((a, j) => (
                  <span key={j} style={{
                    fontSize: 10, padding: '2px 10px', borderRadius: 6,
                    background: a.status === 'done' ? '#D1FAE5' : a.status === 'failed' ? '#FEE2E2' : '#FEF3C7',
                    color: a.status === 'done' ? '#065F46' : a.status === 'failed' ? '#991B1B' : '#92400E',
                    fontWeight: 500,
                  }}>
                    {a.status === 'done' ? '✅' : a.status === 'failed' ? '❌' : '⏳'} {a.action}
                    {a.ticket_id ? ` #${a.ticket_id.slice(-6)}` : ''}
                    {a.agent_id ? `: ${a.name || a.agent_id}` : ''}
                    {a.ui_action ? `: ${a.ui_action}${a.ui_param ? ' → ' + a.ui_param : ''}` : ''}
                    {a.department ? `: ${a.department}` : ''}
                    {a.count ? `: ${a.count} folder(s)` : ''}
                  </span>
                ))}
              </div>
            )}
          </div>
        ))}
        {loading && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 4px' }}>
            <div style={{
              width: 26, height: 26, borderRadius: 8,
              background: 'linear-gradient(135deg, #6366F1, #8B5CF6)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: 12, color: 'white', fontWeight: 700,
            }}>L</div>
            <div style={{ background: '#F5F3FF', borderRadius: 12, padding: '8px 14px', display: 'flex', gap: 4, alignItems: 'center' }}>
              <span style={{ width: 7, height: 7, borderRadius: 4, background: '#A78BFA', animation: 'lillyBounce 1.2s infinite', display: 'inline-block' }} />
              <span style={{ width: 7, height: 7, borderRadius: 4, background: '#A78BFA', animation: 'lillyBounce 1.2s infinite 0.2s', display: 'inline-block' }} />
              <span style={{ width: 7, height: 7, borderRadius: 4, background: '#A78BFA', animation: 'lillyBounce 1.2s infinite 0.4s', display: 'inline-block' }} />
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Suggestions */}
      {messages.length > 0 && !loading && (
        <div style={{ padding: '0 12px 4px', display: 'flex', gap: 6, overflow: 'auto', flexShrink: 0 }}>
          {['Standup brief', 'Engineering stats', 'Weather', 'News'].map((s, i) => (
            <button key={i} onClick={() => sendMessage(s)}
              style={{
                flexShrink: 0, background: '#FAFAFA', border: '1px solid #eee', borderRadius: 12,
                padding: '4px 12px', fontSize: 11, color: '#666', cursor: 'pointer',
                fontFamily: 'inherit', whiteSpace: 'nowrap',
              }}
            >{s}</button>
          ))}
        </div>
      )}

      {/* Input */}
      <div style={{ padding: '10px 12px', borderTop: '1px solid #eee', display: 'flex', gap: 8, alignItems: 'flex-end' }}>
        <button
          onClick={recording ? stopRecording : startRecording}
          style={{
            width: 36, height: 36, borderRadius: 10, border: 'none', cursor: 'pointer', flexShrink: 0,
            background: recording ? '#FEE2E2' : '#F5F5F4',
            color: recording ? '#EF4444' : '#666',
            fontSize: 16, display: 'flex', alignItems: 'center', justifyContent: 'center',
            transition: 'all 0.2s',
          }}
          title={recording ? 'Stop recording' : 'Record voice'}
        >
          {recording ? '⬤' : '🎤'}
        </button>
        <textarea
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={recording ? 'Recording...' : 'Message Lilly...'}
          rows={1}
          style={{
            flex: 1, padding: '9px 12px', border: '1px solid #ddd', borderRadius: 10,
            fontSize: 13, outline: 'none', resize: 'none', fontFamily: 'inherit',
            lineHeight: 1.4, maxHeight: 80,
          }}
          disabled={recording}
        />
        <button
          onClick={() => sendMessage(input)}
          disabled={!input.trim() || loading}
          style={{
            width: 36, height: 36, borderRadius: 10, border: 'none',
            cursor: input.trim() && !loading ? 'pointer' : 'default',
            background: input.trim() && !loading ? '#6366F1' : '#eee',
            color: input.trim() && !loading ? 'white' : '#ccc',
            fontSize: 16, display: 'flex', alignItems: 'center', justifyContent: 'center',
            flexShrink: 0, transition: 'all 0.2s',
          }}
        >➤</button>
      </div>

      <style>{`
        @keyframes lillyBounce {
          0%, 80%, 100% { transform: scale(0.6); opacity: 0.4; }
          40% { transform: scale(1); opacity: 1; }
        }
      `}</style>
    </div>
  );
}
