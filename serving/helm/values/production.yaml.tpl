# Rendered and committed by .github/workflows/manfests-scan-push.yml.
# This file deliberately contains image locations and immutable tags only.
images:
  api:
    repository: ${ECR_REGISTRY}/kubemind-api
    tag: sha-${IMAGE_SHA}
  orchestrator:
    repository: ${ECR_REGISTRY}/kubemind-orchestrator
    tag: sha-${IMAGE_SHA}
  retriever:
    repository: ${ECR_REGISTRY}/kubemind-retriever
    tag: sha-${IMAGE_SHA}
  answer:
    repository: ${ECR_REGISTRY}/kubemind-answer
    tag: sha-${IMAGE_SHA}
