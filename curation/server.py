#!/usr/bin/env python3
"""Curation Questionnaire Server — self-contained, no deps."""

import json, os, sys, signal
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse
import threading

DIR = Path(__file__).parent
ANSWERS = DIR / "curation_answers.json"


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        p = urlparse(self.path).path
        if p in ("/", "/form"):
            self._html(DIR / "form.html")
        elif p == "/api/answers":
            self._json(ANSWERS.read_text() if ANSWERS.exists() else "{}")
        elif p == "/api/health":
            self._json('{"ok":true}')
        else:
            self.send_error(404)

    def do_POST(self):
        if urlparse(self.path).path != "/api/save":
            return self.send_error(404)
        try:
            n = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(n))
            for f in ["datasets", "personas", "base_model"]:
                if not data.get(f):
                    raise ValueError(f"Missing: {f}")
            tmp = str(ANSWERS) + ".tmp"
            with open(tmp, "w") as fp:
                json.dump(data, fp, indent=2)
            os.replace(tmp, str(ANSWERS))
            self._json(json.dumps({"ok": True}))
            print(f"  ✅ Saved → {ANSWERS.name}", flush=True)
        except Exception as e:
            self._json(json.dumps({"ok": False, "error": str(e)}), 400)

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def _html(self, path):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(path.read_bytes())

    def _json(self, body, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.end_headers()
        self.wfile.write(body.encode() if isinstance(body, str) else body)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, fmt, *a):
        pass  # quiet


class ThreadedHTTPServer(HTTPServer):
    allow_reuse_address = True

    def process_request(self, req, addr):
        t = threading.Thread(target=self.process_request_thread, args=(req, addr))
        t.daemon = True
        t.start()

    def process_request_thread(self, req, addr):
        try:
            self.finish_request(req, addr)
        except Exception:
            self.handle_error(req, addr)
        finally:
            self.shutdown_request(req)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5050
    srv = ThreadedHTTPServer(("0.0.0.0", port), H)
    print(f"🐶 Curation form → http://0.0.0.0:{port}/", flush=True)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    srv.serve_forever()
