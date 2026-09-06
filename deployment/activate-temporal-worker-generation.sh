#!/bin/sh
set -eu
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)

fail() {
    printf '%s\n' "temporal_worker_activation=failed code=$1" >&2
    exit 1
}

if [ "$#" -eq 1 ]; then
    mode=--activate
    digest=$1
elif [ "$#" -eq 2 ] && [ "$1" = --migrate-legacy ]; then
    mode=--migrate-legacy
    digest=$2
else
    fail activation_arguments_invalid
fi
case "$digest" in
    *[!0-9a-f]*|'') fail activation_arguments_invalid ;;
esac
[ "${#digest}" -eq 64 ] || fail activation_arguments_invalid
[ "$(id -u)" -eq 0 ] || fail activation_root_required

source_is_root_owned_and_locked() {
    source_path=$1
    case "$source_path" in
        /*) ;;
        *) return 1 ;;
    esac
    [ ! -L "$source_path" ] || return 1
    [ -f "$source_path" ] || return 1
    [ "$(stat -c %u:%g -- "$source_path")" = 0:0 ] || return 1
    permissions=$(stat -c %a -- "$source_path")
    [ $((0$permissions & 0022)) -eq 0 ] || return 1
    parent=$source_path
    while [ "$parent" != / ]; do
        parent=$(dirname -- "$parent")
        [ ! -L "$parent" ] || return 1
        [ -d "$parent" ] || return 1
        [ "$(stat -c %u:%g -- "$parent")" = 0:0 ] || return 1
        permissions=$(stat -c %a -- "$parent")
        [ $((0$permissions & 0022)) -eq 0 ] || return 1
    done
}

for source_path in \
    "$SCRIPT_DIR/activate-temporal-worker-generation.sh" \
    "$SCRIPT_DIR/libexec/install-temporal-worker-bundle.py"
do
    source_is_root_owned_and_locked "$source_path" || \
        fail activation_source_untrusted
done

exec /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    /usr/bin/python3 -I \
    "$SCRIPT_DIR/libexec/install-temporal-worker-bundle.py" \
    "$mode" "$digest"
