#!/bin/sh
set +x
set -eu
umask 077
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
GROUP_MIGRATION_INTENT=remove-medchat-from-medchat-sandbox
SYSTEMCTL_TIMEOUT_SECONDS=15
MEDCHAT_SERVICE_STATE=unknown
TEMPORAL_WORKER_SERVICE_STATE=unknown
OPENSANDBOX_SERVICE_STATE=unknown
SANDBOX_BROKER_SERVICE_STATE=unknown
GROUP_MEMBERSHIP_REVOKED=0
SERVICE_STATE_CAPTURED=0
BOTH_SERVICES_INACTIVE=0
BROKER_ENV_DIRECTORY=/etc/medchat
BROKER_ENV_NAME=sandbox-broker.env

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)

fail() {
    printf '%s\n' "opensandbox_install=failed code=$1" >&2
    exit 1
}

usage() {
    printf '%s\n' 'opensandbox_install=failed code=usage' >&2
    exit 2
}

if [ "$#" -eq 0 ]; then
    DESTDIR=/
elif [ "$#" -eq 2 ] && [ "$1" = "--destdir" ]; then
    case "$2" in
        /*) ;;
        *) usage ;;
    esac

    DESTDIR=$(
        /usr/bin/env -i PATH="$PATH" /usr/bin/python3 -I -c '
import os
import sys
candidate = sys.argv[1]
if not candidate.startswith("/") or any(ord(char) < 32 for char in candidate):
    raise SystemExit(2)
print(os.path.realpath(candidate))
' "$2"
    ) || usage
else
    usage
fi
[ -n "$DESTDIR" ] || usage
PREVIEW_FAIL_AFTER=0
PREVIEW_FAIL_POINT=none
if [ "$DESTDIR" != / ]; then
    case "${MEDCHAT_INSTALL_PREVIEW_FAIL_AFTER-0}" in
        0|1|2|3|4|5|6|7)
            PREVIEW_FAIL_AFTER=${MEDCHAT_INSTALL_PREVIEW_FAIL_AFTER-0}
            ;;
        *) fail preview_failure_invalid ;;
    esac
    case "${MEDCHAT_INSTALL_PREVIEW_FAIL_POINT-none}" in
        none|fsync_parent|post_replace_lstat)
            PREVIEW_FAIL_POINT=${MEDCHAT_INSTALL_PREVIEW_FAIL_POINT-none}
            ;;
        *) fail preview_failure_invalid ;;
    esac
fi

source_is_regular_and_locked() {
    source_path=$1
    [ ! -L "$source_path" ] || return 1
    [ -f "$source_path" ] || return 1
    owner=$(stat -c %u -- "$source_path") || return 1
    [ "$owner" -eq 0 ] || return 1
    permissions=$(stat -c %a -- "$source_path")
    [ $((0$permissions & 0022)) -eq 0 ] || return 1
}

source_ancestors_are_locked() {
    source_path=$1
    case "$source_path" in
        /*) ;;
        *) return 1 ;;
    esac
    source_directory=$(dirname -- "$source_path") || return 1
    while :; do
        [ ! -L "$source_directory" ] || return 1
        [ -d "$source_directory" ] || return 1
        owner=$(stat -c %u -- "$source_directory") || return 1
        [ "$owner" -eq 0 ] || return 1
        permissions=$(stat -c %a -- "$source_directory") || return 1
        [ $((0$permissions & 0022)) -eq 0 ] || return 1
        [ "$source_directory" != / ] || break
        parent_directory=$(dirname -- "$source_directory") || return 1
        [ "$parent_directory" != "$source_directory" ] || return 1
        source_directory=$parent_directory
    done
}

run_systemctl() {
    timeout --signal=KILL "${SYSTEMCTL_TIMEOUT_SECONDS}s" \
        systemctl "$@" >/dev/null 2>&1
}

query_service_state() {
    service_name=$1
    state_output=
    query_status=0
    if state_output=$(
        timeout --signal=KILL "${SYSTEMCTL_TIMEOUT_SECONDS}s" \
            systemctl show --property=LoadState --property=ActiveState \
            "$service_name" 2>/dev/null
    ); then
        :
    else
        query_status=$?
    fi

    load_state=
    active_state=
    state_invalid=0
    while IFS= read -r property; do
        case "$property" in
            LoadState=loaded|LoadState=not-found)
                [ -z "$load_state" ] || state_invalid=1
                load_state=${property#LoadState=}
                ;;
            ActiveState=active|ActiveState=inactive)
                [ -z "$active_state" ] || state_invalid=1
                active_state=${property#ActiveState=}
                ;;
            *) state_invalid=1 ;;
        esac
    done <<EOF
$state_output
EOF

    if { [ "$query_status" -eq 0 ] || [ "$query_status" -eq 1 ]; } \
        && [ "$state_invalid" -eq 0 ] \
        && [ "$load_state" = not-found ] \
        && [ "$active_state" = inactive ]
    then
        printf '%s\n' absent
        return 0
    fi
    [ "$query_status" -eq 0 ] || fail service_state_query_failed
    [ "$state_invalid" -eq 0 ] || fail service_state_invalid
    [ "$load_state" = loaded ] || fail service_state_invalid
    case "$active_state" in
        active|inactive) printf '%s\n' "$active_state" ;;
        *) fail service_state_invalid ;;
    esac
}

query_medchat_service_state() {
    query_service_state medchat.service
}

query_temporal_worker_service_state() {
    query_service_state medchat-temporal-worker.service
}

query_opensandbox_service_state() {
    query_service_state medchat-opensandbox.service
}

query_sandbox_broker_service_state() {
    query_service_state medchat-sandbox-broker.service
}

confirm_inactive() {
    case "$1" in
        medchat.service) state=$(query_medchat_service_state) ;;
        medchat-temporal-worker.service)
            state=$(query_temporal_worker_service_state)
            ;;
        medchat-opensandbox.service) state=$(query_opensandbox_service_state) ;;
        medchat-sandbox-broker.service)
            state=$(query_sandbox_broker_service_state)
            ;;
        *) fail service_name_invalid ;;
    esac
    case "$state" in
        inactive|absent) ;;
        *) fail service_stop_unconfirmed ;;
    esac
}

medchat_service_is_inactive() {
    state=$(query_medchat_service_state) || return 1
    case "$state" in inactive|absent) return 0 ;; esac
    return 1
}

temporal_worker_service_is_inactive() {
    state=$(query_temporal_worker_service_state) || return 1
    case "$state" in inactive|absent) return 0 ;; esac
    return 1
}

opensandbox_service_is_inactive() {
    state=$(query_opensandbox_service_state) || return 1
    case "$state" in inactive|absent) return 0 ;; esac
    return 1
}

sandbox_broker_service_is_inactive() {
    state=$(query_sandbox_broker_service_state) || return 1
    case "$state" in inactive|absent) return 0 ;; esac
    return 1
}

force_quiesce_known_medchat_services() {
    BOTH_SERVICES_INACTIVE=0
    run_systemctl stop medchat-temporal-worker.service || :
    run_systemctl stop medchat.service || :
    run_systemctl stop medchat-sandbox-broker.service || :
    run_systemctl stop medchat-opensandbox.service || :
    if ! temporal_worker_service_is_inactive; then
        run_systemctl kill --kill-whom=all --signal=SIGKILL medchat-temporal-worker.service || :
        run_systemctl stop medchat-temporal-worker.service || :
    fi
    if ! medchat_service_is_inactive; then
        run_systemctl kill --kill-whom=all --signal=SIGKILL medchat.service || :
        run_systemctl stop medchat.service || :
    fi
    if ! sandbox_broker_service_is_inactive; then
        run_systemctl kill --kill-whom=all --signal=SIGKILL \
            medchat-sandbox-broker.service || :
        run_systemctl stop medchat-sandbox-broker.service || :
    fi
    if ! opensandbox_service_is_inactive; then
        run_systemctl kill --kill-whom=all --signal=SIGKILL \
            medchat-opensandbox.service || :
        run_systemctl stop medchat-opensandbox.service || :
    fi
    if temporal_worker_service_is_inactive \
        && medchat_service_is_inactive \
        && sandbox_broker_service_is_inactive \
        && opensandbox_service_is_inactive
    then
        BOTH_SERVICES_INACTIVE=1
        return 0
    fi
    return 1
}

quiesce_shared_medchat_services() {
    MEDCHAT_SERVICE_STATE=$(query_medchat_service_state)
    TEMPORAL_WORKER_SERVICE_STATE=$(query_temporal_worker_service_state)
    OPENSANDBOX_SERVICE_STATE=$(query_opensandbox_service_state)
    SANDBOX_BROKER_SERVICE_STATE=$(query_sandbox_broker_service_state)
    SERVICE_STATE_CAPTURED=1
    if [ "$TEMPORAL_WORKER_SERVICE_STATE" = active ]; then
        run_systemctl stop medchat-temporal-worker.service || fail service_stop_failed
    fi
    if [ "$MEDCHAT_SERVICE_STATE" = active ]; then
        run_systemctl stop medchat.service || fail service_stop_failed
    fi
    if [ "$SANDBOX_BROKER_SERVICE_STATE" = active ]; then
        run_systemctl stop medchat-sandbox-broker.service || fail service_stop_failed
    fi
    if [ "$OPENSANDBOX_SERVICE_STATE" = active ]; then
        run_systemctl stop medchat-opensandbox.service || fail service_stop_failed
    fi
    confirm_inactive medchat-temporal-worker.service
    confirm_inactive medchat.service
    confirm_inactive medchat-sandbox-broker.service
    confirm_inactive medchat-opensandbox.service
    BOTH_SERVICES_INACTIVE=1
}

quiesce_deployment_services() {
    quiesce_shared_medchat_services
}

restore_prior_medchat_services() {
    if [ "$OPENSANDBOX_SERVICE_STATE" = active ] \
        || [ "$SANDBOX_BROKER_SERVICE_STATE" = active ] \
        || [ "$TEMPORAL_WORKER_SERVICE_STATE" = active ]
    then
        run_systemctl start medchat-opensandbox.service || return 1
    fi
    if [ "$SANDBOX_BROKER_SERVICE_STATE" = active ] \
        || [ "$TEMPORAL_WORKER_SERVICE_STATE" = active ]
    then
        run_systemctl start medchat-sandbox-broker.service || return 1
    fi
    if [ "$MEDCHAT_SERVICE_STATE" = active ]; then
        run_systemctl start medchat.service || return 1
    fi
    if [ "$TEMPORAL_WORKER_SERVICE_STATE" = active ]; then
        run_systemctl start medchat-temporal-worker.service || return 1
    fi
}

secure_broker_credentials() {
    /usr/bin/env -i PATH="$PATH" /usr/bin/python3 -I - \
        "$BROKER_ENV_DIRECTORY" "$BROKER_ENV_NAME" >/dev/null 2>&1 <<'PY'
import os
import stat
import sys

directory = sys.argv[1]
name = sys.argv[2]
directory_descriptor = None
descriptor = None


class SecurityError(Exception):
    pass


def fail() -> None:
    raise SecurityError


def unlink_named_path() -> None:
    try:
        os.unlink(name, dir_fd=directory_descriptor)
    except FileNotFoundError:
        return
    try:
        os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return
    fail()


try:
    if name != "sandbox-broker.env" or "/" in name or "\x00" in name:
        fail()
    directory_descriptor = os.open(
        directory,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    directory_metadata = os.fstat(directory_descriptor)
    if (
        not stat.S_ISDIR(directory_metadata.st_mode)
        or stat.S_IMODE(directory_metadata.st_mode) & 0o022
        or directory_metadata.st_uid != os.geteuid()
    ):
        fail()
    try:
        named = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        raise SystemExit(0)
    if not stat.S_ISREG(named.st_mode) or named.st_nlink != 1:
        unlink_named_path()
        raise SystemExit(0)
    descriptor = os.open(
        name,
        os.O_WRONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
        dir_fd=directory_descriptor,
    )
    opened = os.fstat(descriptor)
    if (
        not stat.S_ISREG(opened.st_mode)
        or opened.st_nlink != 1
        or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
    ):
        fail()
    os.fchmod(descriptor, 0o600)
    if os.geteuid() == 0:
        os.fchown(descriptor, 0, 0)
    elif directory == "/etc/medchat":
        fail()
    final = os.fstat(descriptor)
    final_named = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
    expected_uid = 0 if os.geteuid() == 0 else os.geteuid()
    if (
        stat.S_IMODE(final.st_mode) != 0o600
        or final.st_uid != expected_uid
        or (final.st_dev, final.st_ino) != (final_named.st_dev, final_named.st_ino)
    ):
        fail()
except Exception:
    if directory_descriptor is not None:
        try:
            unlink_named_path()
        except (OSError, SecurityError):
            pass
    raise SystemExit(1)
finally:
    if descriptor is not None:
        try:
            os.close(descriptor)
        except OSError:
            pass
    if directory_descriptor is not None:
        try:
            os.close(directory_descriptor)
        except OSError:
            pass
PY
}

warn_credentials_locked_services_not_restored() {
    # Operators must rotate any credential that may have been read before migration.
    printf '%s\n' \
        'opensandbox_install=warning code=credentials_locked_services_not_restored' \
        >&2
}

warn_credential_lockdown_failed_services_not_restored() {
    printf '%s\n' \
        'opensandbox_install=warning code=credential_lockdown_failed_services_not_restored' \
        >&2
}

restore_stopped_services_on_failure() {
    status=$?
    trap - EXIT HUP INT TERM
    if [ "$SERVICE_STATE_CAPTURED" -eq 1 ]; then
        force_quiesce_known_medchat_services || :
        if [ "$BOTH_SERVICES_INACTIVE" -eq 1 ]; then
            remove_legacy_medchat_membership >/dev/null 2>&1 || :
        fi
        if secure_broker_credentials >/dev/null 2>&1; then
            warn_credentials_locked_services_not_restored
        else
            warn_credential_lockdown_failed_services_not_restored
        fi
    fi
    exit "$status"
}

remove_legacy_medchat_membership() {
    GROUP_MEMBERSHIP_REVOKED=0
    timeout --signal=KILL "${SYSTEMCTL_TIMEOUT_SECONDS}s" \
        gpasswd --delete medchat medchat-sandbox >/dev/null 2>&1 || :
    memberships=$(
        timeout --signal=KILL "${SYSTEMCTL_TIMEOUT_SECONDS}s" \
            id -nG medchat 2>/dev/null
    ) || return 1
    case " $memberships " in
        *" medchat-sandbox "*) return 1 ;;
    esac
    GROUP_MEMBERSHIP_REVOKED=1
}

if [ "$DESTDIR" = / ]; then
    [ "$(id -u)" -eq 0 ] || fail root_required
    for source_path in \
        "$SCRIPT_DIR/install.sh" \
        "$SCRIPT_DIR/sandbox.toml" \
        "$SCRIPT_DIR/medchat-opensandbox.service" \
        "$SCRIPT_DIR/medchat-sandbox-broker.service" \
        "$SCRIPT_DIR/medchat-opensandbox-firewall.service" \
        "$SCRIPT_DIR/configure-firewall.sh"
    do
        source_ancestors_are_locked "$source_path" || fail source_untrusted
        source_is_regular_and_locked "$source_path" || fail source_untrusted
    done

    getent group docker >/dev/null 2>&1 || fail docker_group_missing
    getent passwd medchat >/dev/null 2>&1 || fail medchat_user_missing

    getent group medchat-sandbox >/dev/null 2>&1 || \
        groupadd --system medchat-sandbox
    getent passwd medchat-sandbox >/dev/null 2>&1 || \
        useradd --system --gid medchat-sandbox \
            --home-dir /var/lib/medchat-sandbox --no-create-home \
            --shell /usr/sbin/nologin medchat-sandbox

    getent group medchat-opensandbox >/dev/null 2>&1 || \
        groupadd --system medchat-opensandbox
    getent passwd medchat-opensandbox >/dev/null 2>&1 || \
        useradd --system --gid medchat-opensandbox \
            --home-dir /var/lib/opensandbox --no-create-home \
            --shell /usr/sbin/nologin medchat-opensandbox

    trap restore_stopped_services_on_failure EXIT
    trap 'exit 1' HUP INT TERM
    quiesce_deployment_services
    remove_legacy_medchat_membership || fail group_migration_failed

fi

/usr/bin/env -i PATH="$PATH" /usr/bin/python3 -I - \
    "$DESTDIR" "$SCRIPT_DIR/sandbox.toml" \
    "$SCRIPT_DIR/medchat-opensandbox.service" \
    "$SCRIPT_DIR/medchat-sandbox-broker.service" \
    "$SCRIPT_DIR/medchat-opensandbox-firewall.service" \
    "$SCRIPT_DIR/configure-firewall.sh" \
    "$GROUP_MIGRATION_INTENT" "$PREVIEW_FAIL_AFTER" \
    "$PREVIEW_FAIL_POINT" <<'PY'
import json
import os
import re
import stat
import sys
import uuid
from pathlib import Path

root = Path(sys.argv[1])
template_path = Path(sys.argv[2])
opensandbox_unit_path = Path(sys.argv[3])
broker_unit_path = Path(sys.argv[4])
firewall_unit_path = Path(sys.argv[5])
firewall_script_path = Path(sys.argv[6])
group_migration_intent = sys.argv[7]
preview_fail_after = int(sys.argv[8])
preview_fail_point = sys.argv[9]

if group_migration_intent != "remove-medchat-from-medchat-sandbox":
    raise SystemExit(1)
if root == Path("/") and preview_fail_after != 0:
    raise SystemExit(1)
if root == Path("/") and preview_fail_point != "none":
    raise SystemExit(1)
if preview_fail_point not in {"none", "fsync_parent", "post_replace_lstat"}:
    raise SystemExit(1)


def rooted(absolute: str) -> Path:
    return root / absolute.lstrip("/")


def fail() -> None:
    raise SystemExit(1)


def require_locked_regular(
    path: Path,
    maximum_mode: int,
    *,
    expected_uid: int | None = None,
) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError:
        fail()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & ~maximum_mode
    ):
        fail()
    if (
        root == Path("/")
        and expected_uid is not None
        and metadata.st_uid != expected_uid
    ):
        fail()
    return metadata


def ensure_directory(
    path: Path,
    mode: int,
    uid: int,
    gid: int,
    *,
    require_empty: bool = False,
) -> None:
    descriptor = None
    try:
        relative = path.relative_to(root)
        root_metadata = os.lstat(root)
        if not stat.S_ISDIR(root_metadata.st_mode) or stat.S_ISLNK(root_metadata.st_mode):
            fail()
        current = root
        for part in relative.parts:
            current = current / part
            try:
                metadata = os.lstat(current)
            except FileNotFoundError:
                os.mkdir(current, mode)
                metadata = os.lstat(current)
            if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                fail()
        open_flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(path, open_flags)
        except PermissionError:
            # A non-root preview may be repeating an earlier mode-0000 install.
            # Its private, same-owner staging parent makes this recovery local
            # to preview mode; production root never needs this path.
            parent = os.lstat(path.parent)
            candidate = os.lstat(path)
            if (
                root == Path("/")
                or not require_empty
                or not stat.S_ISDIR(candidate.st_mode)
                or stat.S_ISLNK(candidate.st_mode)
                or candidate.st_uid != os.getuid()
                or not stat.S_ISDIR(parent.st_mode)
                or stat.S_ISLNK(parent.st_mode)
                or parent.st_uid != os.getuid()
                or stat.S_IMODE(parent.st_mode) & 0o022
            ):
                fail()
            candidate_identity = (candidate.st_dev, candidate.st_ino)
            os.chmod(path, 0o700)
            descriptor = os.open(path, open_flags)
            recovered = os.fstat(descriptor)
            if (recovered.st_dev, recovered.st_ino) != candidate_identity:
                fail()
        opened = os.fstat(descriptor)
        named = os.lstat(path)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or stat.S_ISLNK(named.st_mode)
            or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
        ):
            fail()
        if require_empty and os.listdir(descriptor):
            fail()
        os.fchmod(descriptor, mode)
        if root == Path("/"):
            os.fchown(descriptor, uid, gid)
        final = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(final.st_mode)
            or stat.S_IMODE(final.st_mode) != mode
        ):
            fail()
        if root == Path("/") and (final.st_uid != uid or final.st_gid != gid):
            fail()
    except (OSError, ValueError):
        fail()
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def parse_environment(path: Path) -> dict[str, str]:
    require_locked_regular(path, 0o600, expected_uid=0)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        fail()
    values: dict[str, str] = {}
    allowed = {"OPEN_SANDBOX_API_KEY", "MEDCHAT_SANDBOX_IMAGE"}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        if not separator or name not in allowed or name in values:
            fail()
        if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
            fail()
        values[name] = value
    if set(values) != allowed:
        fail()
    return values


def read_source(path: Path, *, expected_uid: int | None = None) -> bytes:
    descriptor = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        opened = os.fstat(descriptor)
        named = os.lstat(path)
        if (
            not stat.S_ISREG(opened.st_mode)
            or stat.S_ISLNK(named.st_mode)
            or opened.st_nlink != 1
            or stat.S_IMODE(opened.st_mode) & 0o022
            or (expected_uid is not None and opened.st_uid != expected_uid)
            or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
        ):
            fail()
        content = bytearray()
        while len(content) <= 1024 * 1024:
            chunk = os.read(descriptor, min(65536, 1024 * 1024 + 1 - len(content)))
            if not chunk:
                break
            content.extend(chunk)
        if len(content) > 1024 * 1024:
            fail()
        after = os.fstat(descriptor)
        if (
            (opened.st_dev, opened.st_ino, opened.st_mode, opened.st_size)
            != (after.st_dev, after.st_ino, after.st_mode, after.st_size)
        ):
            fail()
        return bytes(content)
    except OSError:
        fail()
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def fsync_parent(path: Path) -> None:
    parent_descriptor = None
    try:
        parent_descriptor = os.open(
            path.parent,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        os.fsync(parent_descriptor)
    except OSError:
        fail()
    finally:
        if parent_descriptor is not None:
            try:
                os.close(parent_descriptor)
            except OSError:
                pass


def stage_locked(path: Path, content: bytes, mode: int, uid: int, gid: int) -> Path:
    descriptor = None
    staged = path.with_name(f".{path.name}.medchat-{uuid.uuid4().hex}.stage")
    try:
        descriptor = os.open(
            staged,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
        ):
            fail()
        os.fchmod(descriptor, mode)
        if root == Path("/"):
            os.fchown(descriptor, uid, gid)
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                fail()
            view = view[written:]
        os.fsync(descriptor)
        final = os.fstat(descriptor)
        named = staged.lstat()
        expected_uid = uid if root == Path("/") else os.getuid()
        expected_gid = gid if root == Path("/") else os.getgid()
        if (
            not stat.S_ISREG(named.st_mode)
            or (final.st_dev, final.st_ino) != (named.st_dev, named.st_ino)
            or final.st_nlink != 1
            or stat.S_IMODE(final.st_mode) != mode
            or final.st_uid != expected_uid
            or final.st_gid != expected_gid
        ):
            fail()
        return staged
    except OSError:
        fail()
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def snapshot_target(path: Path, mode: int, uid: int) -> tuple[bytes, int, int, int] | None:
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    expected_uid = uid if root == Path("/") else None
    require_locked_regular(path, mode, expected_uid=expected_uid)
    metadata = path.lstat()
    return (
        read_source(path, expected_uid=expected_uid),
        stat.S_IMODE(metadata.st_mode),
        metadata.st_uid,
        metadata.st_gid,
    )


def rollback_generation(
    committed: list[tuple[Path, tuple[int, int]]],
    snapshots: dict[Path, tuple[bytes, int, int, int] | None],
) -> None:
    for path, installed_identity in reversed(committed):
        current = path.lstat()
        if (current.st_dev, current.st_ino) != installed_identity:
            fail()
        previous = snapshots[path]
        if previous is None:
            os.unlink(path)
            fsync_parent(path)
            continue
        content, mode, uid, gid = previous
        restored = stage_locked(path, content, mode, uid, gid)
        os.replace(restored, path)
        fsync_parent(path)


def stage_generation(
    entries: list[tuple[Path, bytes, int, int, int]],
) -> None:
    snapshots: dict[Path, tuple[bytes, int, int, int] | None] = {}
    staged: list[tuple[Path, Path, bytes]] = []
    committed: list[tuple[Path, tuple[int, int]]] = []
    try:
        for path, content, mode, uid, gid in entries:
            snapshots[path] = snapshot_target(path, mode, uid)
            temporary = stage_locked(path, content, mode, uid, gid)
            staged.append((path, temporary, content))
        for path, temporary, expected_content in staged:
            staged_metadata = temporary.lstat()
            os.replace(temporary, path)
            committed.append(
                (path, (staged_metadata.st_dev, staged_metadata.st_ino))
            )
            if preview_fail_point == "fsync_parent" and len(committed) == 1:
                fail()
            fsync_parent(path)
            if preview_fail_point == "post_replace_lstat" and len(committed) == 1:
                fail()
            installed = path.lstat()
            if (
                not stat.S_ISREG(installed.st_mode)
                or (installed.st_dev, installed.st_ino)
                != (staged_metadata.st_dev, staged_metadata.st_ino)
            ):
                fail()
            if read_source(path) != expected_content:
                fail()
            if preview_fail_after and len(committed) == preview_fail_after:
                fail()
    except BaseException:
        try:
            rollback_generation(committed, snapshots)
        except BaseException:
            pass
        raise
    finally:
        for _path, temporary, _expected_content in staged:
            try:
                os.unlink(temporary)
                fsync_parent(temporary)
            except FileNotFoundError:
                pass
            except OSError:
                pass


opensandbox_uid = os.getuid()
opensandbox_gid = os.getgid()
broker_uid = os.getuid()
broker_gid = os.getgid()
if root == Path("/"):
    import grp
    import pwd

    try:
        opensandbox_account = pwd.getpwnam("medchat-opensandbox")
        broker_account = pwd.getpwnam("medchat-sandbox")
        opensandbox_uid = opensandbox_account.pw_uid
        opensandbox_gid = grp.getgrnam("medchat-opensandbox").gr_gid
        broker_uid = broker_account.pw_uid
        broker_gid = grp.getgrnam("medchat-sandbox").gr_gid
    except (KeyError, OSError):
        fail()

ensure_directory(rooted("/etc/medchat"), 0o755, 0, 0)
ensure_directory(
    rooted("/var/lib/opensandbox"), 0o700, opensandbox_uid, opensandbox_gid
)
ensure_directory(
    rooted("/run/opensandbox"), 0o700, opensandbox_uid, opensandbox_gid
)
ensure_directory(
    rooted("/var/lib/opensandbox/approved-empty"),
    0o000,
    0,
    0,
    require_empty=True,
)
ensure_directory(
    rooted("/var/lib/medchat-sandbox"), 0o700, broker_uid, broker_gid
)
ensure_directory(
    rooted("/run/medchat-sandbox"), 0o750, broker_uid, broker_gid
)
ensure_directory(rooted("/etc/systemd/system"), 0o755, 0, 0)
ensure_directory(rooted("/usr/local/libexec"), 0o755, 0, 0)
ensure_directory(rooted("/usr/local/libexec/medchat"), 0o755, 0, 0)

environment_path = rooted("/etc/medchat/opensandbox.env")
values = parse_environment(environment_path)
secret = values["OPEN_SANDBOX_API_KEY"]
image = values["MEDCHAT_SANDBOX_IMAGE"]
if re.fullmatch(r"[A-Za-z0-9_-]{32,256}", secret) is None:
    fail()
if secret == "REPLACE_WITH_RANDOM_BASE64URL_SECRET_000000000000":
    fail()
image_match = re.fullmatch(r"([^\s@]+)@sha256:([0-9a-f]{64})", image)
if image_match is None or set(image_match.group(2)) == {"0"}:
    fail()
if image_match.group(1).startswith("registry.invalid/"):
    fail()

try:
    source_expected_uid = 0 if root == Path("/") else None
    template_source = read_source(template_path, expected_uid=source_expected_uid)
    opensandbox_unit = read_source(
        opensandbox_unit_path, expected_uid=source_expected_uid
    )
    broker_unit = read_source(broker_unit_path, expected_uid=source_expected_uid)
    firewall_unit = read_source(
        firewall_unit_path, expected_uid=source_expected_uid
    )
    firewall_script = read_source(
        firewall_script_path, expected_uid=source_expected_uid
    )
except UnicodeError:
    fail()
committed_assets = (
    template_source,
    opensandbox_unit,
    broker_unit,
    firewall_unit,
    firewall_script,
)
if any(b"\r" in content for content in committed_assets):
    fail()
try:
    template = template_source.decode("utf-8")
except UnicodeError:
    fail()
token = '"${' + 'OPEN_SANDBOX_API_KEY' + '}"'
if template.count(token) != 1:
    fail()
rendered = template.replace(token, json.dumps(secret, ensure_ascii=True))

broker_environment = (
    "OPEN_SANDBOX_API_KEY=" + secret + "\n"
    "MEDCHAT_SANDBOX_IMAGE=" + image + "\n"
).encode("utf-8")
stage_generation(
    [
        (
            rooted("/etc/medchat/opensandbox.toml"),
            rendered.encode("utf-8"),
            0o600,
            opensandbox_uid,
            opensandbox_gid,
        ),
        (
            rooted("/etc/medchat/sandbox-broker.env"),
            broker_environment,
            0o640,
            0,
            broker_gid,
        ),
        (
            rooted("/etc/systemd/system/medchat-opensandbox-firewall.service"),
            firewall_unit,
            0o644,
            0,
            0,
        ),
        (
            rooted("/etc/systemd/system/medchat-opensandbox.service"),
            opensandbox_unit,
            0o644,
            0,
            0,
        ),
        (
            rooted("/etc/systemd/system/medchat-sandbox-broker.service"),
            broker_unit,
            0o644,
            0,
            0,
        ),
        (
            rooted("/usr/local/libexec/medchat/configure-opensandbox-firewall"),
            firewall_script,
            0o755,
            0,
            0,
        ),
    ]
)
PY

if [ "$DESTDIR" = / ]; then
    run_systemctl daemon-reload
    run_systemctl enable medchat-opensandbox-firewall.service \
        medchat-opensandbox.service medchat-sandbox-broker.service
    run_systemctl restart medchat-opensandbox-firewall.service
    restore_prior_medchat_services || fail service_restore_failed
    trap - EXIT HUP INT TERM
fi

printf '%s\n' 'opensandbox_install=passed'
