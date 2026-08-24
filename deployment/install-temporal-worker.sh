#!/bin/sh
set -eu
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
DESTDIR=

usage() {
    printf '%s\n' 'usage: install-temporal-worker.sh --destdir ABSOLUTE_PATH' >&2
    exit 2
}

if [ "$#" -ne 2 ] || [ "$1" != "--destdir" ]; then
    usage
fi
DESTDIR=$2
case "$DESTDIR" in
    /) ;;
    /*) DESTDIR=${DESTDIR%/} ;;
    *) usage ;;
esac

source_is_root_owned_and_locked() {
    source_path=$1
    if [ -L "$source_path" ] || [ ! -f "$source_path" ]; then
        return 1
    fi
    current=$source_path
    while :; do
        owner=$(stat -c %u -- "$current") || return 1
        mode=$(stat -c %a -- "$current") || return 1
        [ "$owner" -eq 0 ] || return 1
        numeric_mode=$((0$mode))
        [ $((numeric_mode & 022)) -eq 0 ] || return 1
        [ "$current" = "/" ] && break
        current=$(dirname -- "$current")
    done
}

if [ "$DESTDIR" = "/" ]; then
    if [ "$(id -u)" -ne 0 ]; then
        printf '%s\n' 'temporal_worker_install=failed code=root_required' >&2
        exit 1
    fi
    for source in \
        "$SCRIPT_DIR/install-temporal-worker.sh" \
        "$SCRIPT_DIR/medchat-temporal-worker.service" \
        "$SCRIPT_DIR/medchat-temporal-worker-prepare.service" \
        "$SCRIPT_DIR/libexec/validate-temporal-worker-env.py" \
        "$SCRIPT_DIR/tmpfiles/medchat-temporal-worker.conf"
    do
        if ! source_is_root_owned_and_locked "$source"; then
            printf '%s\n' \
                'temporal_worker_install=failed code=source_not_root-owned_or_locked' \
                >&2
            exit 1
        fi
    done
fi

install_asset() {
    source_path=$1
    destination=$2
    mode=$3
    if [ "$DESTDIR" = "/" ]; then
        install -D -o root -g root -m "$mode" -- "$source_path" "$destination"
    else
        install -D -m "$mode" -- "$source_path" "$DESTDIR$destination"
    fi
}

install_asset \
    "$SCRIPT_DIR/medchat-temporal-worker.service" \
    /etc/systemd/system/medchat-temporal-worker.service \
    0644
install_asset \
    "$SCRIPT_DIR/medchat-temporal-worker-prepare.service" \
    /etc/systemd/system/medchat-temporal-worker-prepare.service \
    0644
install_asset \
    "$SCRIPT_DIR/libexec/validate-temporal-worker-env.py" \
    /usr/libexec/medchat/validate-temporal-worker-env \
    0755
install_asset \
    "$SCRIPT_DIR/tmpfiles/medchat-temporal-worker.conf" \
    /usr/lib/tmpfiles.d/medchat-temporal-worker.conf \
    0644

manifest=$(mktemp)
trap 'rm -f -- "$manifest"' EXIT HUP INT TERM
for relative_path in \
    etc/systemd/system/medchat-temporal-worker.service \
    etc/systemd/system/medchat-temporal-worker-prepare.service \
    usr/libexec/medchat/validate-temporal-worker-env \
    usr/lib/tmpfiles.d/medchat-temporal-worker.conf
do
    checksum=$(sha256sum -- "$DESTDIR/$relative_path")
    checksum=${checksum%% *}
    printf '%s  %s\n' "$checksum" "$relative_path" >> "$manifest"
done
install_asset \
    "$manifest" \
    /usr/share/medchat/temporal-worker-assets.sha256 \
    0644
