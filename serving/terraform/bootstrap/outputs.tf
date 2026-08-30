output "terraform_state_bucket_name" {
  description = "S3 bucket name to use as the production Terraform backend bucket."
  value       = aws_s3_bucket.terraform_state.id
}

output "terraform_state_key" {
  description = "S3 object key to use as the production Terraform state key."
  value       = local.state_key
}

output "terraform_execution_role_arn" {
  description = "Role ARN for protected Terraform plans and applies."
  value       = aws_iam_role.terraform_execution.arn
}

output "runtime_secret_id" {
  description = "AWS Secrets Manager secret written by bootstrap and read by External Secrets Operator."
  value       = aws_secretsmanager_secret.kubemind_runtime.name
}

output "production_backend_hcl" {
  description = "Write this value to environments/prod/backend.hcl before initializing the production backend."
  value       = <<-EOT
    bucket       = "${aws_s3_bucket.terraform_state.id}"
    key          = "${local.state_key}"
    region       = "${var.aws_region}"
    encrypt      = true
    use_lockfile = true
  EOT
}
