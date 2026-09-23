#!/bin/bash
cd /home/xceb/Lilly_Workspace/lilly-app/core/lilly-ai
exec python3 -m uvicorn lilly_ai:app --host 0.0.0.0 --port 8098 --log-level warning
