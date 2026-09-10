###############################################
# Stage 1 — Build Open Connector (Node.js)
###############################################
FROM node:24-alpine AS oc-build
WORKDIR /oc
COPY open-connector/package.json open-connector/package-lock.json ./
COPY open-connector/tsconfig.json open-connector/vitest.config.ts ./
COPY open-connector/web ./web
COPY open-connector/src ./src
COPY open-connector/scripts ./scripts
COPY open-connector/examples ./examples
RUN npm ci --ignore-scripts
RUN npm run generate:catalog
RUN npm run build
RUN npm run build --workspace web

###############################################
# Stage — OpenLive Agent (placeholder — replace with real openlive repo)
###############################################
FROM node:22-slim AS ol-agent-build
WORKDIR /ol-agent
RUN mkdir -p services/agent/src packages/db/src packages/harness/src packages/shared/src data voice

###############################################
# Stage — OpenLive Web (placeholder — replace with real openlive repo)
###############################################
FROM node:22-slim AS ol-web-build
WORKDIR /ol-web
RUN mkdir -p apps/web/.next apps/web/public apps/web/scripts packages/db/src packages/harness/src packages/shared/src data
RUN echo '{}' > apps/web/.next/build-manifest.json

###############################################
# Stage — Lilly Bridge (Node.js → connects OpenLive to Lilly AI)
###############################################
FROM node:22-slim AS lilly-bridge-build
WORKDIR /bridge
COPY openlive/services/bridge/package.json ./
RUN npm install
COPY openlive/services/bridge/src/ src/

###############################################
# Stage — Open Connector runtime (Node.js)
###############################################
FROM node:24-alpine AS oc-runtime
WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3002
ENV HOST=0.0.0.0
ENV OOMOL_CONNECT_DATA_DIR=/app/data/connect
COPY open-connector/package.json open-connector/package-lock.json ./
COPY open-connector/scripts/healthcheck.ts ./scripts/healthcheck.ts
COPY open-connector/scripts/ensure-generated.ts ./scripts/ensure-generated.ts
COPY open-connector/migrations ./migrations
COPY --from=oc-build /oc/src ./src
COPY --from=oc-build /oc/catalog ./catalog
COPY --from=oc-build /oc/dist ./dist
RUN npm ci --omit=dev --ignore-scripts

###############################################
# Stage 3 — Final image (Python + Node.js)
###############################################
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
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Piper TTS
RUN pip install --no-cache-dir piper-tts
ENV PIPER_VOICE=/voices/lilly_voice.onnx
COPY lillyos/voices/lilly_voice.onnx /voices/lilly_voice.onnx
COPY lillyos/voices/lilly_voice.onnx.json /voices/lilly_voice.onnx.json
COPY lillyos/voices/en-us-amy-medium.onnx /voices/en-us-amy-medium.onnx
COPY lillyos/voices/en-us-amy-medium.onnx.json /voices/en-us-amy-medium.onnx.json
COPY lillyos/voices/en_GB-cori-medium.onnx /voices/en_GB-cori-medium.onnx
COPY lillyos/voices/en_GB-cori-medium.onnx.json /voices/en_GB-cori-medium.onnx.json
COPY lillyos/voices/en_US-lessac-medium.onnx /voices/en_US-lessac-medium.onnx
COPY lillyos/voices/en_US-lessac-medium.onnx.json /voices/en_US-lessac-medium.onnx.json
COPY lillyos/voices/en_US-ryan-medium.onnx /voices/en_US-ryan-medium.onnx
COPY lillyos/voices/en_US-ryan-medium.onnx.json /voices/en_US-ryan-medium.onnx.json
COPY lillyos/voices/en_US-ljspeech-medium.onnx /voices/en_US-ljspeech-medium.onnx
COPY lillyos/voices/en_US-ljspeech-medium.onnx.json /voices/en_US-ljspeech-medium.onnx.json
COPY lillyos/voices/en_US-libritts_r-medium.onnx /voices/en_US-libritts_r-medium.onnx
COPY lillyos/voices/en_US-libritts_r-medium.onnx.json /voices/en_US-libritts_r-medium.onnx.json

# Open Connector runtime
COPY --from=oc-runtime /app /opt/open-connector
ENV OOMOL_CONNECT_DATA_DIR=/app/data/connect
ENV NODE_ENV=production
ENV PORT=3002

# OpenLive Agent runtime
# Install tsx globally (pnpm shims have build-time paths; global tsx works for ESM)
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
COPY start.sh /start.sh
RUN chmod +x /start.sh

# Application files
COPY lilly_ai.py .
COPY ble_advertiser_host.py .
COPY lilly_skills.json .
COPY phone_broker.py .
COPY wiki.html .
COPY openhuman_bridge.py .
COPY auth.py .
COPY auth0_auth.py .

RUN chmod +x /app/ble_advertiser_host.py

EXPOSE 8098 8099 3000 3002 8787 8788 8790

CMD ["/start.sh"]
