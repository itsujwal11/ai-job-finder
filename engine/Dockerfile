# syntax=docker/dockerfile:1
# Build context is the repository root (see docker-compose.yml).

# --- 1. Build the React dashboard ------------------------------------------------
FROM node:22-alpine AS dashboard
WORKDIR /build
COPY dashboard/package.json dashboard/package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci --no-audit --no-fund; else npm install --no-audit --no-fund; fi
COPY dashboard/ ./
RUN npm run build

# --- 2. Python engine -------------------------------------------------------------
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_ROOT=/app
WORKDIR /app/engine

COPY engine/requirements.txt ./
RUN pip install -r requirements.txt

COPY engine/ ./
COPY --from=dashboard /build/dist /app/engine/app/static

RUN useradd --create-home --uid 1001 app \
    && mkdir -p /app/output \
    && chown -R app /app
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
