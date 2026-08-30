module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 21.0"

  name               = var.cluster_name
  kubernetes_version = var.kubernetes_version

  vpc_id                   = var.vpc_id
  subnet_ids               = var.subnet_ids
  control_plane_subnet_ids = var.control_plane_subnet_ids

  endpoint_public_access = true

  # AWS OIDC only authenticates the workflow to AWS. EKS access entries
  # authorize that IAM principal inside the Kubernetes API. Keeping these
  # explicit prevents access from drifting based on who last ran Terraform.
  enable_cluster_creator_admin_permissions = false
  access_entries                           = var.access_entries
  kms_key_administrators                   = var.kms_key_administrators

  node_security_group_additional_rules = {
    ingress_from_load_balancer = {
      description              = "Allow ingress traffic from the load balancer security group"
      protocol                 = "tcp"
      from_port                = 30000
      to_port                  = 32767
      type                     = "ingress"
      source_security_group_id = var.load_balancer_security_group_id
    }
  }

  addons = {
    coredns = {}

    eks-pod-identity-agent = {
      before_compute = true
    }

    kube-proxy = {}

    vpc-cni = {
      before_compute = true
    }

    aws-ebs-csi-driver = {
      service_account_role_arn = aws_iam_role.ebs_csi_driver.arn
    }
  }

  eks_managed_node_groups = {
    default = {
      ami_type       = "AL2023_x86_64_STANDARD"
      instance_types = var.instance_types

      labels = {}

      min_size     = var.min_size
      max_size     = var.max_size
      desired_size = var.desired_size
    }
  }

  tags = var.tags
}

data "aws_iam_policy_document" "ebs_csi_assume_role" {
  statement {
    effect = "Allow"

    actions = [
      "sts:AssumeRoleWithWebIdentity"
    ]

    principals {
      type = "Federated"

      identifiers = [
        module.eks.oidc_provider_arn
      ]
    }

    condition {
      test = "StringEquals"

      variable = "${replace(module.eks.oidc_provider, "https://", "")}:aud"

      values = [
        "sts.amazonaws.com"
      ]
    }

    condition {
      test = "StringEquals"

      variable = "${replace(module.eks.oidc_provider, "https://", "")}:sub"

      values = [
        "system:serviceaccount:kube-system:ebs-csi-controller-sa"
      ]
    }
  }
}

resource "aws_iam_role" "ebs_csi_driver" {
  name = "tech-test-ebs-csi-driver"

  assume_role_policy = data.aws_iam_policy_document.ebs_csi_assume_role.json

  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "ebs_csi_driver" {
  role = aws_iam_role.ebs_csi_driver.name

  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"
}
