#!/usr/bin/python3
"""Create or verify the four fixed Temporal worker runtime directories."""

from __future__ import annotations

import errno
import os
try:
    import pwd
except ImportError:  # pragma: no cover - the production CLI is Linux-only
    pwd = None  # type: ignore[assignment]
import stat
import sys


RUNTIME_DIRECTORIES = (
    "opt/medchat/molecular_chat_system/scratch",
    "opt/medchat/molecular_chat_system/scratch/task_inputs",
    "opt/medchat/molecular_chat_system/scratch/temporal_backups",
    "opt/medchat/molecular_chat_system/temp_docking",
)
PROJECT_COMPONENTS = ("opt", "medchat", "molecular_chat_system")
_STABLE_CODES = frozenset(
    {
        "directory_preparation_failed",
        "directory_arguments_invalid",
        "directory_identity_unavailable",
        "directory_parent_untrusted",
        "directory_path_invalid",
        "directory_entry_unavailable",
        "directory_entry_not_directory",
        "directory_entry_wrong_owner",
        "directory_entry_wrong_mode",
        "directory_entry_changed",
    }
)
_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)


class DirectoryPreparationError(RuntimeError):
    """A stable, sanitized failure for the privileged CLI boundary."""

    def __init__(self, code: str) -> None:
        self.code = code if code in _STABLE_CODES else "directory_preparation_failed"
        super().__init__(self.code)


def _directory_identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _validate_root_parent(descriptor: int) -> None:
    try:
        metadata = os.fstat(descriptor)
    except OSError as exc:
        raise DirectoryPreparationError("directory_entry_unavailable") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise DirectoryPreparationError("directory_parent_untrusted")


def _entry_failure_code(parent_descriptor: int, name: str) -> str:
    try:
        metadata = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return "directory_entry_unavailable"
    except OSError:
        return "directory_path_invalid"
    if stat.S_ISLNK(metadata.st_mode):
        return "directory_path_invalid"
    if not stat.S_ISDIR(metadata.st_mode):
        return "directory_entry_not_directory"
    return "directory_entry_unavailable"


def _open_trusted_parent(parent_descriptor: int, name: str) -> int:
    try:
        descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_descriptor)
    except OSError as exc:
        code = _entry_failure_code(parent_descriptor, name)
        raise DirectoryPreparationError(code) from exc
    try:
        _validate_root_parent(descriptor)
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _verify_runtime_metadata(
    metadata: os.stat_result,
    expected_uid: int,
    expected_gid: int,
) -> None:
    if not stat.S_ISDIR(metadata.st_mode):
        raise DirectoryPreparationError("directory_entry_not_directory")
    if (metadata.st_uid, metadata.st_gid) != (expected_uid, expected_gid):
        raise DirectoryPreparationError("directory_entry_wrong_owner")
    if stat.S_IMODE(metadata.st_mode) != 0o700:
        raise DirectoryPreparationError("directory_entry_wrong_mode")


def _ensure_runtime_directory(
    parent_descriptor: int,
    name: str,
    expected_uid: int,
    expected_gid: int,
) -> int:
    created = False
    try:
        os.mkdir(name, 0o700, dir_fd=parent_descriptor)
        created = True
    except FileExistsError:
        pass
    except OSError as exc:
        raise DirectoryPreparationError("directory_entry_unavailable") from exc

    try:
        descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_descriptor)
    except OSError as exc:
        code = _entry_failure_code(parent_descriptor, name)
        raise DirectoryPreparationError(code) from exc

    try:
        if created:
            try:
                os.fchown(descriptor, expected_uid, expected_gid)
                os.fchmod(descriptor, 0o700)
                os.fsync(descriptor)
                os.fsync(parent_descriptor)
            except OSError as exc:
                raise DirectoryPreparationError("directory_entry_unavailable") from exc
        try:
            before = os.fstat(descriptor)
        except OSError as exc:
            raise DirectoryPreparationError("directory_entry_unavailable") from exc
        _verify_runtime_metadata(before, expected_uid, expected_gid)
        try:
            entry = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        except OSError as exc:
            raise DirectoryPreparationError("directory_entry_changed") from exc
        _verify_runtime_metadata(entry, expected_uid, expected_gid)
        if _directory_identity(before) != _directory_identity(entry):
            raise DirectoryPreparationError("directory_entry_changed")
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _prepare_runtime_directories(
    root_descriptor: int,
    medchat_uid: int,
    medchat_gid: int,
) -> None:
    descriptors: list[int] = []
    try:
        current = os.dup(root_descriptor)
        descriptors.append(current)
        _validate_root_parent(current)
        for component in PROJECT_COMPONENTS:
            current = _open_trusted_parent(current, component)
            descriptors.append(current)

        project_descriptor = current
        scratch = _ensure_runtime_directory(
            project_descriptor,
            "scratch",
            medchat_uid,
            medchat_gid,
        )
        descriptors.append(scratch)
        for child in ("task_inputs", "temporal_backups"):
            descriptors.append(
                _ensure_runtime_directory(
                    scratch,
                    child,
                    medchat_uid,
                    medchat_gid,
                )
            )
        descriptors.append(
            _ensure_runtime_directory(
                project_descriptor,
                "temp_docking",
                medchat_uid,
                medchat_gid,
            )
        )
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def prepare_runtime_directories() -> None:
    if pwd is None:
        raise DirectoryPreparationError("directory_identity_unavailable")
    try:
        identity = pwd.getpwnam("medchat")
    except KeyError as exc:
        raise DirectoryPreparationError("directory_identity_unavailable") from exc
    try:
        root_descriptor = os.open("/", _DIRECTORY_FLAGS)
    except OSError as exc:
        raise DirectoryPreparationError("directory_entry_unavailable") from exc
    try:
        _prepare_runtime_directories(root_descriptor, identity.pw_uid, identity.pw_gid)
    finally:
        os.close(root_descriptor)


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments:
        print(
            "temporal_worker_directory_preparation=failed "
            "code=directory_arguments_invalid",
            file=sys.stderr,
        )
        return 1
    try:
        prepare_runtime_directories()
    except DirectoryPreparationError as exc:
        print(
            f"temporal_worker_directory_preparation=failed code={exc.code}",
            file=sys.stderr,
        )
        return 1
    except Exception:
        print(
            "temporal_worker_directory_preparation=failed "
            "code=directory_preparation_failed",
            file=sys.stderr,
        )
        return 1
    print("temporal_worker_directory_preparation=passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
