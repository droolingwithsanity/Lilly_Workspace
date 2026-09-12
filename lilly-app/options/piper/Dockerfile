###############################################
# Lilly Piper — standalone OpenAI-compatible TTS
# Build context: lilly-app/  (dockerfile: options/piper/Dockerfile)
# Optional service — start with: docker compose --profile extras up -d piper
###############################################
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg ca-certificates curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir piper-tts flask flask-cors

# TTS voice model (Amy medium — same dataset as Lilly's default voice)
COPY options/ai-chat/en_US-amy-medium.onnx /app/voices/en-US-amy-medium.onnx
COPY options/ai-chat/en_US-amy-medium.onnx.json /app/voices/en-US-amy-medium.onnx.json

COPY options/piper/piper_api.py /app/piper_api.py

EXPOSE 5001

CMD ["python3", "/app/piper_api.py"]