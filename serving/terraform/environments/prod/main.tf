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

  github_oidc_provider_arn = "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:oidc-provider/token.actions.githubusercontent.com"
}

data "aws_caller_identity" "current" {}

data "aws_partition" "current" {}

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
  access_entries = merge(var.eks_access_entries, var.github_repository == null ? {} : {
    github_actions_cluster_validation = {
      principal_arn = aws_iam_role.github_actions_cluster_validation[0].arn
      policy_associations = {
        cluster_admin = {
          policy_arn = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"
          access_scope = {
            type = "cluster"
          }
        }
      }
    }
  })
  kms_key_administrators = var.eks_kms_key_administrators
  tags                   = local.tags
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

# The post-deploy GitHub Actions gate has a dedicated identity: unlike image
# promotion, it can read the cluster and authenticate to its Kubernetes API,
# but it has no ECR write permissions.
data "aws_iam_policy_document" "github_actions_cluster_validation_assume_role" {
  count = var.github_repository == null ? 0 : 1

  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.github_oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values = concat([
        "repo:${var.github_repository}:ref:refs/heads/main",
        "repo:${var.github_repository}:ref:refs/tags/v*",
      ], var.github_oidc_subjects)
    }
  }
}

data "aws_iam_policy_document" "github_actions_cluster_validation" {
  count = var.github_repository == null ? 0 : 1

  statement {
    effect = "Allow"
    actions = [
      "eks:AccessKubernetesApi",
      "eks:DescribeCluster",
    ]
    resources = ["arn:${data.aws_partition.current.partition}:eks:${var.aws_region}:${data.aws_caller_identity.current.account_id}:cluster/${var.cluster_name}"]
  }
}

resource "aws_iam_role" "github_actions_cluster_validation" {
  count = var.github_repository == null ? 0 : 1

  name               = "${var.project_name}-${var.environment}-cluster-validation"
  description        = "GitHub Actions OIDC role limited to KubeMind EKS rollout validation"
  assume_role_policy = data.aws_iam_policy_document.github_actions_cluster_validation_assume_role[0].json

  tags = local.tags
}

resource "aws_iam_role_policy" "github_actions_cluster_validation" {
  count = var.github_repository == null ? 0 : 1

  name   = "eks-rollout-validation"
  role   = aws_iam_role.github_actions_cluster_validation[0].id
  policy = data.aws_iam_policy_document.github_actions_cluster_validation[0].json
}
