variable "github_repository" {
  description = "GitHub repository in OWNER/REPOSITORY form allowed to promote scanned images."
  type        = string
}

variable "github_oidc_subjects" {
  description = "Additional exact GitHub OIDC subject patterns, for example immutable repository-ID subjects."
  type        = list(string)
  default     = []
}

variable "repository_arns" {
  description = "ECR repository ARNs the role may push to."
  type        = list(string)
}

variable "role_name" {
  description = "IAM role name used by the image promotion workflow."
  type        = string
}

variable "create_github_oidc_provider" {
  description = "Create the account-wide GitHub Actions OIDC provider. Set false when it already exists."
  type        = bool
  default     = true
}

variable "github_oidc_thumbprints" {
  description = "GitHub Actions OIDC root CA thumbprints accepted by AWS IAM during certificate-chain rotation."
  type        = list(string)
  default = [
    "6938fd4d98bab03faadb97b34396831e3780aea1",
    "ab9d0263244dd0326eb67015705a667e79cfe998",
  ]
}

variable "tags" {
  description = "Tags applied to IAM resources."
  type        = map(string)
  default     = {}
}
