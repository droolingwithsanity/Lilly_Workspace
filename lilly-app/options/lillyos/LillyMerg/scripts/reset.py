import os

# 1. SURGICAL BACKEND REWRITE (main.py)
# This puts 'app' exactly where it belongs: at the TOP.
backend_code = """
from fastapi import FastAPI
import docker
import json
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

client = docker.from_env()

@app.get("/")
def read_root():
    return {"status": "Lilly OS Backend Active", "node": "Innisfil-114"}

@app.get("/api/docker/containers")
def get_containers():
    try:
        # Get live container data from the host socket
        return [c.attrs for c in client.containers.list(all=True)]
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/system/manifest")
def get_manifest():
    try:
        with open("/app/manifest.json", "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"error": "Manifest file missing in /app/"}
"""

# 2. SURGICAL FRONTEND CONFIG (nginx.conf)
# Move to 3001 to avoid Port 80 and 3000 collisions
nginx_conf = """
server {
    listen 3001;
    location / {
        root /usr/share/nginx/html;
        index index.html index.htm;
        try_files $uri $uri/ /index.html;
    }
}
"""

# 3. APPLY CHANGES
print("🛠️ Sanitizing Backend main.py...")
with open("./lar_backend/main.py", "w") as f:
    f.write(backend_code.strip())

print("🛠️ Redirecting Frontend to Port 3001...")
with open("./lar_frontend/nginx.conf", "w") as f:
    f.write(nginx_conf.strip())

# 4. REBUILD & IGNITE
print("🚀 Re-igniting the Phoenix in Host Mode...")
os.system("docker compose down && docker compose up --build -d")
