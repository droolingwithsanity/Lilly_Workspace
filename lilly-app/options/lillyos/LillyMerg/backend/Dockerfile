FROM python:3.10-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
RUN pip install fastapi uvicorn pydantic requests python-multipart aiofiles onnxruntime numpy redis ddgs langdetect faster-whisper pyOpenSSL
COPY . .
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
