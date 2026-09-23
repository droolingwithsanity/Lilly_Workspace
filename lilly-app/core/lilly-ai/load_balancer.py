#!/usr/bin/env python3
"""Load Balancer for Lilly AI - prevents site crashes with health checks and failover."""

import os
import sys
import time
import signal
import subprocess
import threading
import logging
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("LoadBalancer")

# Configuration
MAIN_PORT = 8098
BACKUP_PORT = 8099
HEALTH_CHECK_INTERVAL = 10  # seconds
MAX_FAILURES = 3
RESTART_DELAY = 5
WORK_DIR = Path(__file__).parent


@dataclass
class ServerInstance:
    port: int
    process: Optional[subprocess.Popen] = None
    failures: int = 0
    last_health: float = 0
    active: bool = False


class LoadBalancer:
    def __init__(self):
        self.servers: Dict[int, ServerInstance] = {
            MAIN_PORT: ServerInstance(port=MAIN_PORT),
            BACKUP_PORT: ServerInstance(port=BACKUP_PORT),
        }
        self.running = True
        self._lock = threading.Lock()

    def start_server(self, port: int) -> bool:
        """Start a server instance on the given port."""
        try:
            cmd = [
                sys.executable, "-m", "uvicorn", "lilly_ai:app",
                "--host", "0.0.0.0",
                "--port", str(port),
                "--workers", "1",
                "--log-level", "warning",
            ]
            proc = subprocess.Popen(
                cmd,
                cwd=str(WORK_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                preexec_fn=os.setsid,
            )
            with self._lock:
                self.servers[port].process = proc
                self.servers[port].failures = 0
                self.servers[port].active = True
                self.servers[port].last_health = time.time()
            logger.info(f"Started server on port {port} (PID: {proc.pid})")
            return True
        except Exception as e:
            logger.error(f"Failed to start server on port {port}: {e}")
            return False

    def stop_server(self, port: int) -> None:
        """Stop a server instance."""
        with self._lock:
            inst = self.servers[port]
            if inst.process:
                try:
                    os.killpg(os.getpgid(inst.process.pid), signal.SIGTERM)
                    inst.process.wait(timeout=10)
                except Exception:
                    try:
                        os.killpg(os.getpgid(inst.process.pid), signal.SIGKILL)
                    except Exception:
                        pass
                inst.process = None
                inst.active = False
                logger.info(f"Stopped server on port {port}")

    def health_check(self, port: int) -> bool:
        """Check if server on port is responding."""
        import urllib.request
        try:
            url = f"http://127.0.0.1:{port}/instagram-avatars"
            req = urllib.request.Request(url, method="HEAD")
            resp = urllib.request.urlopen(req, timeout=5)
            return resp.status == 200
        except Exception:
            # Try root endpoint as fallback
            try:
                url = f"http://127.0.0.1:{port}/"
                req = urllib.request.Request(url, method="HEAD")
                resp = urllib.request.urlopen(req, timeout=5)
                return resp.status in (200, 307, 401)
            except Exception:
                return False

    def get_active_port(self) -> int:
        """Get the currently active port."""
        with self._lock:
            for port, inst in self.servers.items():
                if inst.active:
                    return port
        return MAIN_PORT

    def failover(self) -> int:
        """Switch to backup server if main is down."""
        current = self.get_active_port()
        backup = BACKUP_PORT if current == MAIN_PORT else MAIN_PORT

        logger.warning(f"Failover: switching from port {current} to {backup}")

        # Start backup if not running
        if not self.servers[backup].active:
            self.start_server(backup)
            time.sleep(3)

        # Mark backup as active
        with self._lock:
            self.servers[current].active = False
            self.servers[backup].active = True

        return backup

    def monitor_loop(self):
        """Main monitoring loop - checks health and restarts failed servers."""
        logger.info("Load balancer monitor started")
        logger.info(f"  Main server: port {MAIN_PORT}")
        logger.info(f"  Backup server: port {BACKUP_PORT}")
        logger.info(f"  Health check interval: {HEALTH_CHECK_INTERVAL}s")

        while self.running:
            try:
                with self._lock:
                    ports = list(self.servers.keys())

                for port in ports:
                    inst = self.servers[port]

                    if not inst.active and port != self.get_active_port():
                        continue

                    healthy = self.health_check(port)

                    if healthy:
                        inst.failures = 0
                        inst.last_health = time.time()
                    else:
                        inst.failures += 1
                        logger.warning(
                            f"Health check failed on port {port} "
                            f"(failure #{inst.failures})"
                        )

                        if inst.failures >= MAX_FAILURES:
                            logger.error(
                                f"Max failures reached on port {port}, restarting..."
                            )
                            self.stop_server(port)
                            time.sleep(RESTART_DELAY)
                            self.start_server(port)

                            # If this was the active server, failover
                            if port == self.get_active_port():
                                self.failover()

            except Exception as e:
                logger.error(f"Monitor error: {e}")

            time.sleep(HEALTH_CHECK_INTERVAL)

    def setup_nginx_config(self):
        """Generate nginx upstream config for load balancing."""
        config = f"""# Lilly AI Load Balancer - Nginx Configuration
# Add to /etc/nginx/sites-enabled/lilly-ai

upstream lilly_ai {{
    least_conn;
    server 127.0.0.1:{MAIN_PORT} max_fails=3 fail_timeout=30s;
    server 127.0.0.1:{BACKUP_PORT} max_fails=3 fail_timeout=30s backup;

    keepalive 32;
}}

server {{
    listen 80;
    server_name droolingwithsanity.ca;

    # Redirect HTTP to HTTPS
    return 301 https://$server_name$request_uri;
}}

server {{
    listen 443 ssl http2;
    server_name droolingwithsanity.ca;

    # SSL config (update paths to your certs)
    ssl_certificate /etc/letsencrypt/live/droolingwithsanity.ca/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/droolingwithsanity.ca/privkey.pem;

    # Security headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;

    # Timeouts
    proxy_connect_timeout 60s;
    proxy_send_timeout 120s;
    proxy_read_timeout 120s;

    # Buffering
    proxy_buffering on;
    proxy_buffer_size 16k;
    proxy_buffers 8 16k;

    # Rate limiting
    limit_req_zone $binary_remote_addr zone=api:10m rate=30r/s;

    location / {{
        proxy_pass http://lilly_ai;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket support
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        # Retry on failure
        proxy_next_upstream error timeout http_502 http_503 http_504;
        proxy_next_upstream_tries 2;
    }}

    location /api/ {{
        limit_req zone=api burst=50 nodelay;
        proxy_pass http://lilly_ai;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;

        proxy_next_upstream error timeout http_502 http_503;
        proxy_next_upstream_tries 2;
    }}

    # Static assets caching
    location ~* \\.(css|js|png|jpg|jpeg|gif|ico|svg|woff|woff2)$ {{
        proxy_pass http://lilly_ai;
        proxy_cache_valid 200 1h;
        add_header Cache-Control "public, max-age=3600";
    }}

    # Health check endpoint
    location /health {{
        access_log off;
        proxy_pass http://lilly_ai/;
    }}
}}
"""
        config_path = WORK_DIR / "nginx_lilly_ai.conf"
        config_path.write_text(config)
        logger.info(f"Nginx config written to {config_path}")
        return config_path

    def start(self):
        """Start the load balancer and both servers."""
        # Start main server
        logger.info("Starting main server...")
        self.start_server(MAIN_PORT)
        time.sleep(5)

        # Start backup server
        logger.info("Starting backup server...")
        self.start_server(BACKUP_PORT)
        time.sleep(3)

        # Generate nginx config
        self.setup_nginx_config()

        # Start monitor in background
        monitor = threading.Thread(target=self.monitor_loop, daemon=True)
        monitor.start()

        logger.info("=" * 60)
        logger.info("Load balancer running!")
        logger.info(f"  Main:    http://localhost:{MAIN_PORT}")
        logger.info(f"  Backup:  http://localhost:{BACKUP_PORT}")
        logger.info(f"  Config:  nginx_lilly_ai.conf")
        logger.info("=" * 60)

        # Handle shutdown
        def shutdown(sig, frame):
            logger.info("Shutting down load balancer...")
            self.running = False
            self.stop_server(MAIN_PORT)
            self.stop_server(BACKUP_PORT)
            sys.exit(0)

        signal.signal(signal.SIGINT, shutdown)
        signal.signal(signal.SIGTERM, shutdown)

        # Keep alive
        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            shutdown(None, None)


if __name__ == "__main__":
    lb = LoadBalancer()
    lb.start()
