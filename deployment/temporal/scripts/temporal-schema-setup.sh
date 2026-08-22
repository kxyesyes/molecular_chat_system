#!/bin/sh
set -eu

: "${POSTGRES_SEEDS:?required}"
: "${POSTGRES_USER:?required}"
: "${DB_PORT:?required}"
: "${DBNAME:?required}"
: "${VISIBILITY_DBNAME:?required}"

secret_file=/run/secrets/temporal_postgres_password
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

postgres_password=$(cat "$secret_file")
carriage_return=$(printf '\r')
case "$postgres_password" in
    *"$carriage_return"*) exit 1 ;;
esac
[ -n "$postgres_password" ] || exit 1

export PGPASSWORD="$postgres_password"
export SQL_PASSWORD="$postgres_password"
unset postgres_password

psql \
    --quiet \
    --set=ON_ERROR_STOP=1 \
    --host "$POSTGRES_SEEDS" \
    --port "$DB_PORT" \
    --username "$POSTGRES_USER" \
    --dbname postgres \
    --set=temporal_database="$DBNAME" \
    --set=visibility_database="$VISIBILITY_DBNAME" <<'SQL'
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

temporal-sql-tool \
    --plugin postgres12 \
    --ep "$POSTGRES_SEEDS" \
    -u "$POSTGRES_USER" \
    -p "$DB_PORT" \
    --db "$DBNAME" \
    setup-schema -v 0.0
temporal-sql-tool \
    --plugin postgres12 \
    --ep "$POSTGRES_SEEDS" \
    -u "$POSTGRES_USER" \
    -p "$DB_PORT" \
    --db "$DBNAME" \
    update-schema -d /etc/temporal/schema/postgresql/v12/temporal/versioned

temporal-sql-tool \
    --plugin postgres12 \
    --ep "$POSTGRES_SEEDS" \
    -u "$POSTGRES_USER" \
    -p "$DB_PORT" \
    --db "$VISIBILITY_DBNAME" \
    setup-schema -v 0.0
temporal-sql-tool \
    --plugin postgres12 \
    --ep "$POSTGRES_SEEDS" \
    -u "$POSTGRES_USER" \
    -p "$DB_PORT" \
    --db "$VISIBILITY_DBNAME" \
    update-schema -d /etc/temporal/schema/postgresql/v12/visibility/versioned

unset PGPASSWORD SQL_PASSWORD
