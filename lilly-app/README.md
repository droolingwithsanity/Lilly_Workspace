# ══════════════════════════════════════════════════════════════════
#  Lilly AI — portable application folder
# ══════════════════════════════════════════════════════════════════
#  Copy THIS folder (lilly-app/) to any machine with Docker and run:
#
#      docker compose up -d                  # core + piper
#      docker compose --profile extras up -d # optional services
#      docker compose logs -f lilly          # watch the main server
#
#  Everything is self-contained inside lilly-app/ — no absolute paths,
#  no host-specific mounts, all data under ./data.
#
#  Layout:
#    core/
#      lilly-ai/      main app (web UI :8098, whisper :8001, TTS, phone broker)
#      lilly-vision/  YOLOv8 vision server (:8198)
#      lilly-trainer/ YOLO self-training agent (:8199)
#    options/
#      piper/         standalone OpenAI-compatible TTS (:5001)
#      lillyos/       LillyOS web app (backend :8000 / frontend :3006 / redis)
#      webui/         Open WebUI (:3001)
#      ai-chat/       voice model files used by piper
#      lilly-mcp/     (image)       lilly-ide/   code server (:8080)
#      dashboard/     (image)       figranium/   figranium dashboard
#    data/            ALL runtime data (ollama models, memories, ssh keys…)
#
#  First-run notes:
#   1. Put your phone SSH key at data/ssh/  (file named id_ed25519) if you
#      use Termux voice streaming, or leave it empty — everything else works.
#   2. Edit .env to point SENSOR_SERVER_URL at your phone (default
#      100.115.234.87:8099) and your Auth0 credentials if enabled.
#   3. Ollama models already live in data/ollama (copied from the old
#      /root/.ollama on the original machine).
#   4. After code changes in the repo root, run ./deploy.sh to re-sync
#      core files into this folder.
# ══════════════════════════════════════════════════════════════════