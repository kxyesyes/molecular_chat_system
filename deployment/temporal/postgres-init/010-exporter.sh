#!/bin/sh
set -eu

LC_ALL=C
export LC_ALL

fail() {
    printf '%s\n' "ERROR $1" >&2
    exit 1
}

validate_identifier() {
    value=$1
    [ -n "$value" ] && [ "${#value}" -le 63 ] || return 1
    case "$value" in
        [A-Za-z_]*) ;;
        *) return 1 ;;
    esac
    case "$value" in
        *[!A-Za-z0-9_]*) return 1 ;;
    esac
}

[ -n "${PGHOST:-}" ] || fail E_CONFIGURATION
[ -n "${PGPORT:-}" ] || fail E_CONFIGURATION
validate_identifier "${PGUSER:-}" || fail E_IDENTIFIER
validate_identifier "${PGDATABASE:-}" || fail E_IDENTIFIER
validate_identifier "${VISIBILITY_DBNAME:-}" || fail E_IDENTIFIER
[ "$PGDATABASE" != "$VISIBILITY_DBNAME" ] || fail E_IDENTIFIER

postgres_secret_file=${TEMPORAL_POSTGRES_PASSWORD_FILE:-/run/secrets/temporal_postgres_password}
exporter_secret_file=${TEMPORAL_POSTGRES_EXPORTER_PASSWORD_FILE:-/run/secrets/temporal_postgres_exporter_password}
maximum_secret_bytes=1024

[ -f "$postgres_secret_file" ] || fail E_SECRET_FILE
[ -f "$exporter_secret_file" ] || fail E_SECRET_FILE

postgres_secret_size=$(wc -c 2>/dev/null < "$postgres_secret_file") || fail E_SECRET_READ
exporter_secret_size=$(wc -c 2>/dev/null < "$exporter_secret_file") || fail E_SECRET_READ
if [ "$postgres_secret_size" -lt 1 ] ||
   [ "$postgres_secret_size" -gt "$maximum_secret_bytes" ] ||
   [ "$exporter_secret_size" -lt 1 ] ||
   [ "$exporter_secret_size" -gt "$maximum_secret_bytes" ]; then
    fail E_SECRET_FORMAT
fi

postgres_newline_count=$(wc -l 2>/dev/null < "$postgres_secret_file") || fail E_SECRET_READ
exporter_newline_count=$(wc -l 2>/dev/null < "$exporter_secret_file") || fail E_SECRET_READ
if [ "$postgres_newline_count" -ne 0 ] ||
   [ "$exporter_newline_count" -ne 0 ]; then
    fail E_SECRET_FORMAT
fi

postgres_password=$(cat "$postgres_secret_file" 2>/dev/null) || fail E_SECRET_READ
exporter_password=$(cat "$exporter_secret_file" 2>/dev/null) || fail E_SECRET_READ
carriage_return=$(printf '\r')
case "$postgres_password$exporter_password" in
    *"$carriage_return"*) fail E_SECRET_FORMAT ;;
esac
[ -n "$postgres_password" ] || fail E_SECRET_FORMAT
[ -n "$exporter_password" ] || fail E_SECRET_FORMAT

export PGPASSWORD="$postgres_password"
export TEMPORAL_EXPORTER_PASSWORD="$exporter_password"
unset postgres_password exporter_password

if ! psql \
    --no-psqlrc \
    --quiet \
    --set=ON_ERROR_STOP=1 \
    --host "$PGHOST" \
    --port "$PGPORT" \
    --username "$PGUSER" \
    --dbname postgres >/dev/null 2>&1 <<'SQL'
\getenv exporter_password TEMPORAL_EXPORTER_PASSWORD
BEGIN;
DROP ROLE IF EXISTS temporal_exporter;
CREATE ROLE temporal_exporter WITH LOGIN PASSWORD :'exporter_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT
    NOREPLICATION NOBYPASSRLS;
GRANT pg_monitor TO temporal_exporter WITH INHERIT TRUE;
GRANT pg_monitor TO temporal_exporter WITH SET FALSE;
GRANT pg_monitor TO temporal_exporter WITH ADMIN FALSE;
COMMIT;
SQL
then
    fail E_EXPORTER_RESET
fi

unset PGPASSWORD TEMPORAL_EXPORTER_PASSWORD
