#!/usr/bin/env bash
# Re-sync core runtime files from the workspace repo root into lilly-app/.
# Run this after editing lilly_ai.py, start.sh, requirements.txt, html pages,
# skills, voices, open-connector or openlive at the repo root.
set -euo pipefail
cd "$(dirname "$0")/.."   # workspace root

DEST="lilly-app"
CORE="$DEST/core"

echo "Syncing key files into lilly-app/ ..."
cp -a lilly_ai.py start.sh requirements.txt phone_broker.py ble_advertiser_host.py \
      openhuman_bridge.py auth.py auth0_auth.py face_identity.py footprint.py \
      osint_face_lookup.py lilly_skills.json wiki.html bt_radar.html tracker.html \
      nodes.html skills-market.html openlive-human.html openhuman.html alpha_popout.html \
      "$CORE/lilly-ai/"

rsync -a --delete --exclude='models' lillyos/ "$CORE/lilly-ai/lillyos/"

rsync -a --delete --exclude='node_modules' --exclude='.git' --exclude='.env*' \
      open-connector/ "$CORE/lilly-ai/open-connector/"

rsync -a --delete openlive/ "$CORE/lilly-ai/openlive/"

cp -a yolov8_vision_server.py face_recognition_engine.py person_tracker.py \
      node_registry.py blink_detector.py car_classifier.py car_labels.txt \
      yunet.onnx yolov8n-oiv7.pt requirements.txt "$CORE/lilly-vision/"

cp -a yolo_self_trainer.py yolo_trainer_api.py requirements.txt "$CORE/lilly-trainer/"

echo "Done. Rebuild images with:  docker compose up -d --build"