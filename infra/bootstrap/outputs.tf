output "state_bucket" {
  description = "Bucket that stores the platform and preview states"
  value       = aws_s3_bucket.state.bucket
}

output "ci_preview_role_arn" {
  description = "Role assumed by the preview and sweep workflows"
  value       = aws_iam_role.ci_preview.arn
}

output "ci_platform_role_arn" {
  description = "Role assumed by the platform workflow"
  value       = aws_iam_role.ci_platform.arn
}

output "github_variables" {
  description = "Repository variables the workflows expect"
  value = {
    AWS_REGION        = var.aws_region
    TF_STATE_BUCKET   = aws_s3_bucket.state.bucket
    PREVIEW_ROLE_ARN  = aws_iam_role.ci_preview.arn
    PLATFORM_ROLE_ARN = aws_iam_role.ci_platform.arn
  }
}
