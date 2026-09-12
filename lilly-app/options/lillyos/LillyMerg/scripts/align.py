import os

# 1. Update Docker Compose to use 'frontend' (lowercase)
compose_content = """
services:
  lar_backend:
    build: ./lar_backend
    network_mode: "host"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - ./manifest.json:/app/manifest.json
    restart: always

  lar_frontend:
    build: ./frontend
    network_mode: "host"
    restart: always
"""

# 2. Update frontend/Dockerfile to ensure it maps to the current dist
frontend_dockerfile = """
FROM nginx:stable-alpine
# Purge default configs
RUN rm -f /etc/nginx/conf.d/default.conf
# Copy our custom config for port 3001
COPY nginx.conf /etc/nginx/conf.d/lilly.conf
# Copy the bones from your actual dist folder
COPY dist /usr/share/nginx/html
EXPOSE 3001
CMD ["nginx", "-g", "daemon off;"]
"""

print("🛠️ Aligning paths to lowercase 'frontend'...")
with open("docker-compose.yml", "w") as f:
    f.write(compose_content.strip())

with open("frontend/Dockerfile", "w") as f:
    f.write(frontend_dockerfile.strip())

print("🚀 Launching the Lilly Dashboard...")
os.system("docker compose down && docker compose up -d --build")
