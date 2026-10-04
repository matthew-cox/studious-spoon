# ADR 0002: Cognito as the production identity provider

- **Status:** Accepted (direction). Not implemented as of 2026-10-03.
- **Amends:** [ADR 0001](0001-terraform-aws-plan-ready.md). Its Keycloak-on-Fargate pieces are a first-pass
  simplification, not the end state.

## Context
- Locally, Keycloak is the identity provider: it runs in a container, and the realm and seeded users
  are code.
- The Terraform plan (ADR 0001, plan Task 11) runs the same Keycloak on Fargate. That keeps the first
  Terraform pass small: one IdP, one realm, no claim changes in the app.
- Running Keycloak ourselves in production means owning a critical service: security patching on
  Keycloak's release schedule, clustering across Fargate tasks (node discovery, so sessions survive
  scale-out), keeping the admin console off the internet, backing up its database, and its uptime.
  Every login depends on it.

## Decision
- **Production uses Amazon Cognito** (a user pool, with the roles as Cognito groups).
- **Keycloak stays the local identity provider.** Cognito has no official local emulator, and the
  seeded realm is how reviewers and tests sign in as each role.
- **Keycloak on Fargate is the first Terraform pass only.** Replacing it with a Cognito module is
  follow-up work, after the app reads its token claims from configuration (below).

## Consequences
The app speaks standard OIDC, but a few places assume Keycloak's token format. These become settings
(one `pydantic-settings` value each, no environment branches) before production can point at Cognito.
Check the Cognito details against the current AWS docs when implementing:

| Where | Keycloak today | Cognito |
|---|---|---|
| `api/src/shortener_api/auth.py` (roles) | `realm_access.roles` | `cognito:groups` |
| `api/src/shortener_api/auth.py` (username) | `preferred_username` | `username` in access tokens |
| `api/src/shortener_api/auth.py` (audience) | `aud` required | access tokens carry `client_id`, not `aud` |
| `admin/src/shortener_admin/oidc.py` (logout) | discovery `end_session_endpoint` | Cognito's `/logout?client_id=…&logout_uri=…` |
| `tools/keycloak-tools`, `infra/keycloak/` | users and roles seeded via the Keycloak admin API | users and groups from Terraform or the Cognito admin API |

- The role list (`MANAGED_ROLES`, now in three places) maps to Cognito group names, which is a good
  moment to give it one source.
- Usernames stay immutable in Cognito, so the owner filter's assumption still holds.
- Local and production run different IdPs. The e2e tests prove the app against Keycloak only; the
  claim settings need their own tests with Cognito-shaped tokens.
- Account management (disabling a user) moves to Cognito, which has an admin API the admin UI could
  call later.
