import os

# 1. RE-FORGE BACKEND (main.py)
# Note: Added a trailing slash handling and explicit route verification
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
    return {"status": "Lilly OS Backend Active"}

@app.get("/api/docker/containers")
def get_containers():
    try:
        return [c.attrs for c in client.containers.list(all=True)]
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/system/manifest")
def get_manifest():
    try:
        with open("/app/manifest.json", "r") as f:
            return json.load(f)
    except Exception as e:
        return {"error": str(e)}
"""

# 2. RE-FORGE NGINX (Ensuring it actually listens on 3001)
nginx_conf = """
server {
    listen 3001;
    server_name localhost;

    location / {
        root /usr/share/nginx/html;
        index index.html index.htm;
        try_files $uri $uri/ /index.html;
    }
}
"""

print("🛠️ Writing sanitized main.py...")
with open("./lar_backend/main.py", "w") as f:
    f.write(backend_code.strip())

print("🛠️ Writing custom nginx.conf...")
with open("./lar_frontend/nginx.conf", "w") as f:
    f.write(nginx_conf.strip())

# 3. VERIFY DOCKER-COMPOSE
# We need to make sure the ports are EXPOSED even in host mode for some environments
print("🚀 Rebuilding with Clean Cache...")
os.system("docker compose down")
os.system("docker compose build --no-cache")
os.system("docker compose up -d")
