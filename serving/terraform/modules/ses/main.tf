data "aws_caller_identity" "current" {}

data "aws_partition" "current" {}

locals {
  identity_arn = var.enabled ? (
    "arn:${data.aws_partition.current.partition}:ses:${var.aws_region}:${data.aws_caller_identity.current.account_id}:identity/${var.domain_name}"
  ) : "arn:${data.aws_partition.current.partition}:ses:${var.aws_region}:${data.aws_caller_identity.current.account_id}:identity/disabled.invalid"
}

resource "aws_sesv2_email_identity" "this" {
  count = var.enabled ? 1 : 0

  email_identity = var.domain_name
}

resource "aws_route53_record" "dkim" {
  count = var.enabled ? 3 : 0

  zone_id = var.hosted_zone_id
  name    = "${aws_sesv2_email_identity.this[0].dkim_signing_attributes[0].tokens[count.index]}._domainkey.${var.domain_name}"
  type    = "CNAME"
  ttl     = 300
  records = ["${aws_sesv2_email_identity.this[0].dkim_signing_attributes[0].tokens[count.index]}.dkim.amazonses.com"]
}

data "aws_iam_policy_document" "assume_role" {
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
      values   = [var.kubernetes_namespace]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/kubernetes-service-account"
      values   = [var.kubernetes_service_account]
    }
  }
}

resource "aws_iam_role" "this" {
  name               = var.role_name
  description        = "Allows ${title(var.workload_name)} to send transactional email through SES"
  assume_role_policy = data.aws_iam_policy_document.assume_role.json

  tags = var.tags
}

data "aws_iam_policy_document" "send_email" {
  statement {
    sid    = "Send${title(var.workload_name)}TransactionalEmail"
    effect = "Allow"
    actions = [
      "ses:SendEmail",
      "ses:SendRawEmail",
    ]
    resources = [local.identity_arn]
  }
}

resource "aws_iam_role_policy" "send_email" {
  name   = "send-${var.workload_name}-transactional-email"
  role   = aws_iam_role.this.id
  policy = data.aws_iam_policy_document.send_email.json
}

resource "aws_eks_pod_identity_association" "this" {
  cluster_name    = var.cluster_name
  namespace       = var.kubernetes_namespace
  service_account = var.kubernetes_service_account
  role_arn        = aws_iam_role.this.arn
}
