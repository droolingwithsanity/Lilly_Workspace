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
# Stage 2 — Open Connector runtime (Node.js)
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
    ffmpeg wget ca-certificates openssh-client curl && \
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
COPY lillyos/voices/en-us-amy-medium.onnx /voices/en-us-amy-medium.onnx
COPY lillyos/voices/en-us-amy-medium.onnx.json /voices/en-us-amy-medium.onnx.json
COPY lillyos/voices/lilly_voice.onnx /voices/lilly_voice.onnx
COPY lillyos/voices/lilly_voice.json /voices/lilly_voice.onnx.json

# Open Connector runtime
COPY --from=oc-runtime /app /opt/open-connector
ENV OOMOL_CONNECT_DATA_DIR=/app/data/connect
ENV NODE_ENV=production
ENV PORT=3002

# Copy the startup script
COPY start.sh /start.sh
RUN chmod +x /start.sh

# Application files
COPY lilly_ai.py .
COPY lilly_skills.json .
COPY auth.py .

EXPOSE 8098 8099 3002

CMD ["/start.sh"]
