output "identity_arn" {
  description = "SES domain identity ARN, or null when domain verification is disabled."
  value       = try(aws_sesv2_email_identity.this[0].arn, null)
}

output "email_role_arn" {
  description = "EKS Pod Identity role allowed to send email from the SES identity."
  value       = aws_iam_role.this.arn
}
