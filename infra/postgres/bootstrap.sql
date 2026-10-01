-- Creates roles and databases only. On RDS, the equivalent runs once from Terraform/bootstrap.
-- Schemas, tables, and grants are owned by Alembic migrations (api/alembic).
-- Requires psql variables: migrator_pw, api_pw, processor_pw, admin_pw, keycloak_pw.

CREATE ROLE migrator LOGIN PASSWORD :'migrator_pw';
CREATE ROLE api_user LOGIN PASSWORD :'api_pw';
CREATE ROLE processor_user LOGIN PASSWORD :'processor_pw';
CREATE ROLE admin_user LOGIN PASSWORD :'admin_pw';
CREATE ROLE keycloak LOGIN PASSWORD :'keycloak_pw';

CREATE DATABASE keycloak OWNER keycloak;
CREATE DATABASE shortener OWNER migrator;

REVOKE CONNECT ON DATABASE shortener FROM PUBLIC;
GRANT CONNECT ON DATABASE shortener TO api_user, processor_user, admin_user;
