# syntax=docker/dockerfile:1.7

# Public browser/API image. It contains no agent implementations or credentials.
FROM python:3.13-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

COPY --link serving/app/requirements.txt ./requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    python -m pip wheel --prefer-binary --wheel-dir /wheels --requirement requirements.txt

FROM python:3.13-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    HOME=/home/app \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /app

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 app \
    && useradd --system --no-log-init --uid 10001 --gid app --create-home --home-dir /home/app --shell /usr/sbin/nologin app

COPY --link --from=builder /wheels /wheels
COPY --link serving/app/requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir --no-index --find-links=/wheels --requirement requirements.txt \
    && python -m pip check \
    && rm -rf /wheels

COPY --link --chown=10001:10001 serving/app/__init__.py serving/app/api.py serving/app/langfuse.py ./serving/app/
COPY --link --chown=10001:10001 serving/app/static ./serving/app/static/
COPY --link --chown=10001:10001 serving/app/templates ./serving/app/templates/

STOPSIGNAL SIGTERM
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).read()"]
CMD ["python", "-m", "uvicorn", "serving.app.api:app", "--host", "0.0.0.0", "--port", "8000"]
