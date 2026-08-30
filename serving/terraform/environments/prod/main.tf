terraform {
  # The S3 backend uses native lockfiles, which require Terraform 1.10 or
  # newer. CI uses Terraform 1.11.0.
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = local.tags
  }
}

locals {
  service_names = toset([
    "api",
    "orchestrator",
    "retriever",
    "answer"
  ])

  tags = merge({
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
  }, var.tags)
}

# The bootstrap stack creates and writes this secret before production runs.
# Production resolves only its ARN to scope ESO's read permission.
data "aws_secretsmanager_secret" "kubemind_runtime" {
  name = var.runtime_secret_name
}

data "aws_iam_policy_document" "external_secrets_assume_role" {
  statement {
    effect = "Allow"

    actions = [
      "sts:AssumeRole",
      "sts:TagSession",
    ]

    principals {
      type        = "Service"
      identifiers = ["pods.eks.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/kubernetes-namespace"
      values   = ["external-secrets"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/kubernetes-service-account"
      values   = ["external-secrets"]
    }
  }
}

resource "aws_iam_role" "external_secrets" {
  name               = "${var.project_name}-${var.environment}-external-secrets"
  description        = "Allows External Secrets Operator to read the KubeMind runtime secret"
  assume_role_policy = data.aws_iam_policy_document.external_secrets_assume_role.json

  tags = local.tags
}

data "aws_iam_policy_document" "external_secrets_runtime" {
  statement {
    sid    = "ReadKubeMindRuntimeSecret"
    effect = "Allow"

    actions = [
      "secretsmanager:DescribeSecret",
      "secretsmanager:GetSecretValue",
    ]

    resources = [data.aws_secretsmanager_secret.kubemind_runtime.arn]
  }
}

resource "aws_iam_role_policy" "external_secrets_runtime" {
  name   = "read-kubemind-runtime-secret"
  role   = aws_iam_role.external_secrets.id
  policy = data.aws_iam_policy_document.external_secrets_runtime.json
}

resource "aws_eks_pod_identity_association" "external_secrets" {
  cluster_name    = module.eks.cluster_name
  namespace       = "external-secrets"
  service_account = "external-secrets"
  role_arn        = aws_iam_role.external_secrets.arn
}

module "vpc" {
  source = "../../modules/vpc"

  cluster_name         = var.cluster_name
  vpc_cidr             = var.vpc_cidr
  azs                  = var.availability_zones
  public_subnet_cidrs  = var.public_subnet_cidrs
  private_subnet_cidrs = var.private_subnet_cidrs
  tags                 = local.tags
}

module "eks" {
  source = "../../modules/eks"

  cluster_name                    = var.cluster_name
  kubernetes_version              = var.kubernetes_version
  vpc_id                          = module.vpc.vpc_id
  subnet_ids                      = module.vpc.private_subnet_ids
  control_plane_subnet_ids        = module.vpc.private_subnet_ids
  load_balancer_security_group_id = module.vpc.load_balancer_security_group_id
  instance_types                  = var.eks_instance_types
  min_size                        = var.eks_min_size
  max_size                        = var.eks_max_size
  desired_size                    = var.eks_desired_size
  access_entries                  = var.eks_access_entries
  kms_key_administrators          = var.eks_kms_key_administrators
  tags                            = local.tags
}

module "ecr" {
  for_each = local.service_names
  source   = "../../modules/ecr"

  repo_name = "${var.project_name}-${each.key}"
}

module "github_actions_ecr" {
  count  = var.github_repository == null ? 0 : 1
  source = "../../modules/github-actions-ecr"

  github_repository           = var.github_repository
  github_oidc_subjects        = var.github_oidc_subjects
  repository_arns             = [for repository in module.ecr : repository.repository_arn]
  role_name                   = "${var.project_name}-${var.environment}-ecr-promotion"
  create_github_oidc_provider = var.create_github_oidc_provider
  tags                        = local.tags
}
