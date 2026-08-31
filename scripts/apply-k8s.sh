#!/usr/bin/env bash
set -euo pipefail

# Applies the complete KubeMind AWS + Kubernetes deployment. It creates the
# EKS cluster before configuring kubectl for it and never prints secret values.

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
bootstrap_dir="$repo_root/serving/terraform/bootstrap"
production_dir="$repo_root/serving/terraform/environments/prod"
runtime_input="$bootstrap_dir/runtime-secret.auto.tfvars"
langfuse_runtime_secret_name="${LANGFUSE_RUNTIME_SECRET_NAME:-kubemind/prod/langfuse-runtime}"

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_file() {
  test -f "$1" || fail "Required file is missing: $1"
}

ensure_langfuse_runtime_secret() {
  if aws secretsmanager describe-secret --secret-id "$langfuse_runtime_secret_name" --region "$AWS_REGION" >/dev/null 2>&1; then
    printf 'Using existing Langfuse runtime secret %s.\n' "$langfuse_runtime_secret_name"
    return
  fi

  command -v jq >/dev/null 2>&1 || fail "jq is required to generate the Langfuse runtime secret."
  command -v openssl >/dev/null 2>&1 || fail "openssl is required to generate the Langfuse runtime secret."

  # Hex credentials are URL-safe for Langfuse's PostgreSQL and ClickHouse
  # connection strings. The JSON is sent directly to Secrets Manager and is
  # never written to disk or printed.
  local secret_payload
  secret_payload=$(jq -n \
    --arg salt "$(openssl rand -hex 32)" \
    --arg encryption_key "$(openssl rand -hex 32)" \
    --arg nextauth_secret "$(openssl rand -hex 32)" \
    --arg postgresql_password "$(openssl rand -hex 32)" \
    --arg clickhouse_password "$(openssl rand -hex 32)" \
    --arg redis_password "$(openssl rand -hex 32)" \
    --arg s3_user "langfuse" \
    --arg s3_password "$(openssl rand -hex 32)" \
    '{salt:$salt, "encryption-key":$encryption_key, "nextauth-secret":$nextauth_secret, "postgresql-password":$postgresql_password, "clickhouse-password":$clickhouse_password, "redis-password":$redis_password, "s3-user":$s3_user, "s3-password":$s3_password}')

  aws secretsmanager create-secret \
    --name "$langfuse_runtime_secret_name" \
    --description "KubeMind Langfuse runtime dependencies" \
    --secret-string "$secret_payload" \
    --region "$AWS_REGION" \
    --query 'Name' \
    --output text >/dev/null
  unset secret_payload
  printf 'Created Langfuse runtime secret %s.\n' "$langfuse_runtime_secret_name"
}

deployment_profile="${AWS_PROFILE:-isaiahAug26}"
bootstrap_profile="${BOOTSTRAP_AWS_PROFILE:-$deployment_profile}"

# Prefer the selected profile over any stale shell credentials.
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
export AWS_PROFILE="$bootstrap_profile"

export AWS_REGION="${AWS_REGION:-eu-west-2}"
export AWS_DEFAULT_REGION="$AWS_REGION"

actual_account_id=$(aws sts get-caller-identity --query Account --output text)
printf 'Targeting AWS account %s in %s with bootstrap profile %s.\n' "$actual_account_id" "$AWS_REGION" "$bootstrap_profile"

require_file "$bootstrap_dir/terraform.tfvars"
require_file "$runtime_input"
require_file "$production_dir/prod.tfvars"
require_file "$repo_root/serving/helm/values/production.yaml"

if rg -q 'PASTE_.*_HERE|replace-me' "$runtime_input"; then
  fail "Replace all runtime-secret.auto.tfvars placeholders before applying."
fi

if rg -q 'https://github.com/ORG/REPOSITORY.git' "$repo_root/helmfile.yaml"; then
  fail "Replace the placeholder Git repository URL in helmfile.yaml before applying."
fi

ensure_langfuse_runtime_secret

printf 'Applying bootstrap Terraform in AWS account %s...\n' "$actual_account_id"
terraform -chdir="$bootstrap_dir" init -input=false
terraform -chdir="$bootstrap_dir" apply -input=false -auto-approve

terraform -chdir="$bootstrap_dir" output -raw production_backend_hcl > "$production_dir/backend.hcl"
terraform_role_arn=$(terraform -chdir="$bootstrap_dir" output -raw terraform_execution_role_arn)

printf 'Assuming the bootstrap-created Terraform role...\n'
read -r AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN < <(
  aws sts assume-role \
    --role-arn "$terraform_role_arn" \
    --role-session-name kubemind-production-apply \
    --query 'Credentials.[AccessKeyId,SecretAccessKey,SessionToken]' \
    --output text
)
export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
unset AWS_PROFILE

printf 'Applying production Terraform...\n'
terraform -chdir="$production_dir" init -reconfigure -input=false -backend-config=backend.hcl
terraform -chdir="$production_dir" apply -input=false -auto-approve -var-file=prod.tfvars
cluster_name=$(terraform -chdir="$production_dir" output -raw cluster_name)

# Helm and kubectl must authenticate as the operator profile, not the Terraform
# execution role. The operator should be included in eks_access_entries.
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
export AWS_PROFILE="$deployment_profile"
aws eks update-kubeconfig --name "$cluster_name" --region "$AWS_REGION" --profile "$deployment_profile"

printf 'Installing ESO, Argo CD, and Argo applications...\n'
# `sync` directly invokes `helm upgrade`, so it remains compatible with Helm 4
# as well as Helm 3 installations that do not have the Helm diff plugin.
helmfile -f "$repo_root/helmfile.yaml" sync

printf 'Waiting for ESO and the runtime Secret...\n'
kubectl -n external-secrets rollout status deployment/external-secrets --timeout=5m
kubectl -n argocd wait --for=jsonpath='{.status.sync.status}'=Synced application/kubemind-agent-services --timeout=10m
kubectl -n kubemind wait --for=condition=Ready externalsecret/kubemind-runtime --timeout=10m

for deployment in kubemind-api kubemind-orchestrator kubemind-retriever kubemind-answer; do
  kubectl -n kubemind rollout status "deployment/$deployment" --timeout=10m
done

printf 'Deployment complete.\n'
