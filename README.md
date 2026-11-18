# Pull request preview environments on AWS

Every pull request on this repo gets its own copy of the app running on AWS, at its own URL, posted as a comment on the pull request. Push another commit and the same URL updates. Close the pull request and everything it created is torn down. There is an hourly sweeper for anything that slips through.

I built this to understand how preview environments actually work on AWS when you wire them yourself: Terraform for the infrastructure, GitHub Actions with OIDC for the pipeline, ECS on Fargate for the containers. The AWS resources all carry the short name prenv.

The sample app is a small FastAPI notes board with a Postgres database next to it, enough to prove that a preview has real state and that links and form posts work under its path.

## What happens on a pull request

The preview workflow runs on open, push and reopen. It assumes an IAM role through GitHub OIDC (no AWS keys are stored in GitHub), builds the app image, pushes it to ECR tagged `pr-N-<sha>`, and runs `terraform apply` in `infra/preview` with the state key `preview/pr-N.tfstate`. That apply creates seven resources: a log group, a random database password in SSM, a task definition with the app and a Postgres sidecar, a target group, a listener rule on the shared load balancer, and a Fargate Spot service. Terraform waits for the service to reach a steady state, then `previewctl wait` polls the health endpoint, `previewctl smoke` posts a note through the API and reads it back, and a sticky comment on the pull request reports the URL and the timings.

When the pull request closes, the destroy job runs `terraform destroy` for that state key and deletes the state file. The sweep workflow runs every hour, lists the services in the cluster and the state files in the bucket, and destroys anything whose expiry tag has passed or whose pull request is closed or gone.

## Measured on this repo

Numbers from the pull request comments and the workflow logs of PR #1 and PR #2 in this repo, region us-east-2, one task with 0.5 vCPU and 1 GB on Fargate Spot.

| Event | Measured |
| --- | --- |
| New pull request (PR #2) | live 173 s after the pull request opened: image build and push 18 s, terraform apply 124 s, healthy the moment apply returned |
| Push to the same pull request | live 175 s after the push: image build and push 21 s, terraform apply 114 s, healthy 1 s after apply |
| Pull request closed | torn down in 217 s (PR #1) and 218 s (PR #2); 200 s of that is ECS draining the service before it reports inactive, the other six resources go in about a second each |

The apply time is almost all ECS: pulling two images, starting Postgres, waiting for its health check, then for the app health check, then for two healthy target group checks. The platform under it is 46 resources and the bootstrap is 10.

## Layout

```
app/                FastAPI notes app, Dockerfile, pinned requirements
infra/bootstrap/    state bucket, GitHub OIDC provider, the two CI roles (local state, applied once)
infra/platform/     VPC, load balancer, ECR, ECS cluster, task execution role, alarms, budget
infra/preview/      everything one pull request needs, applied once per pull request
previewctl/         small boto3 CLI: list, wait, smoke, cost, sweep, destroy
tests/              app tests, previewctl tests under moto, Terraform rule tests, workflow tests
.github/workflows/  ci, preview, platform, sweep
```

Three Terraform roots instead of one because they change at different speeds and need different permissions. Bootstrap is applied by a person once. Platform is applied by the platform workflow (plan on pull requests, apply on main). Preview is applied by the preview workflow with the narrow preview role.

## Design choices

Paths instead of hostnames. Previews live at `/pr-N` on one load balancer, so there is no domain, no certificate and no DNS to wait for. The listener rule priority is the pull request number, so two previews can never collide. The app is mounted under `PATH_PREFIX`, so its links, redirects and health check all work under the prefix, and the listener's default action answers `404 {"error":"no such preview"}` for everything else.

No NAT gateway. Tasks run in the public subnets with a public IP. Their security group lets the load balancer in on the app port and lets only HTTPS and VPC DNS out, which is what image pulls, log delivery and parameter reads need. An S3 gateway endpoint keeps the ECR layer downloads off the internet path. Private subnets, the NAT gateway and the private route are still in the code behind `nat_gateway = true`.

Postgres as a sidecar, not RDS. Each preview gets its own throwaway database inside the task, with a random 32 character password stored as an SSM SecureString that the task execution role reads when the task starts. The app container waits for the database container to report healthy before it starts. The data lives as long as the task, so every deploy starts with an empty database, which is what you want from a throwaway preview. An RDS instance per preview would add minutes to every deploy and money for nothing a reviewer needs.

Fargate Spot with a circuit breaker. A preview can disappear for a minute when Spot reclaims it, which is fine for a preview, and AWS prices Spot up to 70 percent below on demand. The deployment circuit breaker rolls back if a new image never becomes healthy, and `wait_for_steady_state` makes the apply fail in that case instead of leaving a broken preview behind. It is also why the comment says healthy 0 s after apply: by the time apply returns the task is already serving.

Two CI roles through OIDC, nothing stored. The trust policy only accepts tokens whose subject is this exact repo, in the immutable `repo:owner@id/name@id` form GitHub now issues, on `main` or a pull request. The preview role can only create, change and delete things named `prenv-pr-*`, plus the handful of ECS, ECR, logs and SSM calls that only work on `*`, listed one by one. The platform role is wider but still limited to the prenv names and the home region. I found the exact set of actions by reading the AccessDenied errors of the first runs and checking each fix with `aws iam simulate-principal-policy` before rerunning.

Time to live. Every preview carries an `expires_at` tag, 24 hours after its last deploy by default (`PREVIEW_TTL_HOURS` repository variable). The sweeper reads the tag from the service and the state key from the bucket, so a preview whose service was deleted by hand or whose pull request was deleted still gets cleaned up.

State. One S3 bucket, versioned and encrypted, with `use_lockfile = true` so there is no DynamoDB table. Platform state is one key; each preview has its own key and reads the platform outputs through `terraform_remote_state`. The preview role can read the platform state but only write under `preview/`.

Registry and logs. ECR tags are immutable and scanned on push, untagged images expire after a day and only the 30 newest tagged images are kept. Each preview writes to its own log group with 7 day retention. VPC flow logs and load balancer access logs are on, and the access logs expire after 30 days.

Guardrails. A CloudWatch alarm fires when the load balancer returns more than 10 responses with a 5xx status in five minutes, and an AWS Budget of 30 USD a month notifies at 80 percent actual and 100 percent forecast, both through one SNS topic. At list price the always-on part is the load balancer at 0.0225 USD an hour, about 16 USD a month, and each preview task is at most 0.0247 USD an hour on demand and less on Spot.

## Running it yourself

You need an AWS account with admin credentials in your shell, Terraform 1.10 or newer, Docker, Python 3.11 or newer and the GitHub CLI logged in. Fork or copy the repo first, because the trust policy is pinned to one repo.

Apply the bootstrap once with your own repo and ids. The owner and repo ids are what GitHub puts in the OIDC subject.

```
gh api users/<owner> --jq .id
gh api repos/<owner>/<repo> --jq .id
terraform -chdir=infra/bootstrap init
terraform -chdir=infra/bootstrap apply -var github_repository=<owner>/<repo> -var github_owner_id=<owner id> -var github_repository_id=<repo id>
```

Copy the four values from the `github_variables` output into repository variables, then apply the platform. The platform workflow does the same apply on every push to main that touches `infra/platform`, so the local apply is only needed the first time.

```
gh variable set AWS_REGION --body us-east-2
gh variable set TF_STATE_BUCKET --body <state bucket>
gh variable set PREVIEW_ROLE_ARN --body <preview role arn>
gh variable set PLATFORM_ROLE_ARN --body <platform role arn>
terraform -chdir=infra/platform init -backend-config="bucket=<state bucket>" -backend-config="region=us-east-2"
terraform -chdir=infra/platform apply
```

Open a pull request and watch the preview workflow. Set `ALERT_EMAIL` as a repository variable if you want the alarm and budget mails. To look at previews from your machine:

```
pip install -e .
python -m previewctl list
python -m previewctl smoke <pr number>
python -m previewctl sweep --dry-run --repo <owner>/<repo> --state-bucket <state bucket>
```

`previewctl cost` groups the last 14 days of spend by the `pr` tag, which needs that tag activated as a cost allocation tag in the billing console first.

## Checks

`pytest` runs 72 tests: the app on sqlite (and on a real Postgres 16 in CI), previewctl against moto, Terraform rules parsed with python-hcl2 such as "only the load balancer listens to the internet", "CI policies never grant wildcard actions" and "the preview rule priority is the pull request number", and the workflow files (pinned actions, minimal permissions, no long lived AWS keys). `ruff` checks and formats the Python. CI runs `terraform fmt -check`, `terraform validate` and `tflint` on all three roots, and `checkov` over the Terraform, the Dockerfile and the workflows, where 517 checks pass.

Checkov checks skipped in `.checkov.yaml`, each on purpose:

| Check | Why it is skipped here |
| --- | --- |
| CKV_AWS_2, CKV_AWS_103, CKV_AWS_378, CKV2_AWS_20 | HTTPS, TLS 1.2 and the HTTP to HTTPS redirect need a certificate and a domain; this project runs plain HTTP on the load balancer address on purpose |
| CKV_AWS_260 | the load balancer is meant to be reachable from anywhere on port 80 |
| CKV2_AWS_28 | a WAF in front of a preview load balancer costs more than the previews |
| CKV_AWS_150 | deletion protection off so `terraform destroy` works; it is a variable |
| CKV_AWS_18, CKV_AWS_144, CKV2_AWS_62 | access logging, replication and event notifications are not needed for a state bucket and a log bucket |
| CKV_AWS_145 | load balancer access logs can only be written to a bucket with SSE-S3, not KMS; the state bucket does use KMS |
| CKV_AWS_26, CKV_AWS_158, CKV_AWS_337 | AWS managed keys instead of customer managed KMS keys for the SNS topic, the log groups and the SSM parameter |
| CKV_AWS_338 | 7 day log retention for previews instead of a year |
| CKV_AWS_356 | a few ECS and ECR actions only work with `*` as the resource; the policy lists exactly those |
| CKV_AWS_394 | the subnets take the first two zones the region offers on purpose, so the zone data source is not pinned to zone ids |
| CKV2_AWS_5 | the tasks security group is attached in the preview root, which checkov scans separately |
