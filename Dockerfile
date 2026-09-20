# Both stages use Debian/bookworm so the copied Node binary matches runtime libc.
ARG NODE_IMAGE=node:22-bookworm-slim
ARG PYTHON_IMAGE=python:3.12-slim-bookworm

FROM ${NODE_IMAGE} AS okxquant_frontend-builder
WORKDIR /app/okxquant_frontend
COPY okxquant_frontend/package*.json ./
RUN npm ci
COPY okxquant_frontend/ ./
# Match the repository layout: assets:prepare reads ../docs/images.
COPY docs/images/ /app/docs/images/
RUN npm run build

FROM ${PYTHON_IMAGE} AS runner
ARG OKX_CLI_SPEC=@okx_ai/okx-trade-cli@^1.4.4
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OKXQUANT_DOCKER=1 \
    OKXQUANT_DEPLOYMENT_MODE=docker \
    OKXQUANT_ENV_FILE=/app/config/.env \
    PORT=8080 \
    HOME=/home/okxquant

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl procps git libstdc++6 tzdata gosu \
    && rm -rf /var/lib/apt/lists/*
RUN groupadd --gid 10001 okxquant \
    && useradd --uid 10001 --gid okxquant --create-home --home-dir /home/okxquant okxquant

COPY --from=okxquant_frontend-builder /usr/local/bin/node /usr/local/bin/node
COPY --from=okxquant_frontend-builder /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && npm install --global "${OKX_CLI_SPEC}" \
    && npm cache clean --force \
    && node --version && okx --version
# Optional administrator CLI upgrades live on the existing npm volume, while
# the image always contains a working CLI even when that volume is empty.
ENV NPM_CONFIG_PREFIX=/home/okxquant/.npm-global \
    PATH=/home/okxquant/.npm-global/bin:${PATH}

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY okxquant_backend/ ./okxquant_backend/
COPY okxquant_gateway/ ./okxquant_gateway/
COPY scripts/ ./scripts/
COPY plugins/ ./plugins/
COPY dashboard/ ./dashboard/
COPY tests/ ./tests/
# Keep source contracts for the isolated image-level offline release gate.
COPY okxquant_frontend/ ./okxquant_frontend/
COPY .github/ ./.github/
COPY docker/ ./docker/
COPY deploy/ ./deploy/
COPY Dockerfile compose.yaml ./
COPY docs/ ./docs/
COPY README.md STANDALONE.md env.example ./
COPY --from=okxquant_frontend-builder /app/okxquant_frontend/dist ./okxquant_frontend/dist
COPY docker/entrypoint.sh /usr/local/bin/okxquant-entrypoint
# Host git checkout may use umask 077; copied source must be readable by UID 10001.
RUN chmod -R a+rX /app
RUN sed -i 's/\r$//' /usr/local/bin/okxquant-entrypoint \
    && chmod +x /usr/local/bin/okxquant-entrypoint \
    && mkdir -p /app/config /app/data /app/logs /app/backups \
    && chown -R okxquant:okxquant /app/config /app/data /app/logs /app/backups /home/okxquant

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8080/api/v1/health || exit 1
ENTRYPOINT ["/usr/local/bin/okxquant-entrypoint"]
CMD ["python", "-m", "uvicorn", "okxquant_backend.app:app", "--host", "0.0.0.0", "--port", "8080"]
