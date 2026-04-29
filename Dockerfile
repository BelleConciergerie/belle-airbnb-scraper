# belle-airbnb-scraper : pure FastAPI + pyairbnb-belle (curl_cffi).
# Pas de browser, pas de Playwright. Image legere ~150 MB.
FROM python:3.12-slim

WORKDIR /app

# CA certs (HTTPS) + curl (healthcheck) + git (pour pip install pyairbnb fork).
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# User non-root.
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

COPY pyproject.toml ./
COPY airbnb_scraper ./airbnb_scraper

RUN pip install --no-cache-dir -e . \
    && chown -R appuser:appuser /app

USER appuser

# Healthcheck via /health (curl, ~50 ms).
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS --max-time 3 http://localhost:8000/health > /dev/null || exit 1

EXPOSE 8000

CMD ["uvicorn", "airbnb_scraper.server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
