---
name: repo-to-project
description: Use when turning a git repository into a running project — cloning or checking out a repo, converting it into a project, installing and building it into a Docker container, serving its UI on a free port or local domain with no port conflicts, or speeding up repo work by reusing the workspace's existing patterns (vibe code). Front-load keywords like repo, git clone, project, container, docker, deploy, serve, UI, domain, port, port conflict, dev server, hot reload, npm install, build.
---

# Repo → Project

Turn any git repo into a live, containerized project served at a stable URL —
reusing this workspace's proven patterns instead of reinventing them.

## Workflow

### 1. Take the repo and make it a project

- Accept a git URL, `owner/repo`, or a local path.
- Clone into `projects/<name>/` under the workspace (create `projects/` if missing):
  - `git clone <url> projects/<name>`
  - local dir: `cp -r <path> projects/<name>`
- **Repo becomes a project** — scaffold project identity:
  - `projects/<name>/AGENTS.md` — one page: stack, build command, run command, port, entrypoints.
  - Register it in `opencode.json` under `references` (mirrors how `PrivateCode` /
    `open-connector` are registered) so the model auto-discovers it and edits
    are allowed across the external-directory boundary.
- If the repo lives inside a git tree that should not track it, append
  `projects/` to that tree's `.gitignore`.

### 2. Inspect the stack

- **Node:** `package.json` → `build`/`start`/`dev` scripts and their port.
- **Python:** `requirements.txt` / `pyproject.toml` / `setup.py` → app entry, port.
- **Rust/Go:** `Cargo.toml` / `go.mod`.
- **Dockerfile present?** Prefer it. Otherwise generate one from the templates in
  this skill (`templates/`).
- Detect the internal port: `PORT` env, `server.listen(...)`, `app.run(...)`,
  uvicorn arg, vite `server.port`. Default: `3000` (node) / `8000` (python).

### 3. Install into a container

- Build: `docker build -t <name>:latest projects/<name>`
  (add `-f` if the Dockerfile has a custom name/path).
- Run with an allocator-reserved port (step 4) and bind-mount the repo for
  hot reload when it's a dev server:

  ```bash
  docker run -d --name <name> --restart unless-stopped \
    -p <HOSTPORT>:<APPPORT> \
    -v "$PWD/projects/<name>:/app" \
    <name>:latest
  ```

- For pure dev servers (`npm run dev`, `uvicorn --reload`), bind-mount + watch
  flag so repo edits reflect immediately without rebuild.

### 4. Serve with no port conflict

- Run `scripts/alloc-port.py <name> [preferred]` — it reserves a **stable port
  per project** in `.opencode/deployments.json` and probes the socket so it
  never collides with anything already listening (including Lilly `8098`,
  connector `3002`, code-server `8080`, static server `8199`, ollama `11434`).
- The registry gives each project a **stable URL across restarts**:
  `http://<host>:<port>`.
- If a **domain** is wanted:
  - Quick: add `127.0.0.1 <name>.test` to `/etc/hosts` and bind the container
    to port 80/443 only if free — otherwise keep the mapped port and set
    `APP_BASE_URL=http://<name>.test:<port>`.
  - Clean: point a local reverse proxy (Caddy / Traefik / nginx) at the
    container; `alloc-port.py` records `domain` in the registry for lookup.
- Log the result: container name, URL, and `docker logs -f <name>`.

### 5. Verify

- `curl -sf http://localhost:<port>/` → 200 (or the app's `/health`).
- If the app exposes a UI, confirm the HTML serves and report the URL.
- If the port gets taken later, rerun the allocator, re-map, and recreate the
  container (`docker rm -f <name>` then `docker run ...`).

## Reuse the workspace's vibe code

Speed up by copying proven artifacts instead of writing from scratch:

- `Dockerfile` — multi-stage build pattern (venv/pip, node_modules, slim runtime).
- `docker-compose.yml` — service layout, `env_file`, `restart: unless-stopped`, volumes.
- `deploy-lilly.sh` — banner, `step/ok/fail/warn/info` helpers, prereq checks
  (docker/compose/daemon), health-verification loop.
- `start.sh`, `ai-server-launcher.sh` — launcher conventions.
- `open-connector/` — Node API-proxy pattern (express + fetch + dotenv).
- `termux_utils.py`, `bt_profiles.py`, `skills_engine.py` — Python module
  conventions (typed funcs, `if __name__ == "__main__":` CLI).
- This skill's own templates: `templates/Dockerfile.node.tpl`,
  `templates/Dockerfile.python.tpl`, `templates/compose.service.tpl`.

## Gotchas

- `network_mode: host` (used by lilly) does **not** map ports — for new
  projects prefer bridge networking + `-p` so the allocator's probe works.
- Check `ss -tlnp` / `docker ps` before assuming a port is free; the allocator
  does this automatically.
- Bind-mounting `/app` overrides image files — fine for dev; use an image-only
  run for production-like serving.
- Prefer Node 18+ / Python 3.10+ images matching the repo's requirements.

## Registry format

`.opencode/deployments.json`:

```json
{
  "projects": {
    "<name>": {
      "port": 8123,
      "domain": null,
      "url": "http://localhost:8123",
      "container": "<name>",
      "updated": "2026-07-30T23:00:00"
    }
  }
}
```
