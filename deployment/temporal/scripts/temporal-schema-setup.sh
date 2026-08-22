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

[ -n "${POSTGRES_SEEDS:-}" ] || fail E_CONFIGURATION
[ -n "${DB_PORT:-}" ] || fail E_CONFIGURATION
validate_identifier "${POSTGRES_USER:-}" || fail E_IDENTIFIER
validate_identifier "${DBNAME:-}" || fail E_IDENTIFIER
validate_identifier "${VISIBILITY_DBNAME:-}" || fail E_IDENTIFIER
[ "$DBNAME" != "$VISIBILITY_DBNAME" ] || fail E_IDENTIFIER

secret_file=${TEMPORAL_POSTGRES_PASSWORD_FILE:-/run/secrets/temporal_postgres_password}
maximum_secret_bytes=1024

[ -f "$secret_file" ] || fail E_SECRET_FILE
secret_size=$(wc -c 2>/dev/null < "$secret_file") || fail E_SECRET_READ
if [ "$secret_size" -lt 1 ] || [ "$secret_size" -gt "$maximum_secret_bytes" ]; then
    fail E_SECRET_FORMAT
fi
newline_count=$(wc -l 2>/dev/null < "$secret_file") || fail E_SECRET_READ
if [ "$newline_count" -ne 0 ]; then
    fail E_SECRET_FORMAT
fi

postgres_password=$(cat "$secret_file" 2>/dev/null) || fail E_SECRET_READ
carriage_return=$(printf '\r')
case "$postgres_password" in
    *"$carriage_return"*) fail E_SECRET_FORMAT ;;
esac
[ -n "$postgres_password" ] || fail E_SECRET_FORMAT

export PGPASSWORD="$postgres_password"
export SQL_PASSWORD="$postgres_password"
unset postgres_password

if ! psql \
    --quiet \
    --set=ON_ERROR_STOP=1 \
    --host "$POSTGRES_SEEDS" \
    --port "$DB_PORT" \
    --username "$POSTGRES_USER" \
    --dbname postgres \
    --set=temporal_database="$DBNAME" \
    --set=visibility_database="$VISIBILITY_DBNAME" >/dev/null 2>&1 <<'SQL'
SELECT format('CREATE DATABASE %I', :'temporal_database')
WHERE NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_database
    WHERE datname = :'temporal_database'
)
\gexec

SELECT format('CREATE DATABASE %I', :'visibility_database')
WHERE NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_database
    WHERE datname = :'visibility_database'
)
\gexec
SQL
then
    fail E_SCHEMA_DATABASES
fi

if ! temporal-sql-tool \
    --plugin postgres12 \
    --ep "$POSTGRES_SEEDS" \
    -u "$POSTGRES_USER" \
    -p "$DB_PORT" \
    --db "$DBNAME" \
    setup-schema -v 0.0 >/dev/null 2>&1; then
    fail E_SCHEMA_MIGRATION
fi
if ! temporal-sql-tool \
    --plugin postgres12 \
    --ep "$POSTGRES_SEEDS" \
    -u "$POSTGRES_USER" \
    -p "$DB_PORT" \
    --db "$DBNAME" \
    update-schema -d /etc/temporal/schema/postgresql/v12/temporal/versioned \
    >/dev/null 2>&1; then
    fail E_SCHEMA_MIGRATION
fi

if ! temporal-sql-tool \
    --plugin postgres12 \
    --ep "$POSTGRES_SEEDS" \
    -u "$POSTGRES_USER" \
    -p "$DB_PORT" \
    --db "$VISIBILITY_DBNAME" \
    setup-schema -v 0.0 >/dev/null 2>&1; then
    fail E_SCHEMA_MIGRATION
fi
if ! temporal-sql-tool \
    --plugin postgres12 \
    --ep "$POSTGRES_SEEDS" \
    -u "$POSTGRES_USER" \
    -p "$DB_PORT" \
    --db "$VISIBILITY_DBNAME" \
    update-schema -d /etc/temporal/schema/postgresql/v12/visibility/versioned \
    >/dev/null 2>&1; then
    fail E_SCHEMA_MIGRATION
fi

unset PGPASSWORD SQL_PASSWORD
