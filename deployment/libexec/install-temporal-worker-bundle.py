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
import sys


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
        _validate_directory(metadata, uid, gid)
        entry = os.stat(digest, dir_fd=releases, follow_symlinks=False)
        if _identity(metadata) != _identity(entry):
            raise BundleInstallError("bundle_generation_invalid")
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
            parent = _open_relative_directory(generation, parent_path)
            try:
                payload, asset_meta = _read_named_file(parent, leaf, MAX_ASSET_BYTES)
            finally:
                os.close(parent)
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
        os.close(generation)


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
