# Terraform for AWS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Plan-ready, offline-tested Terraform in `terraform/` that would deploy the shortener to AWS (staging and prod), plugging into an existing VPC, ECS cluster and Route 53 zone. It is never applied.

**Architecture:**
- **Modules:** reusable modules under `terraform/modules/` (`queue`, `secrets`, `data`, `ecr`, `edge`, `service`, `task`, `alarms`, `ci-deploy-role`), composed by one `stack` module.
- **Environment roots:** two thin roots, `terraform/envs/staging` and `terraform/envs/prod`, call `stack` with their sizes.
- **Proving correctness:** `terraform test` with a mocked `aws` provider (the real `random` provider, which needs no credentials) plus `fmt`/`validate`/`tflint`/`trivy`. These run locally via `make tf-check` and in a CI job.
- **Database bootstrap:** a shell script run by a one-off ECS task, integration-tested with testcontainers Postgres.

**Tech Stack:**
- Terraform 1.16.5 (via tfenv)
- providers: `hashicorp/aws` 6.67.x, `hashicorp/random` 3.9.x
- tflint 0.64.0 with `tflint-ruleset-aws` 0.49.0
- trivy 0.75.0
- ADOT collector v0.50.0
- GitHub Actions `hashicorp/setup-terraform@v4`, `terraform-linters/setup-tflint@v6`, `aquasecurity/setup-trivy@v0.3.1`

**Spec:** `docs/superpowers/specs/2026-10-02-terraform-aws-design.md` (decisions T1–T9). Parent: `docs/superpowers/specs/2026-10-01-url-shortener-design.md` §11.

**Batches** (batched subagent-driven execution): (Tasks 1–3), (4–6), (7–9), (10–12).

## Global Constraints

**Never touch real AWS.**
- **Never run** `terraform apply`, `terraform plan` against real AWS, or anything that needs AWS credentials. Every Terraform command is one of `fmt`, `init -backend=false`, `validate` or `test` (with mocks).
- **No secret values** in Terraform state, code, tfvars or commits:
  - Passwords come from `ephemeral "random_password"` blocks, written only via `secret_string_wo` + `secret_string_wo_version`.
  - The RDS master password uses `manage_master_user_password = true`.
  - No `random_password` *resource*, and no `secret_string` argument, anywhere.

**Versions and layout**
- **Version pins:**
  - `.terraform-version` contains `1.16.5`.
  - Every root and module sets `required_version = "~> 1.16"`.
  - `hashicorp/aws ~> 6.67`, `hashicorp/random ~> 3.9` and `hashicorp/archive ~> 2.8` (archive zips the canary script).
- **Modules never configure providers;** only the environment roots do (`provider "aws"` with `default_tags`).
- **Tests:**
  - Each module has `tests/*.tftest.hcl` beside it. Tests use `mock_provider "aws"` and the real `random` provider, and `command = apply`, so that mocked computed values are known.
  - Mock fixtures (realistic ARNs, ACM validation options, the RDS master secret) are given with `mock_resource`/`mock_data` `defaults` in each test file that needs them.
- **IAM:**
  - Policies are built with `jsonencode()` locals, never `aws_iam_policy_document` data sources, so tests can decode and assert on them.
  - `Resource = "*"` is allowed **only** for actions with no resource-level permissions:
    - `xray:PutTraceSegments`, `xray:PutTelemetryRecords`, `xray:GetSamplingRules`, `xray:GetSamplingTargets` (task roles)
    - `ecr:GetAuthorizationToken`, `ecs:RegisterTaskDefinition`, `ecs:DescribeTaskDefinition` (deploy role)
    - `s3:ListAllMyBuckets`, and `cloudwatch:PutMetricData` with a `cloudwatch:namespace = CloudWatchSynthetics` condition (canary role; Synthetics requires both)
    
    No `Action` ever contains `*`.

**Naming and tags**
- Resource names start with `"${var.name_prefix}"`; `name_prefix = "shortener-${env}"`.
- Tags: providers set `default_tags = { Project = "shortener", Environment = <env>, ManagedBy = "terraform" }`. Modules also accept `tags` (map(string), default `{}`) and merge it onto taggable resources.

**Gates and commits**
- **Run before every commit:**
  - `make tf-check` for Terraform changes
  - `uv run ruff format . && make check` for Python or test changes
  
  Chain them with `&&`, never pipe them. Never commit on a red gate.
- **Commit trailer:** the implementing model's own name, e.g. `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

**The user's files**
- The user maintains `README.md` "Prerequisites macOS" and the Makefile `.venv` target. Edit those files surgically, never reformat them, and stage files by name.
- The shell's `git` wrapper summarizes diffs, so use `/usr/bin/git` when the output matters.

**The local stack:** never run `make down`.

## Review Focus

1. **Secrets can't reach state.** If a contributor adds `secret_string = jsonencode(...)` or a `random_password` resource, a test must fail. Test: Task 2 `run "no_secret_values_in_state"` asserts every `aws_secretsmanager_secret_version` has `secret_string == null` and a `secret_string_wo_version`. Task 12's `make tf-check` adds a grep guard.
2. **Bootstrap reruns and RDS's non-superuser master.** Running the bootstrap twice must succeed, and it must work when the master is not a superuser (on RDS, `rds_superuser` can't `CREATE DATABASE … OWNER x` unless it is a member of `x`). Test: Task 7 `tests/test_db_bootstrap.py::test_bootstrap_is_idempotent_with_non_superuser_master`.
3. **The Keycloak admin console when the allowlist is empty.** With `admin_cidrs = []`, `/admin/*` must be denied, not left open and not a plan error. Test: Task 6 `run "restricted_paths_with_empty_allowlist_only_deny"`.
4. **Deploy role over-reach.** Any `*` in `Action`, or `Resource="*"` on an action outside the allowed list, must fail. So must a trust policy without the `environment:` `sub` pin. Test: Task 9 `run "least_privilege"`.
5. **Processor scaling and shutdown.** Scale-in must never cut a batch short (`stopTimeout` 60 ≥ the 25 s grace plus the flush), and scaling must stay within 1–4. Test: Task 10 `run "prod_sizes"` asserts the processor task definition's `stopTimeout` and the scaling target's min and max.

---

## File Structure

```
.terraform-version                       1.16.5
.tflint.hcl                              tflint config (terraform recommended preset + aws ruleset 0.49.0)
trivy.yaml                               trivy config (severity HIGH,CRITICAL; exit-code 1)
Makefile                                 + tf-check target (surgical edit)
.gitignore                               + terraform ignores
.github/workflows/ci.yml                 + terraform job
terraform/
  README.md
  scripts/db-bootstrap.sh                idempotent roles + databases (psql)
  modules/<name>/{versions.tf,variables.tf,main.tf,outputs.tf,tests/<name>.tftest.hcl}
    queue/ secrets/ data/ ecr/ edge/ service/ task/ alarms/ ci-deploy-role/ stack/
  envs/{staging,prod}/{versions.tf,providers.tf,backend.tf,variables.tf,main.tf,outputs.tf,
                       terraform.tfvars.example,.terraform.lock.hcl,tests/<env>.tftest.hcl}
infra/keycloak/Dockerfile                optimized Keycloak with realm baked in
terraform/testing/aws/aws.tfmock.hcl     shared mock-provider fixtures for the stack and env tests
tests/test_db_bootstrap.py               integration test for the bootstrap script
tests/test_keycloak_image.py             static checks for the Keycloak Dockerfile
docs: terraform/README.md, parent spec §2/§11/§12, README.md
```

Every module and root uses this same `versions.tf` (declaring `random` where a module doesn't use it is harmless):

```hcl
terraform {
  required_version = "~> 1.16"
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 6.67" }
    random  = { source = "hashicorp/random", version = "~> 3.9" }
    archive = { source = "hashicorp/archive", version = "~> 2.8" }
  }
}
```

The standard test header (every `*.tftest.hcl` starts with this; tasks add fixtures to it):

```hcl
mock_provider "aws" {
  mock_data "aws_region" {
    defaults = { region = "us-east-1", name = "us-east-1" }
  }
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
}
```

---

### Task 1: Tooling, `make tf-check`, CI job, and the `queue` module

**Files:**
- Create: `.terraform-version`, `.tflint.hcl`, `trivy.yaml`, `terraform/modules/queue/{versions.tf,variables.tf,main.tf,outputs.tf}`, `terraform/modules/queue/tests/queue.tftest.hcl`
- Modify: `Makefile` (add `tf-check`), `.gitignore`, `.github/workflows/ci.yml` (add `terraform` job)

**Interfaces:**
- Produces:
  - **`make tf-check`:** fmt-check, per-root `init -backend=false` + `validate` + `test`, tflint, trivy. Roots are discovered as `terraform/modules/*` plus `terraform/envs/*`, so later tasks only add directories.
  - **The `queue` module:**
    - inputs: `name_prefix` (string), `max_receive_count` (number, default 5), `visibility_timeout_seconds` (number, default 30), `tags`
    - outputs: `queue_name`, `queue_arn`, `queue_url`, `dlq_name`, `dlq_arn`

- [ ] **Step 1: Install the tools (user-approved design T9)**

```bash
brew install tfenv tflint trivy
echo "1.16.5" > .terraform-version
tfenv install        # reads .terraform-version
terraform version    # Terraform v1.16.5
tflint --version     # 0.64.x
trivy --version      # 0.75.x
```
If Homebrew installs different tflint/trivy minors, that's fine locally. CI pins the exact versions.

- [ ] **Step 2: Write the config files**

`.tflint.hcl`:
```hcl
config {
  call_module_type = "local"
}

plugin "terraform" {
  enabled = true
  preset  = "recommended"
}

plugin "aws" {
  enabled = true
  version = "0.49.0"
  source  = "github.com/terraform-linters/tflint-ruleset-aws"
}
```

`trivy.yaml`:
```yaml
severity:
  - HIGH
  - CRITICAL
exit-code: 1
scan:
  skip-dirs:
    - "**/.terraform"
```

Append to `.gitignore`:
```
# Terraform
**/.terraform/
*.tfstate
*.tfstate.*
*.tfplan
crash.log
terraform/modules/**/.terraform.lock.hcl
```
(The environment roots' `.terraform.lock.hcl` files are committed; the modules' are not.)

- [ ] **Step 3: Add `tf-check` to the Makefile** (surgical: append the target, and add `tf-check` to the `.PHONY` line)

```make
TF_ROOTS = $(wildcard terraform/modules/*) $(wildcard terraform/envs/*)

# Offline Terraform checks: never touches AWS (spec T1). Install: brew install tfenv tflint trivy && tfenv install
tf-check:
	@for t in terraform tflint trivy; do command -v $$t >/dev/null || { echo "missing $$t: brew install tfenv tflint trivy && tfenv install"; exit 1; }; done
	terraform fmt -check -recursive terraform
	@for d in $(TF_ROOTS); do \
		echo "== $$d"; \
		terraform -chdir=$$d init -backend=false -input=false -no-color >/dev/null && \
		terraform -chdir=$$d validate -no-color && \
		terraform -chdir=$$d test -no-color || exit 1; \
	done
	tflint --init --config=$(CURDIR)/.tflint.hcl
	cd terraform && tflint --recursive --config=$(CURDIR)/.tflint.hcl
	trivy config --config trivy.yaml terraform
```

- [ ] **Step 4: Write the failing queue test**

`terraform/modules/queue/tests/queue.tftest.hcl`:
```hcl
mock_provider "aws" {
  mock_resource "aws_sqs_queue" {
    defaults = { arn = "arn:aws:sqs:us-east-1:123456789012:mock" }
  }
}

variables {
  name_prefix = "shortener-test"
}

run "names_and_redrive" {
  command = apply

  assert {
    condition     = aws_sqs_queue.main.name == "shortener-test-click-events" && aws_sqs_queue.dlq.name == "shortener-test-click-events-dlq"
    error_message = "queue names must be <prefix>-click-events and <prefix>-click-events-dlq (the services read CLICK_EVENTS_QUEUE_NAME / CLICK_EVENTS_DLQ_NAME)"
  }
  assert {
    condition     = jsondecode(aws_sqs_queue.main.redrive_policy).maxReceiveCount == 5
    error_message = "maxReceiveCount must be 5 (parent spec §5)"
  }
  assert {
    condition     = aws_sqs_queue.main.sqs_managed_sse_enabled && aws_sqs_queue.dlq.sqs_managed_sse_enabled
    error_message = "both queues must be encrypted"
  }
  assert {
    condition     = aws_sqs_queue.main.visibility_timeout_seconds == 30
    error_message = "visibility timeout must match the processor's 30 s assumption (parent spec §5)"
  }
  assert {
    condition     = aws_sqs_queue.dlq.message_retention_seconds == 1209600
    error_message = "DLQ keeps messages for the 14-day maximum"
  }
}
```

- [ ] **Step 5: Run it and watch it fail**

Run: `terraform -chdir=terraform/modules/queue init -backend=false && terraform -chdir=terraform/modules/queue test`
Expected: FAIL. No configuration exists yet (`Reference to undeclared resource`), or init errors because there are no `.tf` files.

- [ ] **Step 6: Implement the module**

`terraform/modules/queue/versions.tf`: the standard `versions.tf` (File Structure).

`terraform/modules/queue/variables.tf`:
```hcl
variable "name_prefix" {
  description = "Prefix for resource names, e.g. shortener-prod."
  type        = string
}

variable "max_receive_count" {
  description = "Receives before a message moves to the DLQ."
  type        = number
  default     = 5
}

variable "visibility_timeout_seconds" {
  description = "Must exceed the processor's batch processing time."
  type        = number
  default     = 30
}

variable "tags" {
  description = "Extra tags for every resource."
  type        = map(string)
  default     = {}
}
```

`terraform/modules/queue/main.tf`:
```hcl
resource "aws_sqs_queue" "dlq" {
  name                      = "${var.name_prefix}-click-events-dlq"
  message_retention_seconds = 1209600 # 14 days: time to inspect and redrive
  sqs_managed_sse_enabled   = true
  tags                      = var.tags
}

resource "aws_sqs_queue" "main" {
  name                       = "${var.name_prefix}-click-events"
  visibility_timeout_seconds = var.visibility_timeout_seconds
  message_retention_seconds  = 345600 # 4 days
  receive_wait_time_seconds  = 20
  sqs_managed_sse_enabled    = true
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = var.max_receive_count
  })
  tags = var.tags
}

resource "aws_sqs_queue_redrive_allow_policy" "dlq" {
  queue_url = aws_sqs_queue.dlq.id
  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.main.arn]
  })
}
```

`terraform/modules/queue/outputs.tf`:
```hcl
output "queue_name" {
  description = "Main queue name."
  value       = aws_sqs_queue.main.name
}
output "queue_arn" {
  description = "Main queue ARN."
  value       = aws_sqs_queue.main.arn
}
output "queue_url" {
  description = "Main queue URL."
  value       = aws_sqs_queue.main.id
}
output "dlq_name" {
  description = "Dead-letter queue name."
  value       = aws_sqs_queue.dlq.name
}
output "dlq_arn" {
  description = "Dead-letter queue ARN."
  value       = aws_sqs_queue.dlq.arn
}
```

- [ ] **Step 7: Run the module test and the full gate**

Run: `terraform -chdir=terraform/modules/queue test`
Expected: `1 passed, 0 failed`.

Run: `make tf-check`
Expected: exit 0 after fmt, validate, test, tflint and trivy. If trivy reports a HIGH or CRITICAL finding on the queues, fix it, or add `#trivy:ignore:<ID>` on the line above the resource with a one-line reason. Never lower the severity threshold.

- [ ] **Step 8: Add the CI job** (append under `jobs:` in `.github/workflows/ci.yml`)

```yaml
  terraform:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
      - id: tfversion
        run: echo "version=$(cat .terraform-version)" >> "$GITHUB_OUTPUT"
      - uses: hashicorp/setup-terraform@v4
        with:
          terraform_version: ${{ steps.tfversion.outputs.version }}
          terraform_wrapper: false
      - uses: terraform-linters/setup-tflint@v6
        with:
          tflint_version: v0.64.0
      - uses: aquasecurity/setup-trivy@v0.3.1
        with:
          version: v0.75.0
      - run: make tf-check
        env:
          GITHUB_TOKEN: ${{ github.token }} # tflint --init downloads the aws ruleset
```

Run: `uvx --from actionlint-py actionlint .github/workflows/ci.yml`
Expected: no output.

- [ ] **Step 9: Commit**

```bash
make tf-check && /usr/bin/git add .terraform-version .tflint.hcl trivy.yaml .gitignore Makefile .github/workflows/ci.yml terraform/modules/queue && /usr/bin/git commit -m "feat(terraform): tooling (tfenv, tflint, trivy, make tf-check, CI) and the queue module

Co-Authored-By: <implementing model> <noreply@anthropic.com>"
```

---

### Task 2: `secrets` module (write-only values, never in state)

**Files:**
- Create: `terraform/modules/secrets/{versions.tf,variables.tf,main.tf,outputs.tf}`, `terraform/modules/secrets/tests/secrets.tftest.hcl`

**Interfaces:**
- Produces: the `secrets` module.
  - **Inputs:**
    - `name_prefix`
    - `db_address` (string, the RDS host)
    - `db_port` (number, default 5432)
    - `secret_version` (number, default 1; bump it to rotate all generated values)
    - `tags`
  - **Outputs:**
    - `db_secret_arns`: `map(string)` keyed by role. Keys `migrator`, `api_user`, `processor_user`, `admin_user`, `keycloak`. Each secret's JSON has keys `username`, `password`, `url`. For `keycloak` the `url` is JDBC (`jdbc:postgresql://…/keycloak?sslmode=require`); for the others it is `postgresql+psycopg://<role>:<pw>@<host>:<port>/shortener?sslmode=require`.
    - `app_secret_arns`: `map(string)` with keys `admin_client_secret`, `admin_cookie_secret`, `keycloak_admin_password`. Each secret's JSON is `{"value": "<generated>"}`.
    - `all_secret_arns`: `list(string)`.

- [ ] **Step 1: Write the failing test**

`terraform/modules/secrets/tests/secrets.tftest.hcl`:
```hcl
mock_provider "aws" {
  mock_resource "aws_secretsmanager_secret" {
    defaults = { arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:mock" }
  }
}

variables {
  name_prefix = "shortener-test"
  db_address  = "db.internal.example"
}

run "all_secrets_exist" {
  command = apply

  assert {
    condition     = toset(keys(output.db_secret_arns)) == toset(["migrator", "api_user", "processor_user", "admin_user", "keycloak"])
    error_message = "one database secret per bootstrap role"
  }
  assert {
    condition     = toset(keys(output.app_secret_arns)) == toset(["admin_client_secret", "admin_cookie_secret", "keycloak_admin_password"])
    error_message = "app secrets: admin client secret, cookie secret, keycloak admin password"
  }
  assert {
    condition     = length(output.all_secret_arns) == 8
    error_message = "all_secret_arns lists every secret"
  }
}

run "no_secret_values_in_state" {
  command = apply

  assert {
    condition = alltrue([
      for v in concat(values(aws_secretsmanager_secret_version.db), values(aws_secretsmanager_secret_version.app)) :
      v.secret_string == null && v.secret_string_wo_version == 1
    ])
    error_message = "secret versions must use secret_string_wo only (spec T6): no secret_string, ever"
  }
}

run "rotation_bumps_wo_version" {
  command = apply
  variables {
    secret_version = 2
  }

  assert {
    condition     = alltrue([for v in values(aws_secretsmanager_secret_version.db) : v.secret_string_wo_version == 2])
    error_message = "secret_version must drive secret_string_wo_version so values can be rotated"
  }
}
```

- [ ] **Step 2: Run and watch it fail**

Run: `terraform -chdir=terraform/modules/secrets init -backend=false && terraform -chdir=terraform/modules/secrets test`
Expected: FAIL (no configuration).

- [ ] **Step 3: Implement**

`variables.tf`:
```hcl
variable "name_prefix" {
  description = "Prefix for secret names."
  type        = string
}
variable "db_address" {
  description = "RDS endpoint host (no port)."
  type        = string
}
variable "db_port" {
  description = "RDS port."
  type        = number
  default     = 5432
}
variable "secret_version" {
  description = "Bump to regenerate every value (written via secret_string_wo)."
  type        = number
  default     = 1
}
variable "tags" {
  description = "Extra tags."
  type        = map(string)
  default     = {}
}
```

`main.tf`:
```hcl
locals {
  # Mirrors infra/postgres/bootstrap.sql: role => database it connects to.
  db_roles = {
    migrator       = "shortener"
    api_user       = "shortener"
    processor_user = "shortener"
    admin_user     = "shortener"
    keycloak       = "keycloak"
  }
  app_secrets = toset(["admin_client_secret", "admin_cookie_secret", "keycloak_admin_password"])
}

# Ephemeral: generated at apply, never written to state or plan (spec T6).
ephemeral "random_password" "db" {
  for_each = local.db_roles
  length   = 32
  special  = false # embedded in DSNs; alphanumerics avoid URL-encoding
}

ephemeral "random_password" "app" {
  for_each = local.app_secrets
  length   = 48
  special  = false
}

resource "aws_secretsmanager_secret" "db" {
  for_each                = local.db_roles
  name                    = "${var.name_prefix}/db/${each.key}"
  recovery_window_in_days = 7
  tags                    = var.tags
}

resource "aws_secretsmanager_secret_version" "db" {
  for_each  = local.db_roles
  secret_id = aws_secretsmanager_secret.db[each.key].id
  secret_string_wo = jsonencode({
    username = each.key
    password = ephemeral.random_password.db[each.key].result
    url = (
      each.key == "keycloak"
      ? "jdbc:postgresql://${var.db_address}:${var.db_port}/${each.value}?sslmode=require"
      : "postgresql+psycopg://${each.key}:${ephemeral.random_password.db[each.key].result}@${var.db_address}:${var.db_port}/${each.value}?sslmode=require"
    )
  })
  secret_string_wo_version = var.secret_version
}

resource "aws_secretsmanager_secret" "app" {
  for_each                = local.app_secrets
  name                    = "${var.name_prefix}/app/${each.key}"
  recovery_window_in_days = 7
  tags                    = var.tags
}

resource "aws_secretsmanager_secret_version" "app" {
  for_each                 = local.app_secrets
  secret_id                = aws_secretsmanager_secret.app[each.key].id
  secret_string_wo         = jsonencode({ value = ephemeral.random_password.app[each.key].result })
  secret_string_wo_version = var.secret_version
}
```

`outputs.tf`:
```hcl
output "db_secret_arns" {
  description = "Database role => secret ARN (JSON keys: username, password, url)."
  value       = { for k, s in aws_secretsmanager_secret.db : k => s.arn }
}
output "app_secret_arns" {
  description = "App secret name => secret ARN (JSON key: value)."
  value       = { for k, s in aws_secretsmanager_secret.app : k => s.arn }
}
output "all_secret_arns" {
  description = "Every secret ARN."
  value       = concat([for s in aws_secretsmanager_secret.db : s.arn], [for s in aws_secretsmanager_secret.app : s.arn])
}
```

If `terraform validate` rejects an ephemeral value inside `jsonencode` in a write-only argument, check the Terraform 1.16 docs for ephemeral values in write-only arguments. Ephemeral values are allowed in write-only arguments, and expressions over them stay ephemeral. Fix the expression rather than dropping write-only, and record the fix in your report.

- [ ] **Step 4: Run the tests**

Run: `terraform -chdir=terraform/modules/secrets test`
Expected: `3 passed, 0 failed`.

- [ ] **Step 5: Gate and commit**

```bash
make tf-check && /usr/bin/git add terraform/modules/secrets && /usr/bin/git commit -m "feat(terraform): secrets module with write-only generated values (never in state)

Co-Authored-By: <implementing model> <noreply@anthropic.com>"
```

---

### Task 3: `data` module (RDS PostgreSQL 16)

**Files:**
- Create: `terraform/modules/data/{versions.tf,variables.tf,main.tf,outputs.tf}`, `terraform/modules/data/tests/data.tftest.hcl`

**Interfaces:**
- Produces: the `data` module.
  - **Inputs:**
    - `name_prefix`, `vpc_id`, `subnet_ids` (list(string))
    - `instance_class` (string), `multi_az` (bool), `backup_retention_days` (number), `deletion_protection` (bool)
    - `allocated_storage` (number, default 20), `max_allocated_storage` (number, default 100), `engine_version` (string, default "16")
    - `client_security_group_ids` (`map(string)`: a static name => SG id; keys must be known at plan)
    - `tags`
  - **Outputs:** `address`, `port`, `master_secret_arn`, `security_group_id`, `instance_identifier`.

- [ ] **Step 1: Write the failing test**

`terraform/modules/data/tests/data.tftest.hcl`:
```hcl
mock_provider "aws" {
  mock_resource "aws_db_instance" {
    defaults = {
      arn     = "arn:aws:rds:us-east-1:123456789012:db:mock"
      address = "mock.abc123.us-east-1.rds.amazonaws.com"
      master_user_secret = [{
        secret_arn    = "arn:aws:secretsmanager:us-east-1:123456789012:secret:rds!db-mock"
        kms_key_id    = "alias/aws/secretsmanager"
        secret_status = "active"
      }]
    }
  }
}

variables {
  name_prefix               = "shortener-test"
  vpc_id                    = "vpc-0123456789abcdef0"
  subnet_ids                = ["subnet-aaaa", "subnet-bbbb"]
  instance_class            = "db.t4g.micro"
  multi_az                  = false
  backup_retention_days     = 1
  deletion_protection       = false
  client_security_group_ids = { api = "sg-api", migrate = "sg-migrate" }
}

run "staging_shape" {
  command = apply

  assert {
    condition     = aws_db_instance.this.engine == "postgres" && startswith(aws_db_instance.this.engine_version, "16")
    error_message = "PostgreSQL 16 (matches local postgres:16.10)"
  }
  assert {
    condition     = aws_db_instance.this.manage_master_user_password && aws_db_instance.this.password == null
    error_message = "RDS manages the master password; Terraform never sets it (spec T6)"
  }
  assert {
    condition     = aws_db_instance.this.storage_encrypted && !aws_db_instance.this.publicly_accessible && !aws_db_instance.this.multi_az
    error_message = "encrypted, private, single-AZ in staging"
  }
  assert {
    condition     = one([for p in aws_db_parameter_group.this.parameter : p.value if p.name == "rds.force_ssl"]) == "1"
    error_message = "rds.force_ssl must be 1"
  }
  assert {
    condition     = output.master_secret_arn == "arn:aws:secretsmanager:us-east-1:123456789012:secret:rds!db-mock"
    error_message = "master secret ARN comes from RDS-managed master_user_secret"
  }
}

run "ingress_only_from_security_groups" {
  command = apply

  assert {
    condition = alltrue([
      for r in values(aws_vpc_security_group_ingress_rule.clients) :
      r.cidr_ipv4 == null && r.cidr_ipv6 == null && r.from_port == 5432 && r.to_port == 5432
    ]) && length(aws_vpc_security_group_ingress_rule.clients) == 2
    error_message = "RDS ingress: 5432 only from the given security groups, never a CIDR"
  }
}

run "prod_shape" {
  command = apply
  variables {
    instance_class        = "db.t4g.small"
    multi_az              = true
    backup_retention_days = 7
    deletion_protection   = true
  }

  assert {
    condition     = aws_db_instance.this.multi_az && aws_db_instance.this.deletion_protection && !aws_db_instance.this.skip_final_snapshot
    error_message = "prod: multi-AZ, deletion protection, final snapshot"
  }
  assert {
    condition     = aws_db_instance.this.backup_retention_period == 7
    error_message = "prod keeps 7 days of backups"
  }
}
```

- [ ] **Step 2: Run and watch it fail**

Run: `terraform -chdir=terraform/modules/data init -backend=false && terraform -chdir=terraform/modules/data test`
Expected: FAIL (no configuration).

- [ ] **Step 3: Implement**

`variables.tf`: declare every input listed in Interfaces, each with a `description`, the types given there, and the defaults given there. The `tags` variable is the same as in the earlier modules.

`main.tf`:
```hcl
resource "aws_db_subnet_group" "this" {
  name       = "${var.name_prefix}-db"
  subnet_ids = var.subnet_ids
  tags       = var.tags
}

resource "aws_db_parameter_group" "this" {
  name   = "${var.name_prefix}-pg16"
  family = "postgres16"

  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
  tags = var.tags
}

resource "aws_security_group" "db" {
  name        = "${var.name_prefix}-db"
  description = "RDS: ingress only from the shortener tasks"
  vpc_id      = var.vpc_id
  tags        = var.tags
}

resource "aws_vpc_security_group_ingress_rule" "clients" {
  for_each                     = var.client_security_group_ids
  security_group_id            = aws_security_group.db.id
  referenced_security_group_id = each.value
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  description                  = "postgres from ${each.key}"
}

resource "aws_db_instance" "this" {
  identifier                          = "${var.name_prefix}-db"
  engine                              = "postgres"
  engine_version                      = var.engine_version
  instance_class                      = var.instance_class
  allocated_storage                   = var.allocated_storage
  max_allocated_storage               = var.max_allocated_storage
  storage_type                        = "gp3"
  storage_encrypted                   = true
  username                            = "shortener_admin"
  manage_master_user_password         = true
  db_subnet_group_name                = aws_db_subnet_group.this.name
  parameter_group_name                = aws_db_parameter_group.this.name
  vpc_security_group_ids              = [aws_security_group.db.id]
  publicly_accessible                 = false
  multi_az                            = var.multi_az
  backup_retention_period             = var.backup_retention_days
  deletion_protection                 = var.deletion_protection
  skip_final_snapshot                 = !var.deletion_protection
  final_snapshot_identifier           = var.deletion_protection ? "${var.name_prefix}-db-final" : null
  copy_tags_to_snapshot               = true
  auto_minor_version_upgrade          = true
  iam_database_authentication_enabled = true
  performance_insights_enabled        = true
  tags                                = var.tags
}
```

`outputs.tf`: `address` (`aws_db_instance.this.address`), `port` (`aws_db_instance.this.port`), `master_secret_arn` (`aws_db_instance.this.master_user_secret[0].secret_arn`), `security_group_id` (`aws_security_group.db.id`), `instance_identifier` (`aws_db_instance.this.identifier`), each with a description.

- [ ] **Step 4: Run the tests**

Run: `terraform -chdir=terraform/modules/data test`
Expected: `3 passed, 0 failed`.

- [ ] **Step 5: Gate and commit**

```bash
make tf-check && /usr/bin/git add terraform/modules/data && /usr/bin/git commit -m "feat(terraform): data module (RDS PostgreSQL 16, TLS forced, RDS-managed master secret)

Co-Authored-By: <implementing model> <noreply@anthropic.com>"
```
If trivy flags HIGH or CRITICAL items here, fix them, or ignore them inline with a reason.

---

### Task 4: `ecr` module

**Files:**
- Create: `terraform/modules/ecr/{versions.tf,variables.tf,main.tf,outputs.tf}`, `terraform/modules/ecr/tests/ecr.tftest.hcl`

**Interfaces:**
- Produces: the `ecr` module.
  - **Inputs:**
    - `name_prefix`
    - `repositories` (list(string), default `["api", "click-processor", "admin", "keycloak"]`)
    - `keep_images` (number, default 30)
    - `tags`
  - **Outputs:** `repository_urls` (`map(string)` keyed by short name), `repository_arns` (`map(string)`).

- [ ] **Step 1: Write the failing test**

`terraform/modules/ecr/tests/ecr.tftest.hcl`:
```hcl
mock_provider "aws" {
  mock_resource "aws_ecr_repository" {
    defaults = {
      arn            = "arn:aws:ecr:us-east-1:123456789012:repository/mock"
      repository_url = "123456789012.dkr.ecr.us-east-1.amazonaws.com/mock"
    }
  }
}

variables {
  name_prefix = "shortener-test"
}

run "repositories" {
  command = apply

  assert {
    condition     = toset(keys(output.repository_urls)) == toset(["api", "click-processor", "admin", "keycloak"])
    error_message = "one repository per image"
  }
  assert {
    condition     = alltrue([for r in values(aws_ecr_repository.this) : r.image_tag_mutability == "IMMUTABLE" && r.image_scanning_configuration[0].scan_on_push])
    error_message = "immutable tags and scan on push"
  }
  assert {
    condition     = aws_ecr_repository.this["api"].name == "shortener-test/api"
    error_message = "repository names are <prefix>/<image>"
  }
  assert {
    condition     = jsondecode(aws_ecr_lifecycle_policy.this["api"].policy).rules[0].selection.countNumber == 30
    error_message = "lifecycle keeps the last 30 images"
  }
}
```

- [ ] **Step 2: Run and watch it fail**

Run: `terraform -chdir=terraform/modules/ecr init -backend=false && terraform -chdir=terraform/modules/ecr test`
Expected: FAIL (no configuration).

- [ ] **Step 3: Implement**

`main.tf`:
```hcl
resource "aws_ecr_repository" "this" {
  for_each             = toset(var.repositories)
  name                 = "${var.name_prefix}/${each.key}"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false

  image_scanning_configuration {
    scan_on_push = true
  }
  encryption_configuration {
    encryption_type = "KMS"
  }
  tags = var.tags
}

resource "aws_ecr_lifecycle_policy" "this" {
  for_each   = aws_ecr_repository.this
  repository = each.value.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "keep the last ${var.keep_images} images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = var.keep_images
      }
      action = { type = "expire" }
    }]
  })
}
```
Write `variables.tf` from Interfaces, with descriptions. `outputs.tf`: `repository_urls = { for k, r in aws_ecr_repository.this : k => r.repository_url }` and `repository_arns` the same shape with `r.arn`.

- [ ] **Step 4: Run the tests**

Run: `terraform -chdir=terraform/modules/ecr test`
Expected: `1 passed, 0 failed`.

- [ ] **Step 5: Gate and commit**

```bash
make tf-check && /usr/bin/git add terraform/modules/ecr && /usr/bin/git commit -m "feat(terraform): ecr module (immutable tags, scan on push, KMS, lifecycle)

Co-Authored-By: <implementing model> <noreply@anthropic.com>"
```

---
### Task 5: `edge` module (ACM, ALB, listeners, DNS)

**Files:**
- Create: `terraform/modules/edge/{versions.tf,variables.tf,main.tf,outputs.tf}`, `terraform/modules/edge/tests/edge.tftest.hcl`

**Interfaces:**
- Produces: the `edge` module.
  - **Inputs:**
    - `name_prefix`
    - `domain` (string, e.g. `shortener.example.com`)
    - `route53_zone_id`, `vpc_id`, `public_subnet_ids` (list(string))
    - `deletion_protection` (bool)
    - `tags`
  - **Outputs:**
    - `hostnames`: `map(string)` with keys `api`, `admin`, `auth` → `go.<domain>`, `admin.<domain>`, `auth.<domain>`
    - `https_listener_arn`, `alb_security_group_id`, `alb_arn_suffix`, `alb_dns_name`, `certificate_arn`
- Services attach their own target groups and host rules to `https_listener_arn` (Task 6), including Keycloak's restricted `/admin/*` rules. The edge stays service-agnostic.

- [ ] **Step 1: Write the failing test**

`terraform/modules/edge/tests/edge.tftest.hcl`:
```hcl
mock_provider "aws" {
  mock_resource "aws_lb" {
    defaults = {
      arn        = "arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/mock/abc"
      arn_suffix = "app/mock/abc"
      dns_name   = "mock-123.us-east-1.elb.amazonaws.com"
      zone_id    = "Z35SXDOTRQ7X7K"
    }
  }
  mock_resource "aws_lb_listener" {
    defaults = { arn = "arn:aws:elasticloadbalancing:us-east-1:123456789012:listener/app/mock/abc/def" }
  }
  mock_resource "aws_acm_certificate" {
    defaults = {
      arn = "arn:aws:acm:us-east-1:123456789012:certificate/mock"
      domain_validation_options = [
        { domain_name = "go.example.test", resource_record_name = "_a.go.example.test.", resource_record_type = "CNAME", resource_record_value = "_x.acm-validations.aws." },
        { domain_name = "admin.example.test", resource_record_name = "_b.admin.example.test.", resource_record_type = "CNAME", resource_record_value = "_y.acm-validations.aws." },
        { domain_name = "auth.example.test", resource_record_name = "_c.auth.example.test.", resource_record_type = "CNAME", resource_record_value = "_z.acm-validations.aws." },
      ]
    }
  }
}

variables {
  name_prefix         = "shortener-test"
  domain              = "example.test"
  route53_zone_id     = "Z0123456789ABC"
  vpc_id              = "vpc-0123456789abcdef0"
  public_subnet_ids   = ["subnet-pub-a", "subnet-pub-b"]
  deletion_protection = false
}

run "certificate_and_dns" {
  command = apply

  assert {
    condition     = output.hostnames == { api = "go.example.test", admin = "admin.example.test", auth = "auth.example.test" }
    error_message = "hostnames: go., admin., auth."
  }
  assert {
    condition     = aws_acm_certificate.this.validation_method == "DNS" && toset(concat([aws_acm_certificate.this.domain_name], tolist(aws_acm_certificate.this.subject_alternative_names))) == toset(values(output.hostnames))
    error_message = "one DNS-validated certificate covering the three hostnames (spec §5)"
  }
  assert {
    condition     = length(aws_route53_record.validation) == 3 && length(aws_route53_record.alias) == 3
    error_message = "a validation record and an alias record per hostname"
  }
}

run "listeners" {
  command = apply

  assert {
    condition     = aws_lb_listener.http.default_action[0].type == "redirect" && aws_lb_listener.http.default_action[0].redirect[0].protocol == "HTTPS"
    error_message = "HTTP redirects to HTTPS"
  }
  assert {
    condition     = aws_lb_listener.https.ssl_policy == "ELBSecurityPolicy-TLS13-1-2-2021-06" && aws_lb_listener.https.default_action[0].fixed_response[0].status_code == "404"
    error_message = "HTTPS uses the TLS 1.3/1.2 policy and a default fixed 404"
  }
  assert {
    condition     = aws_lb.this.drop_invalid_header_fields && !aws_lb.this.internal
    error_message = "public ALB that drops invalid headers"
  }
}
```

- [ ] **Step 2: Run and watch it fail**

Run: `terraform -chdir=terraform/modules/edge init -backend=false && terraform -chdir=terraform/modules/edge test`
Expected: FAIL (no configuration).

- [ ] **Step 3: Implement**

`main.tf`:
```hcl
locals {
  hostnames = {
    api   = "go.${var.domain}"
    admin = "admin.${var.domain}"
    auth  = "auth.${var.domain}"
  }
}

resource "aws_acm_certificate" "this" {
  domain_name               = local.hostnames.api
  subject_alternative_names = [local.hostnames.admin, local.hostnames.auth]
  validation_method         = "DNS"
  tags                      = var.tags

  lifecycle {
    create_before_destroy = true
  }
}

# Keyed by our own hostnames (known at plan), not by domain_validation_options (unknown until apply).
resource "aws_route53_record" "validation" {
  for_each        = toset(values(local.hostnames))
  zone_id         = var.route53_zone_id
  allow_overwrite = true
  ttl             = 300
  name            = one([for o in aws_acm_certificate.this.domain_validation_options : o.resource_record_name if o.domain_name == each.key])
  type            = one([for o in aws_acm_certificate.this.domain_validation_options : o.resource_record_type if o.domain_name == each.key])
  records         = [one([for o in aws_acm_certificate.this.domain_validation_options : o.resource_record_value if o.domain_name == each.key])]
}

resource "aws_acm_certificate_validation" "this" {
  certificate_arn         = aws_acm_certificate.this.arn
  validation_record_fqdns = [for r in aws_route53_record.validation : r.fqdn]
}

resource "aws_security_group" "alb" {
  name        = "${var.name_prefix}-alb"
  description = "Public ALB for the shortener"
  vpc_id      = var.vpc_id
  tags        = var.tags
}

#trivy:ignore:AVD-AWS-0107 public redirect service: the ALB must accept the internet on 80/443
resource "aws_vpc_security_group_ingress_rule" "alb" {
  for_each          = toset(["80", "443"])
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = tonumber(each.key)
  to_port           = tonumber(each.key)
  description       = "internet to ALB ${each.key}"
}

#trivy:ignore:AVD-AWS-0104 targets are in private subnets behind their own security groups (ingress only from this ALB)
resource "aws_vpc_security_group_egress_rule" "alb_to_targets" {
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 0
  to_port           = 65535
  description       = "ALB to targets (no VPC lookup: platform IDs are passed in, spec T4)"
}

#trivy:ignore:AVD-AWS-0053 internet-facing by design (public short links)
resource "aws_lb" "this" {
  name                       = substr("${var.name_prefix}-alb", 0, 32)
  load_balancer_type         = "application"
  internal                   = false
  subnets                    = var.public_subnet_ids
  security_groups            = [aws_security_group.alb.id]
  drop_invalid_header_fields = true
  deletion_protection        = var.deletion_protection
  tags                       = var.tags
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"
    redirect {
      protocol    = "HTTPS"
      port        = "443"
      status_code = "HTTP_301"
    }
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.this.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate_validation.this.certificate_arn

  default_action {
    type = "fixed-response"
    fixed_response {
      content_type = "text/plain"
      message_body = "Not found"
      status_code  = "404"
    }
  }
}

resource "aws_route53_record" "alias" {
  for_each = local.hostnames
  zone_id  = var.route53_zone_id
  name     = each.value
  type     = "A"

  alias {
    name                   = aws_lb.this.dns_name
    zone_id                = aws_lb.this.zone_id
    evaluate_target_health = true
  }
}
```
Write `variables.tf` from Interfaces, with descriptions. `outputs.tf`:
- `hostnames = local.hostnames`
- `https_listener_arn = aws_lb_listener.https.arn`
- `alb_security_group_id = aws_security_group.alb.id`
- `alb_arn_suffix = aws_lb.this.arn_suffix`
- `alb_dns_name = aws_lb.this.dns_name`
- `certificate_arn = aws_acm_certificate_validation.this.certificate_arn`

- [ ] **Step 4: Run the tests**

Run: `terraform -chdir=terraform/modules/edge test`
Expected: `2 passed, 0 failed`.

- [ ] **Step 5: Gate and commit**

```bash
make tf-check && /usr/bin/git add terraform/modules/edge && /usr/bin/git commit -m "feat(terraform): edge module (ACM DNS validation, ALB, HTTPS listener, Route 53 aliases)

Co-Authored-By: <implementing model> <noreply@anthropic.com>"
```

---

### Task 6: `service` module (Fargate service + ADOT sidecar)

**Files:**
- Create: `terraform/modules/service/{versions.tf,variables.tf,main.tf,iam.tf,alb.tf,autoscaling.tf,outputs.tf}`, `terraform/modules/service/tests/service.tftest.hcl`

**Interfaces:**
- Consumes: `edge.https_listener_arn` and `edge.alb_security_group_id` (Task 5); secret ARNs and JSON keys from `secrets`/`data` (Tasks 2–3).
- Produces: the `service` module.
  - **Inputs:**
    - `name_prefix`, `name` (short name, also the app container name), `environment_name` (`staging` | `prod`)
    - `ecs_cluster_arn`, `vpc_id`, `subnet_ids`
    - `image` (full URI with tag)
    - `cpu` (number), `memory` (number), `desired_count` (number), `cpu_architecture` (string, default `"X86_64"`)
    - `container_port` (number, nullable, default null), `command` (list(string), nullable, default null)
    - `env_vars` (map(string), default `{}`)
    - `secrets` (`map(object({ arn = string, key = string }))`, default `{}`; env var name => secret ARN + JSON key)
    - `health_check_command` (list(string), nullable, default null; container health check)
    - `alb` (nullable, default null): `object({ listener_arn = string, security_group_id = string, host = string, priority = number, health_path = string, health_port = optional(number), restricted_paths = optional(object({ paths = list(string), allowed_cidrs = list(string) })) })`
    - `autoscaling` (nullable, default null): `object({ min = number, max = number, cpu_target = optional(number), sqs = optional(object({ queue_name = string, scale_out_at = number, scale_in_at = number })) })`
    - `task_role_statements` (`any`, default `[]`: a list of IAM statement objects)
    - `stop_timeout` (number, default 30), `log_retention_days` (number)
    - `adot_image` (string, default `"public.ecr.aws/aws-observability/aws-otel-collector:v0.50.0"`)
    - `tags`
  - **Outputs:** `security_group_id`, `service_name`, `service_arn`, `task_role_arn`, `execution_role_arn`, `task_definition_family`, `target_group_arn_suffix` (null without ALB), `log_group_name`.

- [ ] **Step 1: Write the failing tests**

`terraform/modules/service/tests/service.tftest.hcl`:
```hcl
mock_provider "aws" {
  mock_data "aws_region" {
    defaults = { region = "us-east-1", name = "us-east-1" }
  }
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::123456789012:role/mock" }
  }
  mock_resource "aws_cloudwatch_log_group" {
    defaults = { arn = "arn:aws:logs:us-east-1:123456789012:log-group:/mock" }
  }
  mock_resource "aws_lb_target_group" {
    defaults = { arn = "arn:aws:elasticloadbalancing:us-east-1:123456789012:targetgroup/mock/abc", arn_suffix = "targetgroup/mock/abc" }
  }
  mock_resource "aws_ecs_service" {
    defaults = { id = "arn:aws:ecs:us-east-1:123456789012:service/cluster/mock" }
  }
}

variables {
  name_prefix        = "shortener-test"
  name               = "api"
  environment_name   = "staging"
  ecs_cluster_arn    = "arn:aws:ecs:us-east-1:123456789012:cluster/platform"
  vpc_id             = "vpc-0123456789abcdef0"
  subnet_ids         = ["subnet-priv-a", "subnet-priv-b"]
  image              = "123456789012.dkr.ecr.us-east-1.amazonaws.com/shortener-test/api:abc123"
  cpu                = 256
  memory             = 512
  desired_count      = 1
  container_port     = 8000
  log_retention_days = 7
  env_vars           = { PUBLIC_BASE_URL = "https://go.example.test" }
  secrets = {
    DATABASE_URL = { arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:db-api", key = "url" }
  }
  alb = {
    listener_arn      = "arn:aws:elasticloadbalancing:us-east-1:123456789012:listener/app/mock/abc/def"
    security_group_id = "sg-alb"
    host              = "go.example.test"
    priority          = 100
    health_path       = "/healthz"
  }
}

run "sidecar_and_dependency" {
  command = apply

  assert {
    condition     = [for c in jsondecode(aws_ecs_task_definition.this.container_definitions) : c.name] == ["api", "adot"]
    error_message = "app container plus the ADOT sidecar"
  }
  assert {
    condition     = jsondecode(aws_ecs_task_definition.this.container_definitions)[0].dependsOn == [{ containerName = "adot", condition = "START" }]
    error_message = "the app starts after the sidecar"
  }
  assert {
    condition     = strcontains(one([for e in jsondecode(aws_ecs_task_definition.this.container_definitions)[1].environment : e.value if e.name == "AOT_CONFIG_CONTENT"]), "awsxray")
    error_message = "the collector exports traces to X-Ray (and metrics via awsemf)"
  }
  assert {
    condition     = contains([for e in jsondecode(aws_ecs_task_definition.this.container_definitions)[0].environment : e.name], "OTEL_EXPORTER_OTLP_ENDPOINT")
    error_message = "the module sets OTEL_EXPORTER_OTLP_ENDPOINT to the sidecar"
  }
}

run "secrets_injected_and_scoped" {
  command = apply

  assert {
    condition     = jsondecode(aws_ecs_task_definition.this.container_definitions)[0].secrets == [{ name = "DATABASE_URL", valueFrom = "arn:aws:secretsmanager:us-east-1:123456789012:secret:db-api:url::" }]
    error_message = "secrets use <arn>:<json-key>:: valueFrom"
  }
  assert {
    condition     = jsondecode(aws_iam_role_policy.execution_secrets[0].policy).Statement[0].Resource == ["arn:aws:secretsmanager:us-east-1:123456789012:secret:db-api"]
    error_message = "the execution role reads only this service's secrets"
  }
}

run "alb_attachment" {
  command = apply

  assert {
    condition     = aws_lb_listener_rule.host[0].priority == 100 && aws_lb_listener_rule.host[0].condition[0].host_header[0].values == toset(["go.example.test"])
    error_message = "host rule at the given priority"
  }
  assert {
    condition     = aws_lb_target_group.this[0].health_check[0].path == "/healthz" && aws_lb_target_group.this[0].target_type == "ip"
    error_message = "IP target group with the liveness health check"
  }
  assert {
    condition     = length(aws_lb_listener_rule.restricted_allow) == 0 && length(aws_lb_listener_rule.restricted_deny) == 0
    error_message = "no restricted-path rules unless asked"
  }
}

run "restricted_paths_with_allowlist" {
  command = apply
  variables {
    alb = {
      listener_arn      = "arn:aws:elasticloadbalancing:us-east-1:123456789012:listener/app/mock/abc/def"
      security_group_id = "sg-alb"
      host              = "auth.example.test"
      priority          = 300
      health_path       = "/health/ready"
      health_port       = 9000
      restricted_paths  = { paths = ["/admin/*"], allowed_cidrs = ["203.0.113.0/24"] }
    }
  }

  assert {
    condition     = aws_lb_listener_rule.restricted_allow[0].priority == 298 && aws_lb_listener_rule.restricted_deny[0].priority == 299
    error_message = "allow (allowlist) then deny, both ahead of the host rule"
  }
  assert {
    condition     = aws_lb_listener_rule.restricted_deny[0].action[0].fixed_response[0].status_code == "403"
    error_message = "everyone else gets 403 on /admin/*"
  }
  assert {
    condition     = toset(keys(aws_vpc_security_group_ingress_rule.from_alb)) == toset(["8000", "9000"])
    error_message = "ALB may reach the app port and the health port"
  }
}

run "restricted_paths_with_empty_allowlist_only_deny" {
  command = apply
  variables {
    alb = {
      listener_arn      = "arn:aws:elasticloadbalancing:us-east-1:123456789012:listener/app/mock/abc/def"
      security_group_id = "sg-alb"
      host              = "auth.example.test"
      priority          = 300
      health_path       = "/health/ready"
      restricted_paths  = { paths = ["/admin/*"], allowed_cidrs = [] }
    }
  }

  assert {
    condition     = length(aws_lb_listener_rule.restricted_allow) == 0 && length(aws_lb_listener_rule.restricted_deny) == 1
    error_message = "an empty allowlist denies /admin/* entirely (never open, never a plan error)"
  }
}

run "worker_without_alb_scales_on_queue" {
  command = apply
  variables {
    name           = "click-processor"
    container_port = null
    alb            = null
    stop_timeout   = 60
    autoscaling    = { min = 1, max = 4, sqs = { queue_name = "shortener-test-click-events", scale_out_at = 100, scale_in_at = 10 } }
  }

  assert {
    condition     = length(aws_lb_target_group.this) == 0 && length(aws_vpc_security_group_ingress_rule.from_alb) == 0
    error_message = "a worker has no target group and no ingress"
  }
  assert {
    condition     = aws_appautoscaling_target.this[0].min_capacity == 1 && aws_appautoscaling_target.this[0].max_capacity == 4
    error_message = "scaling bounds 1-4"
  }
  assert {
    condition     = aws_cloudwatch_metric_alarm.sqs_high[0].dimensions == { QueueName = "shortener-test-click-events" } && aws_cloudwatch_metric_alarm.sqs_high[0].threshold == 100
    error_message = "scale out on visible messages in the click queue"
  }
  assert {
    condition     = jsondecode(aws_ecs_task_definition.this.container_definitions)[0].stopTimeout == 60
    error_message = "stopTimeout reaches the app container"
  }
}
```

- [ ] **Step 2: Run and watch it fail**

Run: `terraform -chdir=terraform/modules/service init -backend=false && terraform -chdir=terraform/modules/service test`
Expected: FAIL (no configuration).

- [ ] **Step 3: Implement `variables.tf`**

Declare every input from Interfaces, each with a `description`, the exact types and defaults, and `nullable = true` for the nullable ones. Add this validation to `alb`:
```hcl
  validation {
    condition     = var.alb == null || try(length(var.alb.restricted_paths.allowed_cidrs) <= 3, true)
    error_message = "An ALB rule allows 5 condition values: host + path + at most 3 source CIDRs."
  }
```

- [ ] **Step 4: Implement `main.tf`** (log groups, security group, task definition, service)

```hcl
data "aws_region" "current" {}

locals {
  full_name    = "${var.name_prefix}-${var.name}"
  cluster_name = element(split("/", var.ecs_cluster_arn), 1)
  health_port  = var.alb == null ? null : coalesce(try(var.alb.health_port, null), var.container_port)
  ingress_ports = var.alb == null ? {} : {
    for p in distinct([var.container_port, local.health_port]) : tostring(p) => p
  }

  adot_config = yamlencode({
    receivers  = { otlp = { protocols = { http = { endpoint = "0.0.0.0:4318" } } } }
    processors = { batch = {} }
    exporters = {
      awsemf = {
        namespace               = "Shortener"
        log_group_name          = aws_cloudwatch_log_group.metrics.name
        dimension_rollup_option = "NoDimensionRollup"
      }
      awsxray = {}
    }
    service = {
      pipelines = {
        traces  = { receivers = ["otlp"], processors = ["batch"], exporters = ["awsxray"] }
        metrics = { receivers = ["otlp"], processors = ["batch"], exporters = ["awsemf"] }
      }
    }
  })

  log_options = { for c in [var.name, "adot"] : c => {
    logDriver = "awslogs"
    options = {
      "awslogs-group"         = aws_cloudwatch_log_group.app.name
      "awslogs-region"        = data.aws_region.current.region
      "awslogs-stream-prefix" = c
    }
  } }

  app_container = merge(
    {
      name             = var.name
      image            = var.image
      essential        = true
      stopTimeout      = var.stop_timeout
      environment      = [for k, v in merge(var.env_vars, { OTEL_EXPORTER_OTLP_ENDPOINT = "http://localhost:4318" }) : { name = k, value = v }]
      secrets          = [for k, s in var.secrets : { name = k, valueFrom = "${s.arn}:${s.key}::" }]
      dependsOn        = [{ containerName = "adot", condition = "START" }]
      logConfiguration = local.log_options[var.name]
      portMappings     = [for p in distinct([var.container_port, local.health_port]) : { containerPort = p, protocol = "tcp" } if p != null]
    },
    var.command == null ? {} : { command = var.command },
    var.health_check_command == null ? {} : {
      healthCheck = { command = var.health_check_command, interval = 15, timeout = 5, retries = 3, startPeriod = 30 }
    },
  )

  adot_container = {
    name              = "adot"
    image             = var.adot_image
    essential         = true
    memoryReservation = 128
    environment       = [{ name = "AOT_CONFIG_CONTENT", value = local.adot_config }]
    logConfiguration  = local.log_options["adot"]
  }
}

resource "aws_cloudwatch_log_group" "app" {
  name              = "/shortener/${var.environment_name}/${var.name}"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

resource "aws_cloudwatch_log_group" "metrics" {
  name              = "/shortener/${var.environment_name}/${var.name}/metrics"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

resource "aws_security_group" "this" {
  name        = local.full_name
  description = "ECS tasks for ${var.name}"
  vpc_id      = var.vpc_id
  tags        = var.tags
}

#trivy:ignore:AVD-AWS-0104 tasks reach RDS, SQS, ECR, Secrets Manager, X-Ray and CloudWatch; egress is limited at the VPC (platform)
resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.this.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
  description       = "AWS APIs and RDS"
}

resource "aws_vpc_security_group_ingress_rule" "from_alb" {
  for_each                     = local.ingress_ports
  security_group_id            = aws_security_group.this.id
  referenced_security_group_id = var.alb.security_group_id
  ip_protocol                  = "tcp"
  from_port                    = each.value
  to_port                      = each.value
  description                  = "ALB to ${var.name}:${each.key}"
}

resource "aws_ecs_task_definition" "this" {
  family                   = local.full_name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.cpu)
  memory                   = tostring(var.memory)
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  container_definitions    = jsonencode([local.app_container, local.adot_container])

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }
  tags = var.tags
}

resource "aws_ecs_service" "this" {
  name                               = local.full_name
  cluster                            = var.ecs_cluster_arn
  task_definition                    = aws_ecs_task_definition.this.arn
  desired_count                      = var.desired_count
  launch_type                        = "FARGATE"
  propagate_tags                     = "SERVICE"
  enable_ecs_managed_tags            = true
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  health_check_grace_period_seconds  = var.alb == null ? null : 60

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = [aws_security_group.this.id]
    assign_public_ip = false
  }

  dynamic "load_balancer" {
    for_each = var.alb == null ? [] : [1]
    content {
      target_group_arn = aws_lb_target_group.this[0].arn
      container_name   = var.name
      container_port   = var.container_port
    }
  }

  # Autoscaling owns desired_count; the deploy pipeline (OIDC role) registers new task definitions.
  lifecycle {
    ignore_changes = [desired_count, task_definition]
  }

  depends_on = [aws_lb_listener_rule.host]
  tags       = var.tags
}
```

- [ ] **Step 5: Implement `iam.tf`**

```hcl
locals {
  ecs_tasks_trust = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "ecs-tasks.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role" "execution" {
  name               = "${local.full_name}-exec"
  assume_role_policy = local.ecs_tasks_trust
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy" # ECR pull + awslogs
}

resource "aws_iam_role_policy" "execution_secrets" {
  count = length(var.secrets) > 0 ? 1 : 0 # keys are known at plan; ARNs may not be
  name  = "read-own-secrets"
  role  = aws_iam_role.execution.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = distinct([for s in values(var.secrets) : s.arn]) }]
  })
}

resource "aws_iam_role" "task" {
  name               = "${local.full_name}-task"
  assume_role_policy = local.ecs_tasks_trust
  tags               = var.tags
}

resource "aws_iam_role_policy" "telemetry" {
  name = "telemetry"
  role = aws_iam_role.task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { # X-Ray has no resource-level permissions
        Effect   = "Allow"
        Action   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
        Resource = "${aws_cloudwatch_log_group.metrics.arn}:*"
      },
    ]
  })
}

resource "aws_iam_role_policy" "extra" {
  count  = length(var.task_role_statements) > 0 ? 1 : 0
  name   = "service"
  role   = aws_iam_role.task.id
  policy = jsonencode({ Version = "2012-10-17", Statement = var.task_role_statements })
}
```

- [ ] **Step 6: Implement `alb.tf`**

```hcl
locals {
  restricted = var.alb == null ? null : try(var.alb.restricted_paths, null)
}

resource "aws_lb_target_group" "this" {
  count                = var.alb == null ? 0 : 1
  name                 = substr(local.full_name, 0, 32)
  port                 = var.container_port
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = var.vpc_id
  deregistration_delay = 30

  health_check {
    path                = var.alb.health_path
    port                = tostring(local.health_port)
    matcher             = "200"
    interval            = 15
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
  tags = var.tags
}

resource "aws_lb_listener_rule" "host" {
  count        = var.alb == null ? 0 : 1
  listener_arn = var.alb.listener_arn
  priority     = var.alb.priority

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.this[0].arn
  }
  condition {
    host_header {
      values = [var.alb.host]
    }
  }
}

resource "aws_lb_listener_rule" "restricted_allow" {
  count        = local.restricted == null ? 0 : (length(local.restricted.allowed_cidrs) > 0 ? 1 : 0)
  listener_arn = var.alb.listener_arn
  priority     = var.alb.priority - 2

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.this[0].arn
  }
  condition {
    host_header {
      values = [var.alb.host]
    }
  }
  condition {
    path_pattern {
      values = local.restricted.paths
    }
  }
  condition {
    source_ip {
      values = local.restricted.allowed_cidrs
    }
  }
}

resource "aws_lb_listener_rule" "restricted_deny" {
  count        = local.restricted == null ? 0 : 1
  listener_arn = var.alb.listener_arn
  priority     = var.alb.priority - 1

  action {
    type = "fixed-response"
    fixed_response {
      content_type = "text/plain"
      message_body = "Forbidden"
      status_code  = "403"
    }
  }
  condition {
    host_header {
      values = [var.alb.host]
    }
  }
  condition {
    path_pattern {
      values = local.restricted.paths
    }
  }
}
```

- [ ] **Step 7: Implement `autoscaling.tf`**

```hcl
locals {
  sqs_scaling = var.autoscaling == null ? null : try(var.autoscaling.sqs, null)
  cpu_target  = var.autoscaling == null ? null : try(var.autoscaling.cpu_target, null)
}

resource "aws_appautoscaling_target" "this" {
  count              = var.autoscaling == null ? 0 : 1
  service_namespace  = "ecs"
  resource_id        = "service/${local.cluster_name}/${aws_ecs_service.this.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = var.autoscaling.min
  max_capacity       = var.autoscaling.max
}

resource "aws_appautoscaling_policy" "cpu" {
  count              = local.cpu_target == null ? 0 : 1
  name               = "${local.full_name}-cpu"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.this[0].service_namespace
  resource_id        = aws_appautoscaling_target.this[0].resource_id
  scalable_dimension = aws_appautoscaling_target.this[0].scalable_dimension

  target_tracking_scaling_policy_configuration {
    target_value       = local.cpu_target
    scale_in_cooldown  = 120
    scale_out_cooldown = 60
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
  }
}

resource "aws_appautoscaling_policy" "sqs" {
  for_each           = local.sqs_scaling == null ? {} : { out = 1, in = -1 }
  name               = "${local.full_name}-sqs-${each.key}"
  policy_type        = "StepScaling"
  service_namespace  = aws_appautoscaling_target.this[0].service_namespace
  resource_id        = aws_appautoscaling_target.this[0].resource_id
  scalable_dimension = aws_appautoscaling_target.this[0].scalable_dimension

  step_scaling_policy_configuration {
    adjustment_type         = "ChangeInCapacity"
    cooldown                = 60
    metric_aggregation_type = "Maximum"
    step_adjustment {
      scaling_adjustment          = each.value
      metric_interval_lower_bound = each.key == "out" ? 0 : null
      metric_interval_upper_bound = each.key == "in" ? 0 : null
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "sqs_high" {
  count               = local.sqs_scaling == null ? 0 : 1
  alarm_name          = "${local.full_name}-queue-high"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = local.sqs_scaling.queue_name }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 2
  threshold           = local.sqs_scaling.scale_out_at
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_appautoscaling_policy.sqs["out"].arn]
  tags                = var.tags
}

resource "aws_cloudwatch_metric_alarm" "sqs_low" {
  count               = local.sqs_scaling == null ? 0 : 1
  alarm_name          = "${local.full_name}-queue-low"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = local.sqs_scaling.queue_name }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 5
  threshold           = local.sqs_scaling.scale_in_at
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_appautoscaling_policy.sqs["in"].arn]
  tags                = var.tags
}
```

- [ ] **Step 8: Implement `outputs.tf`**

```hcl
output "security_group_id" {
  description = "Task security group."
  value       = aws_security_group.this.id
}
output "service_name" {
  description = "ECS service name."
  value       = aws_ecs_service.this.name
}
output "service_arn" {
  description = "ECS service ARN."
  value       = aws_ecs_service.this.id
}
output "task_role_arn" {
  description = "Task role ARN."
  value       = aws_iam_role.task.arn
}
output "execution_role_arn" {
  description = "Execution role ARN."
  value       = aws_iam_role.execution.arn
}
output "task_definition_family" {
  description = "Task definition family."
  value       = aws_ecs_task_definition.this.family
}
output "target_group_arn_suffix" {
  description = "Target group ARN suffix for ALB metrics (null without an ALB)."
  value       = one(aws_lb_target_group.this[*].arn_suffix)
}
output "log_group_name" {
  description = "Application log group."
  value       = aws_cloudwatch_log_group.app.name
}
```

- [ ] **Step 9: Run the tests**

Run: `terraform -chdir=terraform/modules/service test`
Expected: `6 passed, 0 failed`. If an assertion fails because of a plan-time shape the mock doesn't produce (for example, a set vs a list for `host_header.values`), fix the comparison in the test. Never weaken what it checks.

- [ ] **Step 10: Gate and commit**

```bash
make tf-check && /usr/bin/git add terraform/modules/service && /usr/bin/git commit -m "feat(terraform): service module (Fargate + ADOT sidecar, scoped IAM, ALB rules, autoscaling)

Co-Authored-By: <implementing model> <noreply@anthropic.com>"
```

---

### Task 7: `task` module and the database bootstrap script

**Files:**
- Create: `terraform/scripts/db-bootstrap.sh`, `terraform/modules/task/{versions.tf,variables.tf,main.tf,outputs.tf}`, `terraform/modules/task/tests/task.tftest.hcl`, `tests/test_db_bootstrap.py`

**Interfaces:**
- Produces:
  - **The `task` module** (a one-off task definition with no service):
    - inputs: `name_prefix`, `name`, `environment_name`, `vpc_id`, `image`, `cpu` (default 256), `memory` (default 512), `command` (list(string)), `env_vars` (map(string), default `{}`), `secrets` (`map(object({ arn = string, key = string }))`, default `{}`), `log_retention_days`, `cpu_architecture` (default `"X86_64"`), `tags`
    - outputs: `task_definition_family`, `task_definition_arn`, `security_group_id`, `execution_role_arn`, `task_role_arn`
  - **`terraform/scripts/db-bootstrap.sh`:** reads env `PGHOST`, `PGPORT` (default 5432), `PGUSER`, `PGPASSWORD` (the RDS master), plus `MIGRATOR_PASSWORD`, `API_USER_PASSWORD`, `PROCESSOR_USER_PASSWORD`, `ADMIN_USER_PASSWORD`, `KEYCLOAK_PASSWORD`. Honours `PGSSLMODE` (default `require`). It is idempotent.

- [ ] **Step 1: Write the failing bootstrap test**

`tests/test_db_bootstrap.py`:
```python
"""terraform/scripts/db-bootstrap.sh against Postgres 16 with an RDS-like non-superuser master."""

from pathlib import Path

import pytest
from testcontainers.community.postgres import PostgresContainer

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = "/scripts/db-bootstrap.sh"
ROLES = ("migrator", "api_user", "processor_user", "admin_user", "keycloak")


def passwords(suffix: str) -> dict[str, str]:
    return {
        "MIGRATOR_PASSWORD": f"migrator-{suffix}",
        "API_USER_PASSWORD": f"api-{suffix}",
        "PROCESSOR_USER_PASSWORD": f"processor-{suffix}",
        "ADMIN_USER_PASSWORD": f"admin-{suffix}",
        "KEYCLOAK_PASSWORD": f"keycloak-{suffix}",
    }


@pytest.fixture(scope="module")
def postgres():
    container = PostgresContainer(
        "postgres:16.10-alpine", username="postgres", password="super", dbname="postgres"
    ).with_volume_mapping(str(REPO_ROOT / "terraform" / "scripts"), "/scripts", "ro")
    with container:
        # RDS's master is rds_superuser, not a superuser: CREATEROLE + CREATEDB only.
        psql(container, "postgres", "super", "CREATE ROLE rds_master LOGIN CREATEROLE CREATEDB PASSWORD 'master'")
        yield container


def psql(container, user: str, password: str, sql: str, db: str = "postgres") -> str:
    code, out = container.get_wrapped_container().exec_run(
        ["psql", "-v", "ON_ERROR_STOP=1", "-tA", "-h", "127.0.0.1", "-U", user, "-d", db, "-c", sql],
        environment={"PGPASSWORD": password},
    )
    assert code == 0, out.decode()
    return out.decode().strip()


def bootstrap(container, suffix: str) -> tuple[int, str]:
    env = {"PGHOST": "127.0.0.1", "PGUSER": "rds_master", "PGPASSWORD": "master", "PGSSLMODE": "disable", **passwords(suffix)}
    code, out = container.get_wrapped_container().exec_run(["sh", SCRIPT], environment=env)
    return code, out.decode()


def test_bootstrap_is_idempotent_with_non_superuser_master(postgres):
    for attempt in ("one", "two"):
        code, out = bootstrap(postgres, attempt)
        assert code == 0, out
    roles = psql(postgres, "postgres", "super", "SELECT string_agg(rolname, ',' ORDER BY rolname) FROM pg_roles WHERE rolname = ANY(ARRAY['migrator','api_user','processor_user','admin_user','keycloak'])")
    assert roles.split(",") == sorted(ROLES)
    owners = psql(postgres, "postgres", "super", "SELECT string_agg(datname || '=' || pg_get_userbyid(datdba), ',' ORDER BY datname) FROM pg_database WHERE datname IN ('shortener','keycloak')")
    assert owners == "keycloak=keycloak,shortener=migrator"


def test_connect_privileges_match_local_bootstrap(postgres):
    assert bootstrap(postgres, "grants")[0] == 0
    checks = {
        "has_database_privilege('api_user', 'shortener', 'CONNECT')": "t",
        "has_database_privilege('processor_user', 'shortener', 'CONNECT')": "t",
        "has_database_privilege('admin_user', 'shortener', 'CONNECT')": "t",
        "has_database_privilege('api_user', 'keycloak', 'CONNECT')": "f",
    }
    for expression, expected in checks.items():
        assert psql(postgres, "postgres", "super", f"SELECT {expression}") == expected, expression


def test_rerun_rotates_passwords(postgres):
    assert bootstrap(postgres, "old")[0] == 0
    assert bootstrap(postgres, "new")[0] == 0
    assert psql(postgres, "api_user", "api-new", "SELECT current_user", db="shortener") == "api_user"
```
Note: if `testcontainers.community.postgres` isn't the import path in this environment, use whatever `libs/shortener-testing/src/shortener_testing/fixtures.py` imports.

- [ ] **Step 2: Run it and watch it fail**

Run: `. scripts/docker-env.sh && uv run pytest tests/test_db_bootstrap.py -q`
Expected: FAIL. `sh: can't open '/scripts/db-bootstrap.sh'`, so `code != 0`.

- [ ] **Step 3: Write the script**

`terraform/scripts/db-bootstrap.sh` (`chmod +x`):
```sh
#!/bin/sh
# Idempotent RDS equivalent of infra/postgres/bootstrap.sql (spec T7). Run by the db-bootstrap ECS task
# before `migrate` on every deploy. The RDS master is not a superuser, so it grants itself membership
# in the owner roles before creating databases for them (PostgreSQL 16 requires SET on the owner).
set -eu
: "${PGHOST:?}" "${PGUSER:?}" "${PGPASSWORD:?}"
: "${MIGRATOR_PASSWORD:?}" "${API_USER_PASSWORD:?}" "${PROCESSOR_USER_PASSWORD:?}" "${ADMIN_USER_PASSWORD:?}" "${KEYCLOAK_PASSWORD:?}"
export PGSSLMODE="${PGSSLMODE:-require}" PGDATABASE=postgres

psql -v ON_ERROR_STOP=1 -q \
  -v migrator_pw="$MIGRATOR_PASSWORD" -v api_pw="$API_USER_PASSWORD" \
  -v processor_pw="$PROCESSOR_USER_PASSWORD" -v admin_pw="$ADMIN_USER_PASSWORD" \
  -v keycloak_pw="$KEYCLOAK_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN', r)
FROM unnest(ARRAY['migrator', 'api_user', 'processor_user', 'admin_user', 'keycloak']) AS r
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) \gexec

ALTER ROLE migrator PASSWORD :'migrator_pw';
ALTER ROLE api_user PASSWORD :'api_pw';
ALTER ROLE processor_user PASSWORD :'processor_pw';
ALTER ROLE admin_user PASSWORD :'admin_pw';
ALTER ROLE keycloak PASSWORD :'keycloak_pw';

GRANT migrator, keycloak TO CURRENT_USER WITH INHERIT TRUE, SET TRUE;

SELECT 'CREATE DATABASE keycloak OWNER keycloak'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'keycloak') \gexec
SELECT 'CREATE DATABASE shortener OWNER migrator'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'shortener') \gexec

REVOKE CONNECT ON DATABASE shortener FROM PUBLIC;
GRANT CONNECT ON DATABASE shortener TO api_user, processor_user, admin_user;
REVOKE CONNECT ON DATABASE keycloak FROM PUBLIC;
SQL
echo "db-bootstrap: roles and databases are in place"
```

- [ ] **Step 4: Run the bootstrap tests**

Run: `. scripts/docker-env.sh && uv run pytest tests/test_db_bootstrap.py -q`
Expected: `3 passed`. If PostgreSQL rejects `GRANT … WITH INHERIT TRUE, SET TRUE` when the grant already exists, it only issues a NOTICE, which is fine. If it errors, keep the grant idempotent by making it conditional on `pg_has_role(current_user, 'migrator', 'SET')`, and record the change.

- [ ] **Step 5: Write the failing `task` module test**

`terraform/modules/task/tests/task.tftest.hcl`:
```hcl
mock_provider "aws" {
  mock_data "aws_region" {
    defaults = { region = "us-east-1", name = "us-east-1" }
  }
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::123456789012:role/mock" }
  }
}

variables {
  name_prefix        = "shortener-test"
  name               = "migrate"
  environment_name   = "staging"
  vpc_id             = "vpc-0123456789abcdef0"
  image              = "123456789012.dkr.ecr.us-east-1.amazonaws.com/shortener-test/api:abc123"
  command            = ["alembic", "upgrade", "head"]
  log_retention_days = 7
  secrets = {
    MIGRATOR_DATABASE_URL = { arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:db-migrator", key = "url" }
  }
}

run "one_off_definition" {
  command = apply

  assert {
    condition     = jsondecode(aws_ecs_task_definition.this.container_definitions)[0].command == ["alembic", "upgrade", "head"]
    error_message = "migrate runs alembic upgrade head (local compose parity)"
  }
  assert {
    condition     = jsondecode(aws_ecs_task_definition.this.container_definitions)[0].secrets[0].valueFrom == "arn:aws:secretsmanager:us-east-1:123456789012:secret:db-migrator:url::"
    error_message = "MIGRATOR_DATABASE_URL from the migrator secret's url key"
  }
  assert {
    condition     = jsondecode(aws_iam_role_policy.execution_secrets[0].policy).Statement[0].Resource == ["arn:aws:secretsmanager:us-east-1:123456789012:secret:db-migrator"]
    error_message = "reads only its own secrets"
  }
  assert {
    condition     = output.task_definition_family == "shortener-test-migrate"
    error_message = "family is <prefix>-<name>"
  }
}
```

- [ ] **Step 6: Run and watch it fail; implement the module**

Run: `terraform -chdir=terraform/modules/task init -backend=false && terraform -chdir=terraform/modules/task test`
Expected: FAIL (no configuration).

`main.tf` (same patterns as Task 6, without the service, ALB, sidecar or autoscaling; one-off tasks emit no telemetry worth a collector):
```hcl
data "aws_region" "current" {}

locals {
  full_name = "${var.name_prefix}-${var.name}"
  trust = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "ecs-tasks.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_cloudwatch_log_group" "this" {
  name              = "/shortener/${var.environment_name}/${var.name}"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

resource "aws_iam_role" "execution" {
  name               = "${local.full_name}-exec"
  assume_role_policy = local.trust
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "execution_secrets" {
  count = length(var.secrets) > 0 ? 1 : 0
  name  = "read-own-secrets"
  role  = aws_iam_role.execution.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = distinct([for s in values(var.secrets) : s.arn]) }]
  })
}

resource "aws_iam_role" "task" {
  name               = "${local.full_name}-task"
  assume_role_policy = local.trust
  tags               = var.tags
}

resource "aws_security_group" "this" {
  name        = local.full_name
  description = "One-off ${var.name} task"
  vpc_id      = var.vpc_id
  tags        = var.tags
}

#trivy:ignore:AVD-AWS-0104 one-off task reaches RDS, ECR, Secrets Manager and CloudWatch
resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.this.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
  description       = "AWS APIs and RDS"
}

resource "aws_ecs_task_definition" "this" {
  family                   = local.full_name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.cpu)
  memory                   = tostring(var.memory)
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  container_definitions = jsonencode([{
    name        = var.name
    image       = var.image
    essential   = true
    command     = var.command
    environment = [for k, v in var.env_vars : { name = k, value = v }]
    secrets     = [for k, s in var.secrets : { name = k, valueFrom = "${s.arn}:${s.key}::" }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.this.name
        "awslogs-region"        = data.aws_region.current.region
        "awslogs-stream-prefix" = var.name
      }
    }
  }])

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }
  tags = var.tags
}
```
Write `variables.tf` from Interfaces, with descriptions. `outputs.tf`: `task_definition_family` (`aws_ecs_task_definition.this.family`), `task_definition_arn` (`.arn_without_revision`), `security_group_id`, `execution_role_arn`, `task_role_arn`.

- [ ] **Step 7: Run the tests**

Run: `terraform -chdir=terraform/modules/task test`
Expected: `1 passed, 0 failed`.

- [ ] **Step 8: Gates and commit**

```bash
uv run ruff format . && make check && make tf-check && /usr/bin/git add terraform/scripts terraform/modules/task tests/test_db_bootstrap.py && /usr/bin/git commit -m "feat(terraform): one-off task module and idempotent RDS db-bootstrap script (integration-tested)

Co-Authored-By: <implementing model> <noreply@anthropic.com>"
```

---

### Task 8: `alarms` module (SNS, the alarms, and the redirect canary)

**Files:**
- Create: `terraform/modules/alarms/{versions.tf,variables.tf,main.tf,canary.tf,outputs.tf}`, `terraform/modules/alarms/canary/canary.py`, `terraform/modules/alarms/tests/alarms.tftest.hcl`, `tests/test_redirect_canary.py`
- Modify: `.gitignore` (add `terraform/**/.build/`, where the canary zip is written)

**Interfaces:**
- Consumes:
  - `queue.queue_name` and `queue.dlq_name` (Task 1)
  - `edge.alb_arn_suffix` (Task 5)
  - each service's `target_group_arn_suffix` and the processor's `service_name` (Task 6)
  - `data.instance_identifier` (Task 3)
- Produces: the `alarms` module.
  - **Inputs:**
    - `name_prefix`
    - `queue_name`, `dlq_name`
    - `alb_arn_suffix`, `target_group_arn_suffixes` (`map(string)` with static keys)
    - `db_instance_identifier`, `ecs_cluster_name`, `processor_service_name`
    - `error_rate_percent` (number, default 2), `min_requests` (number, default 50), `db_free_storage_bytes` (number, default 2147483648)
    - `canary_url` (string, nullable, default null; null disables the canary): the full URL of the canary short link, e.g. `https://go.<domain>/canary`
    - `canary_runtime_version` (string, default `"syn-python-selenium-6.0"`; check the AWS Synthetics runtime list for the current Python runtime when implementing)
    - `tags`
  - **Outputs:** `topic_arn`, `alarm_names` (list(string)), `canary_name` (string or null).

- [ ] **Step 1: Write the failing test**

`terraform/modules/alarms/tests/alarms.tftest.hcl`:
```hcl
mock_provider "aws" {
  mock_data "aws_region" {
    defaults = { region = "us-east-1", name = "us-east-1" }
  }
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
  mock_resource "aws_sns_topic" {
    defaults = { arn = "arn:aws:sns:us-east-1:123456789012:mock" }
  }
  mock_resource "aws_s3_bucket" {
    defaults = { arn = "arn:aws:s3:::shortener-test-canary-abc", id = "shortener-test-canary-abc" }
  }
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::123456789012:role/mock" }
  }
}

variables {
  canary_url                = "https://go.example.test/canary"
  name_prefix               = "shortener-test"
  queue_name                = "shortener-test-click-events"
  dlq_name                  = "shortener-test-click-events-dlq"
  alb_arn_suffix            = "app/mock/abc"
  target_group_arn_suffixes = { api = "targetgroup/api/1", admin = "targetgroup/admin/2", keycloak = "targetgroup/kc/3" }
  db_instance_identifier    = "shortener-test-db"
  ecs_cluster_name          = "platform"
  processor_service_name    = "shortener-test-click-processor"
}

run "alarm_set" {
  command = apply

  assert {
    condition     = aws_sns_topic.alarms.kms_master_key_id == "alias/aws/sns"
    error_message = "the alarm topic is encrypted"
  }
  assert {
    condition     = aws_cloudwatch_metric_alarm.dlq_not_empty.threshold == 0 && aws_cloudwatch_metric_alarm.dlq_not_empty.dimensions == { QueueName = "shortener-test-click-events-dlq" }
    error_message = "DLQ alarm fires on any visible message"
  }
  assert {
    condition     = aws_cloudwatch_metric_alarm.backlog_stale.threshold == 300 && aws_cloudwatch_metric_alarm.backlog_stale.metric_name == "ApproximateAgeOfOldestMessage"
    error_message = "backlog alarm at 300 s oldest-message age"
  }
  assert {
    condition     = strcontains(one([for q in aws_cloudwatch_metric_alarm.error_rate.metric_query : q.expression if q.id == "rate"]), "50")
    error_message = "5xx rate only counts when requests >= min_requests (quiet nights don't page)"
  }
  assert {
    condition = toset([for q in aws_cloudwatch_metric_alarm.error_rate.metric_query : q.metric[0].metric_name if length(q.metric) > 0]) == toset(["HTTPCode_Target_5XX_Count", "HTTPCode_ELB_5XX_Count", "RequestCount"])
    error_message = "error rate counts target 5xx AND the ALB's own 5xx (502/503 when no target is healthy)"
  }
  assert {
    condition     = length(aws_cloudwatch_metric_alarm.unhealthy_targets) == 3
    error_message = "an unhealthy-targets alarm per target group"
  }
  assert {
    condition     = aws_cloudwatch_metric_alarm.processor_down.treat_missing_data == "breaching" && aws_cloudwatch_metric_alarm.processor_down.namespace == "ECS/ContainerInsights"
    error_message = "processor-down treats missing data as breaching"
  }
  assert {
    condition = alltrue([
      for a in concat([aws_cloudwatch_metric_alarm.dlq_not_empty, aws_cloudwatch_metric_alarm.backlog_stale, aws_cloudwatch_metric_alarm.error_rate, aws_cloudwatch_metric_alarm.db_cpu, aws_cloudwatch_metric_alarm.db_storage, aws_cloudwatch_metric_alarm.processor_down], values(aws_cloudwatch_metric_alarm.unhealthy_targets)) :
      a.alarm_actions == toset(["arn:aws:sns:us-east-1:123456789012:mock"])
    ])
    error_message = "every alarm notifies the SNS topic"
  }
}

run "redirect_canary" {
  command = apply

  assert {
    condition     = aws_synthetics_canary.redirect[0].schedule[0].expression == "rate(1 minute)" && aws_synthetics_canary.redirect[0].run_config[0].environment_variables == { CANARY_URL = "https://go.example.test/canary" }
    error_message = "the canary probes the canary link every minute"
  }
  assert {
    condition     = aws_cloudwatch_metric_alarm.canary[0].metric_name == "SuccessPercent" && aws_cloudwatch_metric_alarm.canary[0].evaluation_periods == 2 && aws_cloudwatch_metric_alarm.canary[0].treat_missing_data == "breaching"
    error_message = "two failed canary runs page; a canary that stops reporting also pages"
  }
  assert {
    condition     = aws_s3_bucket_public_access_block.canary[0].block_public_acls && aws_s3_bucket_public_access_block.canary[0].restrict_public_buckets
    error_message = "canary artifacts bucket is private"
  }
  assert {
    condition = alltrue([
      for st in jsondecode(aws_iam_role_policy.canary[0].policy).Statement :
      st.Resource != "*" || toset(flatten([st.Action])) == toset(["s3:ListAllMyBuckets"]) || toset(flatten([st.Action])) == toset(["cloudwatch:PutMetricData"])
    ])
    error_message = "canary role: Resource \"*\" only where Synthetics requires it"
  }
}

run "canary_disabled" {
  command = apply
  variables {
    canary_url = null
  }

  assert {
    condition     = length(aws_synthetics_canary.redirect) == 0 && length(aws_cloudwatch_metric_alarm.canary) == 0 && length(aws_s3_bucket.canary) == 0
    error_message = "no canary_url, no canary resources"
  }
}
```

- [ ] **Step 2: Run and watch it fail**

Run: `terraform -chdir=terraform/modules/alarms init -backend=false && terraform -chdir=terraform/modules/alarms test`
Expected: FAIL (no configuration).

- [ ] **Step 3: Implement**

`main.tf`:
```hcl
locals {
  actions = [aws_sns_topic.alarms.arn]
}

resource "aws_sns_topic" "alarms" {
  name              = "${var.name_prefix}-alarms"
  kms_master_key_id = "alias/aws/sns"
  tags              = var.tags
}

resource "aws_cloudwatch_metric_alarm" "dlq_not_empty" {
  alarm_name          = "${var.name_prefix}-dlq-not-empty"
  alarm_description   = "Click events are failing processing and reached the DLQ"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = var.dlq_name }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.actions
  tags                = var.tags
}

resource "aws_cloudwatch_metric_alarm" "backlog_stale" {
  alarm_name          = "${var.name_prefix}-click-backlog-stale"
  alarm_description   = "The processor is not keeping up (oldest click older than 5 minutes)"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateAgeOfOldestMessage"
  dimensions          = { QueueName = var.queue_name }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 300
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.actions
  tags                = var.tags
}

resource "aws_cloudwatch_metric_alarm" "error_rate" {
  alarm_name          = "${var.name_prefix}-5xx-rate"
  alarm_description   = "More than ${var.error_rate_percent}% of requests return 5xx"
  evaluation_periods  = 1
  threshold           = var.error_rate_percent
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.actions

  metric_query {
    id          = "rate"
    expression  = "IF(requests >= ${var.min_requests}, 100 * (target_errors + elb_errors) / requests, 0)"
    label       = "5xx percent"
    return_data = true
  }
  metric_query {
    id = "target_errors"
    metric {
      namespace   = "AWS/ApplicationELB"
      metric_name = "HTTPCode_Target_5XX_Count"
      dimensions  = { LoadBalancer = var.alb_arn_suffix }
      stat        = "Sum"
      period      = 300
    }
  }
  metric_query {
    id = "elb_errors" # the ALB's own 502/503/504: what visitors see when no target is healthy
    metric {
      namespace   = "AWS/ApplicationELB"
      metric_name = "HTTPCode_ELB_5XX_Count"
      dimensions  = { LoadBalancer = var.alb_arn_suffix }
      stat        = "Sum"
      period      = 300
    }
  }
  metric_query {
    id = "requests"
    metric {
      namespace   = "AWS/ApplicationELB"
      metric_name = "RequestCount"
      dimensions  = { LoadBalancer = var.alb_arn_suffix }
      stat        = "Sum"
      period      = 300
    }
  }
  tags = var.tags
}

resource "aws_cloudwatch_metric_alarm" "unhealthy_targets" {
  for_each            = var.target_group_arn_suffixes
  alarm_name          = "${var.name_prefix}-${each.key}-unhealthy-targets"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "UnHealthyHostCount"
  dimensions          = { LoadBalancer = var.alb_arn_suffix, TargetGroup = each.value }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 5
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.actions
  tags                = var.tags
}

resource "aws_cloudwatch_metric_alarm" "db_cpu" {
  alarm_name          = "${var.name_prefix}-db-cpu"
  namespace           = "AWS/RDS"
  metric_name         = "CPUUtilization"
  dimensions          = { DBInstanceIdentifier = var.db_instance_identifier }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  threshold           = 80
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = local.actions
  tags                = var.tags
}

resource "aws_cloudwatch_metric_alarm" "db_storage" {
  alarm_name          = "${var.name_prefix}-db-free-storage"
  namespace           = "AWS/RDS"
  metric_name         = "FreeStorageSpace"
  dimensions          = { DBInstanceIdentifier = var.db_instance_identifier }
  statistic           = "Minimum"
  period              = 300
  evaluation_periods  = 1
  threshold           = var.db_free_storage_bytes
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = local.actions
  tags                = var.tags
}

resource "aws_cloudwatch_metric_alarm" "processor_down" {
  alarm_name          = "${var.name_prefix}-processor-down"
  alarm_description   = "No click-processor task is running"
  namespace           = "ECS/ContainerInsights"
  metric_name         = "RunningTaskCount"
  dimensions          = { ClusterName = var.ecs_cluster_name, ServiceName = var.processor_service_name }
  statistic           = "Minimum"
  period              = 60
  evaluation_periods  = 3
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = local.actions
  tags                = var.tags
}
```
`canary/canary.py` (Python Synthetics runtime; standard library only):
```python
"""Redirect canary: HEAD the canary short link without following redirects; pass only on 302.

HEAD returns the same status as GET but records no click, so the canary never pollutes stats.
"""

import os
import urllib.error
import urllib.request


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return None


def check(url: str, timeout: float = 10.0) -> int:
    request = urllib.request.Request(url, method="HEAD")  # noqa: S310 (fixed https URL from config)
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=timeout) as response:
            return int(response.status)
    except urllib.error.HTTPError as error:
        return error.code


def handler(event, context):  # noqa: ANN001, ARG001 (Synthetics entrypoint)
    url = os.environ["CANARY_URL"]
    status = check(url)
    if status != 302:
        raise RuntimeError(f"redirect canary: expected 302 from {url}, got {status}")
    return "ok"
```

`canary.tf`:
```hcl
data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

locals {
  canary = var.canary_url == null ? 0 : 1
}

resource "aws_s3_bucket" "canary" {
  count         = local.canary
  bucket_prefix = "${var.name_prefix}-canary-"
  force_destroy = true
  tags          = var.tags
}

resource "aws_s3_bucket_public_access_block" "canary" {
  count                   = local.canary
  bucket                  = aws_s3_bucket.canary[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "canary" {
  count  = local.canary
  bucket = aws_s3_bucket.canary[0].id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
  }
}

resource "aws_s3_bucket_versioning" "canary" {
  count  = local.canary
  bucket = aws_s3_bucket.canary[0].id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "canary" {
  count  = local.canary
  bucket = aws_s3_bucket.canary[0].id
  rule {
    id     = "expire-artifacts"
    status = "Enabled"
    filter {}
    expiration {
      days = 14
    }
    noncurrent_version_expiration {
      noncurrent_days = 1
    }
  }
}

data "archive_file" "canary" {
  count       = local.canary
  type        = "zip"
  output_path = "${path.module}/.build/canary.zip"
  source {
    content  = file("${path.module}/canary/canary.py")
    filename = "python/canary.py"
  }
}

resource "aws_iam_role" "canary" {
  count = local.canary
  name  = "${var.name_prefix}-canary"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
  tags = var.tags
}

resource "aws_iam_role_policy" "canary" {
  count = local.canary
  name  = "synthetics"
  role  = aws_iam_role.canary[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:PutObject", "s3:GetBucketLocation"], Resource = [aws_s3_bucket.canary[0].arn, "${aws_s3_bucket.canary[0].arn}/*"] },
      { Effect = "Allow", Action = ["s3:ListAllMyBuckets"], Resource = "*" },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = ["arn:aws:logs:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/cwsyn-*"]
      },
      {
        Effect    = "Allow"
        Action    = ["cloudwatch:PutMetricData"]
        Resource  = "*"
        Condition = { StringEquals = { "cloudwatch:namespace" = "CloudWatchSynthetics" } }
      },
    ]
  })
}

resource "aws_synthetics_canary" "redirect" {
  count                    = local.canary
  name                     = substr(replace("${var.name_prefix}-redirect", "shortener-", ""), 0, 21) # e.g. prod-redirect
  artifact_s3_location     = "s3://${aws_s3_bucket.canary[0].id}/"
  execution_role_arn       = aws_iam_role.canary[0].arn
  handler                  = "canary.handler"
  zip_file                 = data.archive_file.canary[0].output_path
  runtime_version          = var.canary_runtime_version
  start_canary             = true
  success_retention_period = 7
  failure_retention_period = 14

  schedule {
    expression = "rate(1 minute)"
  }
  run_config {
    timeout_in_seconds    = 30
    environment_variables = { CANARY_URL = var.canary_url }
  }
  tags = var.tags
}

resource "aws_cloudwatch_metric_alarm" "canary" {
  count               = local.canary
  alarm_name          = "${var.name_prefix}-redirect-canary"
  alarm_description   = "Redirects fail from the outside (DNS, TLS, ALB, api, or the link lookup)"
  namespace           = "CloudWatchSynthetics"
  metric_name         = "SuccessPercent"
  dimensions          = { CanaryName = aws_synthetics_canary.redirect[0].name }
  statistic           = "Average"
  period              = 60
  evaluation_periods  = 2
  threshold           = 100
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = local.actions
  tags                = var.tags
}
```

`tests/test_redirect_canary.py` (runs the real canary function against a local HTTP server; no AWS):
```python
"""terraform/modules/alarms/canary/canary.py: passes only on 302, never follows the redirect."""

import importlib.util
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "canary", Path(__file__).resolve().parents[1] / "terraform/modules/alarms/canary/canary.py"
)
canary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(canary)


@pytest.fixture
def server():
    seen: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_HEAD(self) -> None:
            seen.append(self.path)
            status = {"/canary": 302, "/gone": 410}.get(self.path, 404)
            self.send_response(status)
            if status == 302:
                self.send_header("Location", "http://127.0.0.1:9/never-followed")
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", seen
    httpd.shutdown()
    httpd.server_close()


def test_passes_on_302_without_following(server, monkeypatch):
    base, seen = server
    monkeypatch.setenv("CANARY_URL", f"{base}/canary")
    assert canary.handler({}, None) == "ok"
    assert seen == ["/canary"]  # HEAD only, the Location was never requested


@pytest.mark.parametrize(("path", "status"), [("/missing", 404), ("/gone", 410)])
def test_fails_on_anything_but_302(server, monkeypatch, path, status):
    base, _ = server
    monkeypatch.setenv("CANARY_URL", f"{base}{path}")
    with pytest.raises(RuntimeError, match=f"got {status}"):
        canary.handler({}, None)


def test_fails_when_unreachable(monkeypatch):
    monkeypatch.setenv("CANARY_URL", "http://127.0.0.1:9/canary")
    with pytest.raises(OSError):
        canary.handler({}, None)
```

Write `variables.tf` from Interfaces. `outputs.tf`:
- `canary_name`: `one(aws_synthetics_canary.redirect[*].name)`
- `topic_arn`: `aws_sns_topic.alarms.arn`
- `alarm_names`: `concat([aws_cloudwatch_metric_alarm.dlq_not_empty.alarm_name, aws_cloudwatch_metric_alarm.backlog_stale.alarm_name, aws_cloudwatch_metric_alarm.error_rate.alarm_name, aws_cloudwatch_metric_alarm.db_cpu.alarm_name, aws_cloudwatch_metric_alarm.db_storage.alarm_name, aws_cloudwatch_metric_alarm.processor_down.alarm_name], [for a in aws_cloudwatch_metric_alarm.unhealthy_targets : a.alarm_name], aws_cloudwatch_metric_alarm.canary[*].alarm_name)`

- [ ] **Step 4: Run the tests**

Run: `terraform -chdir=terraform/modules/alarms test && uv run pytest tests/test_redirect_canary.py -q`
Expected: `3 passed, 0 failed` (Terraform), then `4 passed` (pytest). Add `terraform/**/.build/` to `.gitignore`.

- [ ] **Step 5: Gate and commit**

```bash
uv run ruff format . && make check && make tf-check && /usr/bin/git add terraform/modules/alarms tests/test_redirect_canary.py .gitignore && /usr/bin/git commit -m "feat(terraform): alarms module (SNS; DLQ, backlog, ALB+target 5xx rate, unhealthy targets, RDS, processor-down) and a redirect canary

Co-Authored-By: <implementing model> <noreply@anthropic.com>"
```

---
### Task 9: `ci-deploy-role` module (GitHub OIDC, least privilege)

**Files:**
- Create: `terraform/modules/ci-deploy-role/{versions.tf,variables.tf,main.tf,outputs.tf}`, `terraform/modules/ci-deploy-role/tests/ci-deploy-role.tftest.hcl`

**Interfaces:**
- Consumes:
  - ECR repository ARNs (Task 4)
  - service ARNs and role ARNs (Task 6)
  - one-off task families and role ARNs (Task 7)
- Produces: the `ci-deploy-role` module.
  - **Inputs:**
    - `name_prefix`, `environment_name`
    - `github_repository` (`org/repo`), `github_oidc_provider_arn` (string, nullable, default null; null creates the provider)
    - `ecr_repository_arns` (list(string))
    - `ecs_cluster_arn`
    - `service_arns` (map(string)), `run_task_families` (map(string))
    - `pass_role_arns` (list(string))
    - `tags`
  - **Outputs:** `role_arn`, `oidc_provider_arn`.

- [ ] **Step 1: Write the failing test**

`terraform/modules/ci-deploy-role/tests/ci-deploy-role.tftest.hcl`:
```hcl
mock_provider "aws" {
  mock_data "aws_region" {
    defaults = { region = "us-east-1", name = "us-east-1" }
  }
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
  mock_resource "aws_iam_openid_connect_provider" {
    defaults = { arn = "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com" }
  }
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::123456789012:role/mock" }
  }
}

variables {
  name_prefix         = "shortener-test"
  environment_name    = "staging"
  github_repository   = "example/shortener"
  ecr_repository_arns = ["arn:aws:ecr:us-east-1:123456789012:repository/shortener-test/api"]
  ecs_cluster_arn     = "arn:aws:ecs:us-east-1:123456789012:cluster/platform"
  service_arns        = { api = "arn:aws:ecs:us-east-1:123456789012:service/platform/shortener-test-api" }
  run_task_families   = { migrate = "shortener-test-migrate", db_bootstrap = "shortener-test-db-bootstrap" }
  pass_role_arns      = ["arn:aws:iam::123456789012:role/shortener-test-api-task"]
}

run "least_privilege" {
  command = apply

  assert {
    condition = alltrue([
      for s in jsondecode(aws_iam_role_policy.deploy.policy).Statement :
      alltrue([for a in flatten([s.Action]) : !strcontains(a, "*")])
    ])
    error_message = "no wildcard actions"
  }
  assert {
    condition = alltrue([
      for s in jsondecode(aws_iam_role_policy.deploy.policy).Statement :
      s.Resource != "*" || toset(flatten([s.Action])) == toset(["ecr:GetAuthorizationToken", "ecs:RegisterTaskDefinition", "ecs:DescribeTaskDefinition"])
    ])
    error_message = "Resource \"*\" only for actions without resource-level permissions"
  }
  assert {
    condition     = jsondecode(aws_iam_role.deploy.assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"] == "repo:example/shortener:environment:staging"
    error_message = "trust is pinned to the repository and GitHub environment"
  }
  assert {
    condition     = one([for s in jsondecode(aws_iam_role_policy.deploy.policy).Statement : s.Condition.StringEquals["iam:PassedToService"] if s.Sid == "PassOnlyOurRoles"]) == "ecs-tasks.amazonaws.com"
    error_message = "PassRole only to ECS tasks"
  }
  assert {
    condition     = toset(one([for s in jsondecode(aws_iam_role_policy.deploy.policy).Statement : s.Resource if s.Sid == "RunOneOffTasks"])) == toset(["arn:aws:ecs:us-east-1:123456789012:task-definition/shortener-test-migrate:*", "arn:aws:ecs:us-east-1:123456789012:task-definition/shortener-test-db-bootstrap:*"])
    error_message = "RunTask only on the two one-off families"
  }
}

run "existing_oidc_provider" {
  command = apply
  variables {
    github_oidc_provider_arn = "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com"
  }

  assert {
    condition     = length(aws_iam_openid_connect_provider.github) == 0
    error_message = "reuse an existing provider instead of creating a second one"
  }
}
```

- [ ] **Step 2: Run and watch it fail**

Run: `terraform -chdir=terraform/modules/ci-deploy-role init -backend=false && terraform -chdir=terraform/modules/ci-deploy-role test`
Expected: FAIL (no configuration).

- [ ] **Step 3: Implement**

`main.tf`:
```hcl
data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

locals {
  create_provider = var.github_oidc_provider_arn == null
  provider_arn    = local.create_provider ? aws_iam_openid_connect_provider.github[0].arn : var.github_oidc_provider_arn
  cluster_name    = element(split("/", var.ecs_cluster_arn), 1)
  ecs_arn_prefix  = "arn:aws:ecs:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}"
}

resource "aws_iam_openid_connect_provider" "github" {
  count          = local.create_provider ? 1 : 0
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  tags           = var.tags
}

resource "aws_iam_role" "deploy" {
  name                 = "${var.name_prefix}-github-deploy"
  max_session_duration = 3600
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = local.provider_arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = "repo:${var.github_repository}:environment:${var.environment_name}"
        }
      }
    }]
  })
  tags = var.tags
}

resource "aws_iam_role_policy" "deploy" {
  name = "deploy"
  role = aws_iam_role.deploy.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "NoResourceLevelPermissions" # these three actions only accept Resource "*"
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken", "ecs:RegisterTaskDefinition", "ecs:DescribeTaskDefinition"]
        Resource = "*"
      },
      {
        Sid      = "PushImages"
        Effect   = "Allow"
        Action   = ["ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
        Resource = var.ecr_repository_arns
      },
      {
        Sid       = "RunOneOffTasks"
        Effect    = "Allow"
        Action    = ["ecs:RunTask"]
        Resource  = [for f in values(var.run_task_families) : "${local.ecs_arn_prefix}:task-definition/${f}:*"]
        Condition = { ArnEquals = { "ecs:cluster" = var.ecs_cluster_arn } }
      },
      {
        Sid      = "WatchTasks"
        Effect   = "Allow"
        Action   = ["ecs:DescribeTasks", "ecs:StopTask"]
        Resource = ["${local.ecs_arn_prefix}:task/${local.cluster_name}/*"]
      },
      {
        Sid      = "RollOutServices"
        Effect   = "Allow"
        Action   = ["ecs:UpdateService", "ecs:DescribeServices"]
        Resource = values(var.service_arns)
      },
      {
        Sid       = "PassOnlyOurRoles"
        Effect    = "Allow"
        Action    = ["iam:PassRole"]
        Resource  = var.pass_role_arns
        Condition = { StringEquals = { "iam:PassedToService" = "ecs-tasks.amazonaws.com" } }
      },
    ]
  })
}
```
Write `variables.tf` from Interfaces. `outputs.tf`: `role_arn = aws_iam_role.deploy.arn`, `oidc_provider_arn = local.provider_arn`.

- [ ] **Step 4: Run the tests**

Run: `terraform -chdir=terraform/modules/ci-deploy-role test`
Expected: `2 passed, 0 failed`.

- [ ] **Step 5: Gate and commit**

```bash
make tf-check && /usr/bin/git add terraform/modules/ci-deploy-role && /usr/bin/git commit -m "feat(terraform): GitHub OIDC deploy role scoped to this stack (no wildcard actions)

Co-Authored-By: <implementing model> <noreply@anthropic.com>"
```

---

### Task 10: `stack` module and the `staging` / `prod` roots

**Files:**
- Create:
  - the module: `terraform/modules/stack/{versions.tf,variables.tf,main.tf,services.tf,outputs.tf}`, `terraform/modules/stack/tests/stack.tftest.hcl`
  - shared test fixtures: `terraform/testing/aws/aws.tfmock.hcl`
  - each root: `terraform/envs/{staging,prod}/{versions.tf,providers.tf,backend.tf,backend.hcl.example,variables.tf,main.tf,outputs.tf,terraform.tfvars.example,.terraform.lock.hcl}` and `terraform/envs/{staging,prod}/tests/<env>.tftest.hcl`
- Modify:
  - `terraform/modules/data/outputs.tf`: add `multi_az` and `deletion_protection`, read from `aws_db_instance.this`
  - `terraform/modules/service/outputs.tf`: add `container_stop_timeout = jsondecode(aws_ecs_task_definition.this.container_definitions)[0].stopTimeout` and `scaling = one([for t in aws_appautoscaling_target.this : { min = t.min_capacity, max = t.max_capacity }])`

**Interfaces:**
- Consumes: every module from Tasks 1–9 with the inputs and outputs given there.
- Produces: the `stack` module.
  - **Inputs:**
    - `environment_name`
    - `domain`, `route53_zone_id`, `vpc_id`, `private_subnet_ids`, `public_subnet_ids`, `ecs_cluster_arn`
    - `github_repository`, `github_oidc_provider_arn` (default null), `admin_cidrs` (default `[]`)
    - `image_tag`
    - `canary_link_code` (string, required): the generated short code of the dedicated canary link. The API always generates codes, so the link is created once per environment and its code is set here (runbook in Task 12).
    - `sizes` (object below)
  - **Outputs:**
    - `hostnames`, `alb_dns_name`, `ecr_repository_urls`, `deploy_role_arn`, `alarm_topic_arn`, `canary_name`
    - `one_off_tasks`: a map of `{ family, security_group_id }` for `db_bootstrap` and `migrate`
    - `private_subnet_ids`
    - `service_config`: per service, the env-var names and values and the secret env-var names with their secret ARNs. These are ARNs only, never values. It's useful for review and for tests.
    - `service_runtime`: per service, `{ stop_timeout, scaling }`, read from the real resources
    - `db`: `{ multi_az, deletion_protection }`, read from the real resource
- **The roots:** `envs/staging` and `envs/prod` take the platform inputs plus `aws_region` (default `us-east-1`) and pass them to `stack` with their own `sizes`.

`sizes` type:
```hcl
object({
  db_instance_class        = string
  db_multi_az              = bool
  db_backup_retention_days = number
  deletion_protection      = bool
  log_retention_days       = number
  api                      = object({ cpu = number, memory = number, min = number, max = number })
  admin                    = object({ cpu = number, memory = number, count = number })
  keycloak                 = object({ cpu = number, memory = number, count = number })
  processor                = object({ cpu = number, memory = number, min = number, max = number })
})
```

| sizes | staging | prod |
|---|---|---|
| RDS | `db.t4g.micro`, single-AZ, 1-day backups, no deletion protection | `db.t4g.small`, Multi-AZ, 7-day backups, deletion protection |
| logs | 7 days | 30 days |
| api | 256/512, 1–1 | 512/1024, 2–6 |
| admin | 256/512, 1 | 256/512, 2 |
| keycloak | 512/1024, 1 | 1024/2048, 2 |
| processor | 256/512, 1–1 | 256/512, 1–4 |

- [ ] **Step 1: Shared mock fixtures**

`terraform/testing/aws/aws.tfmock.hcl` (used by the stack and environment tests through `mock_provider "aws" { source = "../../testing/aws" }`):
```hcl
mock_data "aws_region" {
  defaults = { region = "us-east-1", name = "us-east-1" }
}
mock_data "aws_caller_identity" {
  defaults = { account_id = "123456789012" }
}
mock_resource "aws_iam_role" {
  defaults = { arn = "arn:aws:iam::123456789012:role/mock" }
}
mock_resource "aws_iam_openid_connect_provider" {
  defaults = { arn = "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com" }
}
mock_resource "aws_cloudwatch_log_group" {
  defaults = { arn = "arn:aws:logs:us-east-1:123456789012:log-group:/mock" }
}
mock_resource "aws_sqs_queue" {
  defaults = { arn = "arn:aws:sqs:us-east-1:123456789012:mock" }
}
mock_resource "aws_ecr_repository" {
  defaults = { arn = "arn:aws:ecr:us-east-1:123456789012:repository/mock", repository_url = "123456789012.dkr.ecr.us-east-1.amazonaws.com/mock" }
}
mock_resource "aws_sns_topic" {
  defaults = { arn = "arn:aws:sns:us-east-1:123456789012:mock" }
}
mock_resource "aws_lb" {
  defaults = { arn = "arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/mock/abc", arn_suffix = "app/mock/abc", dns_name = "mock-123.us-east-1.elb.amazonaws.com", zone_id = "Z35SXDOTRQ7X7K" }
}
mock_resource "aws_lb_listener" {
  defaults = { arn = "arn:aws:elasticloadbalancing:us-east-1:123456789012:listener/app/mock/abc/def" }
}
mock_resource "aws_lb_target_group" {
  defaults = { arn = "arn:aws:elasticloadbalancing:us-east-1:123456789012:targetgroup/mock/abc", arn_suffix = "targetgroup/mock/abc" }
}
mock_resource "aws_ecs_service" {
  defaults = { id = "arn:aws:ecs:us-east-1:123456789012:service/platform/mock" }
}
mock_resource "aws_db_instance" {
  defaults = {
    address = "mock.abc123.us-east-1.rds.amazonaws.com"
    master_user_secret = [{
      secret_arn    = "arn:aws:secretsmanager:us-east-1:123456789012:secret:rds!db-mock"
      kms_key_id    = "alias/aws/secretsmanager"
      secret_status = "active"
    }]
  }
}
mock_resource "aws_acm_certificate" {
  defaults = {
    arn = "arn:aws:acm:us-east-1:123456789012:certificate/mock"
    domain_validation_options = [
      { domain_name = "go.example.test", resource_record_name = "_a.go.example.test.", resource_record_type = "CNAME", resource_record_value = "_x.acm-validations.aws." },
      { domain_name = "admin.example.test", resource_record_name = "_b.admin.example.test.", resource_record_type = "CNAME", resource_record_value = "_y.acm-validations.aws." },
      { domain_name = "auth.example.test", resource_record_name = "_c.auth.example.test.", resource_record_type = "CNAME", resource_record_value = "_z.acm-validations.aws." },
    ]
  }
}
```
Every stack and environment test uses `domain = "example.test"` so the ACM fixture matches.

- [ ] **Step 2: Write the failing stack test**

`terraform/modules/stack/tests/stack.tftest.hcl`:
```hcl
mock_provider "aws" {
  source = "../../testing/aws"
}

variables {
  environment_name   = "staging"
  domain             = "example.test"
  route53_zone_id    = "Z0123456789ABC"
  vpc_id             = "vpc-0123456789abcdef0"
  private_subnet_ids = ["subnet-priv-a", "subnet-priv-b"]
  public_subnet_ids  = ["subnet-pub-a", "subnet-pub-b"]
  ecs_cluster_arn    = "arn:aws:ecs:us-east-1:123456789012:cluster/platform"
  github_repository  = "example/shortener"
  admin_cidrs        = ["203.0.113.0/24"]
  image_tag          = "abc123"
  canary_link_code   = "aZ3kQ9x"
  sizes = {
    db_instance_class        = "db.t4g.micro"
    db_multi_az              = false
    db_backup_retention_days = 1
    deletion_protection      = false
    log_retention_days       = 7
    api                      = { cpu = 256, memory = 512, min = 1, max = 1 }
    admin                    = { cpu = 256, memory = 512, count = 1 }
    keycloak                 = { cpu = 512, memory = 1024, count = 1 }
    processor                = { cpu = 256, memory = 512, min = 1, max = 1 }
  }
}

run "wiring" {
  command = apply

  assert {
    condition     = output.service_config.api.env_vars.OIDC_ISSUER == "https://auth.example.test/realms/shortener" && output.service_config.api.env_vars.OIDC_INTERNAL_URL == "https://auth.example.test/realms/shortener"
    error_message = "the API validates tokens against the public Keycloak issuer"
  }
  assert {
    condition     = output.service_config.admin.env_vars.COOKIE_SECURE == "true" && !contains(keys(output.service_config.admin.env_vars), "OBSERVABILITY_URL")
    error_message = "admin: secure cookies; no Grafana link on AWS (CloudWatch-only, spec T2)"
  }
  assert {
    condition     = !contains(keys(output.service_config.api.env_vars), "SQS_ENDPOINT_URL") && !contains(keys(output.service_config.processor.env_vars), "SQS_ENDPOINT_URL")
    error_message = "SQS_ENDPOINT_URL stays unset on AWS (parent spec §11)"
  }
  assert {
    condition     = output.service_config.processor.secrets.DATABASE_URL == module.secrets.db_secret_arns["processor_user"] && output.service_config.api.secrets.DATABASE_URL == module.secrets.db_secret_arns["api_user"]
    error_message = "each service reads its own database role's secret"
  }
  assert {
    condition     = toset(keys(output.service_config.keycloak.secrets)) == toset(["KC_DB_URL", "KC_DB_PASSWORD", "KC_BOOTSTRAP_ADMIN_PASSWORD", "SHORTENER_ADMIN_CLIENT_SECRET"])
    error_message = "Keycloak gets its DB credentials, the bootstrap admin password and the admin client secret"
  }
  assert {
    condition     = output.service_config.keycloak.env_vars.ADMIN_PUBLIC_BASE_URL == "https://admin.example.test"
    error_message = "the realm's admin redirect URI resolves to the AWS admin hostname"
  }
  assert {
    condition     = output.service_runtime.processor.stop_timeout == 60
    error_message = "processor stopTimeout 60 s (25 s grace + flush; parent spec IX)"
  }
  assert {
    condition     = toset(keys(output.one_off_tasks)) == toset(["db_bootstrap", "migrate"])
    error_message = "the pipeline needs both one-off task families and their security groups"
  }
  assert {
    condition     = output.canary_name != null
    error_message = "every environment gets the outside-in redirect canary"
  }
}
```

- [ ] **Step 3: Run and watch it fail**

Run: `terraform -chdir=terraform/modules/stack init -backend=false && terraform -chdir=terraform/modules/stack test`
Expected: FAIL (no configuration).

- [ ] **Step 4: Implement `stack/main.tf`** (shared modules, data, one-off tasks, alarms, deploy role)

```hcl
data "aws_region" "current" {}

locals {
  name_prefix  = "shortener-${var.environment_name}"
  cluster_name = element(split("/", var.ecs_cluster_arn), 1)
  images       = { for k, url in module.ecr.repository_urls : k => "${url}:${var.image_tag}" }
}

module "queue" {
  source      = "../queue"
  name_prefix = local.name_prefix
}

module "ecr" {
  source      = "../ecr"
  name_prefix = local.name_prefix
}

module "edge" {
  source              = "../edge"
  name_prefix         = local.name_prefix
  domain              = var.domain
  route53_zone_id     = var.route53_zone_id
  vpc_id              = var.vpc_id
  public_subnet_ids   = var.public_subnet_ids
  deletion_protection = var.sizes.deletion_protection
}

module "data" {
  source                = "../data"
  name_prefix           = local.name_prefix
  vpc_id                = var.vpc_id
  subnet_ids            = var.private_subnet_ids
  instance_class        = var.sizes.db_instance_class
  multi_az              = var.sizes.db_multi_az
  backup_retention_days = var.sizes.db_backup_retention_days
  deletion_protection   = var.sizes.deletion_protection
  client_security_group_ids = {
    api          = module.api.security_group_id
    admin        = module.admin.security_group_id
    keycloak     = module.keycloak.security_group_id
    processor    = module.processor.security_group_id
    migrate      = module.migrate.security_group_id
    db_bootstrap = module.db_bootstrap.security_group_id
  }
}

module "secrets" {
  source      = "../secrets"
  name_prefix = local.name_prefix
  db_address  = module.data.address
  db_port     = module.data.port
}

module "db_bootstrap" {
  source             = "../task"
  name_prefix        = local.name_prefix
  name               = "db-bootstrap"
  environment_name   = var.environment_name
  vpc_id             = var.vpc_id
  image              = "public.ecr.aws/docker/library/postgres:16.10-alpine"
  command            = ["sh", "-c", file("${path.module}/../../scripts/db-bootstrap.sh")]
  log_retention_days = var.sizes.log_retention_days
  env_vars           = { PGHOST = module.data.address, PGPORT = tostring(module.data.port) }
  secrets = {
    PGUSER                  = { arn = module.data.master_secret_arn, key = "username" }
    PGPASSWORD              = { arn = module.data.master_secret_arn, key = "password" }
    MIGRATOR_PASSWORD       = { arn = module.secrets.db_secret_arns["migrator"], key = "password" }
    API_USER_PASSWORD       = { arn = module.secrets.db_secret_arns["api_user"], key = "password" }
    PROCESSOR_USER_PASSWORD = { arn = module.secrets.db_secret_arns["processor_user"], key = "password" }
    ADMIN_USER_PASSWORD     = { arn = module.secrets.db_secret_arns["admin_user"], key = "password" }
    KEYCLOAK_PASSWORD       = { arn = module.secrets.db_secret_arns["keycloak"], key = "password" }
  }
}

module "migrate" {
  source             = "../task"
  name_prefix        = local.name_prefix
  name               = "migrate"
  environment_name   = var.environment_name
  vpc_id             = var.vpc_id
  image              = local.images["api"]
  command            = ["alembic", "upgrade", "head"]
  log_retention_days = var.sizes.log_retention_days
  secrets = {
    MIGRATOR_DATABASE_URL = { arn = module.secrets.db_secret_arns["migrator"], key = "url" }
  }
}

module "alarms" {
  source                 = "../alarms"
  name_prefix            = local.name_prefix
  queue_name             = module.queue.queue_name
  dlq_name               = module.queue.dlq_name
  alb_arn_suffix         = module.edge.alb_arn_suffix
  db_instance_identifier = module.data.instance_identifier
  ecs_cluster_name       = local.cluster_name
  processor_service_name = module.processor.service_name
  canary_url             = "https://${module.edge.hostnames.api}/${var.canary_link_code}"
  target_group_arn_suffixes = {
    api      = module.api.target_group_arn_suffix
    admin    = module.admin.target_group_arn_suffix
    keycloak = module.keycloak.target_group_arn_suffix
  }
}

module "ci_deploy_role" {
  source                   = "../ci-deploy-role"
  name_prefix              = local.name_prefix
  environment_name         = var.environment_name
  github_repository        = var.github_repository
  github_oidc_provider_arn = var.github_oidc_provider_arn
  ecr_repository_arns      = values(module.ecr.repository_arns)
  ecs_cluster_arn          = var.ecs_cluster_arn
  service_arns = {
    api       = module.api.service_arn
    admin     = module.admin.service_arn
    keycloak  = module.keycloak.service_arn
    processor = module.processor.service_arn
  }
  run_task_families = {
    db_bootstrap = module.db_bootstrap.task_definition_family
    migrate      = module.migrate.task_definition_family
  }
  pass_role_arns = flatten([
    for m in [module.api, module.admin, module.keycloak, module.processor, module.migrate, module.db_bootstrap] :
    [m.task_role_arn, m.execution_role_arn]
  ])
}
```

- [ ] **Step 5: Implement `stack/services.tf`** (the four services; `local.svc` is also the `service_config` output)

```hcl
locals {
  issuer = "https://${module.edge.hostnames.auth}/realms/shortener"
  common_env = {
    DEPLOYMENT_ENVIRONMENT = var.environment_name
    SERVICE_VERSION        = var.image_tag
    AWS_REGION             = data.aws_region.current.region
  }
  db  = module.secrets.db_secret_arns
  app = module.secrets.app_secret_arns

  svc = {
    api = {
      env_vars = merge(local.common_env, {
        PUBLIC_BASE_URL         = "https://${module.edge.hostnames.api}"
        OIDC_ISSUER             = local.issuer
        OIDC_INTERNAL_URL       = local.issuer
        CLICK_EVENTS_QUEUE_NAME = module.queue.queue_name
      })
      secrets = { DATABASE_URL = local.db["api_user"] }
    }
    admin = {
      env_vars = merge(local.common_env, {
        PUBLIC_BASE_URL   = "https://${module.edge.hostnames.admin}"
        API_BASE_URL      = "https://${module.edge.hostnames.api}"
        OIDC_INTERNAL_URL = local.issuer
        OIDC_CLIENT_ID    = "shortener-admin"
        COOKIE_SECURE     = "true"
      })
      secrets = {
        DATABASE_URL       = local.db["admin_user"]
        OIDC_CLIENT_SECRET = local.app["admin_client_secret"]
        COOKIE_SECRET      = local.app["admin_cookie_secret"]
      }
    }
    keycloak = {
      env_vars = {
        KC_HOSTNAME                 = "https://${module.edge.hostnames.auth}"
        KC_PROXY_HEADERS            = "xforwarded"
        KC_HTTP_ENABLED             = "true"
        KC_DB_USERNAME              = "keycloak"
        KC_BOOTSTRAP_ADMIN_USERNAME = "kcadmin"
        ADMIN_PUBLIC_BASE_URL       = "https://${module.edge.hostnames.admin}"
      }
      secrets = {
        KC_DB_URL                     = local.db["keycloak"]
        KC_DB_PASSWORD                = local.db["keycloak"]
        KC_BOOTSTRAP_ADMIN_PASSWORD   = local.app["keycloak_admin_password"]
        SHORTENER_ADMIN_CLIENT_SECRET = local.app["admin_client_secret"]
      }
    }
    processor = {
      env_vars = merge(local.common_env, {
        CLICK_EVENTS_QUEUE_NAME = module.queue.queue_name
        CLICK_EVENTS_DLQ_NAME   = module.queue.dlq_name
      })
      secrets = { DATABASE_URL = local.db["processor_user"] }
    }
  }

  # JSON key inside each secret, by env var name.
  secret_keys = {
    DATABASE_URL                  = "url"
    KC_DB_URL                     = "url"
    KC_DB_PASSWORD                = "password"
    OIDC_CLIENT_SECRET            = "value"
    COOKIE_SECRET                 = "value"
    KC_BOOTSTRAP_ADMIN_PASSWORD   = "value"
    SHORTENER_ADMIN_CLIENT_SECRET = "value"
  }
  secret_refs = { for name, cfg in local.svc : name => { for env, arn in cfg.secrets : env => { arn = arn, key = local.secret_keys[env] } } }

  service_common = {
    name_prefix        = local.name_prefix
    environment_name   = var.environment_name
    ecs_cluster_arn    = var.ecs_cluster_arn
    vpc_id             = var.vpc_id
    subnet_ids         = var.private_subnet_ids
    log_retention_days = var.sizes.log_retention_days
  }
  alb_common = {
    listener_arn      = module.edge.https_listener_arn
    security_group_id = module.edge.alb_security_group_id
  }
  py_health = "import urllib.request; urllib.request.urlopen('http://127.0.0.1:%d/healthz', timeout=2)"
}

module "api" {
  source               = "../service"
  name                 = "api"
  name_prefix          = local.service_common.name_prefix
  environment_name     = local.service_common.environment_name
  ecs_cluster_arn      = local.service_common.ecs_cluster_arn
  vpc_id               = local.service_common.vpc_id
  subnet_ids           = local.service_common.subnet_ids
  log_retention_days   = local.service_common.log_retention_days
  image                = local.images["api"]
  cpu                  = var.sizes.api.cpu
  memory               = var.sizes.api.memory
  desired_count        = var.sizes.api.min
  container_port       = 8000
  env_vars             = local.svc.api.env_vars
  secrets              = local.secret_refs.api
  health_check_command = ["CMD", "python", "-c", format(local.py_health, 8000)]
  alb                  = merge(local.alb_common, { host = module.edge.hostnames.api, priority = 100, health_path = "/healthz" })
  autoscaling          = { min = var.sizes.api.min, max = var.sizes.api.max, cpu_target = 60 }
  task_role_statements = [{ Effect = "Allow", Action = ["sqs:SendMessage", "sqs:GetQueueUrl"], Resource = [module.queue.queue_arn] }]
}

module "admin" {
  source               = "../service"
  name                 = "admin"
  name_prefix          = local.service_common.name_prefix
  environment_name     = local.service_common.environment_name
  ecs_cluster_arn      = local.service_common.ecs_cluster_arn
  vpc_id               = local.service_common.vpc_id
  subnet_ids           = local.service_common.subnet_ids
  log_retention_days   = local.service_common.log_retention_days
  image                = local.images["admin"]
  cpu                  = var.sizes.admin.cpu
  memory               = var.sizes.admin.memory
  desired_count        = var.sizes.admin.count
  container_port       = 8001
  env_vars             = local.svc.admin.env_vars
  secrets              = local.secret_refs.admin
  health_check_command = ["CMD", "python", "-c", format(local.py_health, 8001)]
  alb                  = merge(local.alb_common, { host = module.edge.hostnames.admin, priority = 200, health_path = "/healthz" })
}

module "keycloak" {
  source             = "../service"
  name               = "keycloak"
  name_prefix        = local.service_common.name_prefix
  environment_name   = local.service_common.environment_name
  ecs_cluster_arn    = local.service_common.ecs_cluster_arn
  vpc_id             = local.service_common.vpc_id
  subnet_ids         = local.service_common.subnet_ids
  log_retention_days = local.service_common.log_retention_days
  image              = local.images["keycloak"]
  cpu                = var.sizes.keycloak.cpu
  memory             = var.sizes.keycloak.memory
  desired_count      = var.sizes.keycloak.count
  container_port     = 8080
  env_vars           = local.svc.keycloak.env_vars
  secrets            = local.secret_refs.keycloak
  alb = merge(local.alb_common, {
    host             = module.edge.hostnames.auth
    priority         = 300
    health_path      = "/health/ready"
    health_port      = 9000
    restricted_paths = { paths = ["/admin/*"], allowed_cidrs = var.admin_cidrs }
  })
}

module "processor" {
  source               = "../service"
  name                 = "click-processor"
  name_prefix          = local.service_common.name_prefix
  environment_name     = local.service_common.environment_name
  ecs_cluster_arn      = local.service_common.ecs_cluster_arn
  vpc_id               = local.service_common.vpc_id
  subnet_ids           = local.service_common.subnet_ids
  log_retention_days   = local.service_common.log_retention_days
  image                = local.images["click-processor"]
  cpu                  = var.sizes.processor.cpu
  memory               = var.sizes.processor.memory
  desired_count        = var.sizes.processor.min
  env_vars             = local.svc.processor.env_vars
  secrets              = local.secret_refs.processor
  health_check_command = ["CMD", "python", "-c", format(local.py_health, 8002)]
  stop_timeout         = 60
  autoscaling = {
    min = var.sizes.processor.min
    max = var.sizes.processor.max
    sqs = { queue_name = module.queue.queue_name, scale_out_at = 100, scale_in_at = 10 }
  }
  task_role_statements = [
    {
      Effect   = "Allow"
      Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:ChangeMessageVisibility", "sqs:GetQueueUrl", "sqs:GetQueueAttributes"]
      Resource = [module.queue.queue_arn]
    },
    {
      Effect   = "Allow"
      Action   = ["sqs:GetQueueAttributes", "sqs:GetQueueUrl"]
      Resource = [module.queue.dlq_arn]
    },
  ]
}
```
In `py_health`, `%d` is a `format()` verb, so `format(local.py_health, 8000)` gives the same command the compose healthchecks use.

- [ ] **Step 6: Implement `stack/variables.tf`, `stack/versions.tf`, `stack/outputs.tf`**

`variables.tf`: every input from Interfaces, with a description; `sizes` uses the type above.

`outputs.tf`:
```hcl
output "hostnames" {
  description = "Public hostnames."
  value       = module.edge.hostnames
}
output "alb_dns_name" {
  description = "ALB DNS name."
  value       = module.edge.alb_dns_name
}
output "ecr_repository_urls" {
  description = "ECR repository URLs by image."
  value       = module.ecr.repository_urls
}
output "deploy_role_arn" {
  description = "Role the CI pipeline assumes via GitHub OIDC."
  value       = module.ci_deploy_role.role_arn
}
output "alarm_topic_arn" {
  description = "SNS topic for alarms (subscribe on-call here)."
  value       = module.alarms.topic_arn
}
output "canary_name" {
  description = "Synthetics canary that HEADs the canary short link every minute."
  value       = module.alarms.canary_name
}
output "private_subnet_ids" {
  description = "Subnets for run-task network configuration."
  value       = var.private_subnet_ids
}
output "one_off_tasks" {
  description = "One-off task families and security groups for the deploy pipeline (bootstrap, then migrate)."
  value = {
    db_bootstrap = { family = module.db_bootstrap.task_definition_family, security_group_id = module.db_bootstrap.security_group_id }
    migrate      = { family = module.migrate.task_definition_family, security_group_id = module.migrate.security_group_id }
  }
}
output "service_config" {
  description = "Per-service env vars and secret env var => secret ARN (ARNs only, never values)."
  value       = local.svc
}
output "service_runtime" {
  description = "Per-service stopTimeout and scaling bounds, read from the planned resources."
  value = {
    for name, m in { api = module.api, admin = module.admin, keycloak = module.keycloak, processor = module.processor } :
    name => { stop_timeout = m.container_stop_timeout, scaling = m.scaling }
  }
}
output "db" {
  description = "RDS protection settings, read from the planned instance."
  value       = { multi_az = module.data.multi_az, deletion_protection = module.data.deletion_protection }
}
```

- [ ] **Step 7: Run the stack test**

Run: `terraform -chdir=terraform/modules/stack init -backend=false && terraform -chdir=terraform/modules/stack test`
Expected: `1 passed, 0 failed`. If Terraform reports a dependency cycle, find the edge with `terraform -chdir=terraform/modules/stack graph`. The design avoids cycles: the RDS ingress rules depend on the service security groups, and the services depend only on `aws_secretsmanager_secret` ARNs, never on the secret versions. Fix it by moving the offending reference, and record what you did.

- [ ] **Step 8: Write the failing environment tests**

`terraform/envs/prod/tests/prod.tftest.hcl`:
```hcl
mock_provider "aws" {
  source = "../../testing/aws"
}

variables {
  domain             = "example.test"
  route53_zone_id    = "Z0123456789ABC"
  vpc_id             = "vpc-0123456789abcdef0"
  private_subnet_ids = ["subnet-priv-a", "subnet-priv-b"]
  public_subnet_ids  = ["subnet-pub-a", "subnet-pub-b"]
  ecs_cluster_arn    = "arn:aws:ecs:us-east-1:123456789012:cluster/platform"
  github_repository  = "example/shortener"
  image_tag          = "abc123"
  canary_link_code   = "aZ3kQ9x"
}

run "prod_sizes" {
  command = apply

  assert {
    condition     = module.stack.db.multi_az && module.stack.db.deletion_protection
    error_message = "prod RDS: Multi-AZ with deletion protection"
  }
  assert {
    condition     = module.stack.service_runtime.processor.stop_timeout == 60 && module.stack.service_runtime.processor.scaling == { min = 1, max = 4 }
    error_message = "processor: stopTimeout 60, scaling 1-4"
  }
  assert {
    condition     = module.stack.service_runtime.api.scaling == { min = 2, max = 6 }
    error_message = "api: 2-6 tasks in prod"
  }
}
```

`terraform/envs/staging/tests/staging.tftest.hcl`: the same file, except the run is named `staging_sizes` and asserts `!module.stack.db.multi_az && !module.stack.db.deletion_protection`, `module.stack.service_runtime.processor.scaling == { min = 1, max = 1 }` and `module.stack.service_runtime.api.scaling == { min = 1, max = 1 }`.

- [ ] **Step 9: Implement the two roots**

`terraform/envs/prod/versions.tf`: the standard `versions.tf`.

`terraform/envs/prod/backend.tf`:
```hcl
# Partial configuration: supply bucket/key/region with -backend-config at init. Never initialized in this
# repo (spec T1); checks use `terraform init -backend=false`.
terraform {
  backend "s3" {
    use_lockfile = true
    encrypt      = true
  }
}
```

`terraform/envs/prod/providers.tf`:
```hcl
provider "aws" {
  region = var.aws_region
  default_tags {
    tags = { Project = "shortener", Environment = "prod", ManagedBy = "terraform" }
  }
}
```

`terraform/envs/prod/variables.tf`: `aws_region` (default `"us-east-1"`), `domain`, `route53_zone_id`, `vpc_id`, `private_subnet_ids`, `public_subnet_ids`, `ecs_cluster_arn`, `github_repository`, `github_oidc_provider_arn` (default null), `admin_cidrs` (default `[]`), `image_tag`, `canary_link_code` (required, no default). Each has a description and a type.

`terraform/envs/prod/main.tf`:
```hcl
module "stack" {
  source                   = "../../modules/stack"
  environment_name         = "prod"
  domain                   = var.domain
  route53_zone_id          = var.route53_zone_id
  vpc_id                   = var.vpc_id
  private_subnet_ids       = var.private_subnet_ids
  public_subnet_ids        = var.public_subnet_ids
  ecs_cluster_arn          = var.ecs_cluster_arn
  github_repository        = var.github_repository
  github_oidc_provider_arn = var.github_oidc_provider_arn
  admin_cidrs              = var.admin_cidrs
  image_tag                = var.image_tag
  canary_link_code         = var.canary_link_code
  sizes = {
    db_instance_class        = "db.t4g.small"
    db_multi_az              = true
    db_backup_retention_days = 7
    deletion_protection      = true
    log_retention_days       = 30
    api                      = { cpu = 512, memory = 1024, min = 2, max = 6 }
    admin                    = { cpu = 256, memory = 512, count = 2 }
    keycloak                 = { cpu = 1024, memory = 2048, count = 2 }
    processor                = { cpu = 256, memory = 512, min = 1, max = 4 }
  }
}
```

`terraform/envs/prod/outputs.tf`: pass through `hostnames`, `alb_dns_name`, `ecr_repository_urls`, `deploy_role_arn`, `alarm_topic_arn`, `one_off_tasks` and `private_subnet_ids` from `module.stack`, each with a description.

`terraform/envs/prod/terraform.tfvars.example`:
```hcl
# Example only: copy to terraform.tfvars with the platform team's real IDs. Never commit real values.
aws_region         = "us-east-1"
domain             = "shortener.example.com"
route53_zone_id    = "Z0123456789EXAMPLE"
vpc_id             = "vpc-0123456789abcdef0"
private_subnet_ids = ["subnet-0aaaaaaaaaaaaaaa1", "subnet-0aaaaaaaaaaaaaaa2"]
public_subnet_ids  = ["subnet-0bbbbbbbbbbbbbbb1", "subnet-0bbbbbbbbbbbbbbb2"]
ecs_cluster_arn    = "arn:aws:ecs:us-east-1:123456789012:cluster/platform"
github_repository  = "example-org/platform-url-shortner"
admin_cidrs        = ["203.0.113.0/24"]
image_tag          = "set-by-the-pipeline"
canary_link_code   = "aZ3kQ9x" # generated code of the dedicated canary link (terraform/README.md runbook)
```

`terraform/envs/prod/backend.hcl.example` (spec T10; the bucket is platform-owned and versioned, with native S3 locking):
```hcl
# terraform init -backend-config=backend.hcl   (copy from this example; real values come from the platform team)
bucket     = "example-platform-terraform-state"
key        = "shortener/prod/terraform.tfstate"
region     = "us-east-1"
kms_key_id = "alias/example-platform-terraform-state"
# use_lockfile = true and encrypt = true are set in backend.tf.
# A platform still on DynamoDB locking would add: dynamodb_table = "example-platform-terraform-locks"
```

`terraform/envs/staging/*`: the same files with `Environment = "staging"`, `environment_name = "staging"`, the staging sizes from the table, `domain = "staging.shortener.example.com"` in the tfvars example, and `key = "shortener/staging/terraform.tfstate"` in `backend.hcl.example`.

Generate the committed lock files (this talks to the Terraform Registry only, never AWS):
```bash
for e in staging prod; do terraform -chdir=terraform/envs/$e init -backend=false && terraform -chdir=terraform/envs/$e providers lock -platform=darwin_arm64 -platform=linux_amd64; done
```

- [ ] **Step 10: Run every test and the full gate**

Run: `make tf-check`
Expected: exit 0. Every module's tests pass, plus `stack` (1), `staging` (1) and `prod` (1), and tflint and trivy are clean.

- [ ] **Step 11: Commit**

```bash
make tf-check && /usr/bin/git add terraform/testing terraform/modules/stack terraform/modules/data/outputs.tf terraform/modules/service/outputs.tf terraform/envs && /usr/bin/git commit -m "feat(terraform): stack module wiring every service, plus staging and prod roots

Co-Authored-By: <implementing model> <noreply@anthropic.com>"
```

---

### Task 11: Keycloak image for AWS (optimized build, realm baked in, no dev client)

**Files:**
- Create: `infra/keycloak/Dockerfile`, `tests/test_keycloak_image.py`
- Modify:
  - `infra/keycloak/realm-export.json`: the admin client's `redirectUris` and `webOrigins` use `${ADMIN_PUBLIC_BASE_URL}`
  - `docker-compose.yml`: the keycloak service gets `ADMIN_PUBLIC_BASE_URL: http://localhost:8001`
  - `.github/workflows/ci.yml`: add `infra/keycloak/Dockerfile` to the `images` matrix

**Interfaces:**
- Consumes: the Task 10 stack sets `ADMIN_PUBLIC_BASE_URL`, `SHORTENER_ADMIN_CLIENT_SECRET`, `KC_*` on the keycloak service.
- Produces: an image that runs `start --optimized --import-realm` with the `shortener` realm minus the `shortener-dev` client.

- [ ] **Step 1: Write the failing tests**

`tests/test_keycloak_image.py`:
```python
"""infra/keycloak/Dockerfile and the realm import: AWS-safe Keycloak (Terraform spec §6)."""

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = (REPO_ROOT / "infra/keycloak/Dockerfile").read_text()
REALM = json.loads((REPO_ROOT / "infra/keycloak/realm-export.json").read_text())
COMPOSE = (REPO_ROOT / "docker-compose.yml").read_text()


def client(client_id: str) -> dict:
    return next(c for c in REALM["clients"] if c["clientId"] == client_id)


def test_image_version_matches_local_compose():
    local = re.search(r"image: quay\.io/keycloak/keycloak:(\S+)", COMPOSE).group(1)
    assert set(re.findall(r"FROM quay\.io/keycloak/keycloak:(\S+)", DOCKERFILE)) == {local}


def test_optimized_postgres_build_with_health():
    assert "kc.sh build" in DOCKERFILE
    assert "KC_DB=postgres" in DOCKERFILE and "KC_HEALTH_ENABLED=true" in DOCKERFILE
    assert 'CMD ["start", "--optimized", "--import-realm"]' in DOCKERFILE


def test_dev_client_is_stripped_from_the_aws_image():
    assert 'select(.clientId == "shortener-dev")' in DOCKERFILE  # jq del(...) in the realm stage
    assert "realm-export.json" in DOCKERFILE


def test_admin_client_urls_come_from_the_environment():
    admin = client("shortener-admin")
    assert admin["redirectUris"] == ["${ADMIN_PUBLIC_BASE_URL}/auth/callback"]
    assert admin["webOrigins"] == ["${ADMIN_PUBLIC_BASE_URL}"]
    assert admin["secret"] == "${SHORTENER_ADMIN_CLIENT_SECRET}"


def test_local_compose_still_resolves_the_placeholder():
    keycloak_block = COMPOSE.split("\n  keycloak:\n", 1)[1].split("\n  keycloak-seed:", 1)[0]
    assert "ADMIN_PUBLIC_BASE_URL: http://localhost:8001" in keycloak_block
```

- [ ] **Step 2: Run and watch them fail**

Run: `uv run pytest tests/test_keycloak_image.py -q`
Expected: FAIL (`FileNotFoundError: infra/keycloak/Dockerfile`).

- [ ] **Step 3: Implement**

`infra/keycloak/Dockerfile` (build context: repo root, like the other images):
```dockerfile
# AWS Keycloak image (Terraform spec §6): optimized for Postgres, realm baked in, dev client removed.
FROM alpine:3.22 AS realm
RUN apk add --no-cache jq
COPY infra/keycloak/realm-export.json /realm.json
# shortener-dev allows password grants for local tests only; it must never exist in a deployed realm.
RUN jq 'del(.clients[] | select(.clientId == "shortener-dev"))' /realm.json > /realm-aws.json

FROM quay.io/keycloak/keycloak:26.4.0 AS builder
ENV KC_DB=postgres KC_HEALTH_ENABLED=true KC_METRICS_ENABLED=false
RUN /opt/keycloak/bin/kc.sh build

FROM quay.io/keycloak/keycloak:26.4.0
COPY --from=builder /opt/keycloak/ /opt/keycloak/
COPY --from=realm /realm-aws.json /opt/keycloak/data/import/realm-export.json
ENTRYPOINT ["/opt/keycloak/bin/kc.sh"]
CMD ["start", "--optimized", "--import-realm"]
```

In `infra/keycloak/realm-export.json`, change the `shortener-admin` client so that:
```json
      "redirectUris": ["${ADMIN_PUBLIC_BASE_URL}/auth/callback"],
      "webOrigins": ["${ADMIN_PUBLIC_BASE_URL}"],
```

In `docker-compose.yml`, add to the keycloak service's `environment:`:
```yaml
      ADMIN_PUBLIC_BASE_URL: http://localhost:8001   # realm import placeholder (admin redirect URI)
```

In `.github/workflows/ci.yml`, add `infra/keycloak/Dockerfile` to the `images` job's `dockerfile` matrix list.

- [ ] **Step 4: Run the tests, build the image, and re-verify local login**

```bash
uv run pytest tests/test_keycloak_image.py -q            # 5 passed
docker build -f infra/keycloak/Dockerfile -t shortener-keycloak:check .
docker run --rm --entrypoint sh shortener-keycloak:check -c 'grep -c shortener-dev /opt/keycloak/data/import/realm-export.json || true'   # 0
make up && make e2e                                      # admin login flow still works with the placeholder
```
Expected: 5 passed, the grep prints `0`, and e2e passes all 38 tests. The realm imports only on a fresh Keycloak database. If e2e fails on the admin redirect URI, the existing local realm predates the change. Delete and recreate only the Keycloak database by running `make down && make up` **only after asking the user**. Never run it unprompted.

- [ ] **Step 5: Gate and commit**

```bash
uv run ruff format . && make check && /usr/bin/git add infra/keycloak/Dockerfile infra/keycloak/realm-export.json docker-compose.yml .github/workflows/ci.yml tests/test_keycloak_image.py && /usr/bin/git commit -m "feat(keycloak): optimized AWS image with the realm baked in, dev client stripped; admin URLs from env

Co-Authored-By: <implementing model> <noreply@anthropic.com>"
```

---

### Task 12: Documentation and the state-safety guard

**Files:**
- Create: `terraform/README.md`
- Modify: `Makefile` (add a secret-in-state grep guard to `tf-check`), `docs/superpowers/specs/2026-10-01-url-shortener-design.md` (§2, §11, §12), `docs/superpowers/specs/2026-10-02-terraform-aws-design.md` (T8 exception note, test layout), `README.md`

**Interfaces:**
- Consumes: everything above.
- Produces: documentation, plus a guard that fails `make tf-check` if `secret_string =` or a `resource "random_password"` appears under `terraform/`.

- [ ] **Step 1: The guard (Review Focus 1)**

Add as the first command after the tool check in the `tf-check` recipe:
```make
	@! grep -rEn --include='*.tf' '(^|[^_])secret_string[[:space:]]*=|resource[[:space:]]+"random_password"' terraform || { echo "secret values must never reach state: use ephemeral random_password + secret_string_wo (spec T6)"; exit 1; }
```
Prove that it fires: temporarily add `secret_string = "x"` to `terraform/modules/secrets/main.tf`, run `make tf-check` (expect the guard's message and a non-zero exit), then revert the line with an Edit (never `git checkout`).

- [ ] **Step 2: `terraform/README.md`**

It contains these sections, using the exact facts from the spec and the code:
1. **What this is:** plan-ready Terraform for AWS, never applied (T1), and how to run `make tf-check`. Prerequisites: `brew install tfenv tflint trivy && tfenv install`.
2. **Architecture:** a Mermaid diagram covering Route 53 → ALB (go./admin./auth.) → ECS services (api, admin, keycloak) with ADOT sidecars, the processor ↔ SQS (+ DLQ), RDS, Secrets Manager, CloudWatch/X-Ray, and the SNS alarms.
3. **Platform inputs:** the table from spec §4.
4. **Layout:** modules → stack → envs; tests live beside each module; the shared mock fixtures are in `terraform/testing/aws`.
5. **Deploy sequence:** the steps from spec §8, as commands a pipeline would run (`aws ecs run-task` with the `one_off_tasks` family plus its security group and `private_subnet_ids`, then `aws ecs update-service`), run by the OIDC role from self-hosted runners.
6. **Secrets:** write-only values, how to rotate them (bump `secret_version`), and the RDS-managed master secret.
6a. **Terraform state (T10):** the platform-owned versioned S3 bucket, one key per environment, native S3 locking (DynamoDB fallback), and `terraform init -backend-config=backend.hcl`.
6b. **Canary link runbook:** after the first deploy of an environment, sign in as an admin and create a link to a stable target (for example `https://example.com/`) through the admin UI or `POST /api/v1/links`. Put its generated code in `canary_link_code` and apply. Never delete or block that link; if someone does, the canary pages, which is the intended behaviour.
7. **Observability:**
   - CloudWatch metrics (EMF namespace `Shortener`), X-Ray traces, Logs Insights on `trace_id`.
   - Trace-id mapping: `1-<first 8 hex>-<remaining 24>`.
   - Dashboards are follow-up work.
   - The alarm table.
8. **Rough monthly cost (us-east-1, on-demand, before data transfer, NAT and the platform's own costs):**
   - staging: about $90–110 (`db.t4g.micro` ~$12, ALB ~$20, Fargate 4 small tasks ~$40, Secrets Manager 8 × $0.40, logs and X-Ray minimal)
   - prod: about $250–320 (Multi-AZ `db.t4g.small` ~$50, ALB ~$25, Fargate 7–13 tasks ~$150–220)
   
   Mark these as estimates to re-check with the AWS Pricing Calculator.
9. **Deliberately not built:**
   - apply
   - dashboards
   - the deploy workflow
   - the Keycloak realm via the Terraform provider
   - CloudFront and WAF
   - incoming-`traceparent` handling at the edge

- [ ] **Step 3: Spec updates**

- **Parent spec `docs/superpowers/specs/2026-10-01-url-shortener-design.md`:**
  - **§2 decisions table:** add one row: `D15 | **Terraform for AWS is plan-ready only, CloudWatch-only, secrets never in state, DB bootstrap as a one-off task** | See 2026-10-02-terraform-aws-design.md (T1, T2, T6, T7).`
  - **§11:** add a first line: `Implemented as Terraform in terraform/ — see 2026-10-02-terraform-aws-design.md, which supersedes this table where they differ (observability: CloudWatch + X-Ray only).`
  - **§12:**
    - Update the Grafana access control item to read "Not applicable on AWS (CloudWatch-only, T2); applies only if Managed Grafana is adopted."
    - Add an item: "Ignore or strip incoming `traceparent` at the public edge (CloudFront/WAF; an ALB can't strip headers)."
    - In parent spec §15 (IX. Disposability), add "(processor `stopTimeout` = 60 s in Terraform)" after the `stopTimeout` sentence.
- **Terraform spec `docs/superpowers/specs/2026-10-02-terraform-aws-design.md`:**
  - **§8, deploy role permissions:** add "Exception: `ecr:GetAuthorizationToken`, `ecs:RegisterTaskDefinition` and `ecs:DescribeTaskDefinition` have no resource-level permissions and use `Resource: \"*\"`; no `Action` contains `*`."
  - **§3:** update the layout so tests live beside each module (`modules/<name>/tests/`) and shared fixtures are in `terraform/testing/aws/`.
  - **§6:** note the realm's `${ADMIN_PUBLIC_BASE_URL}` placeholder and that the AWS image strips `shortener-dev`.

- [ ] **Step 4: `README.md`** (surgical edits; the user maintains "Prerequisites macOS")

- **"Prerequisites macOS" section:** after the Python tooling block, add a block in the same style:
  ```
  ### Terraform tooling (optional; for `make tf-check`)

      ❯ brew install tfenv tflint trivy
      ❯ tfenv install        # reads .terraform-version
  ```
- **Development section:** add `make tf-check   # offline Terraform checks (fmt, validate, tflint, trivy, terraform test); never touches AWS`.
- Add one line pointing to `terraform/README.md`.

- [ ] **Step 5: Final gates and commit**

```bash
make tf-check && uv run ruff format . && make check && /usr/bin/git add Makefile terraform/README.md README.md docs/superpowers/specs && /usr/bin/git commit -m "docs(terraform): README, spec updates, and a guard that keeps secret values out of state

Co-Authored-By: <implementing model> <noreply@anthropic.com>"
```

---

## Plan Done When

- `make tf-check` passes locally. That means `terraform fmt`, then per root `init -backend=false`, `validate` and `test` (every module plus `stack`, `staging` and `prod`), then tflint with the AWS ruleset, then trivy with no unexplained HIGH or CRITICAL findings, then the state-safety guard.
- The CI `terraform` job runs the same checks with pinned tool versions and no AWS credentials.
- `tests/test_db_bootstrap.py` passes against Postgres 16 with a non-superuser master, including reruns and password rotation.
- The Keycloak image builds with `shortener-dev` stripped, and local `make e2e` still passes.
- No secret value appears in any `.tf` file, tfvars or state. Every generated secret is ephemeral plus write-only.
- The docs (`terraform/README.md`, both specs, `README.md`) describe what was built and what was deliberately left out.
