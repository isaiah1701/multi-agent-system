# Web deployment architecture

```mermaid
flowchart LR
    U[User browser]

    subgraph Docker Compose / Kubernetes
        API[API/frontend service<br/>FastAPI + browser assets]
        ORCH[Orchestrator service<br/>guardrails + sessions]
        RETRIEVER[Retriever service<br/>tools + evidence briefing]
        ANSWER[Answer service<br/>grounded response + output guardrails]
        CHROMA[(Chroma vector DB<br/>persistent volume)]
        MODELS[(Hugging Face model cache<br/>persistent volume)]
        INGEST[Ingestion job]
    end

    ANTHROPIC[Anthropic API]
    LANGFUSE[Langfuse<br/>optional]

    U -->|HTTPS / SSE| API
    API -->|POST /v1/ask| ORCH
    ORCH -->|POST /v1/retrieve| RETRIEVER
    RETRIEVER -->|evidence packet| ORCH
    ORCH -->|POST /v1/answer| ANSWER
    RETRIEVER -->|retrieve context| CHROMA
    ANSWER -->|generate answer| ANTHROPIC
    ORCH -. traces .-> LANGFUSE
    RETRIEVER -. traces .-> LANGFUSE
    ANSWER -. traces .-> LANGFUSE

    INGEST -->|read corpus + embed| CHROMA
    INGEST -->|download/reuse models| MODELS
    RETRIEVER -->|rerank + embeddings| MODELS
```

The services have separate images and dependency manifests: the public API/frontend does not contain agent implementations or model credentials; the orchestrator does not contain Chroma, embedding, reranking, answer implementation, or browser assets; the retriever owns retrieval/tools; and the answer service owns answer prompting and output guardrails. Their internal HTTP contracts are tested independently. The in-process graph remains only for local development when neither internal agent URL is configured.
