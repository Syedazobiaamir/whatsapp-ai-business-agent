# FastAPI backend image. Build context = project root (needs database/ for migrations + seed).
#   docker build -f backend/Dockerfile -t wa-agent-backend .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
# Dependencies first: this layer is cached until requirements.txt changes (fast rebuilds).
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt

COPY backend/app backend/app
COPY backend/alembic.ini backend/alembic.ini
COPY database database
COPY docker/backend-entrypoint.sh /usr/local/bin/backend-entrypoint.sh

# Never run as root inside the container.
RUN useradd --create-home --uid 10001 app && chmod +x /usr/local/bin/backend-entrypoint.sh
USER app
WORKDIR /app/backend

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

ENTRYPOINT ["backend-entrypoint.sh"]
# --proxy-headers: trust X-Forwarded-For from nginx so the audit log records the real client IP.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
