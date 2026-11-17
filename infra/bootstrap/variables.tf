variable "aws_region" {
  description = "Region that holds every prenv resource"
  type        = string
  default     = "us-east-2"

  validation {
    condition     = can(regex("^[a-z]{2}-[a-z]+-[0-9]$", var.aws_region))
    error_message = "aws_region must look like us-east-2."
  }
}

variable "github_repository" {
  description = "GitHub repository allowed to assume the CI roles, written as owner/name"
  type        = string
  default     = "adwitiyashukla/terraform-ecs-pull-request-preview-environments-on-aws"

  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_repository))
    error_message = "github_repository must be written as owner/name."
  }
}

variable "github_owner_id" {
  description = "Numeric id of the GitHub account, part of the immutable OIDC subject claim"
  type        = number
  default     = 293685877

  validation {
    condition     = var.github_owner_id > 0
    error_message = "github_owner_id must be a positive number."
  }
}

variable "github_repository_id" {
  description = "Numeric id of the GitHub repository, part of the immutable OIDC subject claim"
  type        = number
  default     = 1460933850

  validation {
    condition     = var.github_repository_id > 0
    error_message = "github_repository_id must be a positive number."
  }
}

variable "state_bucket_name" {
  description = "Name of the Terraform state bucket; empty means prenv-tfstate-<account id>"
  type        = string
  default     = ""

  validation {
    condition     = var.state_bucket_name == "" || can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.state_bucket_name))
    error_message = "state_bucket_name must be a valid S3 bucket name or empty."
  }
}
