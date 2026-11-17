output "aws_region" {
  description = "Region the platform lives in"
  value       = var.aws_region
}

output "vpc_id" {
  description = "VPC that holds the load balancer and the preview tasks"
  value       = aws_vpc.this.id
}

output "task_subnet_ids" {
  description = "Subnets preview tasks are placed in"
  value       = local.task_subnet_ids
}

output "assign_public_ip" {
  description = "Whether preview tasks need a public IP to reach the registry"
  value       = !var.nat_gateway
}

output "tasks_security_group_id" {
  description = "Security group attached to preview tasks"
  value       = aws_security_group.tasks.id
}

output "cluster_arn" {
  description = "ECS cluster that runs previews"
  value       = aws_ecs_cluster.this.arn
}

output "cluster_name" {
  description = "Name of the ECS cluster"
  value       = aws_ecs_cluster.this.name
}

output "listener_arn" {
  description = "HTTP listener that preview rules attach to"
  value       = aws_lb_listener.http.arn
}

output "base_url" {
  description = "Address of the load balancer; previews live under a path prefix on it"
  value       = "http://${aws_lb.this.dns_name}"
}

output "ecr_repository_url" {
  description = "Registry URL for the app image"
  value       = aws_ecr_repository.app.repository_url
}

output "task_execution_role_arn" {
  description = "Role ECS uses to pull images, write logs and read parameters"
  value       = aws_iam_role.task_execution.arn
}

output "app_port" {
  description = "Port the app container listens on"
  value       = var.app_port
}

output "log_retention_days" {
  description = "Retention applied to preview log groups"
  value       = var.log_retention_days
}
