import os

# CONFIGURATION PATHS
FRONTEND_PATH = "./lar_frontend/src/App.jsx"
BACKEND_PATH = "./lar_backend/main.py"
DOCKER_COMPOSE = "./docker-compose.yml"

# 1. FRONTEND: The Dashboard UI
dashboard_code = """
import React, { useState } from 'react';
import useSWR from 'swr';
import { Terminal, Box, Activity, MessageSquare } from 'lucide-react';

const fetcher = (url) => fetch(url).then((res) => res.json());

export default function App() {
  const [input, setInput] = useState('');
  const [messages, setMessages] = useState([{ role: 'assistant', content: 'Lilly online. System state synchronized.' }]);
  const { data: containers } = useSWR('http://100.93.131.114:8000/api/docker/containers', fetcher, { refreshInterval: 5000 });
  const { data: manifest } = useSWR('http://100.93.131.114:8000/api/system/manifest', fetcher);

  const sendMessage = async () => {
    const userMsg = { role: 'user', content: input };
    setMessages(prev => [...prev, userMsg]);
    setInput('');
    const res = await fetch('http://100.93.131.114:11435/api/generate', {
      method: 'POST',
      body: JSON.stringify({ model: 'lilly-v2', prompt: input, stream: false })
    });
    const data = await res.json();
    setMessages(prev => [...prev, { role: 'assistant', content: data.response }]);
  };

  return (
    <div className="flex h-screen bg-[#FDFCFB] p-4 gap-4">
      <div className="w-1/4 space-y-4 overflow-auto">
        <div className="bg-white p-6 rounded-3xl shadow-sm border border-orange-100">
          <h2 className="text-orange-400 font-bold flex gap-2"><Activity size={18}/> WARROOM</h2>
          <p className="text-xs mt-2">ID: {manifest?.id || 'Lilly-Omega'}</p>
        </div>
        <div className="bg-white p-6 rounded-3xl shadow-sm border border-blue-100 flex-grow">
          <h2 className="text-blue-400 font-bold flex gap-2"><Box size={18}/> DOCKER</h2>
          {containers?.map(c => (
            <div key={c.Id} className="mt-2 p-2 bg-slate-50 rounded-xl text-[10px]">
              {c.Names[0]} - <span className="text-green-500">{c.State}</span>
            </div>
          ))}
        </div>
      </div>
      <div className="w-1/2 flex flex-col bg-white rounded-[40px] shadow-2xl border border-slate-100 overflow-hidden">
        <div className="flex-grow p-6 overflow-y-auto space-y-4">
          {messages.map((m, i) => (
            <div key={i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
              <div className={`p-4 rounded-3xl ${m.role === 'user' ? 'bg-orange-400 text-white' : 'bg-slate-100 text-slate-800'}`}>
                {m.content}
              </div>
            </div>
          ))}
        </div>
        <div className="p-6 bg-slate-50 flex gap-2">
          <input value={input} onChange={e => setInput(e.target.value)} className="flex-grow p-4 rounded-2xl border-none shadow-inner" placeholder="Analyze system..." />
          <button onClick={sendMessage} className="p-4 bg-orange-400 text-white rounded-2xl"><MessageSquare /></button>
        </div>
      </div>
      <div className="w-1/4 bg-[#1e1e1e] rounded-3xl p-6 text-green-400 font-mono text-[10px] overflow-auto">
        <h2 className="text-white mb-4">MANIFEST_RAW</h2>
        <pre>{JSON.stringify(manifest, null, 2)}</pre>
      </div>
    </div>
  );
}
"""

# 2. BACKEND: Docker & Manifest Endpoints
backend_patch = """
import docker
import json
from fastapi.middleware.cors import CORSMiddleware

# Enable CORS for the Innisfil IP
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

client = docker.from_env()

@app.get("/api/docker/containers")
def get_containers():
    return [c.attrs for c in client.containers.list(all=True)]

@app.get("/api/system/manifest")
def get_manifest():
    with open("/app/manifest.json", "r") as f:
        return json.load(f)
"""

def forge():
    print("🛠️ Forging Lilly-Omega Interface...")
    with open(FRONTEND_PATH, "w") as f: f.write(dashboard_code)
    with open(BACKEND_PATH, "a") as f: f.write(backend_patch)
    
    print("🚀 Triggering Docker Rebuild...")
    os.system("docker compose up --build -d")
    print("✅ Phoenix is live at http://100.93.131.114:3000")

if __name__ == "__main__":
    forge()
