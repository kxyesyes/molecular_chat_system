#!/bin/sh
set -eu

LC_ALL=C
export LC_ALL
# Long-form depends_on restart propagation requires Docker Compose >= 2.17.0.

fail() {
    printf '%s\n' "ERROR $1" >&2
    exit 1
}

secret_file=${TEMPORAL_POSTGRES_EXPORTER_PASSWORD_FILE:-}
maximum_secret_bytes=1024

[ -n "$secret_file" ] || fail E_SECRET_FILE
[ ! -L "$secret_file" ] || fail E_SECRET_FILE
[ -f "$secret_file" ] || fail E_SECRET_FILE

secret_size=$(wc -c 2>/dev/null < "$secret_file") || fail E_SECRET_READ
newline_count=$(wc -l 2>/dev/null < "$secret_file") || fail E_SECRET_READ
if [ "$secret_size" -lt 1 ] ||
   [ "$secret_size" -gt "$maximum_secret_bytes" ] ||
   [ "$newline_count" -ne 0 ]; then
    fail E_SECRET_FORMAT
fi

secret_value=$(cat "$secret_file" 2>/dev/null) || fail E_SECRET_READ
carriage_return=$(printf '\r')
case "$secret_value" in
    *"$carriage_return"*) fail E_SECRET_FORMAT ;;
esac
[ -n "$secret_value" ] || fail E_SECRET_FORMAT
unset secret_value

compose_version=$(docker compose version --short 2>/dev/null) || fail E_COMPOSE_UNAVAILABLE
compose_version=${compose_version#v}
compose_version=${compose_version%%-*}
compose_version=${compose_version%%+*}
old_ifs=$IFS
IFS=.
set -- $compose_version
IFS=$old_ifs
major=${1:-}
minor=${2:-}
patch=${3:-}
for component in "$major" "$minor" "$patch"; do
    case "$component" in
        ''|*[!0-9]*) fail E_COMPOSE_VERSION ;;
    esac
done
if [ "$major" -lt 2 ] || { [ "$major" -eq 2 ] && [ "$minor" -lt 17 ]; }; then
    fail E_COMPOSE_VERSION
fi

compose_up_help=$(docker compose up --help 2>/dev/null) ||
    fail E_COMPOSE_WAIT_UNSUPPORTED
printf '%s\n' "$compose_up_help" |
    grep -Eq '(^|[[:space:]])--wait([=[:space:]]|$)' ||
    fail E_COMPOSE_WAIT_UNSUPPORTED
printf '%s\n' "$compose_up_help" |
    grep -Eq '(^|[[:space:]])--wait-timeout([=[:space:]]|$)' ||
    fail E_COMPOSE_WAIT_UNSUPPORTED
unset compose_up_help

script_directory=${0%/*}
[ "$script_directory" != "$0" ] || script_directory=.
script_directory=$(CDPATH= cd -- "$script_directory" 2>/dev/null && pwd -P) ||
    fail E_CONFIGURATION
compose_file=$script_directory/../docker-compose.yml
[ -f "$compose_file" ] || fail E_CONFIGURATION

if ! docker compose -f "$compose_file" \
    run --rm temporal-exporter-role-sync >/dev/null 2>&1; then
    fail E_ROLE_SYNC
fi
if ! docker compose -f "$compose_file" \
    up -d --wait --wait-timeout 120 --no-deps --force-recreate \
    postgres-exporter >/dev/null 2>&1; then
    fail E_EXPORTER_HEALTH
fi

printf '%s\n' "OK E_EXPORTER_ROTATED"
