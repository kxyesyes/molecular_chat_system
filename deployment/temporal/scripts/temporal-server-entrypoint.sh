#!/bin/sh
set -eu

fail() {
    printf '%s\n' "ERROR $1" >&2
    exit 1
}

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

export POSTGRES_PWD="$postgres_password"
unset postgres_password

exec /etc/temporal/entrypoint.sh "$@"
