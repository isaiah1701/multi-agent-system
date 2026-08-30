data "aws_caller_identity" "current" {}

data "aws_partition" "current" {}

locals {
  oidc_provider_url = var.cluster_oidc_provider_arn == null ? "" : replace(
    var.cluster_oidc_provider_arn,
    "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:oidc-provider/",
    ""
  )
}

data "aws_iam_policy_document" "assume_role" {

  dynamic "statement" {
    for_each = var.create_role && length(var.trusted_service_principals) > 0 ? [1] : []

    content {
      effect = "Allow"

      actions = [
        "sts:AssumeRole"
      ]

      principals {
        type = "Service"

        identifiers = var.trusted_service_principals
      }
    }
  }

  dynamic "statement" {
    for_each = var.create_role && length(var.trusted_aws_principals) > 0 ? [1] : []

    content {
      effect = "Allow"

      actions = [
        "sts:AssumeRole"
      ]

      principals {
        type = "AWS"

        identifiers = var.trusted_aws_principals
      }
    }
  }

  dynamic "statement" {
    for_each = var.github_repo != null ? [1] : []

    content {
      effect = "Allow"

      actions = [
        "sts:AssumeRoleWithWebIdentity"
      ]

      principals {
        type = "Federated"

        identifiers = [
          "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:oidc-provider/token.actions.githubusercontent.com"
        ]
      }

      condition {
        test = "StringEquals"

        variable = "token.actions.githubusercontent.com:aud"

        values = [
          "sts.amazonaws.com"
        ]
      }

      condition {
        test = "StringLike"

        variable = "token.actions.githubusercontent.com:sub"

        values = [
          "repo:${var.github_repo}:*"
        ]
      }
    }
  }
}

resource "aws_iam_openid_connect_provider" "github" {
  count = var.create_github_oidc ? 1 : 0

  url = "https://token.actions.githubusercontent.com"

  client_id_list = [
    "sts.amazonaws.com"
  ]

  thumbprint_list = [
    var.github_oidc_thumbprint
  ]

  tags = var.tags
}

data "aws_iam_policy_document" "github_actions_assume_role" {
  count = var.create_github_oidc ? 1 : 0

  statement {
    effect = "Allow"

    actions = [
      "sts:AssumeRoleWithWebIdentity"
    ]

    principals {
      type = "Federated"

      identifiers = [
        aws_iam_openid_connect_provider.github[0].arn
      ]
    }

    condition {
      test = "StringEquals"

      variable = "token.actions.githubusercontent.com:aud"

      values = [
        "sts.amazonaws.com"
      ]
    }

    condition {
      test = "StringLike"

      variable = "token.actions.githubusercontent.com:sub"

      values = [
        "repo:${var.github_repo}:*"
      ]
    }
  }
}

resource "aws_iam_role" "github_actions" {
  count = var.create_github_oidc ? 1 : 0

  name                 = coalesce(var.github_oidc_role_name, "${var.project_name}-${var.environment}-github-actions")
  description          = "GitHub Actions OIDC role for ${var.github_repo}"
  assume_role_policy   = data.aws_iam_policy_document.github_actions_assume_role[0].json
  max_session_duration = var.max_session_duration

  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "github_actions" {
  for_each = var.create_github_oidc ? toset(var.github_oidc_policy_arns) : toset([])

  role       = aws_iam_role.github_actions[0].name
  policy_arn = each.value
}

resource "aws_iam_role" "this" {
  count = var.create_role ? 1 : 0

  name                 = var.name
  description          = var.description
  path                 = var.path
  assume_role_policy   = data.aws_iam_policy_document.assume_role.json
  max_session_duration = var.max_session_duration

  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "administrator_access" {
  count = var.create_role ? 1 : 0

  role = aws_iam_role.this[0].name

  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/AdministratorAccess"
}

resource "aws_iam_policy" "route53_irsa" {
  count = var.create_route53_irsa ? 1 : 0

  name = "${var.project_name}-${var.environment}-route53-irsa"

  policy = jsonencode({
    Version = "2012-10-17"

    Statement = [
      {
        Effect = "Allow"

        Action = [
          "route53:ChangeResourceRecordSets"
        ]

        Resource = [
          "arn:aws:route53:::hostedzone/${var.hosted_zone_id}"
        ]
      },
      {
        Effect = "Allow"

        Action = [
          "route53:ListHostedZones",
          "route53:ListResourceRecordSets"
        ]

        Resource = ["*"]
      }
    ]
  })
}

data "aws_iam_policy_document" "route53_irsa_assume_role" {
  for_each = var.create_route53_irsa ? var.route53_irsa_subjects : {}

  statement {
    effect = "Allow"

    actions = [
      "sts:AssumeRoleWithWebIdentity"
    ]

    principals {
      type = "Federated"

      identifiers = [
        var.cluster_oidc_provider_arn
      ]
    }

    condition {
      test = "StringEquals"

      variable = "${local.oidc_provider_url}:sub"

      values = [
        each.value
      ]
    }

    condition {
      test = "StringEquals"

      variable = "${local.oidc_provider_url}:aud"

      values = [
        "sts.amazonaws.com"
      ]
    }
  }
}

resource "aws_iam_role" "route53_irsa" {
  for_each = var.create_route53_irsa ? var.route53_irsa_subjects : {}

  name = "${var.project_name}-${var.environment}-${each.key}-irsa"

  assume_role_policy = data.aws_iam_policy_document.route53_irsa_assume_role[each.key].json

  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "route53_irsa" {
  for_each = var.create_route53_irsa ? var.route53_irsa_subjects : {}

  role = aws_iam_role.route53_irsa[each.key].name

  policy_arn = aws_iam_policy.route53_irsa[0].arn
}
