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
LIVE_SERVICES_QUIESCED=0
GROUP_MEMBERSHIP_REVOKED=0

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

source_is_regular_and_locked() {
    source_path=$1
    [ ! -L "$source_path" ] || return 1
    [ -f "$source_path" ] || return 1
    permissions=$(stat -c %a -- "$source_path")
    [ $((0$permissions & 0022)) -eq 0 ] || return 1
}

run_systemctl() {
    timeout --signal=KILL "${SYSTEMCTL_TIMEOUT_SECONDS}s" \
        systemctl "$@" >/dev/null 2>&1
}

query_medchat_service_state() {
    state=$(
        timeout --signal=KILL "${SYSTEMCTL_TIMEOUT_SECONDS}s" \
            systemctl show --property=ActiveState --value medchat.service \
            2>/dev/null
    ) || fail service_state_query_failed
    case "$state" in
        active|inactive) printf '%s\n' "$state" ;;
        *) fail service_state_invalid ;;
    esac
}

query_temporal_worker_service_state() {
    state=$(
        timeout --signal=KILL "${SYSTEMCTL_TIMEOUT_SECONDS}s" \
            systemctl show --property=ActiveState --value medchat-temporal-worker.service \
            2>/dev/null
    ) || fail service_state_query_failed
    case "$state" in
        active|inactive) printf '%s\n' "$state" ;;
        *) fail service_state_invalid ;;
    esac
}

confirm_inactive() {
    case "$1" in
        medchat.service) state=$(query_medchat_service_state) ;;
        medchat-temporal-worker.service)
            state=$(query_temporal_worker_service_state)
            ;;
        *) fail service_name_invalid ;;
    esac
    [ "$state" = inactive ] || fail service_stop_unconfirmed
}

quiesce_shared_medchat_services() {
    MEDCHAT_SERVICE_STATE=$(query_medchat_service_state)
    TEMPORAL_WORKER_SERVICE_STATE=$(query_temporal_worker_service_state)
    LIVE_SERVICES_QUIESCED=1
    if [ "$TEMPORAL_WORKER_SERVICE_STATE" = active ]; then
        run_systemctl stop medchat-temporal-worker.service || fail service_stop_failed
    fi
    if [ "$MEDCHAT_SERVICE_STATE" = active ]; then
        run_systemctl stop medchat.service || fail service_stop_failed
    fi
    confirm_inactive medchat-temporal-worker.service
    confirm_inactive medchat.service
}

restore_prior_medchat_services() {
    if [ "$MEDCHAT_SERVICE_STATE" = active ]; then
        run_systemctl start medchat.service || return 1
    fi
    if [ "$TEMPORAL_WORKER_SERVICE_STATE" = active ]; then
        run_systemctl start medchat-temporal-worker.service || return 1
    fi
}

restore_stopped_services_on_failure() {
    status=$?
    trap - EXIT HUP INT TERM
    if [ "$LIVE_SERVICES_QUIESCED" -eq 1 ]; then
        run_systemctl stop medchat-temporal-worker.service || :
        run_systemctl stop medchat.service || :
        remove_legacy_medchat_membership >/dev/null 2>&1 || :
        if [ "$GROUP_MEMBERSHIP_REVOKED" -eq 1 ]; then
            restore_prior_medchat_services || :
        fi
    fi
    exit "$status"
}

remove_legacy_medchat_membership() {
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
        "$0" \
        "$SCRIPT_DIR/sandbox.toml" \
        "$SCRIPT_DIR/medchat-opensandbox.service" \
        "$SCRIPT_DIR/medchat-sandbox-broker.service" \
        "$SCRIPT_DIR/../medchat-temporal-worker.service"
    do
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

    usermod --append --groups docker medchat-opensandbox
    trap restore_stopped_services_on_failure EXIT
    trap 'exit 1' HUP INT TERM
    quiesce_shared_medchat_services
    remove_legacy_medchat_membership || fail group_migration_failed

fi

/usr/bin/env -i PATH="$PATH" /usr/bin/python3 -I - \
    "$DESTDIR" "$SCRIPT_DIR/sandbox.toml" \
    "$SCRIPT_DIR/medchat-opensandbox.service" \
    "$SCRIPT_DIR/medchat-sandbox-broker.service" \
    "$SCRIPT_DIR/../medchat-temporal-worker.service" \
    "$GROUP_MIGRATION_INTENT" <<'PY'
import json
import os
import re
import stat
import sys
from pathlib import Path

root = Path(sys.argv[1])
template_path = Path(sys.argv[2])
opensandbox_unit_path = Path(sys.argv[3])
broker_unit_path = Path(sys.argv[4])
worker_unit_path = Path(sys.argv[5])
group_migration_intent = sys.argv[6]

if group_migration_intent != "remove-medchat-from-medchat-sandbox":
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


def read_source(path: Path) -> bytes:
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


def write_locked(path: Path, content: bytes, mode: int, uid: int, gid: int) -> None:
    flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = None
    try:
        if path.exists() or path.is_symlink():
            require_locked_regular(path, mode, expected_uid=uid)
        descriptor = os.open(path, flags, mode)
        opened = os.fstat(descriptor)
        named = path.lstat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
        ):
            fail()
        os.fchmod(descriptor, mode)
        if root == Path("/"):
            os.fchown(descriptor, uid, gid)
        os.ftruncate(descriptor, 0)
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                fail()
            view = view[written:]
        os.fsync(descriptor)
    except OSError:
        fail()
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
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

environment_path = rooted("/etc/medchat/opensandbox.env")
values = parse_environment(environment_path)
secret = values["OPEN_SANDBOX_API_KEY"]
image = values["MEDCHAT_SANDBOX_IMAGE"]
if len(secret) < 32 or len(secret) > 256:
    fail()
if secret == "replace-with-random-secret":
    fail()
image_match = re.fullmatch(r"([^\s@]+)@sha256:([0-9a-f]{64})", image)
if image_match is None or set(image_match.group(2)) == {"0"}:
    fail()
if image_match.group(1).startswith("registry.invalid/"):
    fail()

try:
    template = read_source(template_path).decode("utf-8")
    opensandbox_unit = read_source(opensandbox_unit_path)
    broker_unit = read_source(broker_unit_path)
    worker_unit = read_source(worker_unit_path)
except UnicodeError:
    fail()
token = '"${' + 'OPEN_SANDBOX_API_KEY' + '}"'
if template.count(token) != 1:
    fail()
rendered = template.replace(token, json.dumps(secret, ensure_ascii=True))

write_locked(
    rooted("/etc/medchat/opensandbox.toml"),
    rendered.encode("utf-8"),
    0o600,
    opensandbox_uid,
    opensandbox_gid,
)
broker_environment = (
    "OPEN_SANDBOX_API_KEY=" + secret + "\n"
    "MEDCHAT_SANDBOX_IMAGE=" + image + "\n"
).encode("utf-8")
write_locked(
    rooted("/etc/medchat/sandbox-broker.env"),
    broker_environment,
    0o640,
    0,
    broker_gid,
)
write_locked(
    rooted("/etc/systemd/system/medchat-opensandbox.service"),
    opensandbox_unit,
    0o644,
    0,
    0,
)
write_locked(
    rooted("/etc/systemd/system/medchat-sandbox-broker.service"),
    broker_unit,
    0o644,
    0,
    0,
)
write_locked(
    rooted("/etc/systemd/system/medchat-temporal-worker.service"),
    worker_unit,
    0o644,
    0,
    0,
)
PY

if [ "$DESTDIR" = / ]; then
    run_systemctl daemon-reload
    run_systemctl enable medchat-opensandbox.service medchat-sandbox-broker.service
    restore_prior_medchat_services || fail service_restore_failed
    LIVE_SERVICES_QUIESCED=0
    trap - EXIT HUP INT TERM
fi

printf '%s\n' 'opensandbox_install=passed'
