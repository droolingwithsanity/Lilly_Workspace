Prerequisites
- Docker & Docker Compose installed
- Git
Steps
# 1. Clone with submodules
git clone --recurse-submodules https://github.com/droolingwithsanity/Lilly_Workspace.git
cd Lilly_Workspace

# 2. Create your .env file (copy from example or create new)
cp .env.example .env 2>/dev/null || touch .env
# Edit .env with your API keys and settings (Ollama URL, Auth0, etc.)

# 3. If the external whisper network doesn't exist, remove it from docker-compose.yml
#    or create it: docker network create supernova_default

# 4. Build and run
docker compose up -d --build
Key things to configure in .env:
- OLLAMA_URL — point to your Ollama instance (or remove if not using)
- SENSOR_SERVER_URL — your Termux sensor server IP
- AUTH0_* credentials if you want auth
- PREFER_BACKEND — set to ollama or openai depending on your LLM setup
Ports exposed:
Port	Service
8098	Lilly AI (main)
3007	Open Connector (mapped from 3002)
If you don't need Docker, run directly:
pip install -r requirements.txt
python lilly_ai.py
The main entry point is lilly_ai.py on port 8098.
