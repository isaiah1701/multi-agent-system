# Kubernetes Multi-Agent AI System

## Overview

This is a production-style multi-agent AI system for answering Kubernetes and platform-infrastructure questions. Specialized agents coordinate retrieval, orchestration, grounded answer generation, and evaluation to handle each request end to end.

The platform includes input and output guardrails, source-backed responses, cost and model tracing, request-level observability, and RAGAS quality metrics. It demonstrates the security, reliability, and observability patterns organizations need when operating AI systems at scale.

Was hosted on  [5hort.site](https://5hort.site) not currently live.

### Demo




https://github.com/user-attachments/assets/3d13d763-4818-4c50-abe0-67bec6e42125

### Contents

| Section | What it covers |
| --- | --- |
| [Architecture](#architecture-diagram) | System components, data flow, delivery, and observability. |
| [Prerequisites](#prerequisites) | Required tools, credentials, accounts, and deployment access. |
| [Quick start](#quick-start) | Local setup and links to the cloud-deployment guide. |
| [Agent flow](#agent-flow) | How the orchestrator, retriever, tools, and answer agent collaborate. |
| [Evaluation](#evaluation) | Golden-set design, quality metrics, and measured results. |
| [Cost](#cost) | Per-interaction costs and the optimizations that control them. |
| [Safety](#safety) | Guardrails, enforcement points, and the risks they catch. |
| [Screenshots](#screenshots) | Example answers, traces, agent execution, cost, and injection protection. |
| [Trade-offs and lessons learned](#trade-offs-and-lessons-learned) | Key decisions involving latency, safety, availability, quality, and cost. |



## Architecture diagram 

The system turns curated Kubernetes knowledge and optional tool results into guarded, observable answers.

```mermaid
%%{init: {"flowchart": {"nodeSpacing": 42, "rankSpacing": 58}, "themeVariables": {"fontSize": "17px"}}}%%
flowchart LR
    subgraph KB["Knowledge Base"]
        direction TB
        CORPUS["Kubernetes corpus<br/>and source documents"]
        PREP["Chunking and<br/>embeddings"]
        INDEX[("Chroma index")]
        CORPUS --> PREP --> INDEX
    end

    subgraph RET["Retrieval Layer"]
        SEARCH["Hybrid retrieval<br/>ANN + BM25"]
        RERANK["Reranking and<br/>evidence context"]
        SEARCH --> RERANK
    end

    subgraph AGENTS["Agent Layer"]
        INPUT["User query"]
        ORCH["LangGraph<br/>orchestrator"]
        RETAGENT["Retriever agent"]
        ANSWER["Answer agent"]
        INPUT --> ORCH --> RETAGENT
    end

    subgraph TOOLS["Tool Layer"]
        TOOLBOX["Kubernetes docs · GitHub<br/>platform search · calculator"]
    end

    subgraph RESPONSE["Response Layer"]
        FINAL["Grounded answer<br/>with citations"]
    end

    INDEX --> SEARCH
    RETAGENT --> SEARCH
    RERANK --> RETAGENT
    RETAGENT <--> TOOLBOX
    RETAGENT --> ANSWER --> FINAL

    GUARD["Guardrails<br/>input · tool policy · output"]
    OBS["Observability<br/>traces · cost · RAGAS metrics"]
    GUARD -. controls .-> ORCH
    GUARD -. validates .-> TOOLBOX
    GUARD -. checks .-> FINAL
    OBS -. monitors .-> SEARCH
    OBS -. monitors .-> ORCH
    OBS -. monitors .-> TOOLBOX
    OBS -. monitors .-> ANSWER

    classDef core fill:#eaf2ff,stroke:#2563eb,stroke-width:2px,color:#172554;
    classDef cross fill:#f5f3ff,stroke:#7c3aed,stroke-width:2px,color:#2e1065;
    class ORCH,RETAGENT,ANSWER,SEARCH,RERANK,TOOLBOX,FINAL core;
    class GUARD,OBS cross;
```

- **Corpus / Knowledge Base:** Curated source documents are chunked, embedded, and stored in Chroma.
- **Retrieval:** Hybrid ANN and BM25 search is fused and reranked into relevant evidence.
- **Agents:** LangGraph coordinates retrieval, tool selection, fallback behavior, and answer generation.
- **Tools:** Allow-listed documentation, GitHub, platform-search, and calculator tools extend the workflow.
- **Answer Layer:** Claude produces a streamed response grounded in evidence with source citations.
- **Delivery:** Argo Rollouts uses blue-green retriever releases, keeping the active index online until the warmed preview is ready.
- **Guardrails:** Input scope, tool policy, and output checks enforce relevant and safe behavior.
- **Observability:** Langfuse traces requests, steps, model cost, and quality signals alongside RAGAS metrics.

[View the detailed request-flow diagram](docs/architecture/request-flow.svg) or read the [deployment architecture](docs/ARCHITECTURE.md).

See the [CI/CD architecture](docs/CICD_ARCHITECTURE.md) for the gated path from commit to EKS.

## Prerequisites

- **Local tools:** Git, Python 3.12+, Docker, `kubectl`, Helm, Helmfile, Terraform, and the AWS CLI.
- **API keys:** An Anthropic API key is required. Langfuse project keys are required for tracing; a GitHub token is recommended for reliable Kubernetes repository lookups.
- **Accounts:** GitHub and AWS accounts with access to ECR, EKS, IAM, Secrets Manager, and the required Terraform state resources.
- **Deployment access:** A Kubernetes context for the target cluster and control of a domain or DNS zone when exposing the public services.

## Quick start

Copy the example environment file and add your `ANTHROPIC_API_KEY`, then build, ingest the bundled Kubernetes corpus, and start the services:

```bash
cp .env.example .env
docker compose build
docker compose --profile ingest run --rm ingest
docker compose up
```

Open [http://localhost:8000](http://localhost:8000), or verify the API from another terminal:

```bash
curl -X POST http://localhost:8000/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"What does a PodDisruptionBudget protect against?"}'
```

For a cloud deployment, follow the [AWS zero-to-live runbook](docs/ZERO_TO_LIVE_RUNBOOK.md).

## Agent flow

```mermaid
sequenceDiagram
    actor User
    participant API as API / Frontend
    participant Orchestrator
    participant Retriever
    participant Sources as Corpus and Tools
    participant Answer as Answer Agent

    User->>API: Ask a question
    API->>Orchestrator: Forward the request
    Orchestrator->>Orchestrator: Apply input guardrails
    alt Request is outside scope
        Orchestrator-->>API: Return a safe rejection
    else Request is relevant
        Orchestrator->>Retriever: Request supporting evidence
        Retriever->>Sources: Search and run approved tools
        Sources-->>Retriever: Return source material
        Retriever-->>Orchestrator: Send an evidence briefing
        Orchestrator->>Answer: Provide the question and evidence
        Answer->>Answer: Generate and validate a grounded answer
        Answer-->>Orchestrator: Return the answer and citations
        Orchestrator-->>API: Return the completed response
    end
    API-->>User: Display or stream the result
```

The orchestrator controls the workflow, the retriever gathers evidence, and the answer agent turns that evidence into a cited response after output checks.

## Evaluation

The golden set exercises five paths: scope guardrails, Kubernetes documentation retrieval, cloud-platform lookup, GitHub release lookup, and deterministic calculation. Each completed answer is scored from `0` to `1` for **faithfulness** to its evidence, **relevance** to the question, and **correctness** against the expected answer. CI requires every average to be at least `0.85`, with no failed cases.

Results from the latest full run on 8 September 2026:

```mermaid
flowchart LR
    GOLDEN["Golden set<br/>5 cases"] --> RUN["Scored<br/>5 / 5<br/>0 failures"]
    RUN --> F["Faithfulness<br/>0.890"]
    RUN --> R["Relevance<br/>0.980"]
    RUN --> C["Correctness<br/>0.964"]
    F --> GATE["Quality gate<br/>PASS<br/>all metrics >= 0.85"]
    R --> GATE
    C --> GATE

    classDef pass fill:#ecfdf5,stroke:#059669,stroke-width:2px,color:#064e3b;
    class GATE pass;
```

The cloud-platform case had the lowest individual score (`0.50` faithfulness). The aggregate gate passed, but per-case scores should still be reviewed for regressions hidden by averages.

Run the evaluation and enforce the same quality gate locally:

```bash
python -m eval.evaluate
python scripts/check_golden_set.py --summary eval/summary.json
```

See the [golden-set cases](eval/golden_set.jsonl), [per-case results](eval/results.jsonl), and [summary metrics](eval/summary.json).

## Cost

Langfuse recorded an average model cost of **$0.0106 per interaction** across the latest golden-set run. This is the application cost users incur, excluding the evaluation-only judge. The five application paths cost `$0.0529` in total; evaluation judging added `$0.0021`. These figures are a snapshot from 8 September 2026 and exclude infrastructure, storage, and network costs.

| Interaction path | Model cost |
| --- | ---: |
| Out-of-scope guardrail | $0.0000 |
| Kubernetes documentation | $0.0177 |
| Cloud-platform lookup | $0.0238 |
| GitHub release lookup | $0.0029 |
| Calculator-assisted path | $0.0086 |
| **Average** | **$0.0106** |

Costs are controlled in several layers:

- **Claude model tiering:** Claude Haiku 4.5 handles tool selection, evidence briefing, ambiguous input checks, and exceptional output review. The more capable Claude Sonnet 5 model is reserved for the final grounded answer.
- **Deterministic shortcuts:** Clearly unrelated questions return a fixed scope response without a model call. Arithmetic is performed by the calculator tool, and deterministic input and output checks avoid judge calls on normal paths.
- **Token-budget tiering:** Evidence generation is capped at 300 tokens. Standard answers target about 200 words with a 400-token cap; only explicitly detailed, architectural, migration, urgent, or incident requests receive the extended target of about 360 words and 900 tokens.
- **Small fallback budgets:** Ambiguous input review is capped at 32 tokens and backup output review at 48 tokens. The 80-token judge is used only during evaluation, not normal user interactions.
- **Prompt and context control:** Prompt caching is enabled for stable system, tool, and history prefixes; only necessary tools are selected; retrieved evidence is trimmed before answering; and source-only follow-ups reuse existing evidence when possible.

Actual cost varies with prompt size, selected tools, retrieved context, cache hits, and answer length. Every model call records its model, token usage, and calculated cost in Langfuse for per-request analysis.

## Safety

Safety checks run throughout the request rather than relying on the final model response alone:

```mermaid
flowchart TD
    INPUT["User request"] --> API["API validation<br/>type, length, and extra-field checks"]
    API -->|Invalid| REJECT_API["Reject request"]
    API -->|Valid| SCOPE["Input guardrail<br/>Kubernetes and platform scope"]
    SCOPE -->|Clearly unrelated| SAFE_SCOPE["Fixed scope response<br/>no model or tools"]
    SCOPE -->|Ambiguous| REVIEW_INPUT["Small Haiku review"]
    REVIEW_INPUT -->|Reject| SAFE_SCOPE
    REVIEW_INPUT -->|Allow| TOOLS
    SCOPE -->|Relevant| TOOLS["Tool boundary<br/>registered tools, schemas, domains, and limits"]
    TOOLS --> EVIDENCE["Evidence boundary<br/>normalize sources and provenance"]
    EVIDENCE -->|Missing or failed| FALLBACK["Cautious fallback response"]
    EVIDENCE -->|Usable| ANSWER["Grounded answer generation"]
    ANSWER --> STREAM["Streaming prefix guard<br/>hold and inspect before release"]
    STREAM --> FINAL["Final output guard<br/>secrets, errors, length, citations, attribution"]
    FINAL -->|Allow| PUBLIC["Public response filter<br/>answer and safe source metadata only"]
    FINAL -->|Uncertain| REVIEW_OUTPUT["Small Haiku backup review"]
    REVIEW_OUTPUT -->|Allow| PUBLIC
    FINAL -->|Block| SAFE_OUTPUT["Safe replacement message"]
    REVIEW_OUTPUT -->|Block or fail| SAFE_OUTPUT
    PUBLIC --> USER["User"]
    REJECT_API --> USER
    SAFE_SCOPE --> USER
    FALLBACK --> USER
    SAFE_OUTPUT --> USER

    classDef blocked fill:#fff1f2,stroke:#e11d48,color:#881337;
    classDef safe fill:#ecfdf5,stroke:#059669,color:#064e3b;
    class REJECT_API,SAFE_SCOPE,SAFE_OUTPUT blocked;
    class PUBLIC,USER safe;
```

| Guardrail | What it catches or constrains |
| --- | --- |
| Request validation | Empty or whitespace-only questions, questions over 4,000 characters, oversized identifiers and history, invalid types, and unexpected fields. |
| Scope control | Clearly unrelated requests; ambiguous requests receive a tightly capped classifier review before tools are available. |
| Tool policy | Unknown tools, invalid arguments, excessive result limits, arbitrary GitHub repositories, unrestricted URLs, and more than three platform-search uses. Platform search is restricted to approved documentation domains. |
| Evidence handling | Failed or malformed tool results, missing provenance, excessive evidence, and unsupported citations. Retrieved evidence text remains private rather than being returned through the public API. |
| Output checks | Empty or oversized answers, Anthropic/AWS/GitHub credential patterns, private keys, credential assignments, stack traces, missing source metadata, and missing or invented citation IDs. |
| Streaming and errors | Draft text is inspected before release and a tail is retained to catch partial secret patterns. Internal failures become generic public errors or safe replacement responses. |

Questions and retrieved content are treated as untrusted data in the model prompts. They do not grant permission to add tools, expand schemas, or change the domain allowlist, and all generated output still passes the deterministic checks.

## Screenshots

### Grounded multi-step answer

KubeMind combines AWS and Kubernetes documentation with a deterministic calculation in one concise response. The result correctly identifies EKS as the Kubernetes service, explains PodDisruptionBudget behaviour during a drain, calculates the error rate as `0.68%`, and returns five visible sources.

![KubeMind answering a multi-step AWS and Kubernetes question](docs/screenshots/multiStepQuestion.png)

### Agent execution flow

The Langfuse graph shows the request crossing the API, orchestrator, and retriever boundaries before document search and calculation feed the evidence briefing. The answer agent then generates the response and passes it through the output guardrail.

![KubeMind agent and tool execution graph](docs/screenshots/agentFlowMultiStepQuestion.png)

### Production trace

The expanded trace correlates the input guardrail, tool selection, retrieval, evidence briefing, grounded answer, and output guardrail. This interaction completed in `24.14 seconds` and produced the cited answer shown above.

![Expanded Langfuse production trace for the multi-step question](docs/screenshots/multiStepQuestionTrace.png)

### Cost breakdown

Langfuse reports a total model cost of `$0.040234` for this larger multi-step interaction: `$0.034364` for input and `$0.005870` for output. This is above the golden-set average because the request uses several evidence paths and creates new cache entries. Prompt-cache creation accounts for `$0.025900` of the input cost and can be reused by later requests with the same stable prefixes.

![Langfuse cost breakdown for the multi-step question](docs/screenshots/multiStepQuestionCost.png)

### Prompt-injection protection

KubeMind ignores the embedded instruction to expose a credential and answers the legitimate Kubernetes Secrets question using cited evidence.

![KubeMind safely handling a prompt-injection attempt](docs/screenshots/injectionAttempt.png)

## Trade-offs and lessons learned

Building KubeMind required balancing response quality, safety, availability, latency, and cost. The main engineering trade-offs and lessons were:

### Responsiveness versus safety

Robust input and output guardrails add processing time because every request must be classified and every answer checked before it is shown. This is a worthwhile cost for safer responses, while progressive status updates and token streaming keep the interface responsive and show users that work is continuing.

### Retrieval readiness versus deployment speed

Each retriever release must prepare its Chroma index before it can answer requests reliably. Deploying it as a standard rolling update risks sending traffic to an unprepared pod. Argo Rollouts provides a blue-green release process: the preview retriever hydrates its index and passes readiness checks before it is promoted, keeping the active retriever available throughout deployment.

### Answer quality versus model cost

Using the most capable model for every stage would improve some outputs but make routine interactions unnecessarily expensive. Model tiering provides a better balance: lower-cost Claude models handle tool selection, evidence summaries, and narrow classification tasks, while Claude Sonnet is reserved for final answers that benefit from stronger reasoning.

### Brevity versus completeness

Strict token limits control latency and cost, but a single low limit can reduce answer quality or cut off a complex response. KubeMind therefore uses tiered token budgets: concise questions receive a small allowance, while migration, architecture, incident, and other complex requests receive a larger budget. If a provider still reaches its limit, the answer receives one bounded continuation and an incomplete trailing fragment is never presented as finished.

### Deterministic checks versus model judgement

Deterministic rules are fast, predictable, and inexpensive, so they handle clear scope decisions and known safety patterns. Some ambiguous questions and borderline answers cannot be classified reliably with rules alone. These cases are escalated to a low-cost Claude reviewer with a tightly constrained token budget, preserving nuanced judgement without putting an expensive model on every request path.
