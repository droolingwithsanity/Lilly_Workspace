#!/bin/bash
cd /home/labhrasd/LillyMerg/frontend && npm install
cd /home/labhrasd/LillyMerg && docker compose up --build -d
echo '🔥 Phoenix Risen at 100.93.131.114'