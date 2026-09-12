import React, { useState } from 'react';

const API = import.meta.env.VITE_API_URL || '';

const SEVERITY_COLORS = {
  critical: '#EF4444', high: '#F59E0B', medium: '#3B82F6', low: '#94A3B8',
};

const GRADE_COLORS = {
  'A': '#10B981', 'A-': '#10B981', 'B+': '#34D399', 'B': '#34D399',
  'C+': '#F59E0B', 'C': '#F59E0B', 'D': '#EF4444', 'F': '#EF4444',
};

export default function WebsiteAnalysis({ url: initialUrl, onClose }) {
  const [url, setUrl] = useState(initialUrl || '');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [noRoot, setNoRoot] = useState(false);

  const analyze = async () => {
    if (!url.trim()) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const res = await fetch(`${API}/api/analyze-website`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: url.trim(), has_root_access: !noRoot }),
      });
      if (!res.ok) throw new Error(`Server error: ${res.status}`);
      const data = await res.json();
      setResult(data);
    } catch (e) {
      setError(e.message);
    }
    setLoading(false);
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter') analyze();
  };

  return (
    <div style={{
      position: 'fixed', top: 0, right: 0, bottom: 0, zIndex: 999,
      width: 480, maxWidth: '100vw',
      boxShadow: '-4px 0 24px rgba(0,0,0,0.1)',
      display: 'flex', flexDirection: 'column',
      background: 'white',
    }}>
      {/* Header */}
      <div style={{
        padding: '14px 18px', borderBottom: '1px solid #eee',
        display: 'flex', alignItems: 'center', gap: 10,
      }}>
        <div style={{ fontSize: 18 }}>🌐</div>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 14, fontWeight: 700, color: '#2D2D2D' }}>Website Analysis</div>
          <div style={{ fontSize: 11, color: '#999' }}>Lilly Chief of Staff — Security & Tech Audit</div>
        </div>
        <button onClick={onClose} style={{
          background: 'none', border: 'none', fontSize: 18, cursor: 'pointer',
          color: '#999', padding: '4px 8px', borderRadius: 6,
        }}>✕</button>
      </div>

      {/* URL Input */}
      <div style={{ padding: '14px 18px', borderBottom: '1px solid #f0f0f0' }}>
        <div style={{ display: 'flex', gap: 8 }}>
          <input
            value={url} onChange={e => setUrl(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Enter website URL..."
            style={{
              flex: 1, padding: '9px 12px', border: '1px solid #ddd', borderRadius: 10,
              fontSize: 13, outline: 'none', fontFamily: 'inherit',
            }}
          />
          <button onClick={analyze} disabled={!url.trim() || loading}
            style={{
              padding: '9px 18px', borderRadius: 10, border: 'none',
              background: url.trim() && !loading ? '#2D2D2D' : '#eee',
              color: url.trim() && !loading ? 'white' : '#ccc',
              fontSize: 12, fontWeight: 600, cursor: url.trim() && !loading ? 'pointer' : 'default',
              fontFamily: 'inherit', whiteSpace: 'nowrap',
            }}
          >{loading ? 'Analyzing...' : 'Analyze'}</button>
        </div>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 8, fontSize: 11, color: '#888', cursor: 'pointer' }}>
          <input type="checkbox" checked={noRoot} onChange={e => setNoRoot(e.target.checked)}
            style={{ accentColor: '#2D2D2D' }} />
          No root/server access — generate security advisory
        </label>
      </div>

      {/* Results */}
      <div style={{ flex: 1, overflow: 'auto', padding: '14px 18px' }}>
        {loading && (
          <div style={{ textAlign: 'center', padding: '40px 20px', color: '#999' }}>
            <div style={{ fontSize: 28, marginBottom: 12 }}>🔍</div>
            <div style={{ fontSize: 13 }}>Scanning website...</div>
            <div style={{ fontSize: 11, color: '#bbb', marginTop: 4 }}>Checking headers, SSL, tech stack, and more</div>
          </div>
        )}

        {error && (
          <div style={{ padding: 12, borderRadius: 10, background: '#FEF2F2', border: '1px solid #FECACA', fontSize: 12, color: '#991B1B' }}>
            ❌ {error}
          </div>
        )}

        {result && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            {/* Grade + Status */}
            <div style={{ display: 'flex', gap: 16, alignItems: 'center' }}>
              <div style={{
                width: 60, height: 60, borderRadius: 14,
                background: (GRADE_COLORS[result.grade] || '#94A3B8') + '20',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: 24, fontWeight: 800,
                color: GRADE_COLORS[result.grade] || '#94A3B8',
                border: '2px solid ' + (GRADE_COLORS[result.grade] || '#94A3B8') + '40',
              }}>{result.grade}</div>
              <div>
                <div style={{ fontSize: 13, fontWeight: 600, color: '#2D2D2D' }}>{result.url}</div>
                <div style={{ fontSize: 11, color: '#888', display: 'flex', gap: 12, marginTop: 2 }}>
                  <span>HTTP {result.status_code}</span>
                  {result.response_time_ms && <span>{result.response_time_ms}ms</span>}
                </div>
              </div>
            </div>

            {/* AI Report */}
            <div style={{
              padding: 14, borderRadius: 10, background: '#F8FAFB',
              border: '1px solid #E2E8F0', fontSize: 12, lineHeight: 1.7,
              color: '#333', whiteSpace: 'pre-wrap', fontFamily: 'inherit',
            }}>
              {result.report}
            </div>

            {/* Issues */}
            {(result.issues || []).length > 0 && (
              <div>
                <h4 style={{ fontSize: 11, fontWeight: 600, color: '#999', textTransform: 'uppercase', margin: '0 0 8px 0', letterSpacing: '0.5px' }}>
                  Issues ({result.issues.length})
                </h4>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {result.issues.map((issue, i) => (
                    <div key={i} style={{
                      padding: 10, borderRadius: 8, border: '1px solid',
                      borderColor: (SEVERITY_COLORS[issue.severity] || '#94A3B8') + '30',
                      background: (SEVERITY_COLORS[issue.severity] || '#94A3B8') + '08',
                    }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                        <span style={{
                          padding: '1px 8px', borderRadius: 4,
                          background: SEVERITY_COLORS[issue.severity] || '#94A3B8',
                          color: 'white', fontSize: 9, fontWeight: 700, textTransform: 'uppercase',
                        }}>{issue.severity}</span>
                        <span style={{ fontSize: 12, fontWeight: 600, color: '#2D2D2D' }}>
                          {issue.message}
                        </span>
                      </div>
                      <p style={{ fontSize: 11, color: '#666', margin: '0 0 4px 0', lineHeight: 1.5 }}>
                        {issue.description}
                      </p>
                      {issue.fix && (
                        <div style={{
                          padding: '6px 8px', borderRadius: 6, background: '#FAFAFA',
                          fontSize: 10, color: '#888', fontFamily: 'monospace',
                          border: '1px dashed #ddd',
                        }}>
                          💡 {issue.fix}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Security Headers */}
            {result.security_headers && Object.keys(result.security_headers).length > 0 && (
              <div>
                <h4 style={{ fontSize: 11, fontWeight: 600, color: '#999', textTransform: 'uppercase', margin: '0 0 8px 0', letterSpacing: '0.5px' }}>
                  Security Headers
                </h4>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                  {Object.entries(result.security_headers).map(([hdr, info]) => (
                    <div key={hdr} style={{
                      display: 'flex', alignItems: 'center', gap: 8, padding: '6px 8px',
                      borderRadius: 6, background: info.present ? '#F0FDF4' : '#FEF2F2',
                      fontSize: 11,
                    }}>
                      <span style={{ flexShrink: 0 }}>{info.present ? '✅' : '❌'}</span>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontWeight: 600, color: '#2D2D2D' }}>{hdr}</div>
                        <div style={{ color: '#888', fontSize: 10 }}>{info.description}</div>
                      </div>
                      {info.value && (
                        <span style={{ fontSize: 9, color: '#999', maxWidth: 140, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {info.value}
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Tech Stack */}
            {(result.tech_stack || []).length > 0 && (
              <div>
                <h4 style={{ fontSize: 11, fontWeight: 600, color: '#999', textTransform: 'uppercase', margin: '0 0 8px 0', letterSpacing: '0.5px' }}>
                  Detected Tech Stack
                </h4>
                <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                  {result.tech_stack.map(t => (
                    <span key={t} style={{
                      padding: '4px 10px', borderRadius: 6, background: '#F0F0F0',
                      color: '#555', fontSize: 11,
                    }}>{t}</span>
                  ))}
                </div>
              </div>
            )}

            {/* SSL */}
            {result.ssl_info && result.ssl_info.valid !== undefined && (
              <div style={{ padding: 10, borderRadius: 8, background: '#FAFAFA', fontSize: 11, color: '#666' }}>
                <strong>SSL:</strong> {result.ssl_info.valid ? '✅ Valid' : '❌ Invalid'}
                {result.ssl_info.days_left !== undefined && ` · ${result.ssl_info.days_left} days remaining`}
                {result.ssl_info.issuer && ` · ${result.ssl_info.issuer.slice(0, 60)}`}
              </div>
            )}

            {/* Download report */}
            <button onClick={() => {
              const blob = new Blob([result.report], { type: 'text/plain' });
              const a = document.createElement('a');
              a.href = URL.createObjectURL(blob);
              a.download = `analysis-${result.url.replace(/[^a-z0-9]/gi, '-')}.txt`;
              a.click();
            }}
              style={{
                padding: '8px 16px', borderRadius: 8, border: '1px solid #ddd',
                background: 'white', fontSize: 12, cursor: 'pointer', fontFamily: 'inherit',
                color: '#555', alignSelf: 'flex-start',
              }}
            >📥 Download Report</button>
          </div>
        )}

        {!loading && !result && !error && (
          <div style={{ textAlign: 'center', padding: '40px 20px', color: '#999', fontSize: 12, lineHeight: 1.6 }}>
            <div style={{ fontSize: 32, marginBottom: 10 }}>🔍</div>
            <div>Enter a URL and click Analyze to scan the website</div>
            <div style={{ color: '#bbb', marginTop: 4 }}>Lilly will check security headers, SSL, tech stack, and provide a full report</div>
          </div>
        )}
      </div>
    </div>
  );
}
