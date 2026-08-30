variable "aws_region" {
  description = "AWS region shared by the bootstrap state bucket and production platform."
  type        = string
}

variable "project_name" {
  description = "Short project name used in bootstrap resource names."
  type        = string
  default     = "kubemind"
}

variable "environment" {
  description = "Environment name used in bootstrap resource names."
  type        = string
  default     = "prod"
}

variable "cluster_name" {
  description = "Production EKS cluster name used to scope iam:PassRole. Keep this aligned with environments/prod."
  type        = string
  default     = "kubemind-prod"
}

variable "runtime_secret_name" {
  description = "AWS Secrets Manager name for the KubeMind runtime JSON secret. Keep this aligned with environments/prod."
  type        = string
  default     = "kubemind/prod/runtime"
}

variable "kubemind_runtime_secret" {
  description = "Runtime credentials written to AWS Secrets Manager by bootstrap. Supply these only in the ignored bootstrap terraform.tfvars file."
  type = object({
    ANTHROPIC_API_KEY   = string
    LANGFUSE_PUBLIC_KEY = string
    LANGFUSE_SECRET_KEY = string
  })
  sensitive = true
}

variable "kubemind_runtime_secret_version" {
  description = "Increment this integer whenever kubemind_runtime_secret is rotated."
  type        = number
  default     = 1

  validation {
    condition     = var.kubemind_runtime_secret_version > 0 && floor(var.kubemind_runtime_secret_version) == var.kubemind_runtime_secret_version
    error_message = "kubemind_runtime_secret_version must be a positive integer."
  }
}

variable "terraform_trusted_principal_arns" {
  description = "IAM user or role ARNs allowed to assume the Terraform execution role from a local AWS profile."
  type        = list(string)
}

variable "github_repository" {
  description = "Optional OWNER/REPOSITORY allowed to assume the Terraform role from GitHub Actions after the account GitHub OIDC provider exists."
  type        = string
  default     = null
}

variable "tags" {
  description = "Additional tags applied to bootstrap resources."
  type        = map(string)
  default     = {}
}
