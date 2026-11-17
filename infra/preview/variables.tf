variable "aws_region" {
  description = "Region that holds every prenv resource"
  type        = string
  default     = "us-east-2"

  validation {
    condition     = can(regex("^[a-z]{2}-[a-z]+-[0-9]$", var.aws_region))
    error_message = "aws_region must look like us-east-2."
  }
}

variable "state_bucket" {
  description = "Bucket that holds the platform state this preview reads its wiring from"
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.state_bucket))
    error_message = "state_bucket must be a valid S3 bucket name."
  }
}

variable "pr_number" {
  description = "Pull request number; doubles as the listener rule priority"
  type        = number

  validation {
    condition     = var.pr_number >= 1 && var.pr_number <= 49999 && floor(var.pr_number) == var.pr_number
    error_message = "pr_number must be a whole number between 1 and 49999."
  }
}

variable "image_tag" {
  description = "Tag of the app image in the registry"
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$", var.image_tag))
    error_message = "image_tag must be a valid image tag."
  }
}

variable "ttl_hours" {
  description = "Hours the preview lives after its last deploy before the sweeper removes it"
  type        = number
  default     = 24

  validation {
    condition     = var.ttl_hours >= 1 && var.ttl_hours <= 168
    error_message = "ttl_hours must be between 1 and 168."
  }
}

variable "expires_at" {
  description = "Explicit RFC 3339 expiry; null derives it from ttl_hours at plan time"
  type        = string
  default     = null

  validation {
    condition     = var.expires_at == null || can(formatdate("YYYY", var.expires_at))
    error_message = "expires_at must be an RFC 3339 timestamp or null."
  }
}

variable "cpu" {
  description = "Fargate CPU units for the whole task"
  type        = number
  default     = 512

  validation {
    condition     = contains([256, 512, 1024, 2048, 4096], var.cpu)
    error_message = "cpu must be one of 256, 512, 1024, 2048 or 4096."
  }
}

variable "memory" {
  description = "Fargate memory in MiB for the whole task"
  type        = number
  default     = 1024

  validation {
    condition = (
      (var.cpu == 256 && contains([512, 1024, 2048], var.memory)) ||
      (var.cpu == 512 && var.memory >= 1024 && var.memory <= 4096 && var.memory % 1024 == 0) ||
      (var.cpu == 1024 && var.memory >= 2048 && var.memory <= 8192 && var.memory % 1024 == 0) ||
      (var.cpu == 2048 && var.memory >= 4096 && var.memory <= 16384 && var.memory % 1024 == 0) ||
      (var.cpu == 4096 && var.memory >= 8192 && var.memory <= 30720 && var.memory % 1024 == 0)
    )
    error_message = "memory must be a size Fargate allows for the chosen cpu."
  }
}

variable "spot" {
  description = "Run the task on Fargate Spot instead of on demand"
  type        = bool
  default     = true
}

variable "database" {
  description = "Run a Postgres sidecar next to the app"
  type        = bool
  default     = true
}

variable "postgres_image" {
  description = "Image for the Postgres sidecar"
  type        = string
  default     = "public.ecr.aws/docker/library/postgres:16-alpine"

  validation {
    condition     = length(var.postgres_image) > 0
    error_message = "postgres_image must not be empty."
  }
}
