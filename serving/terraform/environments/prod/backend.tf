terraform {
  # Bucket, key, region, and S3-native locking are supplied through backend.hcl
  # after the local-state bootstrap stack has created the bucket.
  backend "s3" {}
}
