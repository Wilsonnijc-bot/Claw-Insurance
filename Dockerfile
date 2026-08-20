FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# Install Node.js 20 for the WhatsApp bridge
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates gnupg git && \
    mkdir -p /etc/apt/keyrings && \
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg && \
    echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" > /etc/apt/sources.list.d/nodesource.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends nodejs && \
    apt-get purge -y gnupg && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV NANOBOT_PROJECT_ROOT=/app
ENV NANOBOT_PREBUILT_BRIDGE_DIR=/app/bridge
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV PATH="/app/.venv/bin:$PATH"

# Install the locked Python dependencies first (cached layer).
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy the full source and install
COPY nanobot/ nanobot/
COPY bridge/ bridge/
COPY config.example.json /app/config.json
COPY google.example.json /app/google.json
COPY supabase.example.json /app/supabase.json
RUN uv sync --frozen --no-dev && \
    python3 -c "import google.cloud.speech_v2"

# Build the WhatsApp bridge
WORKDIR /app/bridge
RUN npm ci && npm run build && npm prune --omit=dev
WORKDIR /app

# Create project-local runtime directories
RUN mkdir -p /app/data /app/sessions /app/state /app/memory \
    /app/whatsapp-auth /app/whatsapp-web /app/whatsapp-web-debug \
    /app/skills /app/media /app/cron \
    /app/secrets

# Gateway API port used by frontend and external clients
EXPOSE 3456

ENTRYPOINT ["python3", "-m", "nanobot.runtime.docker_entrypoint"]
CMD ["--api-port", "3456"]
