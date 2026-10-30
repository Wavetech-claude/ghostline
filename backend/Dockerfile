# Ghostline api. Build context = the repository root (Coolify: base_directory /, dockerfile_location /backend/Dockerfile).
# The worker is the same image with another CMD (backend/Dockerfile.worker).
FROM python:3.12-slim AS base

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# the one version of the repository, and the commit (Coolify passes SOURCE_COMMIT; else read from .git)
COPY VERSION /app/VERSION
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

COPY backend/pyproject.toml backend/README.md /app/backend/
COPY backend/ghostline /app/backend/ghostline
COPY backend/migrations /app/backend/migrations
RUN pip install --no-cache-dir --no-deps /app/backend

# Coolify passes the commit it builds as the build arg SOURCE_COMMIT (its build context carries no .git).
ARG SOURCE_COMMIT=unknown
RUN printf '%s' "$SOURCE_COMMIT" > /app/COMMIT && echo "commit: $SOURCE_COMMIT"
ENV GHOSTLINE_COMMIT=$SOURCE_COMMIT \
    GHOSTLINE_COMMIT_FILE=/app/COMMIT \
    GHOSTLINE_VERSION_FILE=/app/VERSION \
    GHOSTLINE_MIGRATIONS=/app/backend/migrations \
    WEB_CONCURRENCY=1
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"

# --proxy-headers + --forwarded-allow-ips: Traefik is the only path in (no host port), so X-Forwarded-Proto is trusted.
CMD ["uvicorn", "ghostline.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
