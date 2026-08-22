"""Safely inspect and update the production Temporal canary percentage."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import tempfile
import unicodedata
from typing import Iterator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.task_runtime.config import (  # noqa: E402
    _absolute_path_components,
    _is_reparse_point,
    _stat_identity,
    _stat_version,
)
from src.task_runtime.models import sanitize_task_message  # noqa: E402
from src.task_runtime.rollout import (  # noqa: E402
    MAX_EVIDENCE_BYTES,
    RolloutEvidence,
    validate_transition,
)
from src.task_runtime.trusted_files import (  # noqa: E402
    _TrustedPathBoundary,
    _WindowsSecurityDescriptor,
    _apply_windows_security,
    _capture_trusted_path_boundary,
    _capture_windows_security,
    _normalized_path,
    _path_identities,
    _posix_expected_owner,
    _validate_posix_mutation_boundary,
    _validate_windows_mutation_boundary,
    _windows_allow_ace_is_trusted,
    _windows_current_user_sid,
    _windows_trusted_sids,
)


ROLLOUT_KEY = "MEDCHAT_TEMPORAL_CANARY_PERCENT"
ROLLOUT_PREFIX = ROLLOUT_KEY + "="
ROLLOUT_LEVELS = frozenset({0, 5, 10, 25})
MAX_ROLLOUT_BYTES = 1024 * 1024
MAX_EVIDENCE_FILE_BYTES = MAX_EVIDENCE_BYTES + 4096
MAX_REASON_BYTES = 512
_ASSIGNMENT = re.compile(r"(?:export )?[A-Za-z_][A-Za-z0-9_]*=.*\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_REASON_PUNCTUATION = frozenset(" .,:;_()-")


class _StableArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        self.exit(2, "error: invalid_arguments\n")


@dataclass(frozen=True)
class _SafeSnapshot:
    path: Path
    content: bytes
    metadata: os.stat_result
    path_identities: tuple[tuple[int, int, int, int], ...]
    path_version: tuple[int, ...]


def _read_fd_limited(file_descriptor: int, maximum: int) -> bytes:
    chunks: list[bytes] = []
    remaining = maximum + 1
    while remaining:
        chunk = os.read(file_descriptor, min(64 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    content = b"".join(chunks)
    if len(content) > maximum:
        raise ValueError("file is oversized")
    return content


def _safe_read_snapshot(
    value: Path | str,
    maximum: int,
    *,
    reject_posix_writable: bool = False,
) -> _SafeSnapshot:
    path = _normalized_path(value)
    identities_before = _path_identities(path)
    try:
        lexical = path.lstat()
    except (OSError, RuntimeError):
        raise ValueError("unsafe file") from None
    if _is_reparse_point(lexical) or not stat.S_ISREG(lexical.st_mode):
        raise ValueError("unsafe file")
    if reject_posix_writable and os.name == "posix" and lexical.st_mode & 0o022:
        raise ValueError("unsafe file permissions")
    if lexical.st_size > maximum:
        raise ValueError("file is oversized")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    if os.name == "posix":
        no_follow = getattr(os, "O_NOFOLLOW", None)
        if no_follow is None:
            raise ValueError("safe file reads unavailable")
        flags |= no_follow
    file_descriptor: int | None = None
    try:
        file_descriptor = os.open(path, flags)
        before = os.fstat(file_descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or _is_reparse_point(before)
            or _stat_identity(before) != _stat_identity(lexical)
            or before.st_size > maximum
        ):
            raise ValueError("unsafe file")
        content = _read_fd_limited(file_descriptor, maximum)
        after = os.fstat(file_descriptor)
        if _stat_version(before) != _stat_version(after):
            raise ValueError("file changed while reading")
    except OSError:
        raise ValueError("unsafe file") from None
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)

    try:
        lexical_after = path.lstat()
    except (OSError, RuntimeError):
        raise ValueError("unsafe file") from None
    identities_after = _path_identities(path)
    if (
        identities_after != identities_before
        or _is_reparse_point(lexical_after)
        or _stat_version(lexical_after) != _stat_version(after)
    ):
        raise ValueError("file identity changed")
    return _SafeSnapshot(
        path=path,
        content=content,
        metadata=after,
        path_identities=identities_after,
        path_version=_stat_version(after),
    )


def _decode_rollout(snapshot: _SafeSnapshot) -> tuple[list[str], int]:
    try:
        text = snapshot.content.decode("utf-8")
    except UnicodeError:
        raise ValueError("invalid rollout config") from None
    index = 0
    while index < len(text):
        character = text[index]
        if character == "\r":
            if index + 1 >= len(text) or text[index + 1] != "\n":
                raise ValueError("invalid rollout config")
            index += 2
            continue
        if character == "\n":
            index += 1
            continue
        if character != "\t" and (
            character in {"\u2028", "\u2029"}
            or unicodedata.category(character).startswith("C")
        ):
            raise ValueError("invalid rollout config")
        index += 1

    # splitlines is safe only after excluding every non-LF/CRLF separator.
    lines = text.splitlines(keepends=True)
    if text and not lines:
        raise ValueError("invalid rollout config")
    matches: list[str] = []
    assignment_names: set[str] = set()
    for line in lines:
        core = line.rstrip("\r\n")
        if "\r" in core or "\n" in core:
            raise ValueError("invalid rollout config")
        stripped = core.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if _ASSIGNMENT.fullmatch(core) is None:
            raise ValueError("invalid rollout config")
        assignment = core.removeprefix("export ")
        name = assignment.split("=", 1)[0]
        if name in assignment_names:
            raise ValueError("duplicate rollout config assignment")
        assignment_names.add(name)
        if assignment.startswith(ROLLOUT_PREFIX):
            matches.append(assignment)
    if len(matches) != 1:
        raise ValueError("rollout percent must appear exactly once")
    raw = matches[0].split("=", 1)[1].strip()
    if raw not in {"0", "5", "10", "25"}:
        raise ValueError("unsupported rollout percent")
    return lines, int(raw)


def read_rollout_config(path: Path) -> tuple[list[str], int]:
    """Read a bounded rollout environment file without following links."""

    return _decode_rollout(
        _safe_read_snapshot(path, MAX_ROLLOUT_BYTES, reject_posix_writable=True)
    )


def _validate_mutation_boundary(path: Path) -> object | None:
    try:
        if os.name == "nt":
            return _validate_windows_mutation_boundary(path)
        _validate_posix_mutation_boundary(path)
        return None
    except Exception:
        raise ValueError("unsafe rollout trust boundary") from None


def _validate_lock_target(path: Path) -> None:
    _path_identities(path, include_final=False)
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    except (OSError, RuntimeError):
        raise ValueError("unsafe lock target") from None
    if _is_reparse_point(metadata) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("unsafe lock target")
    if os.name == "posix":
        if metadata.st_mode & 0o022 or not _posix_expected_owner(metadata):
            raise ValueError("unsafe lock target")
    else:
        try:
            security = _capture_windows_security(path)
            trusted = (
                security.owner_sid == _windows_current_user_sid()
                and security.trusted_dacl
            )
        except Exception:
            raise ValueError("unsafe lock target") from None
        if not trusted:
            raise ValueError("unsafe lock target")


@contextmanager
def _rollout_lock(env_path: Path) -> Iterator[None]:
    path = env_path.with_name(env_path.name + ".lock")
    _validate_lock_target(path)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    if os.name == "posix":
        no_follow = getattr(os, "O_NOFOLLOW", None)
        if no_follow is None:
            raise ValueError("safe locking unavailable")
        flags |= no_follow
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError:
        raise ValueError("unsafe lock target") from None
    locked = False
    try:
        opened = os.fstat(descriptor)
        lexical = path.lstat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or _is_reparse_point(opened)
            or _is_reparse_point(lexical)
            or _stat_identity(opened) != _stat_identity(lexical)
            or (os.name == "posix" and opened.st_mode & 0o022)
        ):
            raise ValueError("unsafe lock target")
        if os.name == "nt":
            import msvcrt

            if opened.st_size == 0:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX)
        locked = True
        after_lock = path.lstat()
        if _is_reparse_point(after_lock) or _stat_identity(after_lock) != _stat_identity(opened):
            raise ValueError("unsafe lock target")
        yield
    except OSError:
        raise ValueError("canary lock unavailable") from None
    finally:
        if locked:
            try:
                if os.name == "nt":
                    import msvcrt

                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(descriptor)


def _assert_unchanged(path: Path, snapshot: _SafeSnapshot) -> None:
    try:
        current = _safe_read_snapshot(
            path,
            MAX_ROLLOUT_BYTES,
            reject_posix_writable=True,
        )
    except (OSError, RuntimeError, ValueError):
        raise ValueError("rollout config identity changed") from None
    # The adjacent lock serializes managers, while this exact-content reread
    # catches same-size rewrites whose mtime was restored before replacement.
    if (
        current.path_identities != snapshot.path_identities
        or current.path_version != snapshot.path_version
        or current.content != snapshot.content
    ):
        raise ValueError("rollout config identity changed")


def _render_level(lines: list[str], target: int) -> bytes:
    rendered: list[str] = []
    replacements = 0
    for line in lines:
        core = line.rstrip("\r\n")
        ending = line[len(core) :]
        export_prefix = "export " if core.startswith("export ") else ""
        assignment = core.removeprefix(export_prefix)
        if assignment.startswith(ROLLOUT_PREFIX):
            rendered.append(
                f"{export_prefix}{ROLLOUT_PREFIX}{target}{ending}"
            )
            replacements += 1
        else:
            rendered.append(line)
    if replacements != 1:
        raise ValueError("rollout percent must appear exactly once")
    return "".join(rendered).encode("utf-8")


def _fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_replace_rollout(snapshot: _SafeSnapshot, content: bytes) -> None:
    parent = snapshot.path.parent
    parent_identities = _path_identities(snapshot.path, include_final=False)
    parent_metadata = parent.lstat()
    parent_identity = _stat_identity(parent_metadata)
    source_security = _validate_mutation_boundary(snapshot.path)
    parent_descriptor: int | None = None
    descriptor: int | None = None
    temporary: Path | None = None
    temporary_name: str | None = None
    try:
        if os.name == "posix":
            directory_flags = (
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
            )
            if not getattr(os, "O_DIRECTORY", 0) or not getattr(os, "O_NOFOLLOW", 0):
                raise ValueError("safe parent anchoring unavailable")
            parent_descriptor = os.open(parent, directory_flags)
            if _stat_identity(os.fstat(parent_descriptor)) != parent_identity:
                raise ValueError("rollout parent identity changed")
            flags = (
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | os.O_NOFOLLOW
                | getattr(os, "O_CLOEXEC", 0)
            )
            for _ in range(8):
                candidate = f".{snapshot.path.name}.{secrets.token_hex(12)}.tmp"
                try:
                    descriptor = os.open(
                        candidate,
                        flags,
                        0o600,
                        dir_fd=parent_descriptor,
                    )
                except FileExistsError:
                    continue
                temporary_name = candidate
                temporary = parent / candidate
                break
            if descriptor is None:
                raise ValueError("safe temporary unavailable")
        else:
            descriptor, name = tempfile.mkstemp(
                prefix=f".{snapshot.path.name}.", suffix=".tmp", dir=parent
            )
            temporary = Path(name)
            os.chmod(temporary, 0o600)
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short write")
            view = view[written:]
        os.fsync(descriptor)
        if os.name == "posix" and hasattr(os, "fchown"):
            os.fchown(descriptor, snapshot.metadata.st_uid, snapshot.metadata.st_gid)
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, stat.S_IMODE(snapshot.metadata.st_mode))
        os.fsync(descriptor)
        if os.name == "nt":
            try:
                _apply_windows_security(temporary, source_security)
            except Exception:
                raise ValueError("unsafe rollout security") from None
        temporary_identity = _stat_identity(os.fstat(descriptor))
        os.close(descriptor)
        descriptor = None

        _assert_unchanged(snapshot.path, snapshot)
        if (
            _path_identities(snapshot.path, include_final=False) != parent_identities
            or _stat_identity(parent.lstat()) != parent_identity
            or (
                parent_descriptor is not None
                and _stat_identity(os.fstat(parent_descriptor)) != parent_identity
            )
        ):
            raise ValueError("rollout parent identity changed")
        if parent_descriptor is not None:
            os.replace(
                temporary_name,
                snapshot.path.name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
            )
        else:
            os.replace(temporary, snapshot.path)
        temporary = None
        temporary_name = None
        final = (
            os.stat(
                snapshot.path.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            if parent_descriptor is not None
            else snapshot.path.lstat()
        )
        if _is_reparse_point(final) or _stat_identity(final) != temporary_identity:
            raise ValueError("rollout replacement identity changed")
        if (
            _path_identities(snapshot.path, include_final=False) != parent_identities
            or _stat_identity(parent.lstat()) != parent_identity
            or (
                parent_descriptor is not None
                and _stat_identity(os.fstat(parent_descriptor)) != parent_identity
            )
        ):
            raise ValueError("rollout parent identity changed")
        _fsync_directory(parent)
    except (OSError, RuntimeError):
        raise ValueError("atomic rollout update failed") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                if parent_descriptor is not None and temporary_name is not None:
                    os.unlink(temporary_name, dir_fd=parent_descriptor)
                else:
                    temporary.unlink()
            except FileNotFoundError:
                pass
        if parent_descriptor is not None:
            os.close(parent_descriptor)


def _strict_json_object(content: bytes) -> dict[str, object]:
    try:
        text = content.decode("utf-8")
        if "\x00" in text:
            raise ValueError

        def object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError
                result[key] = value
            return result

        payload = json.loads(text, object_pairs_hook=object_pairs)
    except (UnicodeError, ValueError, json.JSONDecodeError):
        raise ValueError("invalid evidence report") from None
    if type(payload) is not dict:
        raise ValueError("invalid evidence report")
    return payload


def _load_evidence(path: Path) -> tuple[RolloutEvidence, str]:
    boundary = _capture_trusted_path_boundary(path)
    snapshot = _safe_read_snapshot(
        path,
        MAX_EVIDENCE_FILE_BYTES,
        reject_posix_writable=True,
    )
    payload = _strict_json_object(snapshot.content)
    if _capture_trusted_path_boundary(path) != boundary:
        raise ValueError("invalid evidence report")
    digest = payload.pop("sha256", None)
    if type(digest) is not str or _SHA256.fullmatch(digest) is None:
        raise ValueError("invalid evidence report")
    # Metadata checks close read races; RolloutEvidence binds authorization to
    # the canonical payload hash even if content metadata could be preserved.
    return RolloutEvidence.from_payload(payload, digest), digest


def _safe_reason(value: str) -> str:
    if type(value) is not str or value != value.strip() or not value:
        raise ValueError("invalid rollout reason")
    try:
        encoded = value.encode("utf-8")
    except UnicodeError:
        raise ValueError("invalid rollout reason") from None
    if not 1 <= len(encoded) <= MAX_REASON_BYTES:
        raise ValueError("invalid rollout reason")
    for character in value:
        category = unicodedata.category(character)
        if not (category[0] in {"L", "N"} or character in _SAFE_REASON_PUNCTUATION):
            raise ValueError("invalid rollout reason")
    if sanitize_task_message(value) != value:
        raise ValueError("invalid rollout reason")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = _StableArgumentParser(prog="manage_temporal_canary.py", add_help=True)
    commands = parser.add_subparsers(dest="command", required=True)
    status_parser = commands.add_parser("status", add_help=True)
    status_parser.add_argument("--env-file", type=Path, required=True)
    promote = commands.add_parser("promote", add_help=True)
    promote.add_argument("--env-file", type=Path, required=True)
    promote.add_argument("--to", type=int, choices=(5, 10, 25), required=True)
    promote.add_argument("--evidence", type=Path, required=True)
    promote.add_argument("--reason", required=True)
    rollback = commands.add_parser("rollback", add_help=True)
    rollback.add_argument("--env-file", type=Path, required=True)
    rollback.add_argument("--reason", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        env_path = _normalized_path(args.env_file)
        if args.command == "status":
            snapshot = _safe_read_snapshot(
                env_path,
                MAX_ROLLOUT_BYTES,
                reject_posix_writable=True,
            )
            _lines, current_level = _decode_rollout(snapshot)
            _assert_unchanged(env_path, snapshot)
            print(f"status=passed current_level={current_level}")
            return 0

        # Mutation trust is established before creating/opening the adjacent
        # lock and is revalidated inside the lock immediately before replace.
        _validate_mutation_boundary(env_path)
        with _rollout_lock(env_path):
            snapshot = _safe_read_snapshot(
                env_path,
                MAX_ROLLOUT_BYTES,
                reject_posix_writable=True,
            )
            lines, current_level = _decode_rollout(snapshot)

            reason = _safe_reason(args.reason)
            if args.command == "promote":
                evidence, digest = _load_evidence(args.evidence)
                decision = validate_transition(
                    current_level,
                    args.to,
                    evidence,
                    now=datetime.now(timezone.utc),
                )
                target_level = decision.target_level
                _atomic_replace_rollout(snapshot, _render_level(lines, target_level))
                print(
                    f"status=passed current_level={current_level} "
                    f"target_level={target_level} sha256={digest} reason={reason}"
                )
                return 0

            decision = validate_transition(current_level, 0, None)
            _atomic_replace_rollout(snapshot, _render_level(lines, decision.target_level))
            print(
                f"status=passed current_level={current_level} "
                f"target_level=0 reason={reason}"
            )
            return 0
    except SystemExit as exc:
        return int(exc.code)
    except (MemoryError, OSError, RuntimeError, TypeError, ValueError):
        print("error: operation_failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
