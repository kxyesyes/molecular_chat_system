#!/bin/sh
set -eu

PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

fail() {
    printf '%s\n' 'temporal_live_install_isolated=failed code=isolation_unavailable' >&2
    exit 1
}

mounted() {
    findmnt -n --target "$1" >/dev/null 2>&1
}

cleanup_inner() {
    cleanup_failed=0
    if mounted "$STATE/storage/merged"; then
        umount "$STATE/storage/merged" || cleanup_failed=1
    fi
    if mounted "$STATE/storage"; then
        umount "$STATE/storage" || cleanup_failed=1
    fi
    if mounted "$STATE/lower"; then
        umount "$STATE/lower" || cleanup_failed=1
    fi
    if [ "$cleanup_failed" -ne 0 ]; then
        printf '%s\n' 'temporal_live_install_isolated=failed code=cleanup_incomplete' >&2
        return 1
    fi
    mounted "$STATE/storage/merged" && return 1
    mounted "$STATE/storage" && return 1
    mounted "$STATE/lower" && return 1
    rmdir -- "$STATE/lower"
    rmdir -- "$STATE/storage"
    rmdir -- "$STATE"
}

if [ "${1-}" != "--inside" ]; then
    [ "${MEDCHAT_RUN_TEMPORAL_LIVE_INSTALL_TESTS-}" = "1" ] || fail
    [ "$(id -u)" -eq 0 ] || fail
    for tool in /usr/bin/unshare mount umount findmnt chroot mktemp readlink cp chown chmod rmdir
    do
        command -v "$tool" >/dev/null 2>&1 || fail
    done
    SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
    REPOSITORY=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd -P)
    exec /usr/bin/unshare --mount --fork -- "$0" --inside "$REPOSITORY"
fi

[ "$#" -eq 2 ] || fail
REPOSITORY=$2
case "$REPOSITORY" in /*) ;; *) fail ;; esac

mount --make-rprivate /
self_namespace=$(readlink /proc/self/ns/mnt)
pid1_namespace=$(readlink /proc/1/ns/mnt)
[ "$self_namespace" != "$pid1_namespace" ] || fail

STATE=$(mktemp -d /tmp/medchat-temporal-live.XXXXXX) || fail
case "$STATE" in /tmp/medchat-temporal-live.*) ;; *) fail ;; esac
mkdir "$STATE/lower" "$STATE/storage"
trap 'cleanup_inner || exit 1' EXIT
trap 'exit 1' HUP INT TERM
mount --bind / "$STATE/lower"
mount -o remount,bind,ro "$STATE/lower"
findmnt -n -o OPTIONS --target "$STATE/lower" | grep -Eq '(^|,)ro(,|$)' || fail
mount -t tmpfs -o mode=0700,nosuid,nodev,noexec tmpfs "$STATE/storage"
mkdir "$STATE/storage/upper" "$STATE/storage/work" "$STATE/storage/merged"
mount -t overlay overlay \
    -o "lowerdir=$STATE/lower,upperdir=$STATE/storage/upper,workdir=$STATE/storage/work" \
    "$STATE/storage/merged"
[ "$(findmnt -n -o FSTYPE --target "$STATE/storage/merged")" = "overlay" ] || fail

mkdir "$STATE/storage/merged/run/medchat-temporal-live-source"
cp -a "$REPOSITORY/deployment/." \
    "$STATE/storage/merged/run/medchat-temporal-live-source/"
chown -R 0:0 "$STATE/storage/merged/run/medchat-temporal-live-source"
chmod -R go-w "$STATE/storage/merged/run/medchat-temporal-live-source"
chroot "$STATE/storage/merged" /bin/sh -c \
    'cd /run/medchat-temporal-live-source && ./install-temporal-worker.sh --destdir /'

trap - EXIT HUP INT TERM
cleanup_inner
printf '%s\n' 'temporal_live_install_isolated=passed'
