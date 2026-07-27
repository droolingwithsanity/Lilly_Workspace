#!/usr/bin/env python3
import http.server
import json
import os
import subprocess
import sys
import urllib.parse
import threading

PORT = 8080
ROOT = os.path.dirname(os.path.abspath(__file__))
CAPTURE_URL = "http://localhost:8098"
SCREENSHOT_DIR = os.path.join(ROOT, "screenshots")

MIME_OVERRIDES = {
    ".html": "text/html",
    ".css": "text/css",
    ".js": "application/javascript",
    ".json": "application/json",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".mp4": "video/mp4",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/plain",
    ".py": "text/plain",
}

TEXT_EXTS = {
    ".txt", ".md", ".py", ".js", ".ts", ".json", ".css",
    ".sh", ".yml", ".yaml", ".toml", ".cfg", ".ini", ".env",
    ".xml", ".csv", ".log", ".sql", ".c", ".cpp", ".h", ".java",
    ".gradle", ".properties",
}


def human_size(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TB"


class FileBrowser(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)

        if path == "/__capture":
            self.serve_capture_page()
            return
        if path == "/__capture/start":
            self.start_capture()
            return
        if path == "/__capture/status":
            self.capture_status()
            return

        full = os.path.normpath(os.path.join(ROOT, path.lstrip("/")))

        if not full.startswith(ROOT):
            self.send_error(403)
            return

        if os.path.isdir(full):
            self.serve_dir(full, path)
        elif os.path.isfile(full):
            self.serve_file(full, path)
        else:
            self.send_error(404)

    def serve_dir(self, full, url_path):
        items = sorted(os.listdir(full))
        rows = ""

        if url_path != "/":
            parent = os.path.dirname(url_path.rstrip("/"))
            rows += f'<tr><td class="icon">📁</td><td><a href="{parent or "/"}">..</a></td><td></td><td></td></tr>\n'

        for name in items:
            fp = os.path.join(full, name)
            link = urllib.parse.urljoin(url_path.rstrip("/") + "/", name)
            if os.path.isdir(fp):
                icon = "📁"
                size = ""
                link += "/"
            else:
                ext = os.path.splitext(name)[1].lower()
                icon = "📄" if ext not in (".png", ".jpg", ".jpeg", ".gif", ".svg") else "🖼️"
                size = human_size(os.path.getsize(fp))
            rows += f'<tr><td class="icon">{icon}</td><td><a href="{link}">{name}</a></td><td class="size">{size}</td><td></td></tr>\n'

        html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>File Browser - {url_path}</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 20px; background: #1a1a2e; color: #e0e0e0; }}
h1 {{ color: #00d4ff; border-bottom: 1px solid #333; padding-bottom: 8px; }}
table {{ border-collapse: collapse; width: 100%; }}
td, th {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #333; }}
a {{ color: #00d4ff; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
.icon {{ width: 30px; }}
.size {{ color: #888; }}
th {{ color: #00d4ff; }}
.path {{ color: #888; font-size: 0.9em; }}
.capture-btn {{ display: inline-block; padding: 8px 18px; background: linear-gradient(135deg, #f472b6, #a78bfa); color: #fff; border-radius: 8px; text-decoration: none; font-weight: 600; font-size: 0.85rem; margin-left: 16px; vertical-align: middle; }}
.capture-btn:hover {{ box-shadow: 0 4px 16px rgba(167,139,250,0.5); }}
</style></head><body>
<h1>📂 {url_path} <a class="capture-btn" href="/__capture">📸 Capture Screenshots</a></h1>
<p class="path">Serving from: {full}</p>
<table><tr><th></th><th>Name</th><th>Size</th></tr>
{rows}</table></body></html>"""

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode())

    def serve_file(self, full, url_path):
        name = os.path.basename(full)
        ext = os.path.splitext(name)[1].lower()

        if ext in MIME_OVERRIDES:
            content_type = MIME_OVERRIDES[ext]
        else:
            content_type = "application/octet-stream"

        size = os.path.getsize(full)

        if ext in TEXT_EXTS and size < 2_000_000:
            try:
                with open(full, "r", errors="replace") as f:
                    content = f.read()
                html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{name}</title>
<style>
body {{ font-family: monospace; background: #1a1a2e; color: #e0e0e0; margin: 20px; }}
.header {{ background: #16213e; padding: 12px; border-radius: 6px; margin-bottom: 16px; }}
.header a {{ color: #00d4ff; text-decoration: none; margin-right: 16px; }}
pre {{ background: #0f3460; padding: 16px; border-radius: 6px; overflow-x: auto; white-space: pre-wrap; word-wrap: break-word; line-height: 1.5; }}
</style></head><body>
<div class="header">
  <a href="javascript:history.back()">⬅ Back</a>
  <a href="{url_path}" download>⬇ Download</a>
  <span>{name} ({human_size(size)})</span>
</div>
<pre>{content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")}</pre>
</body></html>"""
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(html.encode())
                return
            except Exception:
                pass

        if content_type.startswith("image/"):
            try:
                with open(full, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "public, max-age=3600")
                self.end_headers()
                self.wfile.write(data)
                return
            except Exception:
                pass

        try:
            with open(full, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition", f'inline; filename="{name}"')
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self.send_error(500, str(e))

    def serve_capture_page(self):
        html = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Screenshot Capture</title>
<style>
body { font-family: system-ui, sans-serif; margin: 40px; background: #1a1a2e; color: #e0e0e0; }
h1 { color: #00d4ff; }
.btn { padding: 16px 32px; background: linear-gradient(135deg, #f472b6, #a78bfa); color: #fff;
       border: none; border-radius: 12px; font-size: 1.1rem; font-weight: 600; cursor: pointer;
       margin: 20px 0; transition: all 0.3s; }
.btn:hover { box-shadow: 0 4px 20px rgba(167,139,250,0.5); transform: scale(1.02); }
.btn:disabled { opacity: 0.5; cursor: not-allowed; transform: none; box-shadow: none; }
#status { margin-top: 20px; padding: 16px; background: #16213e; border-radius: 8px; min-height: 60px; }
.log { font-family: monospace; font-size: 0.85rem; line-height: 1.8; }
.log .ok { color: #34d399; }
.log .fail { color: #f87171; }
.log .info { color: #60a5fa; }
.back { color: #00d4ff; text-decoration: none; display: inline-block; margin-bottom: 20px; }
</style></head><body>
<a class="back" href="/">⬅ Back to File Browser</a>
<h1>📸 Screenshot Capture</h1>
<p>Captures real screenshots from the running app at <code>""" + CAPTURE_URL + """</code></p>
<button class="btn" id="captureBtn" onclick="startCapture()">Capture All Screenshots</button>
<div id="status"><span class="log info">Ready. Click capture to start.</span></div>
<script>
let polling = false;
function startCapture() {
  document.getElementById('captureBtn').disabled = true;
  document.getElementById('status').innerHTML = '<span class="log info">Starting capture...</span>';
  fetch('/__capture/start').then(r => r.json()).then(d => {
    if (d.ok) pollStatus();
    else document.getElementById('status').innerHTML = '<span class="log fail">Failed: ' + d.error + '</span>';
  }).catch(e => {
    document.getElementById('status').innerHTML = '<span class="log fail">Error: ' + e + '</span>';
  });
}
function pollStatus() {
  polling = true;
  const iv = setInterval(() => {
    fetch('/__capture/status').then(r => r.json()).then(d => {
      document.getElementById('status').innerHTML = '<div class="log">' + d.log.map(l =>
        '<div class="' + (l.includes('OK') ? 'ok' : l.includes('FAIL') ? 'fail' : 'info') + '">' + l + '</div>'
      ).join('') + '</div>';
      if (d.done) {
        clearInterval(iv);
        document.getElementById('captureBtn').disabled = false;
        polling = false;
      }
    });
  }, 1000);
}
</script>
</body></html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode())

    def start_capture(self):
        if not os.path.isdir(SCREENSHOT_DIR):
            os.makedirs(SCREENSHOT_DIR, exist_ok=True)
        _capture_state["log"] = ["Starting capture..."]
        _capture_state["done"] = False
        t = threading.Thread(target=_run_capture, daemon=True)
        t.start()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True}).encode())

    def capture_status(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(_capture_state).encode())


_capture_state = {"log": [], "done": True}


def _run_capture():
    state = _capture_state
    try:
        import asyncio
        asyncio.run(_do_capture(state))
    except Exception as e:
        state["log"].append(f"FAIL: {e}")
        state["done"] = True


async def _do_capture(state):
    from playwright.async_api import async_playwright

    steps = [
        ("step1-home.png", None, "Landing / avatar picker"),
        ("step2-chat.png", "confirmPickerBypass()||confirmPicker()", "Enter main app (chat)"),
        ("step3-camera-pip.png", "toggleCameraView()", "Camera PiP view"),
        ("step4-detection.png", None, "Detection (camera off, text state)"),
        ("step5-maps-overlay.png", "toggleDashboard()", "Dashboard / maps overlay"),
        ("step6-navigate.png", "toggleCharSwitcher()", "Character switcher"),
        ("step7-notification.png", "toggleConversationMode()", "Conversation mode"),
        ("step8-fullscreen.png", "toggleCodingMode()", "Coding / fullscreen mode"),
        ("step9-youtube-pip.png", None, "Final state"),
    ]

    state["log"].append(f"INFO: Launching browser -> {CAPTURE_URL}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            executable_path="/usr/bin/google-chrome",
            args=["--no-sandbox", "--disable-gpu"]
        )
        ctx = await browser.new_context(
            viewport={"width": 390, "height": 844},
            device_scale_factor=2,
        )
        page = await ctx.new_page()

        try:
            await page.goto(CAPTURE_URL, wait_until="networkidle", timeout=15000)
            await page.wait_for_timeout(3000)
            state["log"].append("OK: Page loaded")
        except Exception as e:
            state["log"].append(f"FAIL: Could not load {CAPTURE_URL} — {e}")
            state["done"] = True
            await browser.close()
            return

        for filename, js_call, label in steps:
            try:
                if js_call:
                    await page.evaluate(js_call)
                    await page.wait_for_timeout(1500)
                out = os.path.join(SCREENSHOT_DIR, filename)
                await page.screenshot(path=out)
                state["log"].append(f"OK: {filename} — {label}")
            except Exception as e:
                state["log"].append(f"FAIL: {filename} — {label}: {e}")

        await browser.close()

    state["log"].append(f"DONE: {len(steps)} screenshots saved to screenshots/")
    state["done"] = True


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    server = http.server.HTTPServer(("0.0.0.0", port), FileBrowser)
    print(f"File browser running at http://localhost:{port}")
    print(f"Serving: {ROOT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()
