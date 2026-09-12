"""Lilly Piper API — OpenAI-compatible /v1/audio/speech using local piper-tts.

Speaks Lilly's voice on any machine without external TTS services.
"""

import os
import subprocess
import tempfile

from flask import Flask, Response, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

VOICE = os.environ.get("PIPER_VOICE", "/app/voices/en-US-amy-medium.onnx")


def _speak(text: str) -> bytes:
    out = tempfile.mktemp(suffix=".wav")
    try:
        proc = subprocess.run(
            ["piper", "--model", VOICE, "--output_file", out],
            input=text.encode("utf-8"),
            capture_output=True,
            timeout=60,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.decode(errors="replace"))
        with open(out, "rb") as f:
            return f.read()
    finally:
        if os.path.exists(out):
            os.unlink(out)


@app.get("/v1/audio/models")
def list_models():
    return jsonify({"data": [{"id": os.path.basename(VOICE), "object": "model"}]})


@app.get("/v1/audio/voices")
def list_voices():
    return jsonify({"voices": [{"voice_id": "lilly", "name": "Lilly"}]})


@app.post("/v1/audio/speech")
def generate_speech():
    try:
        data = request.get_json(force=True) or {}
        text = (data.get("input") or "").strip()
        if not text:
            return jsonify({"error": "empty input"}), 400
        wav = _speak(text)
        return Response(wav, mimetype="audio/wav")
    except Exception as e:  # noqa: BLE001
        import traceback

        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


@app.get("/health")
def health():
    return jsonify({"ok": True, "voice": VOICE})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)
