# The panel: FastAPI serving the API and the built React UI, in one image.

# LTS releases only: a "current" Node has had npm bugs that break installs.
FROM node:24-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
# Retry downloads: on a flaky connection one failed request would otherwise fail the whole build.
RUN npm ci --fetch-retries=5 --fetch-retry-mintimeout=5000 --fetch-retry-maxtimeout=60000
COPY web/ ./
RUN npm run build


FROM python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app/panel
# Dependencies first: they change less often than the code, so this layer stays cached.
COPY panel/pyproject.toml ./
COPY panel/app/__init__.py ./app/__init__.py
RUN pip install -e .
COPY panel/ ./
COPY templates/ /app/templates/
COPY --from=web /web/dist/ /app/web/

ENV POSSUM_TEMPLATES_DIR=/app/templates \
    POSSUM_WEB_DIR=/app/web \
    POSSUM_DATABASE_URL=sqlite+aiosqlite:////data/panel.db \
    POSSUM_BACKUPS_DIR=/data/backups \
    POSSUM_CACHE_DIR=/data/cache

RUN mkdir -p /data && chmod 777 /data
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=2)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
