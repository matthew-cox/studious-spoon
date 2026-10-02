# ADR 0001: Terraform for AWS (plan-ready, offline-tested)

- **Status:** Accepted (design). Implementation **not started** as of 2026-10-02.
- **Spec:** `docs/superpowers/specs/2026-10-02-terraform-aws-design.md` (decisions T1–T10).
- **Plan:** `docs/superpowers/plans/2026-10-02-terraform-aws.md` (12 tasks, about 3,800 lines; read one task at a time).
- **Resume here:** `docs/superpowers/plans/2026-10-02-terraform-aws-HANDOFF.md`.
- **The one-page ops answer** (diagram, state, monitoring): `docs/operations.md`.

## Context
- **Why:** a technical-interview exercise. The URL shortener already runs fully locally (Docker/colima) and was designed to map onto AWS (parent spec `2026-10-01-url-shortener-design.md` §11).
- **Constraint:** the user wants production-shaped Terraform to walk interviewers through, and does **not** want AWS costs.

## Decisions

| # | Decision | Why |
|---|---|---|
| T1 | **Plan-ready only; never `terraform apply`.** Correctness comes from `fmt`, `validate`, `tflint`, `trivy` and `terraform test` with a mocked AWS provider, locally (`make tf-check`) and in CI. | No AWS account or costs. Every check runs offline in seconds. |
| T2 | **CloudWatch-only observability.** An ADOT sidecar sends metrics to CloudWatch (EMF) and traces to X-Ray; logs go to CloudWatch Logs via `awslogs`. | Simple, cheap story. Grafana dashboards don't carry over; CloudWatch dashboards are follow-up work. |
| T3 | **Reusable modules, one `stack` module, thin `envs/staging` and `envs/prod` roots.** | Shows the modules are reusable and gives a promotion story. No workspaces. |
| T4 | **The VPC, subnets, ECS cluster and Route 53 zone are assumed**, passed in as IDs (no data-source lookups). | That's how platform teams split ownership, and plans and tests stay offline. |
| T5 | **This stack owns its ALB, ACM certificate and Route 53 records** (`go.`, `admin.`, `auth.`). | The app controls its own edge and TLS; the zone is shared. |
| T6 | **Secrets never reach state:** ephemeral `random_password` plus write-only `secret_string_wo`; the RDS master password is RDS-managed. | State is a classic secret-leak path. |
| T7 | **DB roles and databases come from a one-off `db-bootstrap` ECS task** (an idempotent `psql` script), not the PostgreSQL provider. | RDS is private, so that provider would break offline planning. |
| T8 | **A GitHub OIDC deploy role** (trust pinned to repository + environment, least privilege); no deploy workflow. The user runs self-hosted runners in AWS. | No long-lived keys. Exception: `ecr:GetAuthorizationToken`, `ecs:RegisterTaskDefinition` and `ecs:DescribeTaskDefinition` only accept `Resource: "*"`. |
| T9 | **Tooling:** Terraform via tfenv (`.terraform-version` = 1.16.5); `tflint` and `trivy` via Homebrew; CI pins the same versions. | One version source for local and CI. |
| T10 | **The Terraform state backend is platform-owned:** a versioned, SSE-KMS, public-access-blocked S3 bucket; one key per environment (`shortener/<env>/terraform.tfstate`); native S3 locking (`use_lockfile`). | Isolates staging from prod and allows recovery from bad writes. DynamoDB lock tables are deprecated since Terraform 1.11 (`dynamodb_table` if the platform still uses them). |

Also decided:
- Alarms go to an SNS topic: DLQ not empty, backlog age > 300 s, 5xx rate > 2% (the ALB's own 5xx **plus** target 5xx), unhealthy targets, RDS CPU/storage, processor down.
- An outside-in **Synthetics canary** sends `HEAD` to a dedicated canary short link on `go.<domain>` every minute and expects 302. It's the only signal for DNS, certificate and ALB-rule failures. HEAD records no click, so the canary never pollutes stats.
- The processor's `stopTimeout` is 60 s.
- Keycloak's `/admin/*` is restricted to an IP allowlist.
- An AWS Keycloak image with the `shortener-dev` client stripped and the admin redirect URI taken from `${ADMIN_PUBLIC_BASE_URL}`.

## Consequences
- Nothing is proven against real AWS. The mocks prove structure and intent, not API acceptance.
- Dashboards, the deploy workflow, managing the Keycloak realm with the Terraform provider, and CloudFront/WAF are out of scope (spec §12).
- The rough cost if it were ever applied: staging about $90–110/month, prod about $250–320/month (estimates).
