# Evaluation Results

**Run date:** 2026-08-23  
**Scope:** Five relevant Kubernetes documentation questions (`kubernetes-01` to `kubernetes-05`)  
**Runner:** `python -m eval.evaluate --offset 5 --limit 5`

## Summary

| Metric | Score |
| --- | ---: |
| Faithfulness | 0.938 |
| Relevance | 0.944 |
| Correctness | 0.936 |
| Cases scored | 5 / 5 |
| Failures | 0 |

The evaluator runs the normal agent workflow and uses one low-token Claude Haiku judge call per completed answer. Scores range from 0 to 1.

## Per-case results

| Case | Focus | Faithfulness | Relevance | Correctness | End-to-end latency |
| --- | --- | ---: | ---: | ---: | ---: |
| `kubernetes-01` | Deployment vs StatefulSet | 0.95 | 0.92 | 0.93 | 43.8 s |
| `kubernetes-02` | Readiness, liveness, and startup probes | 0.95 | 0.98 | 0.96 | 27.4 s |
| `kubernetes-03` | Resource requests and limits | 0.92 | 0.95 | 0.93 | 27.8 s |
| `kubernetes-04` | HorizontalPodAutoscaler behaviour | 0.92 | 0.95 | 0.93 | 26.9 s |
| `kubernetes-05` | PodDisruptionBudget behaviour | 0.95 | 0.92 | 0.93 | 33.3 s |

Average end-to-end latency was **31.8 seconds**. The majority of that time was spent in the main agent workflow; the judge averaged about 2.4 seconds per case.

## Interpretation

This is a promising baseline for grounded Kubernetes-documentation questions: all five cases completed, and each answer scored at least 0.92 on every metric. The sample is intentionally small, so it should not be presented as a final benchmark. A fuller mixed run should include cloud, GitHub, calculation, multi-step, and safety cases, followed by analysis of scores below 0.8 and any execution failures.
