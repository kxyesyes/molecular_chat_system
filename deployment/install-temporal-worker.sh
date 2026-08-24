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
case "$2" in
    /*) ;;
    *) usage ;;
esac
DESTDIR=$(
    /usr/bin/python3 -c '
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

if [ "$DESTDIR" = "/" ]; then
    LIVE_INSTALL=1
else
    LIVE_INSTALL=0
fi

/usr/bin/python3 - "$SCRIPT_DIR" "$DESTDIR" "$LIVE_INSTALL" <<'PY'
from __future__ import annotations

import hashlib
import os
import signal
import stat
import sys


SOURCE_ROOT, DESTINATION_ROOT, LIVE_VALUE = sys.argv[1:]
LIVE_INSTALL = LIVE_VALUE == "1"
DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
READ_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
CREATE_FLAGS = (
    os.O_WRONLY
    | os.O_CREAT
    | os.O_EXCL
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
ASSETS = (
    (
        "medchat-temporal-worker.service",
        "etc/systemd/system/medchat-temporal-worker.service",
        0o644,
    ),
    (
        "medchat-temporal-worker-prepare.service",
        "etc/systemd/system/medchat-temporal-worker-prepare.service",
        0o644,
    ),
    (
        "libexec/validate-temporal-worker-env.py",
        "usr/libexec/medchat/validate-temporal-worker-env",
        0o755,
    ),
    (
        "tmpfiles/medchat-temporal-worker.conf",
        "usr/lib/tmpfiles.d/medchat-temporal-worker.conf",
        0o644,
    ),
)
MANIFEST_PATH = "usr/share/medchat/temporal-worker-assets.sha256"
EXPECTED_UID = 0 if LIVE_INSTALL else os.geteuid()
EXPECTED_GID = 0 if LIVE_INSTALL else os.getegid()


class InstallFailure(RuntimeError):
    pass


INSTALL_SIGNALS = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)


def fail_closed_on_signal(_signum: int, _frame: object) -> None:
    signal.pthread_sigmask(signal.SIG_BLOCK, INSTALL_SIGNALS)
    raise InstallFailure()


for install_signal in INSTALL_SIGNALS:
    signal.signal(install_signal, fail_closed_on_signal)


def identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def read_descriptor(descriptor: int) -> bytes:
    chunks: list[bytes] = []
    while True:
        chunk = os.read(descriptor, 65_536)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def checked_source(relative_path: str) -> bytes:
    descriptor = os.open(os.path.join(SOURCE_ROOT, relative_path), READ_FLAGS)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise InstallFailure()
        if LIVE_INSTALL and (
            before.st_uid != 0
            or before.st_gid != 0
            or stat.S_IMODE(before.st_mode) & 0o022
        ):
            raise InstallFailure()
        payload = read_descriptor(descriptor)
        after = os.fstat(descriptor)
        if identity(before) != identity(after):
            raise InstallFailure()
        return payload
    finally:
        os.close(descriptor)


def open_destination_root() -> int:
    descriptor = os.open("/", DIRECTORY_FLAGS)
    try:
        for component in DESTINATION_ROOT.split("/")[1:]:
            if not component:
                continue
            try:
                os.mkdir(component, 0o755, dir_fd=descriptor)
            except FileExistsError:
                pass
            next_descriptor = os.open(component, DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise InstallFailure()
        if metadata.st_uid != EXPECTED_UID or metadata.st_gid != EXPECTED_GID:
            raise InstallFailure()
        if stat.S_IMODE(metadata.st_mode) & 0o022:
            raise InstallFailure()
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def open_parent(root_descriptor: int, relative_path: str) -> tuple[int, str]:
    components = relative_path.split("/")
    if not components or any(component in {"", ".", ".."} for component in components):
        raise InstallFailure()
    descriptor = os.dup(root_descriptor)
    try:
        for component in components[:-1]:
            try:
                os.mkdir(component, 0o755, dir_fd=descriptor)
            except FileExistsError:
                pass
            next_descriptor = os.open(component, DIRECTORY_FLAGS, dir_fd=descriptor)
            metadata = os.fstat(next_descriptor)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != EXPECTED_UID
                or metadata.st_gid != EXPECTED_GID
                or stat.S_IMODE(metadata.st_mode) & 0o022
            ):
                os.close(next_descriptor)
                raise InstallFailure()
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor, components[-1]
    except Exception:
        os.close(descriptor)
        raise


def verify_metadata(metadata: os.stat_result, mode: int) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != EXPECTED_UID
        or metadata.st_gid != EXPECTED_GID
        or stat.S_IMODE(metadata.st_mode) != mode
    ):
        raise InstallFailure()


def verify_target(
    root_descriptor: int,
    relative_path: str,
    mode: int,
    expected_digest: str,
) -> None:
    parent_descriptor, leaf_name = open_parent(root_descriptor, relative_path)
    try:
        descriptor = os.open(leaf_name, READ_FLAGS, dir_fd=parent_descriptor)
        try:
            before = os.fstat(descriptor)
            verify_metadata(before, mode)
            payload = read_descriptor(descriptor)
            after = os.fstat(descriptor)
            verify_metadata(after, mode)
            if identity(before) != identity(after):
                raise InstallFailure()
            if hashlib.sha256(payload).hexdigest() != expected_digest:
                raise InstallFailure()
            directory_entry = os.stat(
                leaf_name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            verify_metadata(directory_entry, mode)
            if (directory_entry.st_dev, directory_entry.st_ino) != (
                after.st_dev,
                after.st_ino,
            ):
                raise InstallFailure()
        finally:
            os.close(descriptor)
    finally:
        os.close(parent_descriptor)


def install_payload(
    root_descriptor: int,
    relative_path: str,
    mode: int,
    payload: bytes,
) -> str:
    parent_descriptor, leaf_name = open_parent(root_descriptor, relative_path)
    temporary_name = f".medchat-install-{os.getpid()}-{len(payload)}"
    descriptor = -1
    try:
        try:
            os.unlink(temporary_name, dir_fd=parent_descriptor)
        except FileNotFoundError:
            pass
        descriptor = os.open(
            temporary_name,
            CREATE_FLAGS,
            mode,
            dir_fd=parent_descriptor,
        )
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise InstallFailure()
            offset += written
        os.fchmod(descriptor, mode)
        if LIVE_INSTALL:
            os.fchown(descriptor, 0, 0)
        os.fsync(descriptor)
        verify_metadata(os.fstat(descriptor), mode)
        os.close(descriptor)
        descriptor = -1
        os.replace(
            temporary_name,
            leaf_name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
        os.fsync(parent_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary_name, dir_fd=parent_descriptor)
        except FileNotFoundError:
            pass
        os.close(parent_descriptor)
    digest = hashlib.sha256(payload).hexdigest()
    verify_target(root_descriptor, relative_path, mode, digest)
    return digest


def run() -> None:
    destination_descriptor = open_destination_root()
    try:
        installed: list[tuple[str, int, str]] = []
        for source_path, destination_path, mode in ASSETS:
            payload = checked_source(source_path)
            digest = install_payload(
                destination_descriptor,
                destination_path,
                mode,
                payload,
            )
            installed.append((destination_path, mode, digest))

        for destination_path, mode, digest in installed:
            verify_target(destination_descriptor, destination_path, mode, digest)

        manifest = "".join(
            f"{digest}  {destination_path}\n"
            for destination_path, _mode, digest in installed
        ).encode("ascii")
        manifest_digest = install_payload(
            destination_descriptor,
            MANIFEST_PATH,
            0o644,
            manifest,
        )
        verify_target(
            destination_descriptor,
            MANIFEST_PATH,
            0o644,
            manifest_digest,
        )
    finally:
        os.close(destination_descriptor)


try:
    run()
except Exception:
    print(
        "temporal_worker_install=failed code=asset_installation_failed",
        file=sys.stderr,
    )
    raise SystemExit(1)
PY
