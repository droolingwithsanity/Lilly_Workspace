import os

# 1. FIX THE DOCKERFILE (frontend/Dockerfile)
# No 'services:' or 'build:' tags here!
dockerfile_content = """
FROM nginx:stable-alpine
RUN rm -rf /usr/share/nginx/html/*
RUN rm -f /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/conf.d/lilly.conf
COPY dist /usr/share/nginx/html
EXPOSE 3001
CMD ["nginx", "-g", "daemon off;"]
"""

# 2. FIX THE COMPOSE FILE (docker-compose.yml)
compose_content = """
services:
  lar_frontend:
    build:
      context: ./frontend
      dockerfile: Dockerfile
    network_mode: "host"
    restart: always

  lar_backend:
    build:
      context: ./lar_backend
    network_mode: "host"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - ./manifest.json:/app/manifest.json
    restart: always
"""

print("🛠️ Writing clean Dockerfile to ./frontend/Dockerfile...")
with open("frontend/Dockerfile", "w") as f:
    f.write(dockerfile_content.strip())

print("🛠️ Writing clean docker-compose.yml to root...")
with open("docker-compose.yml", "w") as f:
    f.write(compose_content.strip())

print("🚀 Ready to build.")
