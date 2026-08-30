# Container and Kubernetes deployment

The deployment has four networked services:

- `api` is the public FastAPI application and browser frontend. It has no model credentials and calls only the private orchestrator.
- `orchestrator` owns request guardrails and conversation state.
- `retriever` performs tool selection and creates the evidence briefing.
- `answer` produces and validates the cited response.

The orchestrator uses the internal agent services only when both `RETRIEVER_AGENT_URL` and `ANSWER_AGENT_URL` are set. With neither variable set, the existing in-process development workflow remains in use.

## Docker Compose

Put `ANTHROPIC_API_KEY` (and optional Langfuse variables) in `.env`, then start the four services:

```bash
docker compose up --build
```

Run corpus ingestion before serving document-backed answers and whenever the corpus changes:

```bash
docker compose --profile ingest run --rm ingest
```

Only the API is published at `http://localhost:8000`; the orchestrator, retriever, and answer services are private to the Compose network. Everything for the API/frontend—including its `Dockerfile`, dependency manifest, Helm chart, Argo CD applications, and Langfuse integration—lives under [`serving`](../serving). The other services remain in [orchestrator](../agents/orchestrator), [retriever](../agents/retriever), and [answer](../agents/answer). The orchestrator owns the shared transport and conversation support in [`agents/orchestrator/shared`](../agents/orchestrator/shared). Every image has a cached wheel stage and non-root Python slim runtime image.

## Kubernetes

The managed Kubernetes components are limited to four:

- External Secrets Operator (ESO), installed by [`helmfile.yaml`](../helmfile.yaml) and authenticated to AWS through EKS Pod Identity.
- Argo CD, installed by [`helmfile.yaml`](../helmfile.yaml).
- Langfuse, installed by Argo CD from the pinned official Helm chart.
- The API, orchestrator, retriever, and answer services, installed by Argo CD from the local [`serving/helm`](../serving/helm) chart.

`serving/helm` contains the whole Helm configuration: `templates/` contains the agent, ESO, and Argo CD application templates, while `values/` contains the bootstrap, base, and production values. `helmfile sync` installs ESO and Argo CD first, then renders that same chart in bootstrap mode to create the two Argo CD `Application` resources. Before applying, replace `https://github.com/ORG/REPOSITORY.git` in `helmfile.yaml` with the HTTPS clone URL that Argo CD can read. For a private repository, also configure Argo CD repository credentials before the applications are reconciled.

The agent chart deliberately needs `values/production.yaml`; the manifest promotion workflow generates and commits it only after the corresponding images pass Trivy. It contains ECR repository addresses and immutable `sha-<commit>` tags only—no credentials. Create the Langfuse Secret outside Git before syncing the charts:

```bash
kubectl create namespace langfuse --dry-run=client -o yaml | kubectl apply -f -
kubectl -n langfuse create secret generic langfuse-runtime \
  --from-literal=salt="$(openssl rand -base64 32)" \
  --from-literal=encryption-key="$(openssl rand -hex 32)" \
  --from-literal=nextauth-secret="$(openssl rand -base64 32)" \
  --from-literal=postgresql-password="$(openssl rand -base64 32)" \
  --from-literal=clickhouse-password="$(openssl rand -base64 32)" \
  --from-literal=redis-password="$(openssl rand -base64 32)" \
  --from-literal=s3-user=langfuse \
  --from-literal=s3-password="$(openssl rand -base64 32)"

```

Bootstrap Terraform creates and writes the `kubemind/prod/runtime` AWS Secrets Manager secret. Copy [`serving/terraform/bootstrap/terraform.tfvars.example`](../serving/terraform/bootstrap/terraform.tfvars.example) to the ignored `serving/terraform/bootstrap/terraform.tfvars`, then set `kubemind_runtime_secret` with exactly these three properties: `ANTHROPIC_API_KEY`, `LANGFUSE_PUBLIC_KEY`, and `LANGFUSE_SECRET_KEY`. Increment `kubemind_runtime_secret_version` when rotating a value. The secret value is sent through Terraform's write-only argument and is not stored in Terraform state; the ignored tfvars file must still be protected. If tracing is disabled, the two Langfuse properties may be empty strings.

ESO refreshes that JSON every hour and creates the in-cluster `kubemind-runtime` Secret only in the `kubemind` namespace. The `ClusterSecretStore` is restricted to that namespace, and its AWS role can read only `kubemind/prod/runtime`.

Langfuse is reachable to the agent services at `http://langfuse-web.langfuse.svc.cluster.local:3000`. Create a Langfuse project and use that project's public and secret keys in the AWS runtime secret.

The namespace enforces the Kubernetes `baseline` Pod Security Standard. Every workload also uses a non-root UID, RuntimeDefault seccomp, dropped Linux capabilities, no privilege escalation, a read-only root filesystem, and no service-account token. The ingestion Job uses the same security context and stores Chroma data in `kubemind-chroma`; `kubemind-model-cache` retains downloaded embedding models between pods.

The supplied Chroma PVC is `ReadWriteOnce`, so the retriever is deliberately one replica. The orchestrator is also one replica because its LangGraph checkpoint is in-memory; use a shared durable checkpointer before scaling it. The stateless API can scale independently and deliberately receives no runtime credential Secret.

## AWS and Argo CD bootstrap

Terraform creates the AWS platform: the VPC, EKS cluster, one immutable ECR repository per agent, the least-privilege GitHub Actions ECR promotion role, the runtime Secrets Manager secret, and ESO's least-privilege EKS Pod Identity role. Helmfile owns the Kubernetes bootstrap.

1. Copy [`terraform.tfvars.example`](../serving/terraform/bootstrap/terraform.tfvars.example) to the ignored `serving/terraform/bootstrap/terraform.tfvars`, set the runtime credentials and trusted principal, then apply the bootstrap stack. Copy [`prod.tfvars.example`](../serving/terraform/environments/prod/prod.tfvars.example) and [`backend.hcl.example`](../serving/terraform/environments/prod/backend.hcl.example) outside the repository, set your network values, then apply from `serving/terraform/environments/prod` using a protected operator or the approved Terraform workflow.
2. Configure `AWS_REGION`, `AWS_TERRAFORM_ROLE_ARN`, `AWS_CLUSTER_VALIDATION_ROLE_ARN`, `EKS_CLUSTER_NAME`, `TF_STATE_BUCKET`, `TF_STATE_KEY`, `TF_VPC_CIDR`, `TF_AVAILABILITY_ZONES_JSON`, `TF_PUBLIC_SUBNET_CIDRS_JSON`, and `TF_PRIVATE_SUBNET_CIDRS_JSON`. Keep the IAM roles distinct: ECR promotion receives only ECR push permissions; Terraform and cluster validation need their own scoped roles.
3. Run the reviewed production Terraform apply and save the emitted least-privilege ECR promotion role as the GitHub variable `AWS_ECR_PUSH_ROLE_ARN`.
4. Update the repository URL in `helmfile.yaml`, point Helm at the EKS context, and run `helmfile sync`. This installs ESO before Argo CD; Argo CD then creates the `ClusterSecretStore` and `ExternalSecret` before the application Deployments.
5. Let `agent-deploy.yml` pass on `main`. `image-deploy.yml` then scans and promotes the immutable ECR images, and `manfests-scan-push.yml` renders, validates, scans, and commits their Helm image references for Argo CD.

Verify the ESO sync without printing credentials:

```bash
kubectl -n external-secrets rollout status deployment/external-secrets
kubectl get clustersecretstore kubemind-aws-secrets-manager
kubectl -n kubemind get externalsecret kubemind-runtime
kubectl -n kubemind get secret kubemind-runtime
```

The application pods have no AWS credentials or service-account tokens. The API receives the Langfuse keys needed to trace its call to the orchestrator; the private agent services receive the full runtime Secret created by ESO.

## CI/CD gates

- **`agent-deploy.yml`** runs the unit/contract suite, then—on protected branches—the live orchestrator/retriever/answer golden set. Any failed case or score below 0.85 stops delivery.
- **`image-deploy.yml`** builds each service image, hard-fails Trivy on high/critical CVEs, uploads SARIF evidence, then promotes only scanned `sha-<commit>` images (and any matching `v*` release tag) to immutable ECR repositories.
- **`manfests-scan-push.yml`** runs only after the image workflow succeeds on `main`. It renders Helm, runs `kubeconform`, verifies `/health` readiness/liveness probes, hard-fails Trivy on high/critical configuration findings, uploads evidence, and commits the reviewed image references. It then gates the EKS rollout, endpoint liveness, and kube-bench CIS result.
- **`terraform-plan.yml`** runs `fmt`, `validate`, TFLint, and Checkov with high/critical findings as the hard-fail threshold, then performs a protected no-state plan on non-PR runs.
- **`terraform-push.yml`** is protected and manually confirmed. It uses the S3 lockfile backend and applies the reviewed AWS platform configuration.
