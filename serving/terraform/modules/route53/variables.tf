variable "domain_name" {
  type        = string
  description = "The domain name for the Route 53 hosted zone"
}
variable "validation_method" {
  type        = string
  description = "How the ACM validates your domain"
  default     = "DNS"
}
