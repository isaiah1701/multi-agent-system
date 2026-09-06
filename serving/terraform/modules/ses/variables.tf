variable "enabled" {
  description = "Create and verify the SES domain identity and DKIM records."
  type        = bool
  default     = true
}

variable "aws_region" {
  description = "AWS region containing the SES identity."
  type        = string
}

variable "domain_name" {
  description = "Domain to verify with SES."
  type        = string
  default     = null
  nullable    = true
}

variable "hosted_zone_id" {
  description = "Route 53 hosted zone that receives SES Easy DKIM records."
  type        = string
  default     = null
  nullable    = true
}

variable "cluster_name" {
  description = "EKS cluster that runs the email-sending workload."
  type        = string
}

variable "kubernetes_namespace" {
  description = "Namespace of the EKS Pod Identity service account."
  type        = string
}

variable "kubernetes_service_account" {
  description = "Service account allowed to use SES through EKS Pod Identity."
  type        = string
}

variable "role_name" {
  description = "IAM role name for the email-sending workload."
  type        = string
}

variable "workload_name" {
  description = "Short workload name used in descriptions and policy names."
  type        = string
}

variable "tags" {
  description = "Tags applied to IAM resources."
  type        = map(string)
  default     = {}
}

locals {
  domain_configuration_valid = !var.enabled || (
    try(trimspace(var.domain_name), "") != "" &&
    try(trimspace(var.hosted_zone_id), "") != ""
  )
}

check "domain_configuration" {
  assert {
    condition     = local.domain_configuration_valid
    error_message = "domain_name and hosted_zone_id are required when SES domain verification is enabled."
  }
}
