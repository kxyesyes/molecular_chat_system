#!/bin/sh
set -eu

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

export POSTGRES_PWD="$postgres_password"
unset postgres_password

exec /etc/temporal/entrypoint.sh "$@"
