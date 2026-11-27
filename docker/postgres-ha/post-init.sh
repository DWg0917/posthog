#!/bin/sh
set -eu

connection_url="${1:?Patroni must pass the primary connection URL}"
: "${POSTHOG_APP_USER:?POSTHOG_APP_USER must be configured}"
: "${POSTHOG_APP_PASSWORD:?POSTHOG_APP_PASSWORD must be configured}"
: "${SEAWEEDFS_DATABASE_PASSWORD:?SEAWEEDFS_DATABASE_PASSWORD must be configured}"
: "${TEMPORAL_DATABASE_PASSWORD:?TEMPORAL_DATABASE_PASSWORD must be configured}"

psql "$connection_url" \
    --set=ON_ERROR_STOP=1 \
    --set=app_user="$POSTHOG_APP_USER" \
    --set=app_password="$POSTHOG_APP_PASSWORD" \
    --set=seaweedfs_user=seaweedfs \
    --set=seaweedfs_password="$SEAWEEDFS_DATABASE_PASSWORD" \
    --set=temporal_user=temporal \
    --set=temporal_password="$TEMPORAL_DATABASE_PASSWORD" <<'SQL'
SELECT format(
    'CREATE ROLE %I WITH LOGIN SUPERUSER CREATEDB CREATEROLE ENCRYPTED PASSWORD %L',
    :'app_user',
    :'app_password'
)
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'app_user')
\gexec

SELECT format('CREATE DATABASE %I OWNER %I', 'posthog', :'app_user')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'posthog')
\gexec

SELECT format(
    'CREATE ROLE %I WITH LOGIN ENCRYPTED PASSWORD %L',
    :'seaweedfs_user',
    :'seaweedfs_password'
)
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'seaweedfs_user')
\gexec

SELECT format('CREATE DATABASE %I OWNER %I', 'seaweedfs', :'seaweedfs_user')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'seaweedfs')
\gexec

SELECT format(
    'CREATE ROLE %I WITH LOGIN ENCRYPTED PASSWORD %L',
    :'temporal_user',
    :'temporal_password'
)
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'temporal_user')
\gexec

SELECT format('CREATE DATABASE %I OWNER %I', 'temporal', :'temporal_user')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'temporal')
\gexec

SELECT format('CREATE DATABASE %I OWNER %I', 'temporal_visibility', :'temporal_user')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'temporal_visibility')
\gexec
SQL

export POSTGRES_USER="$POSTHOG_APP_USER"
export POSTGRES_DB=posthog
export PGHOST=127.0.0.1
export PGPORT=5433
export PGPASSWORD="$POSTHOG_APP_PASSWORD"

init_scripts_dir="${POSTHOG_POSTGRES_INIT_SCRIPTS:-/usr/local/share/posthog/postgres-init-scripts}"
for init_script in "$init_scripts_dir"/*.sh; do
    bash "$init_script"
done
