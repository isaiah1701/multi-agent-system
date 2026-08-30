# The write-only arguments ensure the runtime credential JSON is sent to AWS
# but is not recorded in either the local bootstrap state or later remote state.
resource "aws_secretsmanager_secret" "kubemind_runtime" {
  name                    = var.runtime_secret_name
  description             = "KubeMind runtime credentials consumed by External Secrets Operator"
  recovery_window_in_days = 7

  tags = local.tags
}

resource "aws_secretsmanager_secret_version" "kubemind_runtime" {
  secret_id                = aws_secretsmanager_secret.kubemind_runtime.id
  secret_string_wo         = jsonencode(var.kubemind_runtime_secret)
  secret_string_wo_version = var.kubemind_runtime_secret_version
}
