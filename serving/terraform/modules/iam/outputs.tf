output "role_name" {
  description = "IAM role name"
  value       = try(aws_iam_role.this[0].name, null)
}

output "role_arn" {
  description = "IAM role ARN"
  value       = try(aws_iam_role.this[0].arn, null)
}

output "role_id" {
  description = "IAM role ID"
  value       = try(aws_iam_role.this[0].id, null)
}

output "route53_irsa_role_arns" {
  description = "Route 53 IRSA role ARNs keyed by workload name"
  value       = { for key, role in aws_iam_role.route53_irsa : key => role.arn }
}

output "github_actions_role_arn" {
  description = "GitHub Actions OIDC role ARN"
  value       = try(aws_iam_role.github_actions[0].arn, null)
}
