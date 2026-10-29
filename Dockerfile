# syntax=docker/dockerfile:1.7
# Multi-stage: builder menyusun venv dengan uv, runtime slim tanpa toolchain, non-root.
FROM python:3.12-slim-bookworm AS builder
COPY --from=ghcr.io/astral-sh/uv:0.10 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

FROM python:3.12-slim-bookworm
RUN groupadd -g 10001 app \
    && useradd -u 10001 -g app -m -s /usr/sbin/nologin app \
    && mkdir -p /data/uploads \
    && chown -R app:app /data
WORKDIR /app
COPY --from=builder --chown=app:app /app /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PORT=3000 \
    UPLOAD_DIR=/data/uploads
EXPOSE 3000
USER app
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:3000/healthz', timeout=2).status == 200 else 1)"
CMD ["ai-service", "serve"]
