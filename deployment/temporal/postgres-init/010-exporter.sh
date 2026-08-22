#!/bin/sh
set -eu

secret_file=/run/secrets/temporal_postgres_exporter_password
maximum_secret_bytes=1024

[ -f "$secret_file" ] || exit 1

secret_size=$(wc -c < "$secret_file")
if [ "$secret_size" -lt 1 ] || [ "$secret_size" -gt "$maximum_secret_bytes" ]; then
    exit 1
fi

newline_count=$(wc -l < "$secret_file")
if [ "$newline_count" -ne 0 ]; then
    exit 1
fi

exporter_password=$(cat "$secret_file")
case "$exporter_password" in
    *"$(printf '\r')"*) exit 1 ;;
esac
[ -n "$exporter_password" ] || exit 1

psql \
    --quiet \
    --set=ON_ERROR_STOP=1 \
    --set=exporter_password="$exporter_password" \
    --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" <<'SQL'
SELECT format(
    'CREATE ROLE temporal_exporter LOGIN PASSWORD %L',
    :'exporter_password'
)
WHERE NOT EXISTS (
    SELECT 1
    FROM pg_catalog.pg_roles
    WHERE rolname = 'temporal_exporter'
)
\gexec

ALTER ROLE temporal_exporter WITH LOGIN PASSWORD :'exporter_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
GRANT pg_monitor TO temporal_exporter;
SQL

unset exporter_password
