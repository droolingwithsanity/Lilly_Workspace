from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import requests
import os

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

OLLAMA = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")

@app.post("/chat")
def chat(payload: dict):
    prompt = payload.get("message", "")

    r = requests.post(
        f"{OLLAMA}/api/generate",
        json={
            "model": "llama3",
            "prompt": prompt,
            "stream": False
        },
        timeout=120
    )

    return r.json()
