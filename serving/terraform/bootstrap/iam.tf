data "aws_iam_policy_document" "terraform_execution_assume_role" {
  statement {
    sid     = "TrustedOperators"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = var.terraform_trusted_principal_arns
    }
  }

  dynamic "statement" {
    for_each = var.github_repository == null ? [] : [var.github_repository]

    content {
      sid     = "GitHubActions"
      effect  = "Allow"
      actions = ["sts:AssumeRoleWithWebIdentity"]

      principals {
        type = "Federated"
        identifiers = [
          "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:oidc-provider/token.actions.githubusercontent.com"
        ]
      }

      condition {
        test     = "StringEquals"
        variable = "token.actions.githubusercontent.com:aud"
        values   = ["sts.amazonaws.com"]
      }

      condition {
        test     = "StringLike"
        variable = "token.actions.githubusercontent.com:sub"
        values = [
          "repo:${statement.value}:ref:refs/heads/main",
          "repo:${statement.value}:ref:refs/tags/v*"
        ]
      }
    }
  }
}

resource "aws_iam_role" "terraform_execution" {
  name                 = "${local.name_prefix}-terraform"
  description          = "Terraform execution role for the ${local.name_prefix} AWS platform"
  assume_role_policy   = data.aws_iam_policy_document.terraform_execution_assume_role.json
  max_session_duration = 3600

  tags = merge(local.tags, {
    Name = "${local.name_prefix}-terraform"
  })
}

data "aws_iam_policy_document" "terraform_execution" {
  # terraform-aws-modules/eks reads the current assumed role to build the KMS
  # key-administrator policy. The execution role can inspect only itself.
  statement {
    sid     = "ReadTerraformExecutionRole"
    effect  = "Allow"
    actions = ["iam:GetRole"]

    resources = [aws_iam_role.terraform_execution.arn]
  }

  # Terraform's S3 backend uses this one state object and its S3-native lock
  # file. Bootstrap itself remains on local state and does not use this role.
  statement {
    sid     = "ReadProductionStateBucket"
    effect  = "Allow"
    actions = ["s3:ListBucket"]

    resources = [aws_s3_bucket.terraform_state.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["${local.state_key}*"]
    }
  }

  statement {
    sid    = "ManageProductionState"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject"
    ]

    resources = ["${aws_s3_bucket.terraform_state.arn}/${local.state_key}"]
  }

  statement {
    sid    = "ManageProductionStateLock"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject"
    ]

    resources = ["${aws_s3_bucket.terraform_state.arn}/${local.state_key}.tflock"]
  }

  # VPC, subnets, a NAT gateway, route tables, security groups, and the
  # managed node group's launch template are all created by the prod modules.
  # EC2 create and describe APIs do not support resource-level permissions for
  # every operation, so this is restricted by action and requested region.
  statement {
    sid    = "ManageProductionNetwork"
    effect = "Allow"
    actions = [
      "ec2:AllocateAddress",
      "ec2:AssociateRouteTable",
      "ec2:AttachInternetGateway",
      "ec2:AuthorizeSecurityGroupEgress",
      "ec2:AuthorizeSecurityGroupIngress",
      "ec2:CreateInternetGateway",
      "ec2:CreateLaunchTemplate",
      "ec2:CreateLaunchTemplateVersion",
      "ec2:CreateNatGateway",
      "ec2:CreateRoute",
      "ec2:CreateRouteTable",
      "ec2:CreateSecurityGroup",
      "ec2:CreateSubnet",
      "ec2:CreateTags",
      "ec2:CreateVpc",
      "ec2:DeleteInternetGateway",
      "ec2:DeleteLaunchTemplate",
      "ec2:DeleteLaunchTemplateVersions",
      "ec2:DeleteNatGateway",
      "ec2:DeleteRoute",
      "ec2:DeleteRouteTable",
      "ec2:DeleteSecurityGroup",
      "ec2:DeleteSubnet",
      "ec2:DeleteTags",
      "ec2:DeleteVpc",
      "ec2:DescribeAddresses",
      "ec2:DescribeAddressesAttribute",
      "ec2:DescribeAvailabilityZones",
      "ec2:DescribeInternetGateways",
      "ec2:DescribeLaunchTemplates",
      "ec2:DescribeLaunchTemplateVersions",
      "ec2:DescribeNatGateways",
      "ec2:DescribeRouteTables",
      "ec2:DescribeSecurityGroups",
      "ec2:DescribeSecurityGroupRules",
      "ec2:DescribeSubnets",
      "ec2:DescribeTags",
      "ec2:DescribeVpcAttribute",
      "ec2:DescribeVpcs",
      "ec2:DetachInternetGateway",
      "ec2:DisassociateRouteTable",
      "ec2:ModifyLaunchTemplate",
      "ec2:ModifySubnetAttribute",
      "ec2:ModifyVpcAttribute",
      "ec2:ReleaseAddress",
      "ec2:RunInstances",
      "ec2:RevokeSecurityGroupEgress",
      "ec2:RevokeSecurityGroupIngress"
    ]

    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.aws_region]
    }
  }

  # EKS creates the control plane, managed node group, add-ons, and an OIDC
  # provider. The current prod inputs do not create EKS access entries, so the
  # role deliberately has no EKS access-entry administration permissions.
  statement {
    sid    = "ManageProductionEks"
    effect = "Allow"
    actions = [
      "eks:CreateAddon",
      "eks:CreateAccessEntry",
      "eks:AssociateAccessPolicy",
      "eks:DeleteAccessEntry",
      "eks:DescribeAccessEntry",
      "eks:DisassociateAccessPolicy",
      "eks:ListAccessEntries",
      "eks:ListAssociatedAccessPolicies",
      "eks:CreateCluster",
      "eks:CreateNodegroup",
      "eks:CreatePodIdentityAssociation",
      "eks:DeleteAddon",
      "eks:DeleteCluster",
      "eks:DeleteNodegroup",
      "eks:DeletePodIdentityAssociation",
      "eks:DescribeAddon",
      "eks:DescribeAddonVersions",
      "eks:DescribeCluster",
      "eks:DescribeNodegroup",
      "eks:DescribePodIdentityAssociation",
      "eks:ListAddons",
      "eks:ListClusters",
      "eks:ListNodegroups",
      "eks:ListPodIdentityAssociations",
      "eks:ListTagsForResource",
      "eks:TagResource",
      "eks:UntagResource",
      "eks:UpdateAddon",
      "eks:UpdateClusterConfig",
      "eks:UpdateClusterVersion",
      "eks:UpdateAccessEntry",
      "eks:UpdateNodegroupConfig",
      "eks:UpdateNodegroupVersion",
      "eks:UpdatePodIdentityAssociation"
    ]

    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.aws_region]
    }
  }

  # EKS v21 creates cluster/node/add-on roles and the EBS CSI role. The
  # optional GitHub ECR-promotion module creates its one named role only when
  # a GitHub repository is set in the prod inputs.
  statement {
    sid    = "ManageProductionRoles"
    effect = "Allow"
    actions = [
      "iam:AttachRolePolicy",
      "iam:CreateRole",
      "iam:DeleteRole",
      "iam:DeleteRolePolicy",
      "iam:DetachRolePolicy",
      "iam:GetRole",
      "iam:GetRolePolicy",
      "iam:ListAttachedRolePolicies",
      "iam:ListRolePolicies",
      "iam:ListRoleTags",
      "iam:PutRolePolicy",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:UpdateAssumeRolePolicy",
      "iam:UpdateRoleDescription"
    ]

    resources = [
      "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:role/${var.cluster_name}-*",
      "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:role/default-eks-node-group-*",
      "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:role/tech-test-ebs-csi-driver",
      "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:role/${local.name_prefix}-ecr-promotion",
      "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:role/${local.name_prefix}-external-secrets"
    ]
  }

  statement {
    sid    = "ManageProductionPolicies"
    effect = "Allow"
    actions = [
      "iam:CreatePolicy",
      "iam:CreatePolicyVersion",
      "iam:DeletePolicy",
      "iam:DeletePolicyVersion",
      "iam:GetPolicy",
      "iam:GetPolicyVersion",
      "iam:ListPolicyTags",
      "iam:ListPolicyVersions",
      "iam:TagPolicy",
      "iam:UntagPolicy"
    ]

    resources = [
      "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:policy/${var.cluster_name}-*"
    ]
  }

  # EKS IRSA creates an issuer-specific OIDC provider. GitHub's provider is
  # managed by the optional ECR-promotion module.
  statement {
    sid    = "ManageProductionOidcProviders"
    effect = "Allow"
    actions = [
      "iam:CreateOpenIDConnectProvider",
      "iam:DeleteOpenIDConnectProvider",
      "iam:GetOpenIDConnectProvider",
      "iam:TagOpenIDConnectProvider",
      "iam:UntagOpenIDConnectProvider",
      "iam:UpdateOpenIDConnectProviderThumbprint"
    ]

    resources = [
      "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:oidc-provider/oidc.eks.${var.aws_region}.amazonaws.com/id/*",
      "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:oidc-provider/token.actions.githubusercontent.com"
    ]
  }

  # IAM only supports listing OpenID Connect providers at account scope.
  statement {
    sid     = "ListOidcProviders"
    effect  = "Allow"
    actions = ["iam:ListOpenIDConnectProviders"]

    resources = ["*"]
  }

  statement {
    sid     = "CreateEksServiceLinkedRole"
    effect  = "Allow"
    actions = ["iam:CreateServiceLinkedRole"]

    resources = [
      "arn:${data.aws_partition.current.partition}:iam::*:role/aws-service-role/eks.amazonaws.com/AWSServiceRoleForAmazonEKS",
      "arn:${data.aws_partition.current.partition}:iam::*:role/aws-service-role/eks-nodegroup.amazonaws.com/AWSServiceRoleForAmazonEKSNodegroup"
    ]

    condition {
      test     = "StringEquals"
      variable = "iam:AWSServiceName"
      values   = ["eks.amazonaws.com", "eks-nodegroup.amazonaws.com"]
    }
  }

  statement {
    sid     = "ReadEksNodegroupServiceLinkedRole"
    effect  = "Allow"
    actions = ["iam:GetRole"]

    resources = [
      "arn:${data.aws_partition.current.partition}:iam::*:role/aws-service-role/eks-nodegroup.amazonaws.com/AWSServiceRoleForAmazonEKSNodegroup"
    ]
  }

  # Production Terraform only needs to resolve the bootstrap-created secret's
  # ARN; ESO receives GetSecretValue through its own Pod Identity role.
  statement {
    sid    = "ReadKubeMindRuntimeSecretMetadata"
    effect = "Allow"
    actions = [
      "secretsmanager:DescribeSecret",
      "secretsmanager:GetResourcePolicy",
    ]

    resources = [
      "arn:${data.aws_partition.current.partition}:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:${var.runtime_secret_name}-*"
    ]
  }

  # EKS must receive the roles created by the prod configuration. The EBS CSI
  # role is currently hard-coded in modules/eks, so it is separately scoped.
  statement {
    sid     = "PassOnlyEksServiceRoles"
    effect  = "Allow"
    actions = ["iam:PassRole"]

    resources = [
      "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:role/${var.cluster_name}-*",
      "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:role/default-eks-node-group-*",
      "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:role/tech-test-ebs-csi-driver"
    ]

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["eks.amazonaws.com"]
    }
  }

  statement {
    sid     = "PassExternalSecretsRoleToEksPods"
    effect  = "Allow"
    actions = ["iam:PassRole"]

    resources = [
      "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:role/${local.name_prefix}-external-secrets"
    ]

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["pods.eks.amazonaws.com"]
    }
  }

  # The EKS module creates a KMS key and alias for envelope encryption of EKS
  # secrets. There is no customer-managed KMS key in bootstrap.
  statement {
    sid    = "ManageEksEncryptionKey"
    effect = "Allow"
    actions = [
      "kms:CancelKeyDeletion",
      "kms:CreateAlias",
      "kms:CreateKey",
      "kms:DeleteAlias",
      "kms:DescribeKey",
      "kms:DisableKey",
      "kms:DisableKeyRotation",
      "kms:EnableKey",
      "kms:EnableKeyRotation",
      "kms:GetKeyPolicy",
      "kms:GetKeyRotationStatus",
      "kms:ListAliases",
      "kms:ListResourceTags",
      "kms:PutKeyPolicy",
      "kms:ScheduleKeyDeletion",
      "kms:TagResource",
      "kms:UntagResource",
      "kms:UpdateAlias"
    ]

    resources = ["*"]
  }

  # EKS v21 defaults to a 90-day control-plane log group. This is permission
  # for that existing prod module behavior, not CloudWatch infrastructure in
  # bootstrap.
  statement {
    sid    = "ManageEksControlPlaneLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:DeleteLogGroup",
      "logs:DescribeLogGroups",
      "logs:ListTagsForResource",
      "logs:PutRetentionPolicy",
      "logs:TagResource",
      "logs:UntagResource"
    ]

    resources = ["*"]
  }

  # The managed node group resolves its AL2023 release from the public EKS SSM
  # parameter path; it does not create or update any SSM parameters.
  statement {
    sid     = "ReadEksOptimizedAmiVersion"
    effect  = "Allow"
    actions = ["ssm:GetParameter"]

    resources = [
      "arn:${data.aws_partition.current.partition}:ssm:${var.aws_region}::parameter/aws/service/eks/optimized-ami/*"
    ]
  }

  # The prod environment creates immutable ECR repositories and their
  # lifecycle policies. Resource discovery and repository creation require a
  # wildcard resource because no repository ARN exists before creation.
  statement {
    sid    = "ManageProductionEcr"
    effect = "Allow"
    actions = [
      "ecr:CreateRepository",
      "ecr:DescribeRepositories"
    ]

    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.aws_region]
    }
  }

  statement {
    sid    = "ManageNamedProductionEcrRepositories"
    effect = "Allow"
    actions = [
      "ecr:DeleteLifecyclePolicy",
      "ecr:DeleteRepository",
      "ecr:GetLifecyclePolicy",
      "ecr:ListTagsForResource",
      "ecr:PutLifecyclePolicy",
      "ecr:TagResource",
      "ecr:UntagResource"
    ]

    resources = [
      "arn:${data.aws_partition.current.partition}:ecr:${var.aws_region}:${data.aws_caller_identity.current.account_id}:repository/${var.project_name}-*"
    ]
  }

  statement {
    sid     = "IdentifyTerraformCaller"
    effect  = "Allow"
    actions = ["sts:GetCallerIdentity"]

    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "terraform_execution" {
  name   = "terraform-production-platform"
  role   = aws_iam_role.terraform_execution.id
  policy = data.aws_iam_policy_document.terraform_execution.json
}
