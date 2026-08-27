#!/usr/bin/python3
"""Stage one immutable content-addressed Temporal worker generation."""

from __future__ import annotations

import errno
try:
    import fcntl
except ImportError:  # pragma: no cover - production is Linux-only
    fcntl = None  # type: ignore[assignment]
import hashlib
import os
import re
import secrets
import signal
import stat
import struct
import subprocess
import sys
import time


BUNDLE_PREFIX = b"MEDCHAT_TEMPORAL_BUNDLE_V1\0"
MANIFEST_PREFIX = "MEDCHAT_TEMPORAL_BUNDLE_V1"
MAX_ASSET_BYTES = 2 * 1024 * 1024
MAX_BUNDLE_BYTES = 8 * 1024 * 1024
MAX_MANIFEST_BYTES = 16 * 1024
ASSET_SPECS = (
    (
        "libexec/prepare-temporal-worker-directories",
        0o755,
        "libexec/prepare-temporal-worker-directories.py",
    ),
    (
        "libexec/validate-temporal-worker-env",
        0o755,
        "libexec/validate-temporal-worker-env.py",
    ),
    (
        "units/medchat-temporal-worker-prepare.service",
        0o644,
        "medchat-temporal-worker-prepare.service",
    ),
    (
        "units/medchat-temporal-worker.service",
        0o644,
        "medchat-temporal-worker.service",
    ),
)
EXPECTED_PATHS = tuple(spec[0] for spec in ASSET_SPECS)
LOCK_RELATIVE = "run/medchat-temporal-worker/install.lock"
RELEASES_RELATIVE = "usr/lib/medchat/temporal-worker/releases"
BASE_RELATIVE = "usr/lib/medchat/temporal-worker"
BOOTSTRAP_LINKS = {
    "etc/systemd/system/medchat-temporal-worker.service": (
        "/usr/lib/medchat/temporal-worker/current/units/"
        "medchat-temporal-worker.service"
    ),
    "etc/systemd/system/medchat-temporal-worker-prepare.service": (
        "/usr/lib/medchat/temporal-worker/current/units/"
        "medchat-temporal-worker-prepare.service"
    ),
}
LEGACY_TMPFILES_RELATIVE = "usr/lib/tmpfiles.d/medchat-temporal-worker.conf"
LEGACY_QUARANTINE_RELATIVE = (
    "usr/lib/medchat/temporal-worker/quarantine/legacy-tmpfiles.disabled"
)
LEGACY_ASSETS = {
    "etc/systemd/system/medchat-temporal-worker.service": (
        0o644,
        "b0e389760fcadb31f6828974f66ca2bc8db2a99f27150cf2f3019e10386d1b20",
    ),
    "etc/systemd/system/medchat-temporal-worker-prepare.service": (
        0o644,
        "7c9a1aef6012d655492b6135d66c4741a3fd2593f319d01cdcda9cf3feffe18a",
    ),
    "usr/libexec/medchat/validate-temporal-worker-env": (
        0o755,
        "c1b9b720e81ee00011403c87781b59da9d3917865ff131039284b233b54de9dc",
    ),
    LEGACY_TMPFILES_RELATIVE: (
        0o644,
        "e76763839fdd6a07ec89130da442840230fd165b9726f23e1174477e7f538ed8",
    ),
}
WORKER_UNIT = "medchat-temporal-worker.service"
PREPARE_UNIT = "medchat-temporal-worker-prepare.service"
PHASE_STOPPING = "stopping"
PHASE_PRE_SWITCH = "pre-switch"
PHASE_SWITCHED = "switched"
PHASE_RELOADED = "reloaded"
PHASE_PREPARE_STARTING = "prepare-starting"
PHASE_WORKER_STARTING = "worker-starting"
PHASE_COMPLETE = "complete"
_ROLLBACK_INTERRUPT_AUTHORITY = object()
_LOWER_HEX = re.compile(r"[0-9a-f]{64}\Z")
_STABLE_CODES = frozenset(
    {
        "bundle_installation_failed",
        "bundle_arguments_invalid",
        "bundle_destination_untrusted",
        "bundle_lock_untrusted",
        "bundle_source_untrusted",
        "bundle_source_changed",
        "bundle_source_too_large",
        "bundle_manifest_invalid",
        "bundle_generation_invalid",
        "bundle_publish_failed",
        "bundle_interrupted",
        "activation_arguments_invalid",
        "activation_root_required",
        "activation_destination_untrusted",
        "activation_generation_invalid",
        "activation_current_invalid",
        "activation_bootstrap_invalid",
        "activation_systemctl_failed",
        "activation_interrupted",
        "activation_rollback_failed",
        "legacy_migration_unsupported",
        "legacy_migration_manual_recovery_required",
    }
)
_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_READ_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_NONBLOCK", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_CREATE_FLAGS = (
    os.O_WRONLY
    | os.O_CREAT
    | os.O_EXCL
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_INSTALL_SIGNALS = tuple(
    signum
    for name in ("SIGHUP", "SIGINT", "SIGTERM")
    if (signum := getattr(signal, name, None)) is not None
)


class BundleInstallError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code if code in _STABLE_CODES else "bundle_installation_failed"
        super().__init__(self.code)


class ManifestInfo:
    def __init__(self, bundle_digest: str, assets: dict[str, tuple[int, int, str]]) -> None:
        self.bundle_digest = bundle_digest
        self.assets = assets


class ActivationState:
    def __init__(self) -> None:
        self.phase = PHASE_PRE_SWITCH
        self.interrupted = False
        self.child: subprocess.Popen[bytes] | None = None


class ActivationSignalContext:
    def __init__(
        self,
        handlers: dict[int, object],
        previous_mask: set[signal.Signals],
    ) -> None:
        self.handlers = handlers
        self.previous_mask = previous_mask


class LinkTransaction:
    def __init__(
        self,
        relative: str,
        temporary_identity: tuple[int, int, int, int, int],
        target: str,
    ) -> None:
        self.relative = relative
        self.temporary_identity = temporary_identity
        self.published_inode_identity: tuple[int, int] | None = None
        self.published_identity: tuple[int, int, int, int, int] | None = None
        self.target = target

    def mark_published(self) -> None:
        self.published_inode_identity = self.temporary_identity[:2]

    def record_published(self, metadata: os.stat_result) -> bool:
        if _identity(metadata) != self.published_inode_identity:
            return False
        self.published_identity = _full_identity(metadata)
        return True


def _signal_failure(_signum: int, _frame: object) -> None:
    signal.pthread_sigmask(signal.SIG_BLOCK, _INSTALL_SIGNALS)
    raise BundleInstallError("bundle_interrupted")


def install_signal_handlers() -> None:
    for signum in _INSTALL_SIGNALS:
        signal.signal(signum, _signal_failure)


def _validate_asset_mapping(assets: dict[str, tuple[int, bytes]]) -> None:
    if set(assets) != set(EXPECTED_PATHS):
        raise BundleInstallError("bundle_manifest_invalid")
    total = 0
    for path, (mode, payload) in assets.items():
        if path not in EXPECTED_PATHS or mode < 0 or mode > 0o7777:
            raise BundleInstallError("bundle_manifest_invalid")
        if not isinstance(payload, bytes) or len(payload) > MAX_ASSET_BYTES:
            raise BundleInstallError("bundle_source_too_large")
        total += len(payload)
    if total > MAX_BUNDLE_BYTES:
        raise BundleInstallError("bundle_source_too_large")


def compute_bundle_digest(assets: dict[str, tuple[int, bytes]]) -> str:
    _validate_asset_mapping(assets)
    digest = hashlib.sha256()
    digest.update(BUNDLE_PREFIX)
    for path in sorted(assets, key=lambda item: item.encode("ascii")):
        try:
            encoded = path.encode("ascii")
        except UnicodeEncodeError as exc:
            raise BundleInstallError("bundle_manifest_invalid") from exc
        mode, payload = assets[path]
        digest.update(struct.pack(">I", len(encoded)))
        digest.update(encoded)
        digest.update(struct.pack(">I", mode))
        digest.update(struct.pack(">Q", len(payload)))
        digest.update(payload)
    return digest.hexdigest()


def build_manifest(assets: dict[str, tuple[int, bytes]], bundle_digest: str) -> bytes:
    _validate_asset_mapping(assets)
    if not _LOWER_HEX.fullmatch(bundle_digest):
        raise BundleInstallError("bundle_manifest_invalid")
    lines = [f"{MANIFEST_PREFIX} {bundle_digest}\n"]
    for path in sorted(assets, key=lambda item: item.encode("ascii")):
        mode, payload = assets[path]
        lines.append(
            f"{hashlib.sha256(payload).hexdigest()} {mode:04o} "
            f"{len(payload)} {path}\n"
        )
    manifest = "".join(lines).encode("ascii")
    if len(manifest) > MAX_MANIFEST_BYTES:
        raise BundleInstallError("bundle_manifest_invalid")
    return manifest


def parse_manifest(payload: bytes) -> ManifestInfo:
    if len(payload) > MAX_MANIFEST_BYTES or b"\r" in payload or not payload.endswith(b"\n"):
        raise BundleInstallError("bundle_manifest_invalid")
    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError as exc:
        raise BundleInstallError("bundle_manifest_invalid") from exc
    lines = text.splitlines()
    if len(lines) != 1 + len(ASSET_SPECS) or any(not line for line in lines):
        raise BundleInstallError("bundle_manifest_invalid")
    header = lines[0].split(" ")
    if len(header) != 2 or header[0] != MANIFEST_PREFIX or not _LOWER_HEX.fullmatch(header[1]):
        raise BundleInstallError("bundle_manifest_invalid")
    assets: dict[str, tuple[int, int, str]] = {}
    ordered: list[str] = []
    for line in lines[1:]:
        fields = line.split(" ")
        if len(fields) != 4:
            raise BundleInstallError("bundle_manifest_invalid")
        asset_digest, mode_text, size_text, path = fields
        if (
            not _LOWER_HEX.fullmatch(asset_digest)
            or not re.fullmatch(r"[0-7]{4}", mode_text)
            or (size_text != "0" and not re.fullmatch(r"[1-9][0-9]*", size_text))
            or path not in EXPECTED_PATHS
            or path.startswith("/")
            or any(part in {"", ".", ".."} for part in path.split("/"))
            or path in assets
        ):
            raise BundleInstallError("bundle_manifest_invalid")
        mode = int(mode_text, 8)
        size = int(size_text)
        if size > MAX_ASSET_BYTES:
            raise BundleInstallError("bundle_manifest_invalid")
        assets[path] = (mode, size, asset_digest)
        ordered.append(path)
    if tuple(ordered) != tuple(sorted(EXPECTED_PATHS, key=lambda item: item.encode("ascii"))):
        raise BundleInstallError("bundle_manifest_invalid")
    return ManifestInfo(header[1], assets)


def _identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _full_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _read_all(descriptor: int, limit: int, code: str) -> bytes:
    chunks: list[bytes] = []
    remaining = limit + 1
    while remaining:
        try:
            chunk = os.read(descriptor, min(65_536, remaining))
        except InterruptedError:
            continue
        except OSError as exc:
            raise BundleInstallError(code) from exc
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    payload = b"".join(chunks)
    if len(payload) > limit:
        raise BundleInstallError("bundle_source_too_large")
    return payload


def _open_absolute_directory(path: str) -> int:
    if not path.startswith("/") or os.path.realpath(path) != path:
        raise BundleInstallError("bundle_destination_untrusted")
    components = [part for part in path.split("/") if part]
    if any(part in {".", ".."} for part in components):
        raise BundleInstallError("bundle_destination_untrusted")
    descriptor = os.open("/", _DIRECTORY_FLAGS)
    try:
        for component in components:
            next_descriptor = os.open(component, _DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _validate_directory(metadata: os.stat_result, uid: int, gid: int, exact_mode: int | None = None) -> None:
    mode = stat.S_IMODE(metadata.st_mode)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or (metadata.st_uid, metadata.st_gid) != (uid, gid)
        or (exact_mode is not None and mode != exact_mode)
        or (exact_mode is None and mode & 0o022)
    ):
        raise BundleInstallError("bundle_destination_untrusted")


def _validate_generation_directory_metadata(
    metadata: os.stat_result, uid: int, gid: int
) -> None:
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or (metadata.st_uid, metadata.st_gid) != (uid, gid)
        or stat.S_IMODE(metadata.st_mode) != 0o555
    ):
        raise BundleInstallError("bundle_generation_invalid")


def _open_or_create_directory(
    parent: int,
    name: str,
    mode: int,
    uid: int,
    gid: int,
    *,
    exact: bool = False,
) -> int:
    created = False
    try:
        os.mkdir(name, mode, dir_fd=parent)
        created = True
    except FileExistsError:
        pass
    try:
        descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent)
    except OSError as exc:
        raise BundleInstallError("bundle_destination_untrusted") from exc
    try:
        if created:
            os.fchown(descriptor, uid, gid)
            os.fchmod(descriptor, mode)
            os.fsync(descriptor)
            os.fsync(parent)
        metadata = os.fstat(descriptor)
        _validate_directory(metadata, uid, gid, mode if exact else None)
        entry = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if _identity(metadata) != _identity(entry):
            raise BundleInstallError("bundle_destination_untrusted")
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _open_tree(root: int, components: tuple[str, ...], uid: int, gid: int) -> int:
    current = os.dup(root)
    try:
        for component in components:
            next_descriptor = _open_or_create_directory(current, component, 0o755, uid, gid)
            os.close(current)
            current = next_descriptor
        return current
    except Exception:
        os.close(current)
        raise


def _acquire_lock(root: int, uid: int, gid: int) -> tuple[int, int]:
    if fcntl is None:
        raise BundleInstallError("bundle_lock_untrusted")
    try:
        run_descriptor = _open_or_create_directory(root, "run", 0o755, uid, gid)
    except BundleInstallError as exc:
        raise BundleInstallError("bundle_lock_untrusted") from exc
    try:
        _validate_directory(os.fstat(run_descriptor), uid, gid)
        lock_directory = _open_or_create_directory(
            run_descriptor,
            "medchat-temporal-worker",
            0o700,
            uid,
            gid,
            exact=True,
        )
    finally:
        os.close(run_descriptor)
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    created = False
    lock = -1
    try:
        try:
            lock = os.open("install.lock", flags, 0o600, dir_fd=lock_directory)
            created = True
        except FileExistsError:
            try:
                lock = os.open(
                    "install.lock",
                    os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=lock_directory,
                )
            except OSError as exc:
                raise BundleInstallError("bundle_lock_untrusted") from exc
        if created:
            os.fchown(lock, uid, gid)
            os.fchmod(lock, 0o600)
            os.fsync(lock)
            os.fsync(lock_directory)
        metadata = os.fstat(lock)
        entry = os.stat("install.lock", dir_fd=lock_directory, follow_symlinks=False)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or (metadata.st_uid, metadata.st_gid) != (uid, gid)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or _identity(metadata) != _identity(entry)
        ):
            raise BundleInstallError("bundle_lock_untrusted")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
        except OSError as exc:
            raise BundleInstallError("bundle_lock_untrusted") from exc
        return lock_directory, lock
    except Exception:
        if lock >= 0:
            os.close(lock)
        os.close(lock_directory)
        raise


def _read_source_file(source_root: int, relative: str, live: bool) -> bytes:
    components = relative.split("/")
    parent = os.dup(source_root)
    try:
        for component in components[:-1]:
            next_parent = os.open(component, _DIRECTORY_FLAGS, dir_fd=parent)
            os.close(parent)
            parent = next_parent
        descriptor = os.open(components[-1], _READ_FLAGS, dir_fd=parent)
        try:
            before = os.fstat(descriptor)
            entry_before = os.stat(
                components[-1], dir_fd=parent, follow_symlinks=False
            )
            if (
                not stat.S_ISREG(before.st_mode)
                or _identity(before) != _identity(entry_before)
                or (live and ((before.st_uid, before.st_gid) != (0, 0) or stat.S_IMODE(before.st_mode) & 0o022))
            ):
                raise BundleInstallError("bundle_source_untrusted")
            payload = _read_all(descriptor, MAX_ASSET_BYTES, "bundle_source_untrusted")
            after = os.fstat(descriptor)
            entry_after = os.stat(
                components[-1], dir_fd=parent, follow_symlinks=False
            )
            if (
                _full_identity(before) != _full_identity(after)
                or _identity(after) != _identity(entry_after)
            ):
                raise BundleInstallError("bundle_source_changed")
            return payload
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise BundleInstallError("bundle_source_untrusted") from exc
    finally:
        os.close(parent)


def read_source_assets(source_root: int, live: bool) -> dict[str, tuple[int, bytes]]:
    assets: dict[str, tuple[int, bytes]] = {}
    total = 0
    for logical, mode, source in ASSET_SPECS:
        payload = _read_source_file(source_root, source, live)
        total += len(payload)
        if total > MAX_BUNDLE_BYTES:
            raise BundleInstallError("bundle_source_too_large")
        assets[logical] = (mode, payload)
    return assets


def _write_file(parent: int, name: str, payload: bytes, mode: int, uid: int, gid: int) -> None:
    descriptor = os.open(name, _CREATE_FLAGS, mode, dir_fd=parent)
    try:
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise BundleInstallError("bundle_publish_failed")
            offset += written
        os.fchown(descriptor, uid, gid)
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        entry = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or (metadata.st_uid, metadata.st_gid) != (uid, gid)
            or stat.S_IMODE(metadata.st_mode) != mode
            or metadata.st_size != len(payload)
            or _identity(metadata) != _identity(entry)
        ):
            raise BundleInstallError("bundle_publish_failed")
    finally:
        os.close(descriptor)


def _read_named_file(parent: int, name: str, limit: int) -> tuple[bytes, os.stat_result]:
    descriptor = os.open(name, _READ_FLAGS, dir_fd=parent)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise BundleInstallError("bundle_generation_invalid")
        payload = _read_all(descriptor, limit, "bundle_generation_invalid")
        after = os.fstat(descriptor)
        entry = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (
            _full_identity(before) != _full_identity(after)
            or _identity(after) != _identity(entry)
        ):
            raise BundleInstallError("bundle_generation_invalid")
        return payload, after
    finally:
        os.close(descriptor)


def _open_relative_directory(parent: int, path: str) -> int:
    current = os.dup(parent)
    try:
        for component in path.split("/"):
            next_descriptor = os.open(component, _DIRECTORY_FLAGS, dir_fd=current)
            os.close(current)
            current = next_descriptor
        return current
    except Exception:
        os.close(current)
        raise


def verify_generation(releases: int, digest: str, uid: int, gid: int) -> None:
    if not _LOWER_HEX.fullmatch(digest):
        raise BundleInstallError("bundle_generation_invalid")
    try:
        generation = os.open(digest, _DIRECTORY_FLAGS, dir_fd=releases)
    except OSError as exc:
        raise BundleInstallError("bundle_generation_invalid") from exc
    try:
        metadata = os.fstat(generation)
        _validate_generation_directory_metadata(metadata, uid, gid)
        entry = os.stat(digest, dir_fd=releases, follow_symlinks=False)
        if _identity(metadata) != _identity(entry):
            raise BundleInstallError("bundle_generation_invalid")
        generation_directories: dict[str, int] = {}
        for name in ("units", "libexec"):
            try:
                descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=generation)
            except OSError as exc:
                raise BundleInstallError("bundle_generation_invalid") from exc
            try:
                directory_metadata = os.fstat(descriptor)
                directory_entry = os.stat(
                    name, dir_fd=generation, follow_symlinks=False
                )
                _validate_generation_directory_metadata(
                    directory_metadata, uid, gid
                )
                if _identity(directory_metadata) != _identity(directory_entry):
                    raise BundleInstallError("bundle_generation_invalid")
                generation_directories[name] = descriptor
            except Exception:
                os.close(descriptor)
                raise
        manifest_payload, manifest_meta = _read_named_file(
            generation, "manifest.sha256", MAX_MANIFEST_BYTES
        )
        if stat.S_IMODE(manifest_meta.st_mode) != 0o644 or (manifest_meta.st_uid, manifest_meta.st_gid) != (uid, gid):
            raise BundleInstallError("bundle_generation_invalid")
        manifest = parse_manifest(manifest_payload)
        if manifest.bundle_digest != digest:
            raise BundleInstallError("bundle_generation_invalid")
        assets: dict[str, tuple[int, bytes]] = {}
        for path, expected_mode, _source in ASSET_SPECS:
            parent_path, leaf = path.rsplit("/", 1)
            payload, asset_meta = _read_named_file(
                generation_directories[parent_path], leaf, MAX_ASSET_BYTES
            )
            declared_mode, declared_size, declared_hash = manifest.assets[path]
            if (
                declared_mode != expected_mode
                or declared_size != len(payload)
                or declared_hash != hashlib.sha256(payload).hexdigest()
                or stat.S_IMODE(asset_meta.st_mode) != expected_mode
                or (asset_meta.st_uid, asset_meta.st_gid) != (uid, gid)
            ):
                raise BundleInstallError("bundle_generation_invalid")
            assets[path] = (expected_mode, payload)
        if compute_bundle_digest(assets) != digest:
            raise BundleInstallError("bundle_generation_invalid")
    finally:
        for descriptor in locals().get("generation_directories", {}).values():
            os.close(descriptor)
        os.close(generation)


def _parse_fragment_path(
    payload: bytes,
    root_path: str,
    digest: str,
    unit: str,
) -> tuple[str, ...]:
    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError as exc:
        raise BundleInstallError("activation_systemctl_failed") from exc
    if text.endswith("\n"):
        text = text[:-1]
    if (
        not text
        or "\n" in text
        or "\r" in text
        or "\x00" in text
        or not text.startswith("/")
        or not _LOWER_HEX.fullmatch(digest)
        or unit not in {PREPARE_UNIT, WORKER_UNIT}
    ):
        raise BundleInstallError("activation_systemctl_failed")
    root_components = tuple(part for part in root_path.split("/") if part)
    path_components = tuple(text.split("/")[1:])
    if any(part in {"", ".", ".."} for part in path_components):
        raise BundleInstallError("activation_systemctl_failed")
    if root_path != "/":
        if path_components[: len(root_components)] != root_components:
            raise BundleInstallError("activation_systemctl_failed")
        path_components = path_components[len(root_components) :]
    expected = tuple(
        f"usr/lib/medchat/temporal-worker/releases/{digest}/units/{unit}".split("/")
    )
    allowed = {
        expected,
        tuple(f"usr/lib/medchat/temporal-worker/current/units/{unit}".split("/")),
        ("etc", "systemd", "system", unit),
    }
    if path_components not in allowed:
        raise BundleInstallError("activation_systemctl_failed")
    return path_components


def _open_fragment_target(
    root: int,
    components: tuple[str, ...],
    digest: str,
    unit: str,
    uid: int,
    gid: int,
) -> int:
    expected = tuple(
        f"usr/lib/medchat/temporal-worker/releases/{digest}/units/{unit}".split("/")
    )
    pending = list(components)
    resolved: list[str] = []
    current = os.dup(root)
    symlinks = 0
    try:
        while pending:
            component = pending.pop(0)
            try:
                entry = os.stat(component, dir_fd=current, follow_symlinks=False)
            except OSError as exc:
                raise BundleInstallError("activation_systemctl_failed") from exc
            if stat.S_ISLNK(entry.st_mode):
                if (entry.st_uid, entry.st_gid) != (uid, gid):
                    raise BundleInstallError("activation_systemctl_failed")
                symlinks += 1
                if symlinks > 8:
                    raise BundleInstallError("activation_systemctl_failed")
                target = os.readlink(component, dir_fd=current)
                absolute = target.startswith("/")
                target_components = tuple(part for part in target.split("/") if part)
                if (
                    not target_components
                    or any(part in {".", ".."} for part in target_components)
                    or "//" in target
                ):
                    raise BundleInstallError("activation_systemctl_failed")
                if absolute:
                    os.close(current)
                    current = os.dup(root)
                    resolved = []
                pending = list(target_components) + pending
                continue
            if pending:
                if not stat.S_ISDIR(entry.st_mode):
                    raise BundleInstallError("activation_systemctl_failed")
                try:
                    next_descriptor = os.open(
                        component, _DIRECTORY_FLAGS, dir_fd=current
                    )
                except OSError as exc:
                    raise BundleInstallError("activation_systemctl_failed") from exc
                descriptor_metadata = os.fstat(next_descriptor)
                if _identity(descriptor_metadata) != _identity(entry):
                    os.close(next_descriptor)
                    raise BundleInstallError("activation_systemctl_failed")
                os.close(current)
                current = next_descriptor
                resolved.append(component)
                continue
            if tuple((*resolved, component)) != expected:
                raise BundleInstallError("activation_systemctl_failed")
            try:
                descriptor = os.open(component, _READ_FLAGS, dir_fd=current)
            except OSError as exc:
                raise BundleInstallError("activation_systemctl_failed") from exc
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or (metadata.st_uid, metadata.st_gid) != (uid, gid)
                or stat.S_IMODE(metadata.st_mode) != 0o644
                or _identity(metadata) != _identity(entry)
            ):
                os.close(descriptor)
                raise BundleInstallError("activation_systemctl_failed")
            return descriptor
        raise BundleInstallError("activation_systemctl_failed")
    finally:
        os.close(current)


def _open_parent(root: int, relative: str, uid: int, gid: int) -> tuple[int, str]:
    components = relative.split("/")
    if any(component in {"", ".", ".."} for component in components):
        raise BundleInstallError("activation_destination_untrusted")
    try:
        return _open_tree(root, tuple(components[:-1]), uid, gid), components[-1]
    except (BundleInstallError, OSError) as exc:
        raise BundleInstallError("activation_destination_untrusted") from exc


def _bootstrap_unit_links(
    root: int, uid: int, gid: int
) -> list[LinkTransaction]:
    created: list[LinkTransaction] = []
    try:
        for relative, target in BOOTSTRAP_LINKS.items():
            parent, leaf = _open_parent(root, relative, uid, gid)
            temporary = f".temporal-worker-link-{os.getpid()}-{secrets.token_hex(8)}"
            try:
                try:
                    metadata = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    os.symlink(target, temporary, dir_fd=parent)
                    temporary_metadata = os.stat(
                        temporary, dir_fd=parent, follow_symlinks=False
                    )
                    transaction = LinkTransaction(
                        relative,
                        _full_identity(temporary_metadata),
                        target,
                    )
                    try:
                        os.link(
                            temporary,
                            leaf,
                            src_dir_fd=parent,
                            dst_dir_fd=parent,
                            follow_symlinks=False,
                        )
                    except FileExistsError as exc:
                        raise BundleInstallError("activation_bootstrap_invalid") from exc
                    transaction.mark_published()
                    created.append(transaction)
                    os.unlink(temporary, dir_fd=parent)
                    published_metadata = os.stat(
                        leaf, dir_fd=parent, follow_symlinks=False
                    )
                    if not transaction.record_published(published_metadata):
                        raise BundleInstallError("activation_bootstrap_invalid")
                    os.fsync(parent)
                    metadata = os.stat(
                        leaf, dir_fd=parent, follow_symlinks=False
                    )
                if not stat.S_ISLNK(metadata.st_mode) or os.readlink(leaf, dir_fd=parent) != target:
                    raise BundleInstallError("activation_bootstrap_invalid")
                entry = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
                if _identity(metadata) != _identity(entry) or (entry.st_uid, entry.st_gid) != (uid, gid):
                    raise BundleInstallError("activation_bootstrap_invalid")
            finally:
                try:
                    os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError:
                    pass
                os.close(parent)
        return created
    except Exception:
        _remove_created_bootstrap_links(root, created, uid, gid)
        raise


def _remove_created_bootstrap_links(
    root: int, created: list[LinkTransaction], uid: int, gid: int
) -> None:
    for transaction in reversed(created):
        parent, leaf = _open_parent(root, transaction.relative, uid, gid)
        try:
            metadata = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
            identity_matches = (
                _full_identity(metadata) == transaction.published_identity
                if transaction.published_identity is not None
                else _identity(metadata) == transaction.published_inode_identity
            )
            if (
                not identity_matches
                or not stat.S_ISLNK(metadata.st_mode)
                or os.readlink(leaf, dir_fd=parent) != transaction.target
            ):
                raise BundleInstallError("activation_rollback_failed")
            os.unlink(leaf, dir_fd=parent)
            os.fsync(parent)
        finally:
            os.close(parent)


def _read_current(base: int, releases: int, uid: int, gid: int) -> str | None:
    try:
        metadata = os.stat("current", dir_fd=base, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if not stat.S_ISLNK(metadata.st_mode) or (metadata.st_uid, metadata.st_gid) != (uid, gid):
        raise BundleInstallError("activation_current_invalid")
    target = os.readlink("current", dir_fd=base)
    match = re.fullmatch(r"releases/([0-9a-f]{64})", target)
    if match is None:
        raise BundleInstallError("activation_current_invalid")
    verify_generation(releases, match.group(1), uid, gid)
    entry = os.stat("current", dir_fd=base, follow_symlinks=False)
    if _identity(metadata) != _identity(entry):
        raise BundleInstallError("activation_current_invalid")
    return match.group(1)


def _set_current(base: int, digest: str | None) -> None:
    temporary = f".current-{os.getpid()}-{secrets.token_hex(8)}"
    if digest is None:
        try:
            os.unlink("current", dir_fd=base)
        except FileNotFoundError:
            pass
        os.fsync(base)
        return
    if not _LOWER_HEX.fullmatch(digest):
        raise BundleInstallError("activation_generation_invalid")
    os.symlink(f"releases/{digest}", temporary, dir_fd=base)
    try:
        os.replace(temporary, "current", src_dir_fd=base, dst_dir_fd=base)
        os.fsync(base)
    finally:
        try:
            os.unlink(temporary, dir_fd=base)
        except FileNotFoundError:
            pass


_ACTIVE_ACTIVATION: ActivationState | None = None


def _activation_signal(_signum: int, _frame: object) -> None:
    state = _ACTIVE_ACTIVATION
    signal.pthread_sigmask(signal.SIG_BLOCK, _INSTALL_SIGNALS)
    if state is None:
        return
    state.interrupted = True
    child = state.child
    if child is not None and child.poll() is None:
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def _install_activation_signal_handlers(
    state: ActivationState,
) -> ActivationSignalContext:
    global _ACTIVE_ACTIVATION
    previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, _INSTALL_SIGNALS)
    handlers: dict[int, object] = {}
    _ACTIVE_ACTIVATION = state
    try:
        for signum in _INSTALL_SIGNALS:
            handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, _activation_signal)
    except Exception:
        _ACTIVE_ACTIVATION = None
        for signum, handler in handlers.items():
            signal.signal(signum, handler)
        signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
        raise
    signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
    return ActivationSignalContext(handlers, previous_mask)


def _restore_activation_signal_handlers(context: ActivationSignalContext) -> None:
    global _ACTIVE_ACTIVATION
    signal.pthread_sigmask(signal.SIG_BLOCK, _INSTALL_SIGNALS)
    _ACTIVE_ACTIVATION = None
    for signum, handler in context.handlers.items():
        signal.signal(signum, handler)
    signal.pthread_sigmask(signal.SIG_SETMASK, context.previous_mask)


def _raise_if_interrupted(state: ActivationState) -> None:
    if state.interrupted:
        raise BundleInstallError("activation_interrupted")


def _run_systemctl(
    systemctl: str,
    arguments: list[str],
    state: ActivationState,
    *,
    accepted: tuple[int, ...] = (0,),
    allow_after_interrupt: bool = False,
    _interrupt_authority: object | None = None,
) -> tuple[int, bytes]:
    if allow_after_interrupt and _interrupt_authority is not _ROLLBACK_INTERRUPT_AUTHORITY:
        raise BundleInstallError("activation_rollback_failed")
    if state.interrupted and not allow_after_interrupt:
        raise BundleInstallError("activation_interrupted")
    try:
        child = subprocess.Popen(
            [systemctl, *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        raise BundleInstallError("activation_systemctl_failed") from exc
    state.child = child
    try:
        deadline = time.monotonic() + 30.0
        while child.poll() is None:
            if state.interrupted and not allow_after_interrupt:
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                terminate_deadline = time.monotonic() + 5.0
                while child.poll() is None and time.monotonic() < terminate_deadline:
                    time.sleep(0.02)
                if child.poll() is None:
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                child.wait(timeout=5)
                raise BundleInstallError("activation_interrupted")
            if time.monotonic() >= deadline:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait(timeout=5)
                raise BundleInstallError("activation_systemctl_failed")
            time.sleep(0.02)
        output = child.communicate()[0] or b""
        if state.interrupted and not allow_after_interrupt:
            raise BundleInstallError("activation_interrupted")
        if child.returncode not in accepted:
            raise BundleInstallError("activation_systemctl_failed")
        return child.returncode, output
    finally:
        state.child = None


def _run_rollback_systemctl(
    systemctl: str,
    arguments: list[str],
    state: ActivationState,
    *,
    accepted: tuple[int, ...] = (0,),
) -> tuple[int, bytes]:
    return _run_systemctl(
        systemctl,
        arguments,
        state,
        accepted=accepted,
        allow_after_interrupt=True,
        _interrupt_authority=_ROLLBACK_INTERRUPT_AUTHORITY,
    )


def _unit_is_active(systemctl: str, unit: str, state: ActivationState) -> bool:
    result, _output = _run_systemctl(
        systemctl, ["is-active", "--quiet", unit], state, accepted=(0, 3)
    )
    return result == 0


def _confirm_inactive(systemctl: str, unit: str, state: ActivationState) -> None:
    if _unit_is_active(systemctl, unit, state):
        raise BundleInstallError("activation_systemctl_failed")


def _loaded_unit_payload(output: bytes) -> bytes:
    if output.startswith(b"# "):
        _header, separator, remainder = output.partition(b"\n")
        if not separator:
            raise BundleInstallError("activation_systemctl_failed")
        if remainder.startswith(b"\n"):
            remainder = remainder[1:]
        return remainder
    return output


def _verify_loaded_units(
    systemctl: str,
    root: int,
    releases: int,
    generation: int,
    digest: str,
    uid: int,
    gid: int,
    root_path: str,
    state: ActivationState,
    *,
    allow_after_interrupt: bool = False,
    _interrupt_authority: object | None = None,
) -> None:
    if allow_after_interrupt and _interrupt_authority is not _ROLLBACK_INTERRUPT_AUTHORITY:
        raise BundleInstallError("activation_rollback_failed")
    verify_generation(releases, digest, uid, gid)
    for unit in (PREPARE_UNIT, WORKER_UNIT):
        _result, fragment = _run_systemctl(
            systemctl,
            ["show", unit, "--property=FragmentPath", "--value"],
            state,
            allow_after_interrupt=allow_after_interrupt,
            _interrupt_authority=_interrupt_authority,
        )
        fragment_components = _parse_fragment_path(
            fragment, root_path, digest, unit
        )
        fragment_descriptor = _open_fragment_target(
            root, fragment_components, digest, unit, uid, gid
        )
        units = os.open("units", _DIRECTORY_FLAGS, dir_fd=generation)
        try:
            expected, expected_metadata = _read_named_file(
                units, unit, MAX_ASSET_BYTES
            )
        finally:
            os.close(units)
        try:
            fragment_before = os.fstat(fragment_descriptor)
            fragment_payload = _read_all(
                fragment_descriptor,
                MAX_ASSET_BYTES,
                "activation_systemctl_failed",
            )
            fragment_after = os.fstat(fragment_descriptor)
            if (
                _full_identity(fragment_before) != _full_identity(fragment_after)
                or _identity(fragment_after) != _identity(expected_metadata)
                or fragment_payload != expected
            ):
                raise BundleInstallError("activation_systemctl_failed")
        finally:
            os.close(fragment_descriptor)
        _result, loaded = _run_systemctl(
            systemctl,
            ["cat", "--no-pager", unit],
            state,
            allow_after_interrupt=allow_after_interrupt,
            _interrupt_authority=_interrupt_authority,
        )
        if _loaded_unit_payload(loaded) != expected:
            raise BundleInstallError("activation_systemctl_failed")


def _rollback_activation(
    root: int,
    base: int,
    releases: int,
    old_digest: str | None,
    old_active: dict[str, bool],
    created_links: list[LinkTransaction],
    uid: int,
    gid: int,
    systemctl: str,
    state: ActivationState,
    root_path: str = "/",
) -> None:
    old_generation = -1
    try:
        for unit in (WORKER_UNIT, PREPARE_UNIT):
            _run_rollback_systemctl(systemctl, ["stop", unit], state)
        _set_current(base, old_digest)
        _run_rollback_systemctl(systemctl, ["daemon-reload"], state)
        if old_digest is not None:
            old_generation = os.open(old_digest, _DIRECTORY_FLAGS, dir_fd=releases)
            _verify_loaded_units(
                systemctl,
                root,
                releases,
                old_generation,
                old_digest,
                uid,
                gid,
                root_path,
                state,
                allow_after_interrupt=True,
                _interrupt_authority=_ROLLBACK_INTERRUPT_AUTHORITY,
            )
            if old_active.get(PREPARE_UNIT):
                _run_rollback_systemctl(systemctl, ["start", PREPARE_UNIT], state)
            if old_active.get(WORKER_UNIT):
                _run_rollback_systemctl(systemctl, ["start", WORKER_UNIT], state)
        else:
            _remove_created_bootstrap_links(root, created_links, uid, gid)
    except Exception as exc:
        raise BundleInstallError("activation_rollback_failed") from exc
    finally:
        if old_generation >= 0:
            os.close(old_generation)


def activate_generation(
    digest: str,
    *,
    root_path: str = "/",
    systemctl: str = "/usr/bin/systemctl",
) -> None:
    if not _LOWER_HEX.fullmatch(digest):
        raise BundleInstallError("activation_arguments_invalid")
    uid = os.geteuid()
    gid = os.getegid()
    state = ActivationState()
    signal_context = _install_activation_signal_handlers(state)
    root = lock_directory = lock = base = releases = generation = -1
    created_links: list[LinkTransaction] = []
    old_digest: str | None = None
    old_active = {PREPARE_UNIT: False, WORKER_UNIT: False}
    switched = False
    stopped = False
    try:
        _raise_if_interrupted(state)
        root = _open_absolute_directory(root_path)
        _validate_directory(os.fstat(root), uid, gid)
        lock_directory, lock = _acquire_lock(root, uid, gid)
        _raise_if_interrupted(state)
        base = _open_tree(root, tuple(BASE_RELATIVE.split("/")), uid, gid)
        _raise_if_interrupted(state)
        releases = os.open("releases", _DIRECTORY_FLAGS, dir_fd=base)
        verify_generation(releases, digest, uid, gid)
        old_digest = _read_current(base, releases, uid, gid)
        created_links = _bootstrap_unit_links(root, uid, gid)
        _raise_if_interrupted(state)
        generation = os.open(digest, _DIRECTORY_FLAGS, dir_fd=releases)
        state.phase = PHASE_STOPPING
        stopped = True
        if old_digest is not None:
            old_active[WORKER_UNIT] = _unit_is_active(
                systemctl, WORKER_UNIT, state
            )
            old_active[PREPARE_UNIT] = _unit_is_active(
                systemctl, PREPARE_UNIT, state
            )
            _run_systemctl(systemctl, ["stop", WORKER_UNIT], state)
            _run_systemctl(systemctl, ["stop", PREPARE_UNIT], state)
            _confirm_inactive(systemctl, WORKER_UNIT, state)
            _confirm_inactive(systemctl, PREPARE_UNIT, state)
        state.phase = PHASE_PRE_SWITCH
        _set_current(base, digest)
        switched = True
        _raise_if_interrupted(state)
        state.phase = PHASE_SWITCHED
        _run_systemctl(systemctl, ["daemon-reload"], state)
        state.phase = PHASE_RELOADED
        _verify_loaded_units(
            systemctl,
            root,
            releases,
            generation,
            digest,
            uid,
            gid,
            root_path,
            state,
        )
        state.phase = PHASE_PREPARE_STARTING
        _run_systemctl(systemctl, ["start", PREPARE_UNIT], state)
        state.phase = PHASE_WORKER_STARTING
        _run_systemctl(systemctl, ["start", WORKER_UNIT], state)
        signal.pthread_sigmask(signal.SIG_BLOCK, _INSTALL_SIGNALS)
        if state.interrupted:
            raise BundleInstallError("activation_interrupted")
        state.phase = PHASE_COMPLETE
    except Exception:
        if stopped:
            signal.pthread_sigmask(signal.SIG_BLOCK, _INSTALL_SIGNALS)
            _rollback_activation(
                root,
                base,
                releases,
                old_digest,
                old_active,
                created_links,
                uid,
                gid,
                systemctl,
                state,
                root_path,
            )
        elif created_links:
            _remove_created_bootstrap_links(root, created_links, uid, gid)
        raise
    finally:
        for descriptor in (generation, releases, base, lock, lock_directory, root):
            if descriptor >= 0:
                os.close(descriptor)
        _restore_activation_signal_handlers(signal_context)


def _read_legacy_regular(
    root: int,
    relative: str,
    expected_mode: int,
    expected_digest: str,
    uid: int,
    gid: int,
) -> bytes:
    parent_path, leaf = relative.rsplit("/", 1)
    try:
        parent = _open_relative_directory(root, parent_path)
        payload, metadata = _read_named_file(parent, leaf, MAX_ASSET_BYTES)
        entry = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
    except (OSError, BundleInstallError) as exc:
        raise BundleInstallError("legacy_migration_unsupported") from exc
    finally:
        if "parent" in locals():
            os.close(parent)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or _identity(metadata) != _identity(entry)
        or (metadata.st_uid, metadata.st_gid) != (uid, gid)
        or stat.S_IMODE(metadata.st_mode) != expected_mode
        or hashlib.sha256(payload).hexdigest() != expected_digest
    ):
        raise BundleInstallError("legacy_migration_unsupported")
    return payload


def _legacy_unit_state(
    root: int, relative: str, uid: int, gid: int
) -> str:
    parent_path, leaf = relative.rsplit("/", 1)
    parent = _open_relative_directory(root, parent_path)
    try:
        metadata = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
        if stat.S_ISLNK(metadata.st_mode):
            if (
                (metadata.st_uid, metadata.st_gid) == (uid, gid)
                and os.readlink(leaf, dir_fd=parent) == BOOTSTRAP_LINKS[relative]
            ):
                return "bootstrap"
            raise BundleInstallError("legacy_migration_unsupported")
    except FileNotFoundError as exc:
        raise BundleInstallError("legacy_migration_unsupported") from exc
    finally:
        os.close(parent)
    mode, digest = LEGACY_ASSETS[relative]
    _read_legacy_regular(root, relative, mode, digest, uid, gid)
    return "legacy"


def _optional_legacy_file(
    root: int, relative: str, mode: int, digest: str, uid: int, gid: int
) -> bool:
    parent_path, leaf = relative.rsplit("/", 1)
    try:
        parent = _open_relative_directory(root, parent_path)
    except OSError:
        return False
    try:
        try:
            os.stat(leaf, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return False
    finally:
        os.close(parent)
    _read_legacy_regular(root, relative, mode, digest, uid, gid)
    return True


def _legacy_quarantine_transition(
    source_present: bool, quarantine_present: bool
) -> str:
    if source_present and not quarantine_present:
        return "move"
    if quarantine_present and not source_present:
        return "complete"
    raise BundleInstallError("legacy_migration_unsupported")


def _validate_quarantine_directory_metadata(
    metadata: os.stat_result, uid: int, gid: int
) -> None:
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or (metadata.st_uid, metadata.st_gid) != (uid, gid)
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise BundleInstallError("legacy_migration_unsupported")


def _quarantine_legacy_tmpfiles(root: int, uid: int, gid: int) -> bool:
    source_mode, source_digest = LEGACY_ASSETS[LEGACY_TMPFILES_RELATIVE]
    source_present = _optional_legacy_file(
        root,
        LEGACY_TMPFILES_RELATIVE,
        source_mode,
        source_digest,
        uid,
        gid,
    )
    quarantine_present = _optional_legacy_file(
        root,
        LEGACY_QUARANTINE_RELATIVE,
        0o600,
        source_digest,
        uid,
        gid,
    )
    transition = _legacy_quarantine_transition(
        source_present, quarantine_present
    )
    if transition == "complete":
        base = _open_relative_directory(root, BASE_RELATIVE)
        try:
            quarantine_directory = os.open(
                "quarantine", _DIRECTORY_FLAGS, dir_fd=base
            )
            try:
                metadata = os.fstat(quarantine_directory)
                entry = os.stat(
                    "quarantine", dir_fd=base, follow_symlinks=False
                )
                _validate_quarantine_directory_metadata(metadata, uid, gid)
                if _identity(metadata) != _identity(entry):
                    raise BundleInstallError("legacy_migration_unsupported")
            finally:
                os.close(quarantine_directory)
        except (OSError, BundleInstallError) as exc:
            raise BundleInstallError("legacy_migration_unsupported") from exc
        finally:
            os.close(base)
        return False
    _quarantine_parent_path, quarantine_leaf = LEGACY_QUARANTINE_RELATIVE.rsplit("/", 1)
    source_parent_path, source_leaf = LEGACY_TMPFILES_RELATIVE.rsplit("/", 1)
    base = _open_relative_directory(root, BASE_RELATIVE)
    try:
        quarantine_parent = _open_or_create_directory(
            base, "quarantine", 0o700, uid, gid, exact=True
        )
        _validate_quarantine_directory_metadata(
            os.fstat(quarantine_parent), uid, gid
        )
    except (OSError, BundleInstallError) as exc:
        raise BundleInstallError("legacy_migration_unsupported") from exc
    finally:
        os.close(base)
    source_parent = _open_relative_directory(root, source_parent_path)
    try:
        os.rename(
            source_leaf,
            quarantine_leaf,
            src_dir_fd=source_parent,
            dst_dir_fd=quarantine_parent,
        )
        descriptor = os.open(quarantine_leaf, _READ_FLAGS, dir_fd=quarantine_parent)
        try:
            os.fchmod(descriptor, 0o600)
            os.fsync(descriptor)
            metadata = os.fstat(descriptor)
            entry = os.stat(
                quarantine_leaf, dir_fd=quarantine_parent, follow_symlinks=False
            )
            if (
                not stat.S_ISREG(metadata.st_mode)
                or (metadata.st_uid, metadata.st_gid) != (uid, gid)
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or _identity(metadata) != _identity(entry)
            ):
                raise BundleInstallError(
                    "legacy_migration_manual_recovery_required"
                )
        finally:
            os.close(descriptor)
        os.fsync(source_parent)
        os.fsync(quarantine_parent)
    except OSError as exc:
        raise BundleInstallError("legacy_migration_manual_recovery_required") from exc
    finally:
        os.close(source_parent)
        os.close(quarantine_parent)
    return True


def _replace_legacy_unit_links(
    root: int,
    states: dict[str, str],
    uid: int,
    gid: int,
) -> list[LinkTransaction]:
    created: list[LinkTransaction] = []
    try:
        for relative, target in BOOTSTRAP_LINKS.items():
            if states[relative] == "bootstrap":
                continue
            parent, leaf = _open_parent(root, relative, uid, gid)
            temporary = f".legacy-unit-{os.getpid()}-{secrets.token_hex(8)}"
            try:
                os.symlink(target, temporary, dir_fd=parent)
                temporary_metadata = os.stat(
                    temporary, dir_fd=parent, follow_symlinks=False
                )
                transaction = LinkTransaction(
                    relative,
                    _full_identity(temporary_metadata),
                    target,
                )
                os.replace(temporary, leaf, src_dir_fd=parent, dst_dir_fd=parent)
                transaction.mark_published()
                created.append(transaction)
                published_metadata = os.stat(
                    leaf, dir_fd=parent, follow_symlinks=False
                )
                if not transaction.record_published(published_metadata):
                    raise BundleInstallError(
                        "legacy_migration_manual_recovery_required"
                    )
                os.fsync(parent)
                metadata = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
                if (
                    not stat.S_ISLNK(metadata.st_mode)
                    or os.readlink(leaf, dir_fd=parent) != target
                    or (metadata.st_uid, metadata.st_gid) != (uid, gid)
                ):
                    raise BundleInstallError(
                        "legacy_migration_manual_recovery_required"
                    )
            finally:
                try:
                    os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError:
                    pass
                os.close(parent)
        return created
    except Exception:
        _remove_created_bootstrap_links(root, created, uid, gid)
        raise


def migrate_legacy_generation(
    digest: str,
    *,
    root_path: str = "/",
    systemctl: str = "/usr/bin/systemctl",
) -> None:
    if not _LOWER_HEX.fullmatch(digest):
        raise BundleInstallError("activation_arguments_invalid")
    uid = os.geteuid()
    gid = os.getegid()
    state = ActivationState()
    signal_context = _install_activation_signal_handlers(state)
    root = lock_directory = lock = base = releases = generation = -1
    quarantined = False
    created_links: list[LinkTransaction] = []
    current_before: str | None = None
    current_changed = False
    try:
        _raise_if_interrupted(state)
        root = _open_absolute_directory(root_path)
        _validate_directory(os.fstat(root), uid, gid)
        lock_directory, lock = _acquire_lock(root, uid, gid)
        _raise_if_interrupted(state)
        base = _open_tree(root, tuple(BASE_RELATIVE.split("/")), uid, gid)
        _raise_if_interrupted(state)
        releases = os.open("releases", _DIRECTORY_FLAGS, dir_fd=base)
        verify_generation(releases, digest, uid, gid)
        generation = os.open(digest, _DIRECTORY_FLAGS, dir_fd=releases)
        for unit in (WORKER_UNIT, PREPARE_UNIT):
            _run_systemctl(systemctl, ["is-active", "--quiet", unit], state, accepted=(0, 3))
            _result, fragment = _run_systemctl(
                systemctl,
                ["show", unit, "--property=FragmentPath", "--value"],
                state,
            )
            try:
                _parse_fragment_path(fragment, root_path, digest, unit)
            except BundleInstallError as exc:
                raise BundleInstallError("legacy_migration_unsupported") from exc
        state.phase = PHASE_STOPPING
        _run_systemctl(systemctl, ["stop", WORKER_UNIT], state)
        _run_systemctl(systemctl, ["stop", PREPARE_UNIT], state)
        _confirm_inactive(systemctl, WORKER_UNIT, state)
        _confirm_inactive(systemctl, PREPARE_UNIT, state)
        unit_states = {
            relative: _legacy_unit_state(root, relative, uid, gid)
            for relative in BOOTSTRAP_LINKS
        }
        helper_relative = "usr/libexec/medchat/validate-temporal-worker-env"
        helper_mode, helper_digest = LEGACY_ASSETS[helper_relative]
        _read_legacy_regular(
            root, helper_relative, helper_mode, helper_digest, uid, gid
        )
        current_before = _read_current(base, releases, uid, gid)
        if current_before not in {None, digest}:
            raise BundleInstallError("legacy_migration_unsupported")
        quarantined = _quarantine_legacy_tmpfiles(root, uid, gid)
        _raise_if_interrupted(state)
        created_links = _replace_legacy_unit_links(root, unit_states, uid, gid)
        _raise_if_interrupted(state)
        if current_before is None:
            current_changed = True
            _set_current(base, digest)
            _raise_if_interrupted(state)
        state.phase = PHASE_SWITCHED
        _run_systemctl(systemctl, ["daemon-reload"], state)
        state.phase = PHASE_RELOADED
        _verify_loaded_units(
            systemctl,
            root,
            releases,
            generation,
            digest,
            uid,
            gid,
            root_path,
            state,
        )
        state.phase = PHASE_PREPARE_STARTING
        _run_systemctl(systemctl, ["start", PREPARE_UNIT], state)
        state.phase = PHASE_WORKER_STARTING
        _run_systemctl(systemctl, ["start", WORKER_UNIT], state)
        signal.pthread_sigmask(signal.SIG_BLOCK, _INSTALL_SIGNALS)
        if state.interrupted:
            raise BundleInstallError("activation_interrupted")
        state.phase = PHASE_COMPLETE
    except Exception as exc:
        if quarantined or created_links or current_changed:
            signal.pthread_sigmask(signal.SIG_BLOCK, _INSTALL_SIGNALS)
            try:
                for unit in (WORKER_UNIT, PREPARE_UNIT):
                    _run_rollback_systemctl(systemctl, ["stop", unit], state)
                if current_changed:
                    _set_current(base, None)
                if created_links:
                    _remove_created_bootstrap_links(
                        root, created_links, uid, gid
                    )
                _run_rollback_systemctl(systemctl, ["daemon-reload"], state)
            except Exception:
                pass
            raise BundleInstallError(
                "legacy_migration_manual_recovery_required"
            ) from exc
        raise
    finally:
        for descriptor in (generation, releases, base, lock, lock_directory, root):
            if descriptor >= 0:
                os.close(descriptor)
        _restore_activation_signal_handlers(signal_context)


def _remove_tree(parent: int, name: str) -> None:
    descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent)
    try:
        os.fchmod(descriptor, 0o700)
        for entry in os.listdir(descriptor):
            metadata = os.stat(entry, dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
                _remove_tree(descriptor, entry)
            else:
                os.unlink(entry, dir_fd=descriptor)
    finally:
        os.close(descriptor)
    os.rmdir(name, dir_fd=parent)


def _publish_generation(
    root: int,
    assets: dict[str, tuple[int, bytes]],
    digest: str,
    uid: int,
    gid: int,
) -> None:
    releases = _open_tree(root, tuple(RELEASES_RELATIVE.split("/")), uid, gid)
    staging = f".staging-{os.getpid()}-{secrets.token_hex(8)}"
    staging_created = False
    try:
        os.mkdir(staging, 0o700, dir_fd=releases)
        staging_created = True
        generation = os.open(staging, _DIRECTORY_FLAGS, dir_fd=releases)
        try:
            os.fchown(generation, uid, gid)
            units = _open_or_create_directory(generation, "units", 0o700, uid, gid, exact=True)
            libexec = _open_or_create_directory(generation, "libexec", 0o700, uid, gid, exact=True)
            try:
                for path, mode, _source in ASSET_SPECS:
                    parent = libexec if path.startswith("libexec/") else units
                    _write_file(parent, path.rsplit("/", 1)[1], assets[path][1], mode, uid, gid)
                _write_file(
                    generation,
                    "manifest.sha256",
                    build_manifest(assets, digest),
                    0o644,
                    uid,
                    gid,
                )
                os.fchmod(units, 0o555)
                os.fchmod(libexec, 0o555)
                os.fsync(units)
                os.fsync(libexec)
            finally:
                os.close(units)
                os.close(libexec)
            os.fchmod(generation, 0o555)
            os.fsync(generation)
        finally:
            os.close(generation)
        os.fsync(releases)
        try:
            os.rename(staging, digest, src_dir_fd=releases, dst_dir_fd=releases)
            staging_created = False
            os.fsync(releases)
        except OSError as exc:
            if exc.errno not in {errno.EEXIST, errno.ENOTEMPTY}:
                raise BundleInstallError("bundle_publish_failed") from exc
            _remove_tree(releases, staging)
            staging_created = False
        verify_generation(releases, digest, uid, gid)
    finally:
        if staging_created:
            try:
                _remove_tree(releases, staging)
            except OSError:
                pass
        os.close(releases)


def install_bundle(source_root_path: str, destination_path: str, live: bool) -> str:
    uid = 0 if live else os.geteuid()
    gid = 0 if live else os.getegid()
    destination = _open_absolute_directory(destination_path)
    source = _open_absolute_directory(source_root_path)
    lock_directory = -1
    lock = -1
    try:
        _validate_directory(os.fstat(destination), uid, gid)
        lock_directory, lock = _acquire_lock(destination, uid, gid)
        assets = read_source_assets(source, live)
        digest = compute_bundle_digest(assets)
        _publish_generation(destination, assets, digest, uid, gid)
        return digest
    finally:
        if lock >= 0:
            os.close(lock)
        if lock_directory >= 0:
            os.close(lock_directory)
        os.close(source)
        os.close(destination)


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) == 2 and arguments[0] in {"--activate", "--migrate-legacy"}:
        if os.geteuid() != 0:
            print(
                "temporal_worker_activation=failed code=activation_root_required",
                file=sys.stderr,
            )
            return 1
        try:
            if arguments[0] == "--activate":
                activate_generation(arguments[1])
            else:
                migrate_legacy_generation(arguments[1])
        except BundleInstallError as exc:
            print(
                f"temporal_worker_activation=failed code={exc.code}",
                file=sys.stderr,
            )
            return 1
        except Exception:
            print(
                "temporal_worker_activation=failed code=activation_systemctl_failed",
                file=sys.stderr,
            )
            return 1
        print(f"temporal_worker_activation=passed digest={arguments[1]}")
        return 0
    if len(arguments) != 5 or arguments[:1] != ["--source-root"] or arguments[2:3] != ["--destdir"] or arguments[4] not in {"--live", "--staging"}:
        print(
            "temporal_worker_bundle_installation=failed code=bundle_arguments_invalid",
            file=sys.stderr,
        )
        return 1
    install_signal_handlers()
    try:
        digest = install_bundle(arguments[1], arguments[3], arguments[4] == "--live")
    except BundleInstallError as exc:
        print(
            f"temporal_worker_bundle_installation=failed code={exc.code}",
            file=sys.stderr,
        )
        return 1
    except Exception:
        print(
            "temporal_worker_bundle_installation=failed code=bundle_installation_failed",
            file=sys.stderr,
        )
        return 1
    print(f"temporal_worker_bundle_installation=passed digest={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
