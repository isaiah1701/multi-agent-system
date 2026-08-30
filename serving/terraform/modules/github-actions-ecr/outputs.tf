output "role_arn" {
  description = "GitHub Actions OIDC role ARN for AWS_ECR_PUSH_ROLE_ARN."
  value       = aws_iam_role.image_promotion.arn
}
