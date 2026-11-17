output "url" {
  description = "Public address of the preview"
  value       = local.url
}

output "path_prefix" {
  description = "Path prefix the listener rule matches on the load balancer"
  value       = local.prefix
}

output "expires_at" {
  description = "When the sweeper is allowed to remove the preview"
  value       = local.expires_at
}

output "service_name" {
  description = "ECS service that runs the preview"
  value       = aws_ecs_service.this.name
}

output "log_group" {
  description = "CloudWatch log group with app and database output"
  value       = aws_cloudwatch_log_group.this.name
}
