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

variable "state_bucket_name" {
  description = "Name of the Terraform state bucket; empty means prenv-tfstate-<account id>"
  type        = string
  default     = ""

  validation {
    condition     = var.state_bucket_name == "" || can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.state_bucket_name))
    error_message = "state_bucket_name must be a valid S3 bucket name or empty."
  }
}
