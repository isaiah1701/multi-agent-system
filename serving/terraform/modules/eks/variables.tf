variable "cluster_name" {
  description = "EKS cluster name"
  type        = string
}

variable "kubernetes_version" {
  description = "Kubernetes version"
  type        = string
  default     = "1.35"
}

variable "vpc_id" {
  description = "VPC ID"
  type        = string
}

variable "subnet_ids" {
  description = "Worker node subnets"
  type        = list(string)
}

variable "control_plane_subnet_ids" {
  description = "Control plane subnets"
  type        = list(string)
}

variable "instance_types" {
  description = "EC2 instance types for node group"
  type        = list(string)
}

variable "min_size" {
  type = number
}

variable "max_size" {
  type = number
}

variable "desired_size" {
  type = number
}

variable "load_balancer_security_group_id" {
  description = "Security group allowed to reach EKS nodes"
  type        = string
  default     = null
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "access_entries" {
  description = "Stable EKS access entries for CI and operator principals"
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

variable "kms_key_administrators" {
  description = "Stable KMS key administrators for EKS encryption key management"
  type        = list(string)
  default     = []
}
