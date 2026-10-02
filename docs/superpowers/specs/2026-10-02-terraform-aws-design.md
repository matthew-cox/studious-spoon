# Terraform for AWS — Design

- **Status:** design approved in conversation 2026-10-02; this document is the written spec.
- **Parent spec:** `2026-10-01-url-shortener-design.md` (§11 AWS Mapping is the starting point; this document supersedes it where they differ).
- **Context:** technical interview exercise. The Terraform must be production-shaped and reviewable, and provably correct **without an AWS account**.

## 1. Goals and Non-Goals

**Goals**
- Terraform in `terraform/` that would deploy the shortener (api, admin, click-processor, Keycloak, RDS, SQS) to AWS for two environments, `staging` and `prod`.
- Plug into existing platform infrastructure (VPC, ECS cluster, Route 53 zone) through variables.
- Prove it offline: `terraform fmt`, `validate`, `tflint`, a `trivy` config scan, and `terraform test` with mocked AWS providers, locally (`make tf-check`) and in CI.
- No secret value ever stored in Terraform state or committed.
- The AWS side of a deploy pipeline: a least-privilege GitHub OIDC deploy role.
- A small set of CloudWatch alarms that cover the failure modes the system was designed around.

**Non-Goals**
- `terraform apply` into a real account. No AWS costs are incurred, ever.
- The VPC, subnets, NAT/VPC endpoints, ECS cluster, Route 53 hosted zone, and the self-hosted GitHub runners (platform-owned).
- The deploy workflow itself (building, pushing, rolling out).
- CloudWatch dashboards (follow-up, see §9).
- Keycloak realm management with the Keycloak Terraform provider (parent spec §12 item 14), CloudFront, WAF.

## 2. Key Decisions

| # | Decision | Rationale |
|---|---|---|
| T1 | **Plan-ready only, never applied** | Avoids AWS costs. Correctness comes from `validate`, `tflint`, `trivy`, and `terraform test` with mocked providers, all of which run offline in seconds. |
| T2 | **CloudWatch-only observability.** ADOT collector sidecar → CloudWatch metrics (EMF) and X-Ray traces; logs via the `awslogs` driver | Simplest, cheapest AWS story, no extra services. The Grafana dashboard does not carry over, so CloudWatch dashboards are follow-up work (by hand, via CloudWatch's AI dashboard generation, or in Terraform), with `scripts/gen-dashboard` as the reference for what to show. |
| T3 | **Reusable modules + two thin environment roots** (`envs/staging`, `envs/prod`) | Shows the modules are genuinely reusable and gives a promotion story. The roots differ only in sizes and protection settings. Workspaces were rejected for environment separation. |
| T4 | **Platform infrastructure is assumed and passed in as IDs/ARNs**, not looked up with data sources | Matches how platform teams split ownership. Plain variables keep `plan` and tests fully offline. |
| T5 | **This stack owns its ALB, ACM certificate, and Route 53 records**; the hosted zone is assumed | The app controls its own edge and TLS lifecycle; the zone is shared. |
| T6 | **Secrets never in state**: ephemeral `random_password` + the AWS provider's write-only `secret_string_wo`; the RDS master password is RDS-managed | Terraform state is a common secret-leak path. Values exist only in Secrets Manager. |
| T7 | **Database roles and databases are created by a one-off `db-bootstrap` ECS task**, not the Terraform PostgreSQL provider | RDS is private, so that provider would need network access during `plan`, which breaks offline planning and tests. The task reuses the local `bootstrap.sql` semantics, made idempotent. |
| T8 | **GitHub OIDC deploy role** (trust pinned to repository + GitHub environment), no deploy workflow | Fits self-hosted runners in AWS. No long-lived keys; least privilege is the interesting part. A workflow that can never run would be unproven code. |
| T9 | **Tooling:** Terraform via **tfenv** (`.terraform-version`), `tflint` and `trivy` via Homebrew locally; pinned releases in CI | One version source for local and CI. |

## 3. Layout

```
.terraform-version
terraform/
  modules/
    edge/            ACM certificate + DNS validation, ALB, listeners, host rules, Route 53 alias records
    data/            RDS PostgreSQL 16, subnet group, parameter group, security group
    queue/           SQS click-events + click-events-dlq, redrive policy
    secrets/         Secrets Manager secrets with write-only values
    service/         generic Fargate service (app + ADOT sidecar), IAM, SG, log group, optional ALB attachment and autoscaling
    task/            one-off task definition (db-bootstrap, migrate)
    ecr/             one repository per image, scan on push, lifecycle policy
    alarms/          SNS topic + CloudWatch alarms
    ci-deploy-role/  GitHub OIDC provider (optional) + deploy role
  envs/
    staging/         main.tf, variables.tf, outputs.tf, backend.tf (S3, partial config), terraform.tfvars.example
    prod/            same shape, prod sizes
  tests/             terraform test files (*.tftest.hcl) with mock providers
  README.md
infra/keycloak/Dockerfile   optimized Keycloak image with the realm baked in (the one addition outside terraform/)
```

- **State:** each environment root declares an S3 backend with native S3 locking (`use_lockfile = true`) via partial configuration. It is never initialized here (`init -backend=false`).
- **Versions:** Terraform `~> 1.13` and AWS provider `~> 6.x`; exact current releases are pinned when the plan is written. `random` provider for ephemeral passwords.

## 4. Platform Inputs (per environment)

| Variable | Meaning |
|---|---|
| `vpc_id` | Existing VPC |
| `private_subnet_ids` | Subnets with outbound access (NAT or VPC endpoints) for tasks and RDS |
| `public_subnet_ids` | Subnets for the ALB |
| `ecs_cluster_arn` | Existing Fargate-enabled cluster with Container Insights |
| `route53_zone_id`, `domain` | Existing hosted zone and the base domain, e.g. `shortener.example.com` |
| `github_repository` | `org/repo` for the OIDC trust policy |
| `github_oidc_provider_arn` | Optional; when null the module creates the provider |
| `admin_cidrs` | CIDRs allowed to reach the Keycloak admin console |
| `image_tag` | Image tag to deploy (set by the pipeline) |

## 5. Edge

- **Hostnames:** `go.<domain>` → api (redirects and `/api/v1`), `admin.<domain>` → admin UI, `auth.<domain>` → Keycloak.
- **Certificate:** one ACM certificate for the three names, validated with DNS records this stack creates in the assumed zone.
- **ALB:** public, in `public_subnet_ids`.
  - HTTPS on 443 with a TLS 1.3/1.2 policy. HTTP on 80 redirects to HTTPS.
  - Host-header rules map each hostname to its target group. The default action is a fixed 404.
  - `auth.<domain>` with path `/admin/*` is forwarded only for source IPs in `admin_cidrs`, and gets a fixed 403 otherwise. The login and OIDC endpoints stay public.
- **DNS:** Route 53 alias A/AAAA records for the three names point to the ALB.

## 6. Data and Secrets

**RDS (`data`)**
- PostgreSQL 16 (matches local `postgres:16.10`), encrypted gp3 storage, `rds.force_ssl = 1`.
- `manage_master_user_password = true`: RDS creates and rotates the master secret.
- Security group: 5432 only from the ECS services' and one-off tasks' security groups, never from a CIDR.

| | staging | prod |
|---|---|---|
| Instance | `db.t4g.micro` | `db.t4g.small` |
| Multi-AZ | no | yes |
| Backup retention | 1 day | 7 days |
| Deletion protection / final snapshot | no | yes |

**Database bootstrap (`db-bootstrap` task)**
- Same result as `infra/postgres/bootstrap.sql`:
  - login roles `migrator`, `api_user`, `processor_user`, `admin_user`, `keycloak`
  - databases `shortener` (owner `migrator`) and `keycloak` (owner `keycloak`)
  - `CONNECT` grants
- Runs `psql` in the `postgres:16` image with an idempotent script rendered by `templatefile`. Roles and databases are created only when missing; passwords are always (re)set from Secrets Manager.
- Credentials: the RDS master secret plus the per-role secrets, injected by ECS.

**Secrets (`secrets`)**
- Values come from ephemeral `random_password` resources written through `secret_string_wo` (Terraform ≥ 1.11, AWS provider ≥ 6), so they never enter state.
- One JSON secret per database role, for example: `{"username": "api_user", "password": "…", "url": "postgresql+psycopg://api_user:…@<rds-endpoint>:5432/shortener?sslmode=require"}`.
  - The services keep their existing `DATABASE_URL` setting (it comes from the `url` key).
  - The bootstrap task reads the `password` key.
- App secrets: the admin OIDC client secret, the admin cookie secret, and the Keycloak bootstrap admin password.
- Each service's execution role can read only the secrets it needs.

**Configuration**
- Plain environment variables in task definitions:
  - `API_BASE_URL`, `PUBLIC_BASE_URL`
  - `OIDC_ISSUER` and the internal OIDC URL, both `https://auth.<domain>/realms/shortener`
  - SQS queue names; `SQS_ENDPOINT_URL` stays unset on AWS
  - `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318` (the sidecar)
  - `DEPLOYMENT_ENVIRONMENT`, `SERVICE_VERSION` (the image tag), `COOKIE_SECURE=true`
  - `OBSERVABILITY_URL` unset (no Grafana on AWS; the admin link stays hidden)
- No application code changes.

**Keycloak image (`infra/keycloak/Dockerfile`)**
- Based on `quay.io/keycloak/keycloak:26.4.0`. Builds with `kc.sh build --db=postgres --health-enabled=true` and runs `start --optimized --import-realm`.
- The realm JSON is baked in. The admin client secret comes from an `${ENV}` placeholder in the import file, so it is never committed.
- Clustering uses the default `jdbc-ping` cache stack over the database (no multicast needed on Fargate).

## 7. Services

**Generic `service` module**
- **Inputs:**
  - name, ECR repository + `image_tag`, CPU and memory, desired count
  - environment map, secrets map (secret ARN + JSON key), command override
  - container port, container health check
  - optional ALB attachment (host header, rule priority, health path, health port)
  - optional autoscaling (CPU target tracking or SQS step scaling)
  - extra task-role policy JSON, `stop_timeout`
- **Each task:**
  - The app container plus the **ADOT collector sidecar** (`public.ecr.aws/aws-observability/aws-otel-collector`, pinned), configured via `AOT_CONFIG_CONTENT`: OTLP receiver on `localhost:4318` → `awsemf` exporter (namespace `Shortener`, dimension `service.name`) and `awsxray` exporter.
  - The app container `dependsOn` the sidecar (`START`).
  - Both containers log through `awslogs` to a per-service log group: 7-day retention in staging, 30 in prod.
- **Deployment:** circuit breaker with rollback on; 100% minimum healthy, 200% maximum.
- **Network:** private subnets, no public IP.
- **IAM:**
  - Each task role can write X-Ray segments and its own EMF log stream, plus any service-specific permissions below.
  - Each execution role can pull from ECR, write to its log group, and read only its own secrets.

| Service | Hostname | ALB health check | staging | prod | Task-role extras | stopTimeout |
|---|---|---|---|---|---|---|
| `api` | `go.` | `/healthz` | 1 | 2–6, CPU target 60% | `sqs:SendMessage`, `GetQueueUrl` on `click-events` | 30 s |
| `admin` | `admin.` | `/healthz` | 1 | 2 | — | 30 s |
| `keycloak` | `auth.` | `/health/ready` on port 9000 | 1 | 2 | — | 30 s |
| `click-processor` | — | container health check | 1 | 1–4, step scaling on visible messages | receive, delete and change visibility on `click-events`; `GetQueueAttributes` on both queues | 60 s |

- **ALB health checks use `/healthz` (liveness), not `/readyz`**, so a database blip doesn't pull every target at once.
- **Processor `stopTimeout` of 60 s** covers its 25 s shutdown grace plus about 6 s of telemetry flush. This resolves the parent spec's deferred "ECS stopTimeout" item.

**Security groups**
- ALB: 80/443 from anywhere.
- Each exposed service: its container port from the ALB security group only.
- Processor: no ingress.
- RDS: as in §6.

**One-off tasks (`task` module)**
- `db-bootstrap` (§6).
- `migrate`: the api image running `alembic upgrade head`, as the local `migrate` compose service does. `MIGRATOR_DATABASE_URL` is injected from the `migrator` secret's `url` key.

## 8. Delivery (AWS side only)

- **ECR (`ecr`):** repositories `api`, `click-processor`, `admin`, `keycloak`; scan on push; immutable tags; a lifecycle policy that keeps the last 30 images.
- **Deploy role (`ci-deploy-role`):**
  - Trust: GitHub OIDC with `sub` = `repo:<github_repository>:environment:<env>` and `aud` = `sts.amazonaws.com`.
  - Permissions, all scoped to this stack's resources with no `*` resources or actions:
    - push to the four repositories
    - register task definitions for this stack's families
    - run the two one-off task definitions on the cluster
    - update the four services
    - `iam:PassRole` on this stack's task and execution roles only
- **Deploy sequence** (documented, not built), run from self-hosted runners in AWS:
  1. build and push images tagged with the git SHA
  2. run `db-bootstrap`, then `migrate`, waiting for each to exit 0
  3. update the four services to the new tag and wait for stability (the circuit breaker rolls back failures)

## 9. Observability on AWS

- **Metrics:** the existing custom metrics (`shortener.redirects`, `shortener.processor.*`, `shortener.queue.depth`, …) go to CloudWatch through EMF in the `Shortener` namespace. HTTP server metrics keep the bounded attribute Views from the shared library.
- **Traces:** X-Ray. It accepts W3C-format trace IDs (since October 2023) and the ADOT exporter converts them, so no X-Ray ID generator is needed.
  - The admin error page's 32-hex reference corresponds to X-Ray id `1-<first 8 hex>-<remaining 24 hex>`; `terraform/README.md` documents this.
  - Span links (processor batch → producers) are preserved as X-Ray links.
- **Logs:** JSON lines on stdout → CloudWatch Logs (`awslogs`). Logs Insights queries can filter on `trace_id`. OTLP log export is not used on AWS.
- **Dashboards:** follow-up work (T2).

**Alarms (`alarms`)**
- An SNS topic encrypted with KMS. Subscriptions are left to the platform or on-call setup; the topic ARN is an output.

| Alarm | Condition | Missing data |
|---|---|---|
| DLQ not empty | `click-events-dlq` `ApproximateNumberOfMessagesVisible` > 0 for 5 min | not breaching |
| Click backlog stale | `click-events` `ApproximateAgeOfOldestMessage` > 300 s for 5 min | not breaching |
| API/redirect errors | ALB target 5xx ÷ requests > 2% for 5 min, only when requests ≥ 50 (metric math) | not breaching |
| Unhealthy targets | any target group `UnHealthyHostCount` > 0 for 5 min | not breaching |
| RDS pressure | `CPUUtilization` > 80% for 15 min, or `FreeStorageSpace` < 2 GiB | breaching |
| Processor down | Container Insights `RunningTaskCount` < 1 for the processor service | breaching |

## 10. Verification

**`terraform test`** (mock `aws` and `random` providers, no credentials). Each module has a test file, and each environment root has one that plans the full stack from `terraform.tfvars.example`. Required assertions:
- SQS redrive `maxReceiveCount = 5`.
- prod RDS: `multi_az`, `deletion_protection`, `rds.force_ssl = 1`. staging: not Multi-AZ.
- Secrets use only `secret_string_wo`; no `secret_string` and no non-ephemeral `random_password`.
- Deploy role: no `*` in `Resource` or `Action`; trust `sub` is pinned to the repository and environment.
- The `/admin/*` rule on `auth.` matches `admin_cidrs`.
- RDS ingress only from security groups.
- Processor: scaling bounds 1–4, `stopTimeout = 60`.
- Every service task definition has the ADOT sidecar, and the app container depends on it.
- Each execution role's secret access is limited to its own secrets.

**Static checks**
- `tflint` with the AWS ruleset plugin (pinned).
- `trivy config` fails on HIGH or CRITICAL findings. Accepted findings get an inline ignore with a reason (for example, the public ALB ingress on 443).

**Local:** `make tf-check` runs `terraform fmt -check -recursive`, then per root `init -backend=false` and `validate`, then `tflint`, `trivy config`, and `terraform test`. It prints an install hint if `terraform`, `tflint` or `trivy` is missing.

**CI:** a new `terraform` job in `.github/workflows/ci.yml` installs Terraform from `.terraform-version` (`hashicorp/setup-terraform`) plus pinned `tflint` and `trivy`, then runs `make tf-check`. No AWS credentials; never `apply`.

## 11. Documentation Changes

- **`terraform/README.md`:**
  - a Mermaid architecture diagram
  - platform inputs
  - the deploy sequence
  - how to run the checks
  - the X-Ray trace-id mapping
  - what is deliberately not built
  - a rough monthly cost per environment
- **Parent spec:**
  - §11 points to this document.
  - The decisions table gains short entries for T1, T2, T6 and T7.
  - The §12 items resolved or touched here (ECS `stopTimeout`; Grafana access control becomes "not applicable on AWS: CloudWatch-only") are updated.
  - The incoming-`traceparent`-at-the-edge item is recorded as future work (an ALB can't strip headers; needs CloudFront or WAF).
- **`README.md`:** tool install lines (`tfenv`, `tflint`, `trivy`) in the user-maintained "Prerequisites macOS" section, plus `make tf-check` under Development.

## 12. Future Work

- CloudWatch dashboards (by hand, AI-generated, or `aws_cloudwatch_dashboard`).
- Keycloak realm via the Keycloak Terraform provider (parent §12 item 14).
- CloudFront + WAF in front of the ALB: edge caching (parent §12 item 1), stripping or ignoring incoming `traceparent` at the edge, rate limiting.
- The deploy workflow for the self-hosted runners.
- Optional switch to Amazon Managed Prometheus + Managed Grafana if the PromQL dashboard should carry over.
