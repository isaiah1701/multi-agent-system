# multi-agent-system

## Observability

The agent records Langfuse `@observe` traces for the request workflow, guardrail, tool selection, retrieval/tools, and Anthropic generations. Copy `.env.example` to `.env`, then set `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, and (if needed) `LANGFUSE_BASE_URL`. With the Langfuse values blank, tracing is a no-op and the application runs normally.

The CLI flushes telemetry before exiting so completed traces are visible without waiting for the background exporter.

## Safety

Input relevance checks reject non-Kubernetes requests before tools or models run. Final answers then pass a zero-cost output gate that blocks credential-like strings and Python stack traces. If a successful documentation, GitHub, or platform lookup is used but the answer has no visible attribution, a 48-token Haiku review is used as a backup—not the primary guardrail. A rejected, unavailable, or malformed backup review returns a safe fallback rather than the model draft. Langfuse records the guardrail decisions without storing the answer text in the guardrail spans.

## Local conversation memory and prompt caching

The runtime graph uses LangGraph `InMemorySaver` checkpointing. Reuse a thread ID within the same Python process to retain prior turns:

```bash
python -m agents.orchestrator.orchestrator --thread-id local-pdb-chat "What is a PDB?"
python -m agents.orchestrator.orchestrator --chat --thread-id local-pdb-chat
```

`--chat` is the useful local workflow: every turn in that process uses one thread ID. A one-shot CLI command launched in a new process starts with a new in-memory store, even if you reuse its thread ID.

This is development-only persistence. Before deploying multiple EKS replicas, replace `InMemorySaver` with a shared LangGraph-supported durable checkpointer (for example PostgreSQL); pod-local memory cannot provide a consistent session across replicas.

Anthropic prompt caching is enabled by default with `PROMPT_CACHING_ENABLED=true`. Stable system instructions and tool definitions are marked for caching, and prior conversation context is placed before the changing current question. It reduces repeated prompt processing only; every turn still performs a new model generation. Langfuse generation usage records Anthropic's `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, and `cache_read_input_tokens` when the API returns them.

Standard answers have a 400-token hard cap and a 200-word target, but the model is asked to use less when a shorter answer is complete. A deterministic budget guardrail permits the 900-token extended cap only for explicitly detailed, urgent, migration, architecture, or incident requests. Configure the values with `ANSWER_MAX_TOKENS`, `ANSWER_TARGET_WORDS`, `ANSWER_EXTENDED_MAX_TOKENS`, and `ANSWER_EXTENDED_TARGET_WORDS`.

## Browser interface

Install the dependencies, configure `.env`, then start the single-process FastAPI application:

```bash
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn serving.api:app --host 0.0.0.0 --port 8000 --reload
```

Open `http://localhost:8000` for the chat interface, `http://localhost:8000/docs` for the OpenAPI UI, and `http://localhost:8000/health` for the lightweight Kubernetes probe. The browser creates a `sessionStorage` thread ID and sends it to the existing LangGraph workflow on each request; conversation memory is therefore retained only while that API process remains running.

The browser uses `POST /ask/stream`, a server-sent-events endpoint. Each provider fragment is held briefly and checked for sensitive data, stack traces, and required evidence attribution before it is released. The complete response still passes the existing final output guardrail; if that gate replaces the draft with a safe fallback, the browser replaces the partial message too. `POST /ask` remains available for non-streaming JSON clients.
