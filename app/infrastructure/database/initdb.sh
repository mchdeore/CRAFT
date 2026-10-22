#!/bin/bash
# First boot only, as the postgres superuser: logins, databases, extension, default grants.
# Passwords come from the environment, so none is committed. Also run by CI against its service.
set -euo pipefail

psql -v ON_ERROR_STOP=1 -U postgres \
     -v owner_pw="$CRAFT_OWNER_DB_PASSWORD" -v app_pw="$CRAFT_APP_DB_PASSWORD" <<'SQL'
create role craft_owner login password :'owner_pw';
create role craft_app   login password :'app_pw';
create database craft_dev  owner craft_owner;
create database craft_test owner craft_owner;
SQL

for db in craft_dev craft_test; do
  psql -v ON_ERROR_STOP=1 -U postgres -d "$db" <<'SQL'
create extension if not exists vector;
grant usage on schema public to craft_app;
alter default privileges for role craft_owner in schema public grant select on tables to craft_app;
alter default privileges for role craft_owner in schema public grant usage on sequences to craft_app;
SQL
done
