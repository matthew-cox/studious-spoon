#!/usr/bin/env bash
# Runs once, on first start of an empty data volume (docker-entrypoint-initdb.d).
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v migrator_pw="$MIGRATOR_DB_PASSWORD" \
  -v api_pw="$API_DB_PASSWORD" \
  -v processor_pw="$PROCESSOR_DB_PASSWORD" \
  -v admin_pw="$ADMIN_DB_PASSWORD" \
  -v keycloak_pw="$KEYCLOAK_DB_PASSWORD" \
  -f /bootstrap/bootstrap.sql
