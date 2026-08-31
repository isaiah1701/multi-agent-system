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

ingress:
  enabled: true
  className: alb
  hostname: 5hort.site
  tls:
    enabled: true
  annotations:
    alb.ingress.kubernetes.io/scheme: internet-facing
    alb.ingress.kubernetes.io/target-type: ip
    alb.ingress.kubernetes.io/listen-ports: '[{"HTTP":80},{"HTTPS":443}]'
    alb.ingress.kubernetes.io/ssl-redirect: "443"
    external-dns.alpha.kubernetes.io/hostname: 5hort.site
