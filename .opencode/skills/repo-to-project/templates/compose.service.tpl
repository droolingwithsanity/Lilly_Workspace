# Service snippet for repo-to-project skill — merge into docker-compose.yml
# or use standalone:  docker compose -f compose.<name>.yml up -d
#
# Fill in:
#   <name>       project/container name
#   <HOSTPORT>   run: python3 .opencode/skills/repo-to-project/scripts/alloc-port.py <name>
#   <APPPORT>    the app's internal port (3000 node / 8000 python)
services:
  <name>:
    build: ./projects/<name>
    image: <name>:latest
    container_name: <name>
    restart: unless-stopped
    ports:
      - "<HOSTPORT>:<APPPORT>"
    volumes:
      - ./projects/<name>:/app        # hot reload in dev; remove for production-like serving
    environment:
      - PORT=<APPPORT>
      - NODE_ENV=development
    extra_hosts:
      - "host.docker.internal:host-gateway"
