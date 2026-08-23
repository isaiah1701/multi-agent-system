# Domain: Enterprise Kubernetes Knowledge and Decision Support

## Purpose

This project is intended to be a production-style multi-agent GenAI system for
understanding, evaluating, and operating Kubernetes in an organisational
context. Its domain is not Kubernetes education alone: it is **enterprise
Kubernetes knowledge and decision support**.

The system should connect Kubernetes mechanisms to the outcomes that matter to
engineering organisations: reliability and availability, operational risk,
security and governance, scalability, developer productivity, infrastructure
efficiency, cost implications, resilience, maintainability, platform
standardisation, and operational complexity.

Kubernetes concepts must therefore be interpreted against real requirements,
not presented as isolated features. Workloads, scheduling, networking, storage,
security, configuration, policy, cluster architecture, and resource management
all involve technical choices with operational consequences. For example:

| Kubernetes decision | Organisational implications to explain |
| --- | --- |
| Resource requests and limits | Capacity utilisation, workload stability, and infrastructure-cost exposure |
| Autoscaling | Demand handling, resilience, and cost-efficiency trade-offs |
| Pod-disruption controls | Availability during maintenance and node-failure risk |
| RBAC and other security controls | Security posture, least privilege, and governance |
| Scheduling constraints | Resilience, utilisation, and node or infrastructure requirements |
| Deployment vs. StatefulSet | Application architecture, storage and identity needs, and operational complexity |
| Networking architecture | Isolation, reliability, and platform complexity |

These are relationships to reason about, not promises of a particular outcome.
The system must not present an exact financial impact without relevant
organisational data.

## Corpus

`corpus/kubernetes/` contains a deliberately curated subset of upstream
Kubernetes documentation, primarily material from the official **Concepts**
section. The checked-in content covers areas including architecture, workloads,
scheduling and eviction, services and networking, storage, security,
configuration, policy, cluster administration, containers, and extensions.

Official Kubernetes documentation is the authoritative technical source because
it describes Kubernetes APIs, semantics, controllers, and operational behaviour
at the project source. It should ground claims about how Kubernetes works. The
corpus is intentionally narrower than the full Kubernetes website: restricting
it to relevant material reduces retrieval noise and makes the knowledge boundary
clearer. A curated corpus does not make unsupported topics authoritative, and
it does not replace organisation-specific evidence.

The knowledge flow separates source material from derived retrieval artefacts:

```text
Raw/source documentation (corpus/kubernetes/)
  -> cleaning and processing
  -> chunks
  -> embeddings and indexes
  -> retrieved context for an answer
```

The raw documentation is the source of truth. Cleaning may remove presentation
markup or normalise text; chunking and indexing make passages retrievable; and
retrieval context is only the selected evidence supplied for a particular
question. These stages must not be conflated: a chunk, embedding, or retrieved
excerpt is a derivative of the source, not an independent authority. At present,
the repository contains the raw curated corpus; it does not version generated
chunks, embeddings, or indexes.

## Intended Users

Likely users include Platform Engineers, DevOps Engineers, SREs, Software
Engineers, Cloud and Platform Architects, Engineering Managers, and technical
leadership. They need different depths of explanation.

An engineer may ask how taints and tolerations affect placement; an engineering
manager may ask what risk concentrated placement creates. A useful answer keeps
the Kubernetes mechanism accurate while translating it into the appropriate
operational or organisational concern.

## Questions in Scope

The system should support both technical questions and questions that connect
technical decisions to operations and business context.

Technical questions include:

- How do Kubernetes taints and tolerations work?
- When should a workload use a StatefulSet instead of a Deployment?
- How does Kubernetes Service discovery work?
- How should workloads be distributed across nodes?
- What Kubernetes controls reduce the impact of node failures?

Business and operational questions include:

- What business risk does running all replicas on one node create?
- Why should the organisation enforce resource requests and limits?
- What are the operational benefits and trade-offs of autoscaling?
- How can Kubernetes improve application resilience?
- What Kubernetes controls can reduce deployment risk?
- What are the operational implications of running stateful workloads on Kubernetes?
- Which Kubernetes security controls should a platform team standardise?
- How could poor scheduling decisions affect reliability and infrastructure utilisation?

Decision-support questions include:

- Should this workload use a Deployment or StatefulSet, and what are the operational trade-offs?
- What controls should a production Kubernetes platform require by default?
- What are the reliability implications of this Kubernetes architecture?
- What Kubernetes mechanisms could address this availability requirement?

When appropriate, answers should explain both the underlying Kubernetes
mechanism and its practical organisational impact.

## Boundaries and Limitations

The corpus supplies technical Kubernetes knowledge, not the context of a
particular organisation. It cannot establish exact cost savings,
organisation-specific risk levels, SLA or SLO requirements, production-cluster
state, application behaviour, cloud-provider pricing, compliance requirements,
or company policies. Questions about those subjects require supplied context,
organisational data, or appropriate external tools.

Documentation alone also cannot reliably answer live operational questions such
as, “Why are our production Pods restarting?” That investigation can require
Kubernetes API access, metrics, logs, events, and application-specific
information. The corpus can explain possible mechanisms and diagnostic concepts,
but it is not evidence of current cluster state.

## Answering Principles

Responses should:

1. Ground Kubernetes claims in retrieved official documentation.
2. Translate mechanisms into operational and business implications when useful.
3. Clearly separate documented facts from recommendations and inferred implications.
4. State when the available evidence is insufficient.
5. Never invent organisation-specific context.
6. Identify when live-cluster information or external tools are required.
7. Explain trade-offs rather than presenting an architecture as universally correct.
8. Adapt technical depth to the question and intended audience.

## Domain Goal

The central goal is to turn authoritative Kubernetes knowledge into useful
engineering and organisational decision support. The system should help
organisations move from “What does Kubernetes do?” towards “What does this
Kubernetes capability mean for our reliability, security, cost, operational
risk, and engineering effectiveness?”
