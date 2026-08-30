# syntax=docker/dockerfile:1.7

# Grounded-answer image. It excludes Chroma, local embedding models, and tools.
FROM python:3.13-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

RUN apt-get update \
    && apt-get install --yes --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY --link agents/answer/requirements.txt ./requirements.txt
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

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 app \
    && useradd --system --no-log-init --uid 10001 --gid app --create-home --home-dir /home/app --shell /usr/sbin/nologin app

COPY --link --from=builder /wheels /wheels
COPY --link agents/answer/requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir --no-index --find-links=/wheels --requirement requirements.txt \
    && python -m pip check \
    && rm -rf /wheels

COPY --link --chown=10001:10001 agents/__init__.py ./agents/
COPY --link --chown=10001:10001 agents/answer/__init__.py agents/answer/agent.py agents/answer/service.py ./agents/answer/
COPY --link --chown=10001:10001 agents/orchestrator/__init__.py agents/orchestrator/retry.py ./agents/orchestrator/
COPY --link --chown=10001:10001 agents/orchestrator/llm ./agents/orchestrator/llm
COPY --link --chown=10001:10001 agents/orchestrator/shared ./agents/orchestrator/shared
COPY --link --chown=10001:10001 guardrails ./guardrails
COPY --link --chown=10001:10001 serving/app/__init__.py serving/app/langfuse.py ./serving/app/

STOPSIGNAL SIGTERM
USER 10001:10001
EXPOSE 8082
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8082/health', timeout=2).read()"]
CMD ["python", "-m", "uvicorn", "agents.answer.service:app", "--host", "0.0.0.0", "--port", "8082"]
