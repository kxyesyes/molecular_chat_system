#!/bin/sh
set -eu

postgres_secret_file=/run/secrets/temporal_postgres_password
exporter_secret_file=/run/secrets/temporal_postgres_exporter_password
maximum_secret_bytes=1024

[ -f "$postgres_secret_file" ] || exit 1
[ -f "$exporter_secret_file" ] || exit 1

postgres_secret_size=$(wc -c < "$postgres_secret_file")
exporter_secret_size=$(wc -c < "$exporter_secret_file")
if [ "$postgres_secret_size" -lt 1 ] ||
   [ "$postgres_secret_size" -gt "$maximum_secret_bytes" ] ||
   [ "$exporter_secret_size" -lt 1 ] ||
   [ "$exporter_secret_size" -gt "$maximum_secret_bytes" ]; then
    exit 1
fi

postgres_newline_count=$(wc -l < "$postgres_secret_file")
exporter_newline_count=$(wc -l < "$exporter_secret_file")
if [ "$postgres_newline_count" -ne 0 ] ||
   [ "$exporter_newline_count" -ne 0 ]; then
    exit 1
fi

postgres_password=$(cat "$postgres_secret_file")
exporter_password=$(cat "$exporter_secret_file")
carriage_return=$(printf '\r')
case "$postgres_password$exporter_password" in
    *"$carriage_return"*) exit 1 ;;
esac
[ -n "$postgres_password" ] || exit 1
[ -n "$exporter_password" ] || exit 1

export PGPASSWORD="$postgres_password"
export TEMPORAL_EXPORTER_PASSWORD="$exporter_password"
unset postgres_password exporter_password

psql \
    --quiet \
    --set=ON_ERROR_STOP=1 \
    --host "$PGHOST" \
    --port "$PGPORT" \
    --username "$PGUSER" \
    --dbname "$PGDATABASE" <<'SQL'
\getenv exporter_password TEMPORAL_EXPORTER_PASSWORD
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

unset PGPASSWORD TEMPORAL_EXPORTER_PASSWORD
