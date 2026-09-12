import os

base_dir = "/home/labhrasd/LillyMerg/lar_backend"

# 1. Break the loop: remove broken links or confused folders
if os.path.islink(base_dir):
    os.unlink(base_dir)
if os.path.exists(base_dir):
    import shutil
    shutil.rmtree(base_dir)

# 2. Rebuild the physical structure
os.makedirs(base_dir, exist_ok=True)

# 3. Restore the stabilized main.py
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
    except: return {"error": "Manifest missing", "nodes": ["Innisfil-Master"]}

@app.post("/api/chat")
async def chat(payload: dict):
    msg = payload.get("message")
    try:
        # Proxy to local Ollama worker
        r = requests.post("http://localhost:11435/api/generate", 
                          json={"model": "llama3", "prompt": msg, "stream": False}, timeout=15)
        return {"response": r.json().get("response")}
    except Exception as e:
        return {"response": f"Lilly Node Offline: {str(e)}"}

@app.get("/api/docker/containers")
def containers():
    if not client: return {"error": "Socket unreachable"}
    return [c.attrs for c in client.containers.list(all=True)]
"""

# 4. Create the Dockerfile for the Backend
backend_dockerfile = """
FROM python:3.10-slim
WORKDIR /app
RUN pip install fastapi uvicorn requests docker
COPY . .
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
"""

with open(f"{base_dir}/main.py", "w") as f: f.write(backend_main.strip())
with open(f"{base_dir}/Dockerfile", "w") as f: f.write(backend_dockerfile.strip())

print(f"✅ Physical backend restored at {base_dir}. Links purged.")
