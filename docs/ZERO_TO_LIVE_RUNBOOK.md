# KubeMind: zero-to-live AWS runbook

This is the recovery runbook for rebuilding KubeMind from an empty AWS account state into a live EKS service. It is written for a human operator and for an AI agent. Follow the order exactly: the state bucket and runtime secret must exist before the EKS stack, images must exist before Argo deploys the workloads, and the Route 53 name servers must be delegated at GoDaddy before ACM can issue HTTPS.

## Outcome and ownership

The completed platform contains:

- Bootstrap Terraform: an S3 Terraform backend, a Terraform execution role, and the `kubemind/prod/runtime` AWS Secrets Manager secret.
- Production Terraform: VPC, EKS, managed nodes, ECR repositories, scoped Pod Identity roles, Route 53 hosted zone, ACM certificate, and ECR/GitHub roles.
- Helmfile: External Secrets, Argo CD, AWS Load Balancer Controller, cert-manager, and ExternalDNS.
- Argo CD: Langfuse and KubeMind services from `serving/helm`, including automatic Chroma corpus ingestion on retriever rollout.
- Route 53/ALB: `5hort.site` is an ExternalDNS alias to the public ALB; the AWS Load Balancer Controller discovers the ACM certificate and serves HTTPS once ACM is issued.

Current target account and region are `522565516627` and `eu-west-2`; the expected operator identity is `arn:aws:iam::522565516627:user/isaiah` via profile `isaiahAug26`. Change all account-, region-, domain-, repository-, and network-specific values deliberately when rebuilding elsewhere.

## Preconditions

Install Terraform >= 1.10, AWS CLI v2, Docker with buildx, kubectl, Helm, Helmfile, jq, openssl, and ripgrep. Authenticate the operator profile and verify the target before changing anything:

```bash
export AWS_PROFILE=isaiahAug26
export AWS_REGION=eu-west-2
export AWS_DEFAULT_REGION="$AWS_REGION"
aws sts get-caller-identity
# Must report Account 522565516627 and user/isaiah.
```

The repository must be pushed to GitHub and Argo CD must be able to read it. Keep these files local/ignored; never commit secret values:

- `serving/terraform/bootstrap/runtime-secret.auto.tfvars`: `ANTHROPIC_API_KEY`, `LANGFUSE_PUBLIC_KEY`, and `LANGFUSE_SECRET_KEY`.
- `serving/terraform/bootstrap/terraform.tfvars`: trusted principal, region, project, and GitHub repository.
- `serving/terraform/environments/prod/prod.tfvars`: EKS sizing, access entry for `isaiah`, domain, and network CIDRs.

Set `domain_name = "5hort.site"` and `wait_for_acm_validation = false` in `prod.tfvars` for the first production apply. The false setting prevents Terraform waiting while GoDaddy has not yet delegated the domain.

## 1. Bootstrap Terraform

Bootstrap uses local state intentionally, because it creates the remote state bucket used by the production stack.

```bash
terraform -chdir=serving/terraform/bootstrap init -input=false
terraform -chdir=serving/terraform/bootstrap apply -input=false -auto-approve
terraform -chdir=serving/terraform/bootstrap output -raw production_backend_hcl \
  > serving/terraform/environments/prod/backend.hcl
terraform -chdir=serving/terraform/bootstrap output -raw terraform_execution_role_arn
```

The bootstrap apply writes the runtime secret to Secrets Manager with a write-only Terraform argument, so its value is not stored in Terraform state. It also returns the role used for the protected production apply.

## 2. Apply EKS, ECR, Route 53, and ACM

Assume the bootstrap Terraform role for the production Terraform process. Do not use its credentials for kubectl; retain the `isaiah` profile for cluster administration later.

```bash
TF_ROLE=$(terraform -chdir=serving/terraform/bootstrap output -raw terraform_execution_role_arn)
read AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN < <(
  aws sts assume-role --role-arn "$TF_ROLE" --role-session-name kubemind-production-apply \
    --query 'Credentials.[AccessKeyId,SecretAccessKey,SessionToken]' --output text
)
export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
unset AWS_PROFILE

terraform -chdir=serving/terraform/environments/prod init -reconfigure -input=false \
  -backend-config=backend.hcl
terraform -chdir=serving/terraform/environments/prod apply -input=false -auto-approve \
  -var-file=prod.tfvars
```

Capture the deployment handoff values without exposing secrets:

```bash
terraform -chdir=serving/terraform/environments/prod output cluster_name
terraform -chdir=serving/terraform/environments/prod output route53_name_servers
terraform -chdir=serving/terraform/environments/prod output acm_certificate_arn
terraform -chdir=serving/terraform/environments/prod output ecr_repository_urls
```

## 3. GoDaddy delegation (the required manual step)

Terraform prints four Route 53 name servers. In GoDaddy, replace the domain's existing nameservers for `5hort.site` with **exactly** those four values. Do not create four ordinary A records: change the registrar nameserver delegation.

The AI/operator handoff must print this explicit block from Terraform output:

```text
GoDaddy nameservers for 5hort.site:
<terraform route53_name_servers output, four names>
```

Wait until public DNS shows the Route 53 servers, then check ACM. DNS propagation can take time.

```bash
dig +short NS 5hort.site
aws acm describe-certificate --region eu-west-2 \
  --certificate-arn "$(terraform -chdir=serving/terraform/environments/prod output -raw acm_certificate_arn)" \
  --query 'Certificate.Status' --output text
# Continue only after this becomes ISSUED for the HTTPS verification.
```

Route 53 owns the ACM validation CNAME and, after ExternalDNS is installed, the root alias record. Do not manually delete either record.

## 4. Build and publish all four images before deploying workloads

Production Terraform creates empty ECR repositories. Publish the images for the exact Git commit that will be referenced by `serving/helm/values/production.yaml` before starting Helmfile/Argo. The standard path is the protected GitHub `Agent deploy` then `Image deploy` workflow, with `AWS_ECR_PUSH_ROLE_ARN` configured from the production Terraform output.

For an operator-run recovery, build and push all four services with one immutable SHA tag:

```bash
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
export AWS_PROFILE=isaiahAug26 AWS_REGION=eu-west-2
IMAGE_SHA=$(git rev-parse HEAD)
ECR_REGISTRY="$(aws sts get-caller-identity --query Account --output text).dkr.ecr.${AWS_REGION}.amazonaws.com"
aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$ECR_REGISTRY"

for service_dockerfile in \
  'api serving/app/Dockerfile' \
  'orchestrator agents/orchestrator/Dockerfile' \
  'retriever agents/retriever/Dockerfile' \
  'answer agents/answer/Dockerfile'; do
  read service dockerfile <<<"$service_dockerfile"
  docker buildx build --platform linux/amd64 --push \
    --tag "$ECR_REGISTRY/kubemind-$service:sha-$IMAGE_SHA" \
    --file "$dockerfile" .
done
```

Update the four `images.*.tag` values in `serving/helm/values/production.yaml` to `sha-$IMAGE_SHA`, commit and push that change. Never use a mutable `latest` tag.

## 5. Configure kubectl and install the EKS control plane add-ons

Return to the operator profile, configure the cluster, and run Helmfile. Helmfile installs External Secrets, Argo CD, the AWS Load Balancer Controller, cert-manager, and ExternalDNS. The bootstrap chart then creates Argo applications for Langfuse and KubeMind.

```bash
export AWS_PROFILE=isaiahAug26 AWS_REGION=eu-west-2
CLUSTER_NAME=$(terraform -chdir=serving/terraform/environments/prod output -raw cluster_name)
aws eks update-kubeconfig --name "$CLUSTER_NAME" --region "$AWS_REGION" --profile "$AWS_PROFILE"

helmfile -f helmfile.yaml sync
kubectl -n external-secrets rollout status deployment/external-secrets --timeout=5m
kubectl -n cert-manager rollout status deployment/cert-manager --timeout=5m
kubectl -n external-dns rollout status deployment/external-dns --timeout=5m
kubectl -n argocd wait --for=jsonpath='{.status.sync.status}'=Synced \
  application/kubemind-agent-services --timeout=10m
kubectl -n kubemind wait --for=condition=Ready externalsecret/kubemind-runtime --timeout=10m
```

`helmfile.yaml` uses domain filter `5hort.site`, an AWS Route 53 ExternalDNS registry, and the cert-manager/external-dns Pod Identity service accounts created by production Terraform. The KubeMind ingress has HTTP 80 and HTTPS 443 listeners, SSL redirect, certificate discovery, and the ExternalDNS hostname annotation.

## 6. Corpus ingestion and rollout checks

The retriever Deployment has an `ingest-kubernetes-corpus` init container. It embeds the repository's `corpus/kubernetes/` into the persistent Chroma volume before the retriever becomes Ready. It is therefore part of normal Argo rollout; no separate manual ingestion is needed for EKS.

```bash
kubectl -n kubemind rollout status deployment/kubemind-retriever --timeout=15m
kubectl -n kubemind rollout status deployment/kubemind-api --timeout=10m
kubectl -n kubemind rollout status deployment/kubemind-orchestrator --timeout=10m
kubectl -n kubemind rollout status deployment/kubemind-answer --timeout=10m
kubectl -n kubemind logs deployment/kubemind-retriever -c ingest-kubernetes-corpus --tail=100
```

For local development only, the equivalent ingestion command is:

```bash
docker compose --profile ingest run --rm ingest
```

## 7. Return the ALB and HTTPS endpoints

Wait for the ingress to receive an ALB hostname. The raw ALB URL is available before DNS/ACM completes; the preferred production endpoint is `https://5hort.site` after ACM status is `ISSUED`.

```bash
ALB_HOST=$(kubectl -n kubemind get ingress kubemind -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')
printf 'Raw ALB endpoint: http://%s\n' "$ALB_HOST"
printf 'HTTPS endpoint: https://5hort.site\n'

curl --fail --max-time 20 "http://$ALB_HOST/health"
curl --fail --max-time 20 https://5hort.site/health
curl --fail --silent --show-error --max-time 120 -X POST https://5hort.site/ask \
  -H 'content-type: application/json' \
  --data '{"question":"what is a pod","thread_id":"smoke-test"}'
```

The final AI/operator response must include exactly these two values, populated from the live commands rather than guessed:

```text
GoDaddy nameservers: <the four Terraform Route 53 nameservers>
Raw ALB endpoint: http://<ingress status hostname>
Preferred live endpoint: https://5hort.site
```

## AI execution contract

If another AI is asked to perform this recovery, give it this contract:

1. Verify AWS account/region and operator identity before every apply or destroy.
2. Never print, commit, or place runtime secret values in a command output or document.
3. Use `apply_patch` for repository edits, commit only intentional files, and preserve unrelated worktree changes.
4. Apply bootstrap, write `backend.hcl`, then apply production Terraform under the bootstrap Terraform role.
5. Read and return `route53_name_servers`; stop only for the registrar delegation step, then resume after it propagates.
6. Build/push all four immutable ECR images and commit matching Helm tags before Argo deployment.
7. Run Helmfile, wait for ExternalSecret, corpus ingestion/retriever rollout, and ALB creation.
8. Return the exact name servers and ALB hostname from command output, and verify `/health` plus one `/ask` request.
9. On teardown, remove Kubernetes/Helm resources before Terraform, destroy production before bootstrap, and prove both Terraform states are empty.

## Controlled teardown (this is destructive)

To return to no infrastructure managed by these stacks, destroy in this order: Argo applications/workloads, Helmfile controller releases, production Terraform, then bootstrap Terraform. This deletes the ALB, EKS cluster/nodes/VPC, Route 53 hosted zone and DNS records, ACM certificate, ECR repositories and images, state bucket, and runtime secret. Removing the Route 53 zone does **not** revert GoDaddy's nameservers; set them back at GoDaddy separately if desired.

Use the commands only after confirming no resources should be retained:

```bash
# Delete Argo-managed workloads while the EKS API is still available.
kubectl -n argocd delete application kubemind-agent-services langfuse --ignore-not-found --wait=true

# Remove Helmfile-owned controllers and their Kubernetes resources.
helmfile -f helmfile.yaml destroy

# ExternalDNS aliases are outside Terraform state. Inspect and delete only the
# A/AAAA/TXT records it created; keep NS/SOA and the Terraform-managed ACM CNAME.
ZONE_ID=$(aws route53 list-hosted-zones-by-name --dns-name 5hort.site \
  --query 'HostedZones[?Name==`5hort.site.`].Id' --output text | sed 's#^/hostedzone/##')
aws route53 list-resource-record-sets --hosted-zone-id "$ZONE_ID" \
  --query 'ResourceRecordSets[?Type==`A` || Type==`AAAA` || Type==`TXT`]' --output json \
  | jq '{Changes: map({Action:"DELETE", ResourceRecordSet:.})}' \
  > /tmp/kubemind-external-dns-records.json
# Review this file before running the next command; it must not contain NS, SOA, or ACM CNAME records.
aws route53 change-resource-record-sets --hosted-zone-id "$ZONE_ID" \
  --change-batch file:///tmp/kubemind-external-dns-records.json

# Re-assume the bootstrap Terraform role and destroy production first.
export AWS_PROFILE=isaiahAug26 AWS_REGION=eu-west-2
TF_ROLE=$(terraform -chdir=serving/terraform/bootstrap output -raw terraform_execution_role_arn)
read AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN < <(
  aws sts assume-role --role-arn "$TF_ROLE" --role-session-name kubemind-production-destroy \
    --query 'Credentials.[AccessKeyId,SecretAccessKey,SessionToken]' --output text
)
export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
unset AWS_PROFILE
terraform -chdir=serving/terraform/environments/prod destroy -input=false -auto-approve \
  -var-file=prod.tfvars

# Return to the operator profile and remove the bootstrap stack last.
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
export AWS_PROFILE=isaiahAug26
terraform -chdir=serving/terraform/bootstrap destroy -input=false -auto-approve
```

Verify the requested zero-state result:

```bash
terraform -chdir=serving/terraform/environments/prod state list
terraform -chdir=serving/terraform/bootstrap state list
# Both commands must print no resource addresses.
```

If a deletion is blocked by a finalizer or AWS dependency, resolve that named dependency, rerun the same destroy, and do not delete Terraform state manually. Terraform state must be empty because the resources were actually destroyed, not because their addresses were removed from state.
