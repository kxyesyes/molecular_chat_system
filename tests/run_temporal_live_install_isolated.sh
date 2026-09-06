#!/bin/sh
set -eu

PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

fail() {
    printf '%s\n' 'temporal_live_install_isolated=failed code=isolation_unavailable' >&2
    exit 1
}

validate_invocation() {
    [ "${MEDCHAT_RUN_TEMPORAL_LIVE_INSTALL_TESTS-}" = "1" ] || fail
    for tool in \
        /usr/bin/env /usr/bin/unshare mount umount findmnt mountpoint chroot mktemp readlink \
        cp chown chmod rmdir mkdir stat dirname id
    do
        command -v "$tool" >/dev/null 2>&1 || fail
    done
    [ "$(id -u)" -eq 0 ] || fail
}

mounted() {
    mountpoint -q -- "$1"
}

unmount_checked() {
    target=$1
    unmount_failed=0
    if mounted "$target"; then
        umount "$target" || unmount_failed=1
    fi
    if mounted "$target"; then
        unmount_failed=1
    fi
    [ "$unmount_failed" -eq 0 ]
}

verify_boundary() {
    boundary_source=$1
    boundary_target=$2
    [ -d "$boundary_source" ] || fail
    [ ! -L "$boundary_source" ] || fail
    [ -d "$boundary_target" ] || fail
    [ ! -L "$boundary_target" ] || fail
    mountpoint -q "$boundary_target" || fail
    reported_target=$(findmnt -n -o TARGET --target "$boundary_target") || fail
    [ "$reported_target" = "$boundary_target" ] || fail
    source_identity=$(stat -c "%d:%i" -- "$boundary_source") || fail
    target_identity=$(stat -c "%d:%i" -- "$boundary_target") || fail
    [ "$source_identity" = "$target_identity" ] || fail
}

bind_boundary() {
    boundary_relative=$1
    boundary_name=$2
    boundary_source="$STATE/storage/boundaries/$boundary_name"
    boundary_target="$STATE/storage/merged/$boundary_relative"
    mkdir -p "$boundary_target"
    [ ! -L "$boundary_target" ] || fail
    mount --bind "$boundary_source" "$boundary_target"
    verify_boundary "$boundary_source" "$boundary_target"
}

cleanup_inner() {
    cleanup_failed=0
    unmount_checked "$STATE/storage/merged/run/medchat-temporal-worker" \
        || cleanup_failed=1
    unmount_checked "$STATE/storage/merged/usr/libexec/medchat" \
        || cleanup_failed=1
    unmount_checked "$STATE/storage/merged/usr/lib/medchat" \
        || cleanup_failed=1
    unmount_checked "$STATE/storage/merged/etc/systemd/system" \
        || cleanup_failed=1
    unmount_checked "$STATE/storage/merged" || cleanup_failed=1
    unmount_checked "$STATE/storage" || cleanup_failed=1
    unmount_checked "$STATE/lower" || cleanup_failed=1
    if [ "$cleanup_failed" -ne 0 ]; then
        printf '%s\n' 'temporal_live_install_isolated=failed code=cleanup_incomplete' >&2
        return 1
    fi
    if [ -d "$STATE/lower" ]; then
        rmdir -- "$STATE/lower"
    fi
    if [ -d "$STATE/storage" ]; then
        rmdir -- "$STATE/storage"
    fi
    rmdir -- "$STATE"
}

validate_invocation

if [ "${1-}" != "--inside" ]; then
    [ "$#" -eq 0 ] || fail
    SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
    REPOSITORY=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd -P)
    exec /usr/bin/env -i \
        PATH=/usr/sbin:/usr/bin:/sbin:/bin \
        MEDCHAT_RUN_TEMPORAL_LIVE_INSTALL_TESTS=1 \
        /usr/bin/unshare --mount --fork -- "$0" --inside "$REPOSITORY"
fi

[ "$#" -eq 2 ] || fail
REPOSITORY=$2
case "$REPOSITORY" in /*) ;; *) fail ;; esac

self_namespace=$(readlink /proc/self/ns/mnt) || fail
pid1_namespace=$(readlink /proc/1/ns/mnt) || fail
[ "$self_namespace" != "$pid1_namespace" ] || fail

mount --make-rprivate /
STATE=$(mktemp -d /tmp/medchat-temporal-live.XXXXXX) || fail
case "$STATE" in /tmp/medchat-temporal-live.*) ;; *) fail ;; esac
trap 'cleanup_inner || exit 1' EXIT
trap 'exit 1' HUP INT TERM
mkdir "$STATE/lower" "$STATE/storage"

mount --bind / "$STATE/lower"
mount -o remount,bind,ro "$STATE/lower"
lower_options=$(findmnt -n -o OPTIONS --target "$STATE/lower") || fail
case ",$lower_options," in *,ro,*) ;; *) fail ;; esac

mount -t tmpfs -o mode=0700,nosuid,nodev,noexec tmpfs "$STATE/storage"
[ "$(findmnt -n -o FSTYPE --target "$STATE/storage")" = "tmpfs" ] || fail
[ "$(findmnt -n -o TARGET --target "$STATE/storage")" = "$STATE/storage" ] \
    || fail
mkdir \
    "$STATE/storage/upper" \
    "$STATE/storage/work" \
    "$STATE/storage/merged" \
    "$STATE/storage/boundaries"
mkdir \
    "$STATE/storage/boundaries/etc-systemd-system" \
    "$STATE/storage/boundaries/usr-lib-medchat" \
    "$STATE/storage/boundaries/usr-libexec-medchat" \
    "$STATE/storage/boundaries/run-medchat-temporal-worker"
chmod 0700 "$STATE/storage/boundaries/run-medchat-temporal-worker"
mkdir "$STATE/storage/boundaries/run-medchat-temporal-worker/source"
cp -a "$REPOSITORY/deployment/." \
    "$STATE/storage/boundaries/run-medchat-temporal-worker/source/"
chown -R 0:0 \
    "$STATE/storage/boundaries/run-medchat-temporal-worker/source"
chmod -R go-w \
    "$STATE/storage/boundaries/run-medchat-temporal-worker/source"

mount -t overlay overlay \
    -o "lowerdir=$STATE/lower,upperdir=$STATE/storage/upper,workdir=$STATE/storage/work" \
    "$STATE/storage/merged"
[ "$(findmnt -n -o FSTYPE --target "$STATE/storage/merged")" = "overlay" ] \
    || fail

bind_boundary "etc/systemd/system" "etc-systemd-system"
bind_boundary "usr/lib/medchat" "usr-lib-medchat"
bind_boundary "usr/libexec/medchat" "usr-libexec-medchat"
bind_boundary "run/medchat-temporal-worker" "run-medchat-temporal-worker"

/usr/bin/env -i \
    PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    MEDCHAT_RUN_TEMPORAL_LIVE_INSTALL_TESTS=1 \
    chroot "$STATE/storage/merged" \
    /usr/bin/env -i \
    PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    MEDCHAT_RUN_TEMPORAL_LIVE_INSTALL_TESTS=1 \
    /bin/sh -c \
    'cd /run/medchat-temporal-worker/source && sh ./install-temporal-worker.sh --destdir /'

trap - EXIT HUP INT TERM
cleanup_inner
printf '%s\n' 'temporal_live_install_isolated=passed'
