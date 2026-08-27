#!/bin/sh
set -eu
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)

usage() {
    printf '%s\n' 'temporal_worker_install=failed code=usage' >&2
    exit 2
}

if [ "$#" -ne 2 ] || [ "$1" != "--destdir" ]; then
    usage
fi
case "$2" in
    /*) ;;
    *) usage ;;
esac
DESTDIR=$(
    /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    /usr/bin/python3 -I -c '
import os
import sys
path = sys.argv[1]
if not path.startswith("/") or any(ord(character) < 32 for character in path):
    raise SystemExit(2)
print(os.path.realpath(path))
' "$2"
) || usage
[ -n "$DESTDIR" ] || usage

source_is_root_owned_and_locked() {
    source_path=$1
    case "$source_path" in
        /*) ;;
        *) return 1 ;;
    esac
    [ ! -L "$source_path" ] || return 1
    [ -f "$source_path" ] || return 1
    [ "$(stat -c %u:%g -- "$source_path")" = "0:0" ] || return 1
    permissions=$(stat -c %a -- "$source_path")
    [ $((0$permissions & 0022)) -eq 0 ] || return 1
    parent=$source_path
    while [ "$parent" != / ]; do
        parent=$(dirname -- "$parent")
        [ ! -L "$parent" ] || return 1
        [ -d "$parent" ] || return 1
        [ "$(stat -c %u:%g -- "$parent")" = "0:0" ] || return 1
        permissions=$(stat -c %a -- "$parent")
        [ $((0$permissions & 0022)) -eq 0 ] || return 1
    done
}

if [ "$DESTDIR" = / ]; then
    [ "$(id -u)" -eq 0 ] || {
        printf '%s\n' 'temporal_worker_install=failed code=root_required' >&2
        exit 1
    }
    for source_path in \
        "$SCRIPT_DIR/install-temporal-worker.sh" \
        "$SCRIPT_DIR/medchat-temporal-worker.service" \
        "$SCRIPT_DIR/medchat-temporal-worker-prepare.service" \
        "$SCRIPT_DIR/libexec/validate-temporal-worker-env.py" \
        "$SCRIPT_DIR/libexec/prepare-temporal-worker-directories.py" \
        "$SCRIPT_DIR/libexec/install-temporal-worker-bundle.py"
    do
        source_is_root_owned_and_locked "$source_path" || {
            printf '%s\n' \
                'temporal_worker_install=failed code=source_not_root_owned' >&2
            exit 1
        }
    done
    mode=--live
else
    mode=--staging
fi

exec /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    /usr/bin/python3 -I \
    "$SCRIPT_DIR/libexec/install-temporal-worker-bundle.py" \
    --source-root "$SCRIPT_DIR" \
    --destdir "$DESTDIR" \
    "$mode"
