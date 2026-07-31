# ─── Python service template (repo-to-project skill) ───────────────
# Multi-stage: deps → slim runtime.
# Copy into projects/<name>/Dockerfile, then:
#   docker build -t <name>:latest projects/<name>
#   docker run -d --name <name> -p <HOSTPORT>:<APPPORT> <name>:latest
#
# Override the entry script at build time if it isn't main.py:
#   docker build --build-arg APP_ENTRY=server.py -t <name>:latest projects/<name>
FROM python:3.11-slim AS deps
WORKDIR /app
COPY requirements*.txt ./
RUN pip install --no-cache-dir -r requirements.txt

FROM python:3.11-slim
WORKDIR /app
COPY --from=deps /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY . .
ARG APP_ENTRY=main.py
ENV APP_ENTRY=$APP_ENTRY
ARG APP_PORT=8000
ENV PORT=$APP_PORT
EXPOSE $APP_PORT
CMD ["sh", "-c", "python $APP_ENTRY"]
