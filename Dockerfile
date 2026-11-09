# syntax=docker/dockerfile:1
# ReserveGuard: the API, the ops console (/console) and the built website (/) in one image.
# Build: docker build --build-arg SITE_URL=https://<public address> -t reserveguard .
# Secrets (Airwallex and Anthropic keys, the API token) come from the environment at run time,
# never from the image.

# --- the marketing website (Next.js static export) ---
# Built on the build machine's own platform: the output is plain files, so no emulation is needed.
FROM --platform=$BUILDPLATFORM node:22-bookworm-slim AS site
WORKDIR /site
COPY site/package.json site/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY site/ ./
ARG SITE_URL
RUN test -n "$SITE_URL" || (echo "SITE_URL build arg is required" >&2 && exit 1)
RUN SITE_URL="$SITE_URL" npm run build

# --- the API ---
FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.6.14 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PROJECT_ENVIRONMENT=/app/.venv
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
# Installed in place (editable) so the code finds /app/data next to /app/src.
RUN uv sync --frozen --no-dev
COPY data/samples ./data/samples
COPY data/sandbox_sellers.json ./data/sandbox_sellers.json
COPY --from=site /site/out ./site/out

RUN useradd --system --uid 10001 --no-create-home app \
    && mkdir -p /var/lib/reserveguard && chown app /var/lib/reserveguard
USER app
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    RESERVEGUARD_DB_PATH=/var/lib/reserveguard/reserveguard.db
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"
CMD ["uvicorn", "reserveguard.api:app", "--host", "0.0.0.0", "--port", "8000"]
