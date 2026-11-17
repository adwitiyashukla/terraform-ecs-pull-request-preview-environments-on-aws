data "aws_caller_identity" "current" {}

locals {
  account_id        = data.aws_caller_identity.current.account_id
  state_bucket_name = var.state_bucket_name != "" ? var.state_bucket_name : "prenv-tfstate-${local.account_id}"
  github_owner      = split("/", var.github_repository)[0]
  github_name       = split("/", var.github_repository)[1]
  github_subject    = "repo:${local.github_owner}@${var.github_owner_id}/${local.github_name}@${var.github_repository_id}"
  github_subjects = [
    "${local.github_subject}:pull_request",
    "${local.github_subject}:ref:refs/heads/main",
  ]
  home_regions = [var.aws_region, "us-east-1"]
}

resource "aws_s3_bucket" "state" {
  bucket = local.state_bucket_name
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    bucket_key_enabled = true

    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    id     = "expire-old-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 90
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_policy_document" "github_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = local.github_subjects
    }
  }
}

data "aws_iam_policy_document" "ci_preview" {
  statement {
    sid       = "EcrLogin"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid = "EcrPush"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:CompleteLayerUpload",
      "ecr:DescribeImages",
      "ecr:DescribeRepositories",
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:ListImages",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
    ]
    resources = ["arn:aws:ecr:${var.aws_region}:${local.account_id}:repository/prenv/*"]
  }

  statement {
    sid       = "StateList"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.state.arn]
  }

  statement {
    sid       = "StateReadPlatform"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.state.arn}/platform/terraform.tfstate"]
  }

  statement {
    sid       = "StatePreview"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.state.arn}/preview/*"]
  }

  statement {
    sid       = "LogsDescribe"
    actions   = ["logs:DescribeLogGroups"]
    resources = ["*"]
  }

  statement {
    sid = "LogsPreview"
    actions = [
      "logs:CreateLogGroup",
      "logs:DeleteLogGroup",
      "logs:PutRetentionPolicy",
      "logs:DeleteRetentionPolicy",
      "logs:TagResource",
      "logs:UntagResource",
      "logs:ListTagsForResource",
      "logs:TagLogGroup",
      "logs:UntagLogGroup",
      "logs:ListTagsLogGroup",
    ]
    resources = [
      "arn:aws:logs:${var.aws_region}:${local.account_id}:log-group:/prenv/preview/*",
      "arn:aws:logs:${var.aws_region}:${local.account_id}:log-group:/prenv/preview/*:*",
    ]
  }

  statement {
    sid       = "ParametersDescribe"
    actions   = ["ssm:DescribeParameters"]
    resources = ["*"]
  }

  statement {
    sid = "ParametersPreview"
    actions = [
      "ssm:PutParameter",
      "ssm:GetParameter",
      "ssm:GetParameters",
      "ssm:DeleteParameter",
      "ssm:AddTagsToResource",
      "ssm:RemoveTagsFromResource",
      "ssm:ListTagsForResource",
    ]
    resources = ["arn:aws:ssm:${var.aws_region}:${local.account_id}:parameter/prenv/preview/*"]
  }

  statement {
    sid       = "TaskDefinitionRegister"
    actions   = ["ecs:RegisterTaskDefinition"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/project"
      values   = ["prenv"]
    }
  }

  statement {
    sid = "TaskDefinitionManage"
    actions = [
      "ecs:DeregisterTaskDefinition",
      "ecs:DeleteTaskDefinitions",
      "ecs:DescribeTaskDefinition",
      "ecs:TagResource",
      "ecs:UntagResource",
      "ecs:ListTagsForResource",
    ]
    resources = ["arn:aws:ecs:${var.aws_region}:${local.account_id}:task-definition/prenv-pr-*:*"]
  }

  statement {
    sid = "ServiceManage"
    actions = [
      "ecs:CreateService",
      "ecs:UpdateService",
      "ecs:DeleteService",
      "ecs:DescribeServices",
      "ecs:TagResource",
      "ecs:UntagResource",
      "ecs:ListTagsForResource",
    ]
    resources = ["arn:aws:ecs:${var.aws_region}:${local.account_id}:service/prenv/pr-*"]
  }

  statement {
    sid       = "ClusterRead"
    actions   = ["ecs:DescribeClusters", "ecs:ListServices", "ecs:ListTasks", "ecs:DescribeTasks"]
    resources = ["*"]
  }

  statement {
    sid       = "PassTaskRoles"
    actions   = ["iam:PassRole"]
    resources = ["arn:aws:iam::${local.account_id}:role/prenv-task-execution"]

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }

  statement {
    sid       = "LoadBalancingRead"
    actions   = ["elasticloadbalancing:Describe*"]
    resources = ["*"]
  }

  statement {
    sid = "TargetGroups"
    actions = [
      "elasticloadbalancing:CreateTargetGroup",
      "elasticloadbalancing:DeleteTargetGroup",
      "elasticloadbalancing:ModifyTargetGroup",
      "elasticloadbalancing:ModifyTargetGroupAttributes",
      "elasticloadbalancing:AddTags",
      "elasticloadbalancing:RemoveTags",
    ]
    resources = ["arn:aws:elasticloadbalancing:${var.aws_region}:${local.account_id}:targetgroup/prenv-pr-*/*"]
  }

  statement {
    sid       = "RulesCreate"
    actions   = ["elasticloadbalancing:CreateRule"]
    resources = ["arn:aws:elasticloadbalancing:${var.aws_region}:${local.account_id}:listener/app/prenv/*/*"]
  }

  statement {
    sid = "RulesManage"
    actions = [
      "elasticloadbalancing:DeleteRule",
      "elasticloadbalancing:ModifyRule",
      "elasticloadbalancing:SetRulePriorities",
      "elasticloadbalancing:AddTags",
      "elasticloadbalancing:RemoveTags",
    ]
    resources = ["arn:aws:elasticloadbalancing:${var.aws_region}:${local.account_id}:listener-rule/app/prenv/*/*/*"]
  }
}

data "aws_iam_policy_document" "ci_platform" {
  statement {
    sid       = "StateList"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.state.arn]
  }

  statement {
    sid       = "StatePlatform"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.state.arn}/platform/*"]
  }

  statement {
    sid       = "NetworkRead"
    actions   = ["ec2:Describe*"]
    resources = ["*"]
  }

  statement {
    sid = "NetworkWrite"
    actions = [
      "ec2:CreateVpc",
      "ec2:DeleteVpc",
      "ec2:ModifyVpcAttribute",
      "ec2:CreateSubnet",
      "ec2:DeleteSubnet",
      "ec2:ModifySubnetAttribute",
      "ec2:CreateInternetGateway",
      "ec2:DeleteInternetGateway",
      "ec2:AttachInternetGateway",
      "ec2:DetachInternetGateway",
      "ec2:CreateRouteTable",
      "ec2:DeleteRouteTable",
      "ec2:CreateRoute",
      "ec2:DeleteRoute",
      "ec2:ReplaceRoute",
      "ec2:AssociateRouteTable",
      "ec2:DisassociateRouteTable",
      "ec2:CreateNatGateway",
      "ec2:DeleteNatGateway",
      "ec2:AllocateAddress",
      "ec2:ReleaseAddress",
      "ec2:CreateSecurityGroup",
      "ec2:DeleteSecurityGroup",
      "ec2:AuthorizeSecurityGroupIngress",
      "ec2:AuthorizeSecurityGroupEgress",
      "ec2:RevokeSecurityGroupIngress",
      "ec2:RevokeSecurityGroupEgress",
      "ec2:ModifySecurityGroupRules",
      "ec2:UpdateSecurityGroupRuleDescriptionsIngress",
      "ec2:UpdateSecurityGroupRuleDescriptionsEgress",
      "ec2:CreateVpcEndpoint",
      "ec2:DeleteVpcEndpoints",
      "ec2:ModifyVpcEndpoint",
      "ec2:CreateFlowLogs",
      "ec2:DeleteFlowLogs",
      "ec2:CreateTags",
      "ec2:DeleteTags",
    ]
    resources = ["arn:aws:ec2:${var.aws_region}:${local.account_id}:*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = local.home_regions
    }
  }

  statement {
    sid       = "LoadBalancingRead"
    actions   = ["elasticloadbalancing:Describe*"]
    resources = ["*"]
  }

  statement {
    sid = "LoadBalancingWrite"
    actions = [
      "elasticloadbalancing:CreateLoadBalancer",
      "elasticloadbalancing:DeleteLoadBalancer",
      "elasticloadbalancing:ModifyLoadBalancerAttributes",
      "elasticloadbalancing:SetSecurityGroups",
      "elasticloadbalancing:SetSubnets",
      "elasticloadbalancing:SetIpAddressType",
      "elasticloadbalancing:CreateListener",
      "elasticloadbalancing:DeleteListener",
      "elasticloadbalancing:ModifyListener",
      "elasticloadbalancing:ModifyListenerAttributes",
      "elasticloadbalancing:AddTags",
      "elasticloadbalancing:RemoveTags",
    ]
    resources = ["arn:aws:elasticloadbalancing:${var.aws_region}:${local.account_id}:*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = local.home_regions
    }
  }

  statement {
    sid       = "ClusterCreate"
    actions   = ["ecs:CreateCluster", "ecs:DescribeClusters"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = local.home_regions
    }
  }

  statement {
    sid = "ClusterManage"
    actions = [
      "ecs:DeleteCluster",
      "ecs:UpdateCluster",
      "ecs:UpdateClusterSettings",
      "ecs:PutClusterCapacityProviders",
      "ecs:TagResource",
      "ecs:UntagResource",
      "ecs:ListTagsForResource",
    ]
    resources = ["arn:aws:ecs:${var.aws_region}:${local.account_id}:cluster/prenv"]
  }

  statement {
    sid = "Registry"
    actions = [
      "ecr:CreateRepository",
      "ecr:DeleteRepository",
      "ecr:DescribeRepositories",
      "ecr:PutImageScanningConfiguration",
      "ecr:PutImageTagMutability",
      "ecr:PutLifecyclePolicy",
      "ecr:GetLifecyclePolicy",
      "ecr:DeleteLifecyclePolicy",
      "ecr:GetRepositoryPolicy",
      "ecr:ListTagsForResource",
      "ecr:TagResource",
      "ecr:UntagResource",
    ]
    resources = ["arn:aws:ecr:${var.aws_region}:${local.account_id}:repository/prenv/*"]
  }

  statement {
    sid       = "LogsDescribe"
    actions   = ["logs:DescribeLogGroups"]
    resources = ["*"]
  }

  statement {
    sid = "LogsManage"
    actions = [
      "logs:CreateLogGroup",
      "logs:DeleteLogGroup",
      "logs:PutRetentionPolicy",
      "logs:DeleteRetentionPolicy",
      "logs:TagResource",
      "logs:UntagResource",
      "logs:ListTagsForResource",
      "logs:TagLogGroup",
      "logs:UntagLogGroup",
      "logs:ListTagsLogGroup",
    ]
    resources = [
      "arn:aws:logs:${var.aws_region}:${local.account_id}:log-group:/prenv/*",
      "arn:aws:logs:${var.aws_region}:${local.account_id}:log-group:/prenv/*:*",
    ]
  }

  statement {
    sid = "Roles"
    actions = [
      "iam:CreateRole",
      "iam:DeleteRole",
      "iam:GetRole",
      "iam:UpdateRole",
      "iam:UpdateAssumeRolePolicy",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:ListRoleTags",
      "iam:AttachRolePolicy",
      "iam:DetachRolePolicy",
      "iam:PutRolePolicy",
      "iam:DeleteRolePolicy",
      "iam:GetRolePolicy",
      "iam:ListRolePolicies",
      "iam:ListAttachedRolePolicies",
      "iam:ListInstanceProfilesForRole",
      "iam:PassRole",
    ]
    resources = ["arn:aws:iam::${local.account_id}:role/prenv-*"]
  }

  statement {
    sid       = "ServiceLinkedRoles"
    actions   = ["iam:CreateServiceLinkedRole"]
    resources = ["arn:aws:iam::${local.account_id}:role/aws-service-role/*"]
  }

  statement {
    sid = "Alerts"
    actions = [
      "sns:CreateTopic",
      "sns:DeleteTopic",
      "sns:GetTopicAttributes",
      "sns:SetTopicAttributes",
      "sns:Subscribe",
      "sns:Unsubscribe",
      "sns:GetSubscriptionAttributes",
      "sns:ListSubscriptionsByTopic",
      "sns:TagResource",
      "sns:UntagResource",
      "sns:ListTagsForResource",
    ]
    resources = ["arn:aws:sns:${var.aws_region}:${local.account_id}:prenv-*"]
  }

  statement {
    sid = "Alarms"
    actions = [
      "cloudwatch:PutMetricAlarm",
      "cloudwatch:DeleteAlarms",
      "cloudwatch:DescribeAlarms",
      "cloudwatch:TagResource",
      "cloudwatch:UntagResource",
      "cloudwatch:ListTagsForResource",
    ]
    resources = ["arn:aws:cloudwatch:${var.aws_region}:${local.account_id}:alarm:prenv-*"]
  }

  statement {
    sid = "Budgets"
    actions = [
      "budgets:ViewBudget",
      "budgets:ModifyBudget",
      "budgets:ListTagsForResource",
      "budgets:TagResource",
      "budgets:UntagResource",
    ]
    resources = ["arn:aws:budgets::${local.account_id}:budget/prenv-*"]
  }

  statement {
    sid = "AccessLogsBucket"
    actions = [
      "s3:CreateBucket",
      "s3:DeleteBucket",
      "s3:ListBucket",
      "s3:ListBucketVersions",
      "s3:GetBucket*",
      "s3:PutBucket*",
      "s3:DeleteBucketPolicy",
      "s3:GetAccelerateConfiguration",
      "s3:GetEncryptionConfiguration",
      "s3:PutEncryptionConfiguration",
      "s3:GetLifecycleConfiguration",
      "s3:PutLifecycleConfiguration",
      "s3:GetReplicationConfiguration",
      "s3:DeleteObject",
      "s3:DeleteObjectVersion",
    ]
    resources = ["arn:aws:s3:::prenv-alb-logs-*", "arn:aws:s3:::prenv-alb-logs-*/*"]
  }
}

resource "aws_iam_role" "ci_preview" {
  name               = "prenv-ci-preview"
  assume_role_policy = data.aws_iam_policy_document.github_trust.json
}

resource "aws_iam_role_policy" "ci_preview" {
  name   = "preview"
  role   = aws_iam_role.ci_preview.id
  policy = data.aws_iam_policy_document.ci_preview.json
}

resource "aws_iam_role" "ci_platform" {
  name               = "prenv-ci-platform"
  assume_role_policy = data.aws_iam_policy_document.github_trust.json
}

resource "aws_iam_role_policy" "ci_platform" {
  name   = "platform"
  role   = aws_iam_role.ci_platform.id
  policy = data.aws_iam_policy_document.ci_platform.json
}
