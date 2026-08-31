output "cluster_name" {
  description = "EKS cluster name used by Argo CD and post-deploy validation."
  value       = module.eks.cluster_name
}

output "cluster_endpoint" {
  description = "EKS API endpoint."
  value       = module.eks.cluster_endpoint
}

output "ecr_repository_urls" {
  description = "Immutable-image ECR repository URLs keyed by service."
  value       = { for service, repository in module.ecr : service => repository.repository_url }
}

output "github_actions_ecr_role_arn" {
  description = "Set this value as the protected GitHub variable AWS_ECR_PUSH_ROLE_ARN."
  value       = try(module.github_actions_ecr[0].role_arn, null)
}

output "github_actions_cluster_validation_role_arn" {
  description = "Set this value as the protected GitHub variable AWS_CLUSTER_VALIDATION_ROLE_ARN."
  value       = try(aws_iam_role.github_actions_cluster_validation[0].arn, null)
}

output "runtime_secret_id" {
  description = "AWS Secrets Manager secret ID created and populated by the bootstrap stack."
  value       = data.aws_secretsmanager_secret.kubemind_runtime.name
}

output "external_secrets_role_arn" {
  description = "EKS Pod Identity role used by External Secrets Operator."
  value       = aws_iam_role.external_secrets.arn
}

output "aws_load_balancer_controller_role_arn" {
  description = "Pod Identity role used by the AWS Load Balancer Controller."
  value       = module.aws_load_balancer_controller.role_arn
}
