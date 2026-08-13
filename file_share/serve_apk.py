#!/usr/bin/env python3
"""Serve APK from current directory over HTTP.
Run this on your phone in the Downloads folder:
  cd /sdcard/Download
  python3 serve_apk.py

Then from the host, download with:
  curl http://<phone-ip>:8000/<apk-name>.apk -o apk.apk
"""

import os
import socket
from http.server import HTTPServer, SimpleHTTPRequestHandler

PORT = 8000


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    hostname = socket.gethostname()
    try:
        ip = socket.gethostbyname(hostname)
    except Exception:
        ip = "127.0.0.1"
    print(f"Serving APK at http://{ip}:{PORT}/")
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
