import os

# --- 1. FRONTEND: Update App.jsx with Sidebar & Chat Logic ---
frontend_app = """
import React, { useState, useEffect } from 'react';

function App() {
  const [messages, setMessages] = useState([{ role: 'assistant', content: 'Lilly online. How can I help with the hive today?' }]);
  const [input, setInput] = useState('');
  const [manifest, setManifest] = useState(null);

  useEffect(() => {
    fetch('http://100.93.131.114:8001/api/system/manifest')
      .then(res => res.json())
      .then(data => setManifest(data))
      .catch(err => console.error("Manifest fetch failed", err));
  }, []);

  const handleSend = async () => {
    if (!input.trim()) return;
    const userMsg = { role: 'user', content: input };
    setMessages(prev => [...prev, userMsg]);
    setInput('');

    try {
      const response = await fetch('http://100.93.131.114:8001/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: input })
      });
      const data = await response.json();
      setMessages(prev => [...prev, { role: 'assistant', content: data.response }]);
    } catch (e) {
      setMessages(prev => [...prev, { role: 'assistant', content: 'Lilly: Connection to worker node lost.' }]);
    }
  };

  return (
    <div className="flex h-screen bg-[#FDFCFB] text-[#2D2D2D] font-sans">
      <div className="flex-1 flex flex-col p-6 overflow-hidden">
        <div className="flex-1 overflow-y-auto space-y-4 mb-4 pr-2">
          {messages.map((m, i) => (
            <div key={i} className={`p-4 rounded-2xl max-w-[85%] ${m.role === 'user' ? 'bg-[#FFB7B7] text-white self-end ml-auto' : 'bg-white shadow-sm border border-[#EEE] self-start'}`}>
              {m.content}
            </div>
          ))}
        </div>
        <div className="flex gap-3 bg-white p-2 rounded-full shadow-md border border-[#EEE]">
          <input 
            className="flex-1 px-5 py-2 focus:outline-none bg-transparent"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSend()}
            placeholder="Type a command for Lilly..."
          />
          <button onClick={handleSend} className="bg-[#2D2D2D] text-white px-6 py-2 rounded-full font-medium">Send</button>
        </div>
      </div>

      <div className="w-80 bg-white border-l border-[#EEE] p-8 hidden lg:flex flex-col">
        <div className="bg-[#FFB7B7] text-white text-[10px] font-black tracking-tighter px-3 py-1 rounded-md w-fit mb-8">LILLY-CORE</div>
        <div className="space-y-8">
          <div>
            <h3 className="text-[11px] font-bold uppercase tracking-[0.2em] text-[#AAA] mb-4">Hive Nodes</h3>
            <div className="space-y-3">
              {manifest?.hive_nodes?.map(node => (
                <div key={node} className="flex items-center gap-3 text-sm font-medium">
                  <div className="w-2 h-2 bg-green-400 rounded-full animate-pulse"></div> {node}
                </div>
              ))}
            </div>
          </div>
          <div>
            <h3 className="text-[11px] font-bold uppercase tracking-[0.2em] text-[#AAA] mb-4">Registry</h3>
            <div className="flex flex-wrap gap-2">
              {manifest?.found_labs?.map(lab => (
                <span key={lab} className="bg-[#F3F4F6] px-3 py-1 rounded-md text-[10px] font-bold text-[#666]">{lab}</span>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
export default App;
"""

# --- 2. BACKEND: Update main.py with Chat POST Route ---
backend_main = """
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import requests, docker, json, os

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

try:
    client = docker.from_env()
except:
    client = None

@app.get("/api/system/manifest")
def get_manifest():
    try:
        with open("/app/manifest.json", "r") as f: return json.load(f)
    except: return {"error": "Manifest missing"}

@app.post("/api/chat")
async def chat(payload: dict):
    msg = payload.get("message")
    try:
        # Forwarding to Ollama on host
        r = requests.post("http://localhost:11435/api/generate", 
                          json={"model": "llama3", "prompt": msg, "stream": False}, timeout=10)
        return {"response": r.json().get("response")}
    except Exception as e:
        return {"response": f"Lilly-Error: {str(e)}"}
"""

with open("frontend/src/App.jsx", "w") as f: f.write(frontend_app.strip())
with open("lar_backend/main.py", "w") as f: f.write(backend_main.strip())

print("✅ Files updated. Ready for rebuild.")
