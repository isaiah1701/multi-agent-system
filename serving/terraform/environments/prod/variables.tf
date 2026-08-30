variable "aws_region" {
  description = "AWS region for the production environment."
  type        = string
}

variable "project_name" {
  description = "Short project name used in AWS resource names."
  type        = string
  default     = "kubemind"
}

variable "environment" {
  description = "Deployment environment name."
  type        = string
  default     = "prod"
}

variable "cluster_name" {
  description = "EKS cluster name."
  type        = string
  default     = "kubemind-prod"
}

variable "runtime_secret_name" {
  description = "AWS Secrets Manager name containing the KubeMind runtime credentials as a JSON object."
  type        = string
  default     = "kubemind/prod/runtime"

  validation {
    condition     = length(trimspace(var.runtime_secret_name)) > 0
    error_message = "runtime_secret_name must not be empty."
  }
}

variable "kubernetes_version" {
  description = "EKS Kubernetes version."
  type        = string
  default     = "1.35"
}

variable "vpc_cidr" {
  description = "CIDR assigned to the production VPC."
  type        = string
}

variable "availability_zones" {
  description = "At least two availability zones used for EKS subnets."
  type        = list(string)

  validation {
    condition     = length(var.availability_zones) >= 2
    error_message = "At least two availability zones are required for EKS."
  }
}

variable "public_subnet_cidrs" {
  description = "Public subnet CIDRs, one per availability zone."
  type        = list(string)
}

variable "private_subnet_cidrs" {
  description = "Private subnet CIDRs, one per availability zone."
  type        = list(string)
}

variable "eks_instance_types" {
  description = "EKS managed node-group instance types."
  type        = list(string)
  default     = ["m6i.large"]
}

variable "eks_min_size" {
  description = "Minimum EKS managed node count."
  type        = number
  default     = 2
}

variable "eks_max_size" {
  description = "Maximum EKS managed node count."
  type        = number
  default     = 5
}

variable "eks_desired_size" {
  description = "Initial EKS managed node count."
  type        = number
  default     = 2
}

variable "eks_access_entries" {
  description = "EKS access entries for operators and CI principals."
  type = map(object({
    principal_arn     = string
    kubernetes_groups = optional(list(string))
    type              = optional(string, "STANDARD")
    user_name         = optional(string)
    tags              = optional(map(string), {})
    policy_associations = optional(map(object({
      policy_arn = string
      access_scope = object({
        namespaces = optional(list(string))
        type       = string
      })
    })), {})
  }))
  default = {}
}

variable "eks_kms_key_administrators" {
  description = "IAM principal ARNs permitted to administer the EKS encryption key."
  type        = list(string)
  default     = []
}

variable "github_repository" {
  description = "GitHub repository in OWNER/REPOSITORY form. Set it to create the least-privilege ECR promotion role."
  type        = string
  default     = null
}

variable "create_github_oidc_provider" {
  description = "Create the account-wide GitHub Actions OIDC provider. Set false if the AWS account already has one."
  type        = bool
  default     = true
}

variable "tags" {
  description = "Additional AWS resource tags."
  type        = map(string)
  default     = {}
}
