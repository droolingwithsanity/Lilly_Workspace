# ─── Node.js service template (repo-to-project skill) ───────────────
# Multi-stage: deps → build → slim runtime.
# Copy into projects/<name>/Dockerfile, then:
#   docker build -t <name>:latest projects/<name>
#   docker run -d --name <name> -p <HOSTPORT>:<APPPORT> <name>:latest
FROM node:20-slim AS deps
WORKDIR /app
COPY package*.json ./
RUN npm ci || npm install

FROM node:20-slim AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ARG APP_PORT=3000
ENV PORT=$APP_PORT
RUN npm run build || true
EXPOSE $APP_PORT
CMD ["npm", "start"]
