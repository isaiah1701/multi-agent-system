output "role_arn" {
  description = "Pod Identity role for the AWS Load Balancer Controller."
  value       = aws_iam_role.controller.arn
}
