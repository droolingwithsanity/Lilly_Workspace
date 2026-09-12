FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y netcat-openbsd && rm -rf /var/lib/apt/lists/*

# Install Python Neural Mesh
RUN pip install fastapi uvicorn redis

COPY . .

# Start the Python Brain
CMD ["python", "brain_node.py"]
