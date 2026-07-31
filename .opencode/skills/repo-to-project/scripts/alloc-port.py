#!/usr/bin/env python3
"""Conflict-free port allocation for the repo-to-project skill.

Usage:
  alloc-port.py <project> [preferred-port]

Reserves a stable host port per project in .opencode/deployments.json.
Probes sockets so it never collides with anything already listening
(including Lilly 8098, connector 3002, code-server 8080, static server
8199, and ollama 11434). A project keeps the same port across restarts.
"""

import datetime
import json
import os
import socket
import sys

# scripts/ → repo-to-project/ → skills/ → .opencode/ → workspace root
WORKSPACE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
)
REGISTRY = os.path.join(WORKSPACE, ".opencode", "deployments.json")

# Known in-use services that must never be handed out.
RESERVED = {3002, 8080, 8098, 8199, 11434}

# Preferred allocation pool: 8100-8999 first, then 3100-8090, then 3003-3099.
RANGE = list(range(8100, 9000)) + list(range(3100, 8090)) + list(range(3003, 3099))


def load_registry() -> dict:
    try:
        with open(REGISTRY, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"projects": {}}


def save_registry(reg: dict) -> None:
    os.makedirs(os.path.dirname(REGISTRY), exist_ok=True)
    with open(REGISTRY, "w", encoding="utf-8") as f:
        json.dump(reg, f, indent=2)
        f.write("\n")


def port_free(port: int) -> bool:
    if port in RESERVED:
        return False
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        s.bind(("0.0.0.0", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: alloc-port.py <project> [preferred-port]")
    project = sys.argv[1]
    preferred = (
        int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else None
    )

    reg = load_registry()
    projects = reg.setdefault("projects", {})
    entry = projects.setdefault(project, {})

    used = {p.get("port") for p in projects.values() if p.get("port")}

    # Stable reuse: keep the existing port while it is still free.
    if entry.get("port") and port_free(entry["port"]):
        print(entry["port"])
        return

    # Preferred port (if given, free, and not claimed by another project).
    if preferred and port_free(preferred) and preferred not in used:
        chosen = preferred
    else:
        chosen = next((p for p in RANGE if port_free(p) and p not in used), None)
        if chosen is None:
            sys.exit("no free port found")

    entry["port"] = chosen
    entry["url"] = f"http://localhost:{chosen}"
    entry["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
    save_registry(reg)
    print(chosen)


if __name__ == "__main__":
    main()
