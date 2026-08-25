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

from src.task_runtime.secure_io import read_file_snapshot


_CHUNK_BYTES = 1024 * 1024
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_UUID_HEX_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_ENERGY_PATTERN = re.compile(r"^REMARK VINA RESULT:\s+(\S+)")
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
    "receptor": frozenset({".pdb", ".pdbqt"}),
    "ligand": frozenset({".sdf", ".mol", ".pdb", ".pdbqt"}),
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


def _identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


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


def _safe_unlink_created(path: Path, created_identity: tuple[int, int] | None) -> None:
    if created_identity is None:
        return
    try:
        metadata = path.lstat()
        if (
            _identity(metadata) == created_identity
            and stat.S_ISREG(metadata.st_mode)
            and not stat.S_ISLNK(metadata.st_mode)
            and not _is_reparse(metadata)
        ):
            path.unlink()
    except (FileNotFoundError, OSError, RuntimeError):
        pass


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
    part_identity: tuple[int, int] | None = None
    published_identity: tuple[int, int] | None = None
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
            _safe_unlink_created(part, part_identity)
            part_identity = None
        else:
            if _identity(input_root.lstat()) != parent_identity:
                raise ValueError
            os.link(part, final, follow_symlinks=False)
            published = True
            published_identity = _identity(after)
            part.unlink()
            part_identity = None
            final_metadata = final.lstat()
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
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if part is not None:
            _safe_unlink_created(part, part_identity)
        if published and final is not None:
            _safe_unlink_created(final, published_identity)
        raise InputStagingError("input staging failed") from None


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


def _parse_pose_energies(content: bytes) -> tuple[float, ...]:
    text = content.decode("utf-8")
    energies: list[float] = []
    for line in text.splitlines():
        match = _ENERGY_PATTERN.match(line)
        if match is None:
            continue
        energy = float(match.group(1))
        if not math.isfinite(energy):
            raise ValueError
        energies.append(energy)
    if not energies:
        raise ValueError
    return tuple(energies)


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

    result_content = _read_regular_snapshot(root / "result.json", max_output_bytes)
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
    if type(pose_count) is not int or pose_count <= 0:
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
    if type(warnings) is not list or any(type(item) is not str for item in warnings):
        raise ValueError
    pose_values = payload["pose_files"]
    if type(pose_values) is not list:
        raise ValueError

    parsed_paths = [_pose_relative_path(item) for item in pose_values]
    aliases = [item[0].casefold() for item in parsed_paths]
    if len(parsed_paths) != pose_count or len(set(aliases)) != len(aliases):
        raise ValueError

    total_bytes = len(result_content)
    all_energies: list[float] = []
    pose_names: list[str] = []
    for name, relative in parsed_paths:
        projected = _project_without_links(root, relative)
        content = _read_regular_snapshot(projected, max_output_bytes)
        total_bytes += len(content)
        if total_bytes > max_output_bytes:
            raise ValueError
        energies = _parse_pose_energies(content)
        if not math.isclose(energies[0], best_energy, rel_tol=0.0, abs_tol=1e-6):
            raise ValueError
        all_energies.extend(energies)
        pose_names.append(name)
    if not all_energies or not math.isclose(
        min(all_energies), best_energy, rel_tol=0.0, abs_tol=1e-6
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
