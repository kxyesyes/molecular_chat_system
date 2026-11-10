"""Filesystem operations shared by broker input staging and artifact publication."""

import os
from pathlib import Path, PurePosixPath
import stat

from src.task_runtime.config import _absolute_path_components
from src.task_runtime.secure_io import _is_reparse


def _real_absolute_directory(candidate: object) -> Path:
    if not isinstance(candidate, Path) or not candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError
    try:
        lexical = Path(os.path.abspath(candidate))
        for component in _absolute_path_components(lexical):
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
