# npm нужен только для `npm ci` при сборке; в рантайме вызывается лишь `node`
# (app/scrapers/fingerprint_profile.py). Зависимости ставит отдельная стадия,
# чтобы Debian-пакет `npm` (~350 node-* пакетов, ~2 мин apt) не попадал в
# образ. node:18 = та же major-версия, что `nodejs` 18.x из Debian bookworm.
FROM node:18-bookworm-slim AS fingerprint-node
WORKDIR /build
COPY scripts/fingerprint-node/package.json scripts/fingerprint-node/package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts \
    && npm cache clean --force

FROM python:3.12-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HS_API_BIND_HOST=0.0.0.0 \
    HS_API_PORT=8000 \
    DISPLAY=:99

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      ca-certificates curl git nodejs xvfb \
      libnss3 libnspr4 libatk-bridge2.0-0 libatk1.0-0 libcups2 libdrm2 \
      libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
      libgbm1 libasound2 libpangocairo-1.0-0 libpango-1.0-0 libcairo2 \
      libatspi2.0-0 libgtk-3-0 fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install -r /app/requirements.txt \
    && python -m patchright install chromium

COPY scripts/fingerprint-node/package.json scripts/fingerprint-node/package-lock.json /app/scripts/fingerprint-node/
COPY --from=fingerprint-node /build/node_modules /app/scripts/fingerprint-node/node_modules

COPY app /app/app
COPY web /app/web
COPY scripts /app/scripts
COPY .env.example /app/.env.example

RUN chmod +x /app/scripts/*.sh \
    && mkdir -p /var/lib/hs-data-api/datasets /var/lib/hs-data-api/statuses /var/lib/hs-data-api/logs

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["python", "-m", "app.server"]
