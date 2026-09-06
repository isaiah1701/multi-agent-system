resource "aws_route53_zone" "primary" {
  name = var.domain_name
}

# Create acm
resource "aws_acm_certificate" "application" {
  domain_name               = var.domain_name
  subject_alternative_names = ["*.${var.domain_name}"]
  validation_method         = var.validation_method

  lifecycle {
    create_before_destroy = true
  }
}


resource "aws_route53_record" "application" {
  # ACM uses one validation record for an apex and its wildcard. Keep the
  # resource key known at plan time and select the apex validation option.
  for_each = toset([var.domain_name])

  name = one([
    for dvo in aws_acm_certificate.application.domain_validation_options :
    dvo.resource_record_name if dvo.domain_name == each.key
  ])
  records = [one([
    for dvo in aws_acm_certificate.application.domain_validation_options :
    dvo.resource_record_value if dvo.domain_name == each.key
  ])]
  type = one([
    for dvo in aws_acm_certificate.application.domain_validation_options :
    dvo.resource_record_type if dvo.domain_name == each.key
  ])

  allow_overwrite = true
  ttl             = 60
  zone_id         = aws_route53_zone.primary.zone_id
}

resource "aws_acm_certificate_validation" "example" {
  count = var.wait_for_validation ? 1 : 0

  certificate_arn         = aws_acm_certificate.application.arn
  validation_record_fqdns = [for record in aws_route53_record.application : record.fqdn]
}
