variable "cluster_name" {
  type        = string
  description = "EKS cluster name that hosts the controller."
}

variable "tags" {
  type        = map(string)
  description = "Tags applied to IAM resources."
  default     = {}
}
