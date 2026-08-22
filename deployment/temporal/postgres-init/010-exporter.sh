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
SELECT 'CREATE ROLE temporal_exporter NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS'
WHERE NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'temporal_exporter'
)
\gexec
SQL
then
    fail E_EXPORTER_PREPARE
fi

if ! psql \
    --no-psqlrc \
    --quiet \
    --set=ON_ERROR_STOP=1 \
    --host "$PGHOST" \
    --port "$PGPORT" \
    --username "$PGUSER" \
    --dbname postgres >/dev/null 2>&1 <<'SQL'
DO $audit$
DECLARE
    exporter_oid oid := (
        SELECT oid FROM pg_catalog.pg_roles WHERE rolname = 'temporal_exporter'
    );
BEGIN
    IF exporter_oid IS NULL OR EXISTS (
        SELECT 1
        FROM pg_catalog.pg_auth_members membership
        JOIN pg_catalog.pg_roles granted_role
          ON granted_role.oid = membership.roleid
        WHERE membership.member = exporter_oid
          AND granted_role.rolname <> 'pg_monitor'
    ) OR EXISTS (
        SELECT 1 FROM pg_catalog.pg_database WHERE datdba = exporter_oid
    ) OR EXISTS (
        SELECT 1
        FROM pg_catalog.pg_database database_object
        CROSS JOIN LATERAL pg_catalog.aclexplode(database_object.datacl) privilege
        WHERE privilege.grantee = exporter_oid
          AND privilege.privilege_type IN ('CREATE', 'TEMPORARY')
    ) OR EXISTS (
        SELECT 1 FROM pg_catalog.pg_default_acl WHERE defaclrole = exporter_oid
    ) THEN
        RAISE EXCEPTION 'exporter audit rejected';
    END IF;
END
$audit$;
SQL
then
    fail E_EXPORTER_AUDIT
fi

audit_database() {
    database_name=$1
    if ! psql \
        --no-psqlrc \
        --quiet \
        --set=ON_ERROR_STOP=1 \
        --host "$PGHOST" \
        --port "$PGPORT" \
        --username "$PGUSER" \
        --dbname "$database_name" >/dev/null 2>&1 <<'SQL'
DO $audit$
DECLARE
    exporter_oid oid := (
        SELECT oid FROM pg_catalog.pg_roles WHERE rolname = 'temporal_exporter'
    );
BEGIN
    IF exporter_oid IS NULL OR EXISTS (
        SELECT 1 FROM pg_catalog.pg_namespace WHERE nspowner = exporter_oid
    ) OR EXISTS (
        SELECT 1 FROM pg_catalog.pg_class WHERE relowner = exporter_oid
    ) OR EXISTS (
        SELECT 1 FROM pg_catalog.pg_proc WHERE proowner = exporter_oid
    ) OR EXISTS (
        SELECT 1 FROM pg_catalog.pg_type WHERE typowner = exporter_oid
    ) OR EXISTS (
        SELECT 1 FROM pg_catalog.pg_largeobject_metadata WHERE lomowner = exporter_oid
    ) OR EXISTS (
        SELECT 1
        FROM pg_catalog.pg_namespace schema_object
        CROSS JOIN LATERAL pg_catalog.aclexplode(schema_object.nspacl) privilege
        WHERE privilege.grantee = exporter_oid
          AND privilege.privilege_type = 'CREATE'
    ) OR EXISTS (
        SELECT 1
        FROM pg_catalog.pg_class relation_object
        CROSS JOIN LATERAL pg_catalog.aclexplode(relation_object.relacl) privilege
        WHERE privilege.grantee = exporter_oid
          AND (
              (relation_object.relkind = 'S'
               AND privilege.privilege_type IN ('UPDATE', 'USAGE'))
              OR
              (relation_object.relkind <> 'S'
               AND privilege.privilege_type IN (
                   'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER'
               ))
          )
    ) OR EXISTS (
        SELECT 1
        FROM pg_catalog.pg_proc function_object
        CROSS JOIN LATERAL pg_catalog.aclexplode(function_object.proacl) privilege
        WHERE privilege.grantee = exporter_oid
          AND privilege.privilege_type = 'EXECUTE'
    ) OR EXISTS (
        SELECT 1
        FROM pg_catalog.pg_type type_object
        CROSS JOIN LATERAL pg_catalog.aclexplode(type_object.typacl) privilege
        WHERE privilege.grantee = exporter_oid
          AND privilege.privilege_type = 'USAGE'
    ) THEN
        RAISE EXCEPTION 'exporter audit rejected';
    END IF;
END
$audit$;
SQL
    then
        fail E_EXPORTER_AUDIT
    fi
}

audit_database "$PGDATABASE"
audit_database "$VISIBILITY_DBNAME"

if ! psql \
    --no-psqlrc \
    --quiet \
    --set=ON_ERROR_STOP=1 \
    --host "$PGHOST" \
    --port "$PGPORT" \
    --username "$PGUSER" \
    --dbname postgres \
    --set=temporal_database="$PGDATABASE" \
    --set=visibility_database="$VISIBILITY_DBNAME" >/dev/null 2>&1 <<'SQL'
\getenv exporter_password TEMPORAL_EXPORTER_PASSWORD
ALTER ROLE temporal_exporter WITH LOGIN PASSWORD :'exporter_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
GRANT pg_monitor TO temporal_exporter;
SELECT format(
    'GRANT CONNECT ON DATABASE %I TO temporal_exporter', database_name
)
FROM (VALUES (:'temporal_database'), (:'visibility_database')) databases(database_name)
\gexec
SQL
then
    fail E_EXPORTER_SYNC
fi

unset PGPASSWORD TEMPORAL_EXPORTER_PASSWORD
