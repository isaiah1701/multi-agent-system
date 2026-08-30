provider "aws" {
  region = var.aws_region

  default_tags {
    tags = local.tags
  }
}

data "aws_caller_identity" "current" {}

data "aws_partition" "current" {}

locals {
  name_prefix       = "${var.project_name}-${var.environment}"
  state_bucket_name = lower("${local.name_prefix}-terraform-state-${data.aws_caller_identity.current.account_id}")
  state_key         = "${var.project_name}/${var.environment}/terraform.tfstate"

  tags = merge({
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
    Stack       = "bootstrap"
  }, var.tags)
}
