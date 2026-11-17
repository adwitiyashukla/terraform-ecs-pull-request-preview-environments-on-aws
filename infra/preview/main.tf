data "terraform_remote_state" "platform" {
  backend = "s3"

  config = {
    bucket = var.state_bucket
    key    = "platform/terraform.tfstate"
    region = var.aws_region
  }
}

locals {
  platform        = data.terraform_remote_state.platform.outputs
  name            = "pr-${var.pr_number}"
  prefix          = "/pr-${var.pr_number}"
  url             = "${local.platform.base_url}${local.prefix}"
  image           = "${local.platform.ecr_repository_url}:${var.image_tag}"
  expires_at      = var.expires_at != null ? var.expires_at : timeadd(plantimestamp(), "${var.ttl_hours}h")
  db_password_arn = one(aws_ssm_parameter.db_password[*].arn)
  log_group       = "/prenv/preview/${local.name}"
  tags = {
    pr         = tostring(var.pr_number)
    image_tag  = var.image_tag
    url        = local.url
    expires_at = local.expires_at
  }
  health_command = "python -c \"import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:${local.platform.app_port}${local.prefix}/healthz', timeout=3).status == 200 else 1)\""
  app_container = {
    name                   = "app"
    image                  = local.image
    essential              = true
    readonlyRootFilesystem = true
    portMappings = [
      {
        containerPort = local.platform.app_port
        protocol      = "tcp"
      },
    ]
    environment = [
      { name = "PREVIEW_NAME", value = local.name },
      { name = "PREVIEW_URL", value = local.url },
      { name = "PATH_PREFIX", value = local.prefix },
      { name = "IMAGE_TAG", value = var.image_tag },
      { name = "EXPIRES_AT", value = local.expires_at },
      { name = "PORT", value = tostring(local.platform.app_port) },
      { name = "DB_HOST", value = "127.0.0.1" },
      { name = "DB_NAME", value = "app" },
      { name = "DB_USER", value = "app" },
    ]
    secrets   = var.database ? [{ name = "DB_PASSWORD", valueFrom = local.db_password_arn }] : []
    dependsOn = var.database ? [{ containerName = "db", condition = "HEALTHY" }] : []
    healthCheck = {
      command     = ["CMD-SHELL", local.health_command]
      interval    = 15
      timeout     = 5
      retries     = 3
      startPeriod = 30
    }
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = local.log_group
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "app"
      }
    }
  }
  db_container = {
    name      = "db"
    image     = var.postgres_image
    essential = true
    environment = [
      { name = "POSTGRES_USER", value = "app" },
      { name = "POSTGRES_DB", value = "app" },
    ]
    secrets = [{ name = "POSTGRES_PASSWORD", valueFrom = local.db_password_arn }]
    healthCheck = {
      command     = ["CMD-SHELL", "pg_isready -U app -d app"]
      interval    = 10
      timeout     = 5
      retries     = 5
      startPeriod = 15
    }
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = local.log_group
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "db"
      }
    }
  }
  containers = concat([local.app_container], var.database ? [local.db_container] : [])
}

resource "aws_cloudwatch_log_group" "this" {
  name              = local.log_group
  retention_in_days = local.platform.log_retention_days
  tags              = local.tags
}

resource "random_password" "db" {
  count = var.database ? 1 : 0

  length  = 32
  special = false
}

resource "aws_ssm_parameter" "db_password" {
  count = var.database ? 1 : 0

  name  = "/prenv/preview/${local.name}/db_password"
  type  = "SecureString"
  value = random_password.db[0].result
  tags  = local.tags
}

resource "aws_ecs_task_definition" "this" {
  family                   = "prenv-${local.name}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.cpu)
  memory                   = tostring(var.memory)
  execution_role_arn       = local.platform.task_execution_role_arn
  container_definitions    = jsonencode(local.containers)
  tags                     = local.tags

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
}

resource "aws_lb_target_group" "this" {
  name                 = "prenv-${local.name}"
  port                 = local.platform.app_port
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = local.platform.vpc_id
  deregistration_delay = 5
  tags                 = local.tags

  health_check {
    path                = "${local.prefix}/healthz"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
    matcher             = "200"
  }
}

resource "aws_lb_listener_rule" "this" {
  listener_arn = local.platform.listener_arn
  priority     = var.pr_number
  tags         = local.tags

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.this.arn
  }

  condition {
    path_pattern {
      values = [local.prefix, "${local.prefix}/*"]
    }
  }
}

resource "aws_ecs_service" "this" {
  name                              = local.name
  cluster                           = local.platform.cluster_arn
  task_definition                   = aws_ecs_task_definition.this.arn
  desired_count                     = 1
  health_check_grace_period_seconds = 90
  propagate_tags                    = "SERVICE"
  wait_for_steady_state             = true
  tags                              = local.tags

  capacity_provider_strategy {
    capacity_provider = var.spot ? "FARGATE_SPOT" : "FARGATE"
    weight            = 1
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = local.platform.task_subnet_ids
    security_groups  = [local.platform.tasks_security_group_id]
    assign_public_ip = local.platform.assign_public_ip
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.this.arn
    container_name   = "app"
    container_port   = local.platform.app_port
  }

  timeouts {
    create = "15m"
    update = "15m"
    delete = "15m"
  }

  depends_on = [aws_lb_listener_rule.this, aws_cloudwatch_log_group.this]
}
