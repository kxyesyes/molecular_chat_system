"""Fail-closed input staging and scientific docking-result validation."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from src.task_runtime.config import _stat_identity, _stat_version
from src.task_runtime.secure_io import read_file_snapshot


_CHUNK_BYTES = 1024 * 1024
_POSE_READ_CHUNK_BYTES = 64 * 1024
_MAX_RESULT_JSON_BYTES = 1024 * 1024
_MAX_POSE_LINE_BYTES = 4096
_MAX_POSE_FILES = 64
_MAX_POSE_MODES = 256
_MAX_WARNINGS = 64
_MAX_WARNING_LENGTH = 1024
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_UUID_HEX_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_ASCII_FLOAT = (
    rb"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?"
)
_ENERGY_PATTERN = re.compile(
    rb"^REMARK VINA RESULT:[ \t]+("
    + _ASCII_FLOAT
    + rb")[ \t]+("
    + _ASCII_FLOAT
    + rb")[ \t]+("
    + _ASCII_FLOAT
    + rb")[ \t]*$"
)
_ENERGY_CLAIM_PATTERN = re.compile(
    rb"^[ \t]*REMARK[ \t]+VINA[ \t]+RESULT(?=[: \t]|$)"
)
_WINDOWS_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_EXPECTED_RESULT_FIELDS = frozenset(
    {
        "schema_version",
        "status",
        "receptor_sha256",
        "ligand_sha256",
        "pose_count",
        "best_energy",
        "pose_files",
        "vina_version",
        "meeko_version",
        "warnings",
    }
)
_ALLOWED_SUFFIXES = {
    "receptor": frozenset({".pdb"}),
    "ligand": frozenset({".sdf", ".mol"}),
}


class InputStagingError(ValueError):
    """A sanitized failure while staging an untrusted upload."""


class ScientificOutputError(ValueError):
    """A sanitized failure at the scientific-result trust gate."""


@dataclass(frozen=True)
class StagedInput:
    """Immutable, host-path-free metadata for one staged input."""

    relative_path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class ValidatedScientificOutput:
    """Immutable, host-path-free metadata accepted by the result gate."""

    schema_version: int
    status: str
    receptor_sha256: str
    ligand_sha256: str
    pose_count: int
    best_energy: float
    pose_files: tuple[str, ...]
    vina_version: str
    meeko_version: str
    warnings: tuple[str, ...] = field(repr=False)


def _is_reparse(metadata: os.stat_result) -> bool:
    return bool(getattr(metadata, "st_file_attributes", 0) & _WINDOWS_REPARSE_POINT)


def _identity(metadata: os.stat_result) -> tuple[int, ...]:
    return _stat_identity(metadata)


def _file_identity(
    metadata: os.stat_result,
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    return _stat_identity(metadata), _stat_version(metadata)


def _absolute_components(path: Path) -> list[Path]:
    current = Path(path.anchor)
    components = [current]
    for part in path.parts[1:]:
        current /= part
        components.append(current)
    return components


def _real_absolute_directory(candidate: object) -> Path:
    if not isinstance(candidate, Path) or not candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError
    try:
        lexical = Path(os.path.abspath(candidate))
        for component in _absolute_components(lexical):
            metadata = component.lstat()
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or _is_reparse(metadata)
            ):
                raise ValueError
        resolved = lexical.resolve(strict=True)
        if lexical != resolved:
            raise ValueError
        return lexical
    except (OSError, RuntimeError, ValueError):
        raise ValueError from None


def _ensure_real_child_directory(parent: Path, name: str) -> Path:
    child = parent / name
    try:
        child.mkdir(mode=0o700)
    except FileExistsError:
        pass
    metadata = child.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or _is_reparse(metadata)
    ):
        raise ValueError
    return child


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError
        view = view[written:]


def _fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _safe_unlink_created(
    path: Path,
    created_identity: tuple[int, ...] | None,
    created_version: tuple[int, ...] | None = None,
) -> bool:
    if created_identity is None:
        return False
    try:
        metadata = path.lstat()
        if (
            _identity(metadata) == created_identity
            and (created_version is None or _stat_version(metadata) == created_version)
            and stat.S_ISREG(metadata.st_mode)
            and not stat.S_ISLNK(metadata.st_mode)
            and not _is_reparse(metadata)
        ):
            path.unlink()
            return True
    except (FileNotFoundError, OSError, RuntimeError):
        pass
    return False


def _validate_stage_arguments(
    state_root: object,
    job_id: object,
    role: object,
    suffix: object,
    source: object,
    max_bytes: object,
    client_filename: object,
) -> tuple[Path, str, str, BinaryIO, int]:
    root = _real_absolute_directory(state_root)
    if type(job_id) is not str or _UUID_HEX_PATTERN.fullmatch(job_id) is None:
        raise ValueError
    if type(role) is not str or role not in _ALLOWED_SUFFIXES:
        raise ValueError
    if type(suffix) is not str or suffix not in _ALLOWED_SUFFIXES[role]:
        raise ValueError
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError
    if client_filename is not None and type(client_filename) is not str:
        raise ValueError
    if not callable(getattr(source, "read", None)):
        raise ValueError
    return root, job_id, f"{role}{suffix}", source, max_bytes  # type: ignore[return-value]


def stage_input(
    state_root: Path,
    job_id: str,
    role: str,
    suffix: str,
    source: BinaryIO,
    max_bytes: int,
    *,
    client_filename: str | None = None,
) -> StagedInput:
    """Stream one bounded upload into a server-named per-job input file."""

    part: Path | None = None
    final: Path | None = None
    part_identity: tuple[int, ...] | None = None
    part_version: tuple[int, ...] | None = None
    published_identity: tuple[int, ...] | None = None
    published_version: tuple[int, ...] | None = None
    descriptor: int | None = None
    published = False
    try:
        root, validated_job_id, server_name, reader, limit = _validate_stage_arguments(
            state_root,
            job_id,
            role,
            suffix,
            source,
            max_bytes,
            client_filename,
        )
        jobs = _ensure_real_child_directory(root, "jobs")
        job_root = _ensure_real_child_directory(jobs, validated_job_id)
        input_root = _ensure_real_child_directory(job_root, "input")
        parent_identity = _identity(input_root.lstat())
        part = input_root / f".{server_name}.part"
        final = input_root / server_name
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        descriptor = os.open(part, flags, 0o600)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise ValueError
        part_identity = _identity(opened)

        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = reader.read(_CHUNK_BYTES)
            if type(chunk) is not bytes:
                raise ValueError
            if not chunk:
                break
            if len(chunk) > _CHUNK_BYTES or total + len(chunk) > limit:
                raise ValueError
            _write_all(descriptor, chunk)
            digest.update(chunk)
            total += len(chunk)
        if total == 0:
            raise ValueError
        os.fsync(descriptor)
        after = os.fstat(descriptor)
        part_version = _stat_version(after)
        named = part.lstat()
        if (
            after.st_nlink != 1
            or _identity(after) != part_identity
            or _identity(named) != part_identity
            or _is_reparse(named)
            or _identity(input_root.lstat()) != parent_identity
        ):
            raise ValueError
        os.close(descriptor)
        descriptor = None

        try:
            existing = final.lstat()
        except FileNotFoundError:
            existing = None
        if existing is not None:
            if (
                not stat.S_ISREG(existing.st_mode)
                or stat.S_ISLNK(existing.st_mode)
                or _is_reparse(existing)
                or existing.st_nlink != 1
            ):
                raise ValueError
            snapshot = read_file_snapshot(final, limit)
            if snapshot.sha256 != digest.hexdigest() or len(snapshot.content) != total:
                raise ValueError
            if not _safe_unlink_created(part, part_identity, part_version):
                raise ValueError
            part_identity = None
            part_version = None
        else:
            if _identity(input_root.lstat()) != parent_identity:
                raise ValueError
            os.link(part, final, follow_symlinks=False)
            published = True
            linked_part = part.lstat()
            linked_final = final.lstat()
            if (
                _identity(linked_part) != part_identity
                or not stat.S_ISREG(linked_part.st_mode)
                or linked_part.st_nlink != 2
                or _is_reparse(linked_part)
                or _file_identity(linked_part) != _file_identity(linked_final)
            ):
                raise ValueError
            published_identity = _identity(linked_final)
            published_version = _stat_version(linked_final)
            if not _safe_unlink_created(
                part,
                _identity(linked_part),
                _stat_version(linked_part),
            ):
                raise ValueError
            part_identity = None
            part_version = None
            final_metadata = final.lstat()
            published_version = _stat_version(final_metadata)
            if (
                _identity(final_metadata) != _identity(after)
                or not stat.S_ISREG(final_metadata.st_mode)
                or final_metadata.st_nlink != 1
                or _is_reparse(final_metadata)
            ):
                raise ValueError
            _fsync_directory(input_root)

        relative = PurePosixPath("jobs", validated_job_id, "input", server_name)
        return StagedInput(str(relative), total, digest.hexdigest())
    except Exception:
        if published and final is not None:
            _safe_unlink_created(final, published_identity, published_version)
        raise InputStagingError("input staging failed") from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if part is not None:
            _safe_unlink_created(part, part_identity, part_version)


def _reject_json_constant(_: str) -> None:
    raise ValueError


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _read_regular_snapshot(path: Path, maximum_bytes: int) -> bytes:
    before = path.lstat()
    if (
        not stat.S_ISREG(before.st_mode)
        or stat.S_ISLNK(before.st_mode)
        or _is_reparse(before)
        or before.st_nlink != 1
        or before.st_size <= 0
        or before.st_size > maximum_bytes
    ):
        raise ValueError
    snapshot = read_file_snapshot(path, maximum_bytes)
    after = path.lstat()
    if (
        _identity(before) != _identity(after)
        or after.st_nlink != 1
        or len(snapshot.content) != before.st_size
    ):
        raise ValueError
    return snapshot.content


def _project_without_links(root: Path, relative: PurePosixPath) -> Path:
    projected = root
    for index, part in enumerate(relative.parts):
        projected /= part
        metadata = projected.lstat()
        if stat.S_ISLNK(metadata.st_mode) or _is_reparse(metadata):
            raise ValueError
        if index < len(relative.parts) - 1 and not stat.S_ISDIR(metadata.st_mode):
            raise ValueError
    return projected


def _pose_relative_path(value: object) -> tuple[str, PurePosixPath]:
    if type(value) is not str or not value or "\\" in value or ":" in value:
        raise ValueError
    if not value.isascii() or unicodedata.normalize("NFC", value) != value:
        raise ValueError
    pieces = value.split("/")
    if any(part in {"", ".", ".."} or part.endswith((" ", ".")) for part in pieces):
        raise ValueError
    relative = PurePosixPath(value)
    if relative.is_absolute() or str(relative) != value:
        raise ValueError
    if not relative.parts or relative.parts[0] != "poses" or relative.suffix != ".pdbqt":
        raise ValueError
    return value, relative


def _append_pose_line_energy(
    line: bytes, energies: list[float], maximum_modes: int
) -> None:
    if line.endswith(b"\r"):
        line = line[:-1]
    if _ENERGY_CLAIM_PATTERN.match(line) is None:
        return
    match = _ENERGY_PATTERN.fullmatch(line)
    if match is None:
        raise ValueError
    values = tuple(float(match.group(index)) for index in (1, 2, 3))
    if any(not math.isfinite(value) for value in values):
        raise ValueError
    if len(energies) >= maximum_modes:
        raise ValueError
    energies.append(values[0])


def _consume_pose_chunk(
    chunk: bytes,
    line_buffer: bytearray,
    energies: list[float],
    maximum_modes: int,
) -> None:
    if any(
        value not in (9, 10, 13) and not 32 <= value <= 126
        for value in chunk
    ):
        raise ValueError
    start = 0
    while True:
        newline = chunk.find(b"\n", start)
        if newline < 0:
            line_buffer.extend(chunk[start:])
            if len(line_buffer) > _MAX_POSE_LINE_BYTES:
                raise ValueError
            return
        line_buffer.extend(chunk[start:newline])
        if len(line_buffer) > _MAX_POSE_LINE_BYTES:
            raise ValueError
        _append_pose_line_energy(bytes(line_buffer), energies, maximum_modes)
        line_buffer.clear()
        start = newline + 1


def _read_pose_energies(
    path: Path, maximum_bytes: int, maximum_modes: int
) -> tuple[tuple[float, ...], int]:
    descriptors: list[int] = []
    parent_paths: list[Path] = []
    try:
        if maximum_bytes < 0 or maximum_modes <= 0:
            raise ValueError
        if os.name == "posix":
            directory_flags = (
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
            )
            if not getattr(os, "O_DIRECTORY", 0) or not getattr(os, "O_NOFOLLOW", 0):
                raise ValueError
            current = os.open(path.anchor, directory_flags)
            descriptors.append(current)
            directory_identities = [_identity(os.fstat(current))]
            parent_paths.append(Path(path.anchor))
            parts = path.parts[1:]
            if not parts:
                raise ValueError
            current_path = Path(path.anchor)
            for component in parts[:-1]:
                current = os.open(component, directory_flags, dir_fd=current)
                descriptors.append(current)
                metadata = os.fstat(current)
                if not stat.S_ISDIR(metadata.st_mode) or _is_reparse(metadata):
                    raise ValueError
                directory_identities.append(_identity(metadata))
                current_path /= component
                parent_paths.append(current_path)
            flags = (
                os.O_RDONLY
                | os.O_NONBLOCK
                | getattr(os, "O_NOCTTY", 0)
                | os.O_NOFOLLOW
                | getattr(os, "O_CLOEXEC", 0)
            )
            descriptor = os.open(parts[-1], flags, dir_fd=current)
            descriptors.append(descriptor)
            before = os.fstat(descriptor)
            named_before = os.stat(parts[-1], dir_fd=current, follow_symlinks=False)
        else:
            current_path = Path(path.anchor)
            for component in path.parts[1:-1]:
                current_path /= component
                metadata = current_path.lstat()
                if (
                    not stat.S_ISDIR(metadata.st_mode)
                    or stat.S_ISLNK(metadata.st_mode)
                    or _is_reparse(metadata)
                ):
                    raise ValueError
                parent_paths.append(current_path)
            directory_identities = [
                _identity(parent.lstat()) for parent in parent_paths
            ]
            flags = (
                os.O_RDONLY
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_NONBLOCK", 0)
            )
            descriptor = os.open(path, flags)
            descriptors.append(descriptor)
            before = os.fstat(descriptor)
            named_before = path.lstat()

        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(named_before.st_mode)
            or _is_reparse(named_before)
            or before.st_nlink != 1
            or named_before.st_nlink != 1
            or before.st_size <= 0
            or before.st_size > maximum_bytes
            or _file_identity(before) != _file_identity(named_before)
        ):
            raise ValueError

        energies: list[float] = []
        line_buffer = bytearray()
        total = 0
        while True:
            request = min(_POSE_READ_CHUNK_BYTES, maximum_bytes + 1 - total)
            if request <= 0:
                raise ValueError
            chunk = os.read(descriptor, request)
            if not chunk:
                break
            total += len(chunk)
            if total > maximum_bytes:
                raise ValueError
            _consume_pose_chunk(chunk, line_buffer, energies, maximum_modes)
        if line_buffer:
            _append_pose_line_energy(bytes(line_buffer), energies, maximum_modes)
        if not energies:
            raise ValueError

        after = os.fstat(descriptor)
        if os.name == "posix":
            named_after = os.stat(parts[-1], dir_fd=current, follow_symlinks=False)
            current_directory_identities = [
                _identity(os.fstat(item)) for item in descriptors[:-1]
            ]
        else:
            named_after = path.lstat()
            current_directory_identities = [
                _identity(parent.lstat()) for parent in parent_paths
            ]
        named_directory_identities: list[tuple[int, ...]] = []
        for parent in parent_paths:
            parent_metadata = parent.lstat()
            if (
                not stat.S_ISDIR(parent_metadata.st_mode)
                or stat.S_ISLNK(parent_metadata.st_mode)
                or _is_reparse(parent_metadata)
            ):
                raise ValueError
            named_directory_identities.append(_identity(parent_metadata))
        if (
            total != before.st_size
            or after.st_nlink != 1
            or named_after.st_nlink != 1
            or _file_identity(before) != _file_identity(after)
            or _file_identity(before) != _file_identity(named_after)
            or directory_identities != current_directory_identities
            or directory_identities != named_directory_identities
        ):
            raise ValueError
        return tuple(energies), total
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _validate_scientific_output(
    output_root: object,
    receptor_sha256: object,
    ligand_sha256: object,
    max_output_bytes: object,
) -> ValidatedScientificOutput:
    if (
        type(receptor_sha256) is not str
        or _SHA256_PATTERN.fullmatch(receptor_sha256) is None
        or type(ligand_sha256) is not str
        or _SHA256_PATTERN.fullmatch(ligand_sha256) is None
        or type(max_output_bytes) is not int
        or max_output_bytes <= 0
    ):
        raise ValueError
    root = _real_absolute_directory(output_root)
    poses_root = root / "poses"
    poses_metadata = poses_root.lstat()
    if (
        not stat.S_ISDIR(poses_metadata.st_mode)
        or stat.S_ISLNK(poses_metadata.st_mode)
        or _is_reparse(poses_metadata)
    ):
        raise ValueError

    result_content = _read_regular_snapshot(
        root / "result.json", min(max_output_bytes, _MAX_RESULT_JSON_BYTES)
    )
    payload = json.loads(
        result_content.decode("utf-8"),
        parse_constant=_reject_json_constant,
        object_pairs_hook=_unique_json_object,
    )
    if type(payload) is not dict or frozenset(payload) != _EXPECTED_RESULT_FIELDS:
        raise ValueError
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise ValueError
    if type(payload["status"]) is not str or payload["status"] != "succeeded":
        raise ValueError
    if payload["receptor_sha256"] != receptor_sha256 or payload["ligand_sha256"] != ligand_sha256:
        raise ValueError
    pose_count = payload["pose_count"]
    if type(pose_count) is not int or not 0 < pose_count <= _MAX_POSE_MODES:
        raise ValueError
    best_value = payload["best_energy"]
    if type(best_value) not in (int, float):
        raise ValueError
    best_energy = float(best_value)
    if not math.isfinite(best_energy):
        raise ValueError
    for key in ("vina_version", "meeko_version"):
        if type(payload[key]) is not str or not payload[key].strip():
            raise ValueError
    warnings = payload["warnings"]
    if (
        type(warnings) is not list
        or len(warnings) > _MAX_WARNINGS
        or any(
            type(item) is not str or len(item) > _MAX_WARNING_LENGTH
            for item in warnings
        )
    ):
        raise ValueError
    pose_values = payload["pose_files"]
    if (
        type(pose_values) is not list
        or not pose_values
        or len(pose_values) > _MAX_POSE_FILES
    ):
        raise ValueError

    parsed_paths = [_pose_relative_path(item) for item in pose_values]
    aliases = [item[0].casefold() for item in parsed_paths]
    if len(set(aliases)) != len(aliases):
        raise ValueError

    total_bytes = len(result_content)
    all_energies: list[float] = []
    pose_names: list[str] = []
    for index, (name, relative) in enumerate(parsed_paths):
        projected = _project_without_links(root, relative)
        energies, pose_bytes = _read_pose_energies(
            projected,
            max_output_bytes - total_bytes,
            _MAX_POSE_MODES - len(all_energies),
        )
        total_bytes += pose_bytes
        if index == 0 and not math.isclose(
            energies[0], best_energy, rel_tol=0.0, abs_tol=1e-6
        ):
            raise ValueError
        all_energies.extend(energies)
        pose_names.append(name)
    if (
        len(all_energies) != pose_count
        or all_energies != sorted(all_energies)
        or not math.isclose(
            min(all_energies), best_energy, rel_tol=0.0, abs_tol=1e-6
        )
    ):
        raise ValueError

    return ValidatedScientificOutput(
        schema_version=1,
        status="succeeded",
        receptor_sha256=receptor_sha256,
        ligand_sha256=ligand_sha256,
        pose_count=pose_count,
        best_energy=best_energy,
        pose_files=tuple(pose_names),
        vina_version=payload["vina_version"],
        meeko_version=payload["meeko_version"],
        warnings=tuple(warnings),
    )


def validate_scientific_output(
    output_root: Path,
    receptor_sha256: str,
    ligand_sha256: str,
    max_output_bytes: int,
) -> ValidatedScientificOutput:
    """Validate bounded sandbox output before any result is trusted or published."""

    try:
        return _validate_scientific_output(
            output_root,
            receptor_sha256,
            ligand_sha256,
            max_output_bytes,
        )
    except Exception:
        raise ScientificOutputError("scientific output validation failed") from None
