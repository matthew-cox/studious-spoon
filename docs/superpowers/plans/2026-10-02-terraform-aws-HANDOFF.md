# Terraform for AWS: handoff (read this first)

- **Status (2026-10-02):** design and plan approved and committed. **No implementation yet.** The `terraform/` directory doesn't exist.
- **Why this file exists:** resuming should cost as few tokens as possible. Read this file, then *one task at a time* from the plan. Don't load the whole 3,800-line plan.

## Where things are

| What | Path | Size |
|---|---|---|
| One-page ops answer (diagram, state, monitoring) | `docs/operations.md` | ~1 page |
| Decisions (ADR) | `docs/adr/0001-terraform-aws-plan-ready.md` | ~40 lines |
| Design spec (binding) | `docs/superpowers/specs/2026-10-02-terraform-aws-design.md` | 264 lines |
| Implementation plan | `docs/superpowers/plans/2026-10-02-terraform-aws.md` | 3,840 lines |
| Parent spec (§11 AWS mapping) | `docs/superpowers/specs/2026-10-01-url-shortener-design.md` | — |

Plan sections, by line (`sed -n 'A,Bp'` or Read with an offset):

| Lines | Section |
|---|---|
| 1–130 | Header, Global Constraints, Review Focus, file map, shared `versions.tf` and test header |
| 131 | T1 tooling + `make tf-check` + CI + `queue` |
| 394 | T2 `secrets` |
| 600 | T3 `data` (RDS) |
| 792 | T4 `ecr` |
| 902 | T5 `edge` |
| 1144 | T6 `service` (largest) |
| 1824 | T7 `task` + `db-bootstrap.sh` + pytest |
| 2132 | T8 `alarms` + redirect canary |
| 2686 | T9 `ci-deploy-role` |
| 2892 | T10 `stack` + envs + shared mocks + backend.hcl |
| 3629 | T11 Keycloak image + realm placeholder |
| 3750 | T12 docs + state guard |

## How to resume (cheapest path)

1. **Re-check versions** (they were current on 2026-10-02) and update `.terraform-version` and the plan's pins if needed:
   `curl -s https://api.github.com/repos/hashicorp/terraform/releases/latest`, and the same for `terraform-provider-aws`, `tflint`, `tflint-ruleset-aws`, `trivy` and `aws-otel-collector`.
2. **Branch:** `git checkout -b terraform-aws` (the primary branch is `main`).
3. **Execute** with the superpowers subagent-driven-development skill, which was the user's choice for earlier plans: batched, with Sonnet implementers and reviewers and an Opus final review.
   - Batches: (1–3), (4–6), (7–9), (10–12).
   - The skill's `scripts/task-brief PLAN N` extracts one task to a file, so neither the controller nor the subagents read the whole plan.
4. **Native/inline** execution is the cheaper alternative if a fresh reviewer per batch isn't wanted.

## Verify these first (unproven assumptions in the plan)

- **Mocked computed values:** `terraform test` with `mock_provider "aws"` + `command = apply` must yield known values, and `mock_resource` `defaults` must be applied (ACM `domain_validation_options`, RDS `master_user_secret`).
- **Ephemeral values in write-only arguments:** ephemeral `random_password` inside `jsonencode()` in `secret_string_wo` must pass `terraform validate` (Terraform ≥ 1.11, AWS provider ≥ 6).
- **The bootstrap grant:** PG16 must accept `GRANT migrator, keycloak TO CURRENT_USER WITH INHERIT TRUE, SET TRUE`. Task 7 has a fallback.
- **Keycloak import placeholders:** Keycloak must resolve `${ADMIN_PUBLIC_BASE_URL}` in realm import, as it already does for `${SHORTENER_ADMIN_CLIENT_SECRET}`.
- **The Synthetics Python runtime name:** `canary_runtime_version` defaults to `syn-python-selenium-6.0`; check the AWS Synthetics runtime list for the current one.
- **Trivy findings:** findings at the plan's severity threshold must be fixed or ignored inline with a reason; never lower the threshold.

## User constraints (keep)

- Never `terraform apply` and never touch AWS (no account, no costs).
- Never `make down` on the user's local stack without asking.
- The user maintains `README.md` "Prerequisites macOS" and the Makefile `.venv` target, so make surgical edits only.
- The shell's `git` wrapper summarizes output; use `/usr/bin/git` when the output matters.
- Executable Python scripts go in `scripts/` with a uv shebang plus a PEP 723 header.
- The local Keycloak host port is 8180, and Docker is colima (`scripts/docker-env.sh`).
