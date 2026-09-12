###############################################
# Lilly AI - core application image
# Build context: lilly-app/  (dockerfile: core/lilly-ai/Dockerfile)
# Portable: all COPY paths are relative to the build context root.
###############################################

# Stage 1 — Build Open Connector (Node.js)
FROM node:24-alpine AS oc-build
WORKDIR /oc
COPY core/lilly-ai/open-connector/package.json core/lilly-ai/open-connector/package-lock.json ./
COPY core/lilly-ai/open-connector/tsconfig.json core/lilly-ai/open-connector/vitest.config.ts ./
COPY core/lilly-ai/open-connector/web ./web
COPY core/lilly-ai/open-connector/src ./src
COPY core/lilly-ai/open-connector/scripts ./scripts
COPY core/lilly-ai/open-connector/examples ./examples
RUN npm ci --ignore-scripts
RUN npm run generate:catalog
RUN npm run build
RUN npm run build --workspace web

# Stage — OpenLive Agent (placeholder — replace with real openlive repo)
FROM node:22-slim AS ol-agent-build
WORKDIR /ol-agent
RUN mkdir -p services/agent/src packages/db/src packages/harness/src packages/shared/src data voice

# Stage — OpenLive Web (placeholder — replace with real openlive repo)
FROM node:22-slim AS ol-web-build
WORKDIR /ol-web
RUN mkdir -p apps/web/.next apps/web/public apps/web/scripts packages/db/src packages/harness/src packages/shared/src data
RUN echo '{}' > apps/web/.next/build-manifest.json

# Stage — Lilly Bridge (Node.js → connects OpenLive to Lilly AI)
FROM node:22-slim AS lilly-bridge-build
WORKDIR /bridge
COPY core/lilly-ai/openlive/services/bridge/package.json ./
RUN npm install
COPY core/lilly-ai/openlive/services/bridge/src/ src/

# Stage — Open Connector runtime (Node.js)
FROM node:24-alpine AS oc-runtime
WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3002
ENV HOST=0.0.0.0
ENV OOMOL_CONNECT_DATA_DIR=/app/data/connect
COPY core/lilly-ai/open-connector/package.json core/lilly-ai/open-connector/package-lock.json ./
COPY core/lilly-ai/open-connector/scripts/healthcheck.ts ./scripts/healthcheck.ts
COPY core/lilly-ai/open-connector/scripts/ensure-generated.ts ./scripts/ensure-generated.ts
COPY core/lilly-ai/open-connector/migrations ./migrations
COPY --from=oc-build /oc/src ./src
COPY --from=oc-build /oc/catalog ./catalog
COPY --from=oc-build /oc/dist ./dist
RUN npm ci --omit=dev --ignore-scripts

# Stage 3 — Final image (Python + Node.js)
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg wget ca-certificates openssh-client curl git \
    bluez bluez-tools sudo && \
    rm -rf /var/lib/apt/lists/*

# Install Node.js 24.x for Open Connector
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl gnupg && \
    mkdir -p /etc/apt/keyrings && \
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
      | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg && \
    echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_24.x nodistro main" \
      > /etc/apt/sources.list.d/nodesource.list && \
    apt-get update && apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python dependencies
COPY core/lilly-ai/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Piper TTS
RUN pip install --no-cache-dir piper-tts
ENV PIPER_VOICE=/voices/lilly_voice.onnx
COPY core/lilly-ai/lillyos/voices/lilly_voice.onnx /voices/lilly_voice.onnx
COPY core/lilly-ai/lillyos/voices/lilly_voice.onnx.json /voices/lilly_voice.onnx.json
COPY core/lilly-ai/lillyos/voices/en-us-amy-medium.onnx /voices/en-us-amy-medium.onnx
COPY core/lilly-ai/lillyos/voices/en-us-amy-medium.onnx.json /voices/en-us-amy-medium.onnx.json
COPY core/lilly-ai/lillyos/voices/en_GB-cori-medium.onnx /voices/en_GB-cori-medium.onnx
COPY core/lilly-ai/lillyos/voices/en_GB-cori-medium.onnx.json /voices/en_GB-cori-medium.onnx.json
COPY core/lilly-ai/lillyos/voices/en_US-lessac-medium.onnx /voices/en_US-lessac-medium.onnx
COPY core/lilly-ai/lillyos/voices/en_US-lessac-medium.onnx.json /voices/en_US-lessac-medium.onnx.json
COPY core/lilly-ai/lillyos/voices/en_US-ryan-medium.onnx /voices/en_US-ryan-medium.onnx
COPY core/lilly-ai/lillyos/voices/en_US-ryan-medium.onnx.json /voices/en_US-ryan-medium.onnx.json
COPY core/lilly-ai/lillyos/voices/en_US-ljspeech-medium.onnx /voices/en_US-ljspeech-medium.onnx
COPY core/lilly-ai/lillyos/voices/en_US-ljspeech-medium.onnx.json /voices/en_US-ljspeech-medium.onnx.json
COPY core/lilly-ai/lillyos/voices/en_US-libritts_r-medium.onnx /voices/en_US-libritts_r-medium.onnx
COPY core/lilly-ai/lillyos/voices/en_US-libritts_r-medium.onnx.json /voices/en_US-libritts_r-medium.onnx.json

# Open Connector runtime
COPY --from=oc-runtime /app /opt/open-connector
ENV OOMOL_CONNECT_DATA_DIR=/app/data/connect
ENV NODE_ENV=production
ENV PORT=3002

# OpenLive Agent runtime
RUN npm install -g tsx 2>/dev/null

# Copy agent source (placeholder — replace with real openlive repo)
COPY --from=ol-agent-build /ol-agent/ /ol-agent/
RUN mkdir -p /ol-agent/data /ol-agent/voice

# OpenLive Web runtime (placeholder — replace with real openlive repo)
COPY --from=ol-web-build /ol-web/ /ol-web/
RUN mkdir -p /ol-web/data

# Lilly Bridge (connects OpenLive to Lilly AI)
COPY --from=lilly-bridge-build /bridge/node_modules/ /opt/openlive-bridge/node_modules/
COPY --from=lilly-bridge-build /bridge/src/ /opt/openlive-bridge/src/

# Copy the startup script
COPY core/lilly-ai/start.sh /start.sh
RUN chmod +x /start.sh

# Application files
COPY core/lilly-ai/lilly_ai.py .
COPY core/lilly-ai/ble_advertiser_host.py .
COPY core/lilly-ai/lilly_skills.json .
COPY core/lilly-ai/phone_broker.py .
COPY core/lilly-ai/wiki.html .
COPY core/lilly-ai/openhuman_bridge.py .
COPY core/lilly-ai/auth.py .
COPY core/lilly-ai/auth0_auth.py .

RUN chmod +x /app/ble_advertiser_host.py

EXPOSE 8098 8099 3000 3002 8787 8788 8790 8001

CMD ["/start.sh"]