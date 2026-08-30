variable "create_role" {
  description = "Whether to create a generic IAM role"
  type        = bool
  default     = true
}

variable "name" {
  description = "IAM role name"
  type        = string
  default     = null
}

variable "description" {
  description = "IAM role description"
  type        = string
  default     = "Permissive IAM role for rapid development"
}

variable "path" {
  description = "IAM path for the role"
  type        = string
  default     = "/"
}

variable "max_session_duration" {
  description = "Maximum session duration in seconds"
  type        = number
  default     = 3600
}

variable "trusted_service_principals" {
  description = "AWS service principals allowed to assume this role"
  type        = list(string)
  default     = ["ec2.amazonaws.com", "pods.eks.amazonaws.com"]
}

variable "trusted_aws_principals" {
  description = "AWS principal ARNs allowed to assume this role"
  type        = list(string)
  default     = []
}

variable "create_instance_profile" {
  description = "Whether to create an instance profile for EC2 use"
  type        = bool
  default     = false
}

variable "instance_profile_name" {
  description = "Optional custom name for the instance profile"
  type        = string
  default     = null
}

variable "create_route53_irsa" {
  description = "Whether to create Route 53 IRSA roles"
  type        = bool
  default     = false
}

variable "create_github_oidc" {
  description = "Whether to create a GitHub Actions OIDC provider and role"
  type        = bool
  default     = false
}

variable "project_name" {
  description = "Project name used in IAM resource naming"
  type        = string
  default     = null
}

variable "environment" {
  description = "Environment name used in IAM resource naming"
  type        = string
  default     = null
}

variable "cluster_name" {
  description = "EKS cluster name"
  type        = string
  default     = null
}

variable "cluster_oidc_provider_arn" {
  description = "EKS cluster OIDC provider ARN for IRSA"
  type        = string
  default     = null
}

variable "domain_name" {
  description = "Domain managed in Route 53"
  type        = string
  default     = null
}

variable "hosted_zone_id" {
  description = "Route 53 hosted zone ID"
  type        = string
  default     = null
}

variable "route53_irsa_subjects" {
  description = "Map of role keys to Kubernetes service account subjects"
  type        = map(string)
  default     = {}
}

variable "tags" {
  description = "Tags to apply to IAM resources"
  type        = map(string)
  default     = {}
}


variable "github_repo" {
  type        = string
  description = "GitHub repo allowed to assume role via OIDC"
  default     = null
}

variable "github_oidc_role_name" {
  description = "Optional custom IAM role name for GitHub Actions OIDC"
  type        = string
  default     = null
}

variable "github_oidc_thumbprint" {
  description = "Root CA thumbprint for token.actions.githubusercontent.com"
  type        = string
  default     = "6938fd4d98bab03faadb97b34396831e3780aea1"
}

variable "github_oidc_policy_arns" {
  description = "Managed policy ARNs to attach to the GitHub Actions OIDC role"
  type        = list(string)
  default     = ["arn:aws:iam::aws:policy/AdministratorAccess"]
}
