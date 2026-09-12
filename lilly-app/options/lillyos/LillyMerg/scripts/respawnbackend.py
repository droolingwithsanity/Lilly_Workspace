import os

base_dir = "/home/labhrasd/LillyMerg/backend"
os.makedirs(base_dir, exist_ok=True)

# 1. The Clean main.py (Targeting Port 8000)
main_py = """
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
    except: return {"status": "default", "hive_nodes": ["Innisfil-114"]}

@app.post("/api/chat")
async def chat(payload: dict):
    msg = payload.get("message")
    try:
        r = requests.post("http://localhost:11435/api/generate", 
                          json={"model": "llama3", "prompt": msg, "stream": False}, timeout=15)
        return {"response": r.json().get("response")}
    except Exception as e:
        return {"response": f"Lilly Node Offline: {str(e)}"}
"""

# 2. The Backend Dockerfile
dockerfile = """
FROM python:3.10-slim
WORKDIR /app
RUN pip install fastapi uvicorn requests docker
COPY . .
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
"""

with open(f"{base_dir}/main.py", "w") as f: f.write(main_py.strip())
with open(f"{base_dir}/Dockerfile", "w") as f: f.write(dockerfile.strip())

print("✨ Backend folder populated and ready.")

