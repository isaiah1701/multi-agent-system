terraform {
  # The bootstrap state is deliberately local: this stack creates the S3 bucket
  # that the production environment uses for remote state.
  backend "local" {}

  # The bootstrap secret uses a write-only provider argument, which keeps its
  # value out of state and requires Terraform 1.11 or newer.
  required_version = ">= 1.11.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}
