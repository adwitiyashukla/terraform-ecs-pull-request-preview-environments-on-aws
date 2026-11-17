variable "aws_region" {
  description = "Region that holds every prenv resource"
  type        = string
  default     = "us-east-2"

  validation {
    condition     = can(regex("^[a-z]{2}-[a-z]+-[0-9]$", var.aws_region))
    error_message = "aws_region must look like us-east-2."
  }
}

variable "vpc_cidr" {
  description = "CIDR block of the VPC; subnets are carved out of it with four extra bits"
  type        = string
  default     = "10.0.0.0/16"

  validation {
    condition     = can(cidrhost(var.vpc_cidr, 0)) && tonumber(split("/", var.vpc_cidr)[1]) <= 20
    error_message = "vpc_cidr must be a valid CIDR block no smaller than a /20."
  }
}

variable "az_count" {
  description = "Number of availability zones to spread subnets across"
  type        = number
  default     = 2

  validation {
    condition     = var.az_count >= 2 && var.az_count <= 3
    error_message = "az_count must be 2 or 3."
  }
}

variable "nat_gateway" {
  description = "Run tasks in private subnets behind a NAT gateway instead of public subnets with public IPs"
  type        = bool
  default     = false
}

variable "flow_logs" {
  description = "Send VPC flow logs to CloudWatch"
  type        = bool
  default     = true
}

variable "alb_access_logs" {
  description = "Write load balancer access logs to a private S3 bucket"
  type        = bool
  default     = true
}

variable "container_insights" {
  description = "Enable CloudWatch Container Insights on the cluster"
  type        = bool
  default     = true
}

variable "log_retention_days" {
  description = "Retention for every prenv log group"
  type        = number
  default     = 7

  validation {
    condition     = contains([1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365], var.log_retention_days)
    error_message = "log_retention_days must be one of the values CloudWatch accepts."
  }
}

variable "app_port" {
  description = "Port the app container listens on"
  type        = number
  default     = 8000

  validation {
    condition     = var.app_port >= 1024 && var.app_port <= 65535
    error_message = "app_port must be between 1024 and 65535."
  }
}

variable "alert_email" {
  description = "Email address subscribed to alarms and budget alerts; empty disables the subscription"
  type        = string
  default     = ""

  validation {
    condition     = var.alert_email == "" || can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.alert_email))
    error_message = "alert_email must be an email address or empty."
  }
}

variable "monthly_budget_usd" {
  description = "Monthly budget for the account in US dollars"
  type        = number
  default     = 30

  validation {
    condition     = var.monthly_budget_usd > 0
    error_message = "monthly_budget_usd must be positive."
  }
}

variable "alb_5xx_threshold" {
  description = "Number of load balancer 5xx responses in five minutes that trips the alarm"
  type        = number
  default     = 10

  validation {
    condition     = var.alb_5xx_threshold >= 1
    error_message = "alb_5xx_threshold must be at least 1."
  }
}

variable "ecr_keep_images" {
  description = "Number of tagged images kept in the registry"
  type        = number
  default     = 30

  validation {
    condition     = var.ecr_keep_images >= 1 && var.ecr_keep_images <= 1000
    error_message = "ecr_keep_images must be between 1 and 1000."
  }
}

variable "deletion_protection" {
  description = "Protect the load balancer from deletion"
  type        = bool
  default     = false
}
