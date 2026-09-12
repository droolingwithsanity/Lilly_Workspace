#!/bin/bash
set -e

echo "🧬 Building Lilly..."

mkdir -p frontend backend

############################
# .env
############################
cat << 'EOL' > .env
OLLAMA_BASE_URL=http://ollama:11434
API_PORT=8000
FRONTEND_PORT=3000
EOL

############################
# docker-compose.yml
############################
cat << 'EOL' > docker-compose.yml
version: "3.9"

services:
  ollama:
    image: ollama/ollama
    container_name: lilly_ollama
    ports:
      - "11434:11434"
    volumes:
      - ollama_data:/root/.ollama
    restart: unless-stopped

  backend:
    build: ./backend
    container_name: lilly_backend
    ports:
      - "8000:8000"
    env_file:
      - .env
    depends_on:
      - ollama
    restart: unless-stopped

  frontend:
    build: ./frontend
    container_name: lilly_frontend
    ports:
      - "3000:3000"
    depends_on:
      - backend
    restart: unless-stopped

volumes:
  ollama_data:
EOL

############################
# BACKEND
############################
cat << 'EOL' > backend/requirements.txt
fastapi
uvicorn
requests
EOL

cat << 'EOL' > backend/main.py
from fastapi import FastAPI
import requests
import os

app = FastAPI()

OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")

@app.get("/")
def root():
    return {"status": "Lilly backend alive"}

@app.get("/chat")
def chat(prompt: str):
    res = requests.post(
        f"{OLLAMA_URL}/api/generate",
        json={
            "model": "llama3",
            "prompt": prompt,
            "stream": False
        }
    )
    return res.json()
EOL

cat << 'EOL' > backend/Dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
EOL

############################
# FRONTEND
############################
cat << 'EOL' > frontend/package.json
{
  "name": "lilly-frontend",
  "version": "1.0.0",
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^18.0.0",
    "react-dom": "^18.0.0"
  },
  "devDependencies": {
    "vite": "^5.0.0",
    "@vitejs/plugin-react": "^4.0.0"
  }
}
EOL

cat << 'EOL' > frontend/vite.config.js
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 3000
  }
})
EOL

cat << 'EOL' > frontend/tsconfig.json
{
  "compilerOptions": {
    "jsx": "react-jsx",
    "strict": true
  }
}
EOL

cat << 'EOL' > frontend/index.html
<!DOCTYPE html>
<html>
<head>
  <title>Lilly</title>
</head>
<body>
  <div id="root"></div>
  <script type="module" src="/main.jsx"></script>
</body>
</html>
EOL

cat << 'EOL' > frontend/main.jsx
import React, { useState } from "react";
import ReactDOM from "react-dom/client";

function App() {
  const [input, setInput] = useState("");
  const [response, setResponse] = useState("");

  const send = async () => {
    const res = await fetch(\`http://localhost:8000/chat?prompt=\${input}\`);
    const data = await res.json();
    setResponse(data.response || JSON.stringify(data));
  };

  return (
    <div style={{ padding: 20 }}>
      <h1>Lilly</h1>
      <input value={input} onChange={e => setInput(e.target.value)} />
      <button onClick={send}>Send</button>
      <pre>{response}</pre>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
EOL

cat << 'EOL' > frontend/Dockerfile
FROM node:20

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install

COPY . .

EXPOSE 3000

CMD ["npm", "run", "dev"]
EOL

echo "🚀 Starting Lilly..."
docker compose up --build
