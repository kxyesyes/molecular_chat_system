"""Real temporary snapshot boundaries; no real Vina or deployment assets."""
import hashlib
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.task_runtime import secure_io


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "ancestor" / "parent" / "pose.pdbqt"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"synthetic pose")
    return path.resolve()


def after_read(monkeypatch, change):
    original = secure_io._bounded_read

    def read(fd, limit):
        content = original(fd, limit)
        change()
        return content

    monkeypatch.setattr(secure_io, "_bounded_read", read)


@pytest.mark.parametrize("height", [0, 1])
def test_unrelated_sibling_change_does_not_invalidate_file(source, monkeypatch, height):
    ancestor = source.parents[height]
    before = source.stat()

    def change():
        old = ancestor.stat()
        sibling = ancestor / "unrelated-temporary"
        sibling.write_bytes(b"unrelated")
        sibling.unlink()
        os.utime(ancestor, ns=(old.st_atime_ns, old.st_mtime_ns + 10_000_000))

    after_read(monkeypatch, change)
    result = secure_io.read_file_snapshot(source, 1024)
    assert result.content == b"synthetic pose"
    assert result.sha256 == hashlib.sha256(result.content).hexdigest()
    assert result.identity == secure_io._identity(before)


@pytest.mark.parametrize("change", ["modify", "replace", "missing"])
def test_file_change_still_rejected(source, monkeypatch, change):
    def mutate():
        before = source.stat()
        if change == "modify":
            source.write_bytes(b"different pose")
            os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns + 10_000_000))
        elif change == "replace":
            replacement = source.with_name("replacement")
            replacement.write_bytes(b"synthetic pose")
            os.replace(replacement, source)
        else:
            source.unlink()

    after_read(monkeypatch, mutate)
    with pytest.raises(ValueError, match="^unsafe file snapshot$"):
        secure_io.read_file_snapshot(source, 1024)


@pytest.mark.skipif(os.name != "posix", reason="POSIX pinned directory/named path semantics")
@pytest.mark.parametrize("change", ["replace", "symlink", "permissions"])
def test_pinned_directory_remains_bound_to_named_path(source, monkeypatch, change):
    parent = source.parent
    original_mode = stat.S_IMODE(parent.stat().st_mode)

    def mutate():
        if change == "permissions":
            parent.chmod(original_mode ^ stat.S_IXUSR)
        else:
            moved = parent.with_name("moved-parent")
            parent.rename(moved)
            if change == "symlink":
                parent.symlink_to(moved, target_is_directory=True)
            else:
                parent.mkdir()
                (parent / source.name).write_bytes(b"synthetic pose")

    after_read(monkeypatch, mutate)
    try:
        with pytest.raises(ValueError, match="^unsafe file snapshot$"):
            secure_io.read_file_snapshot(source, 1024)
    finally:
        if change == "permissions":
            parent.chmod(original_mode)


@pytest.mark.skipif(os.name != "nt", reason="Windows ancestor lstat boundary")
@pytest.mark.parametrize("field", ["st_ino", "st_mode", "st_file_attributes"])
def test_windows_changed_ancestor_identity_is_rejected(source, monkeypatch, field):
    original = Path.lstat
    changed = False

    def lstat(path, *args, **kwargs):
        value = original(path, *args, **kwargs)
        if changed and path == source.parent:
            fields = {name: getattr(value, name) for name in dir(value) if name.startswith("st_")}
            fields[field] = (value.st_mode ^ stat.S_IWRITE if field == "st_mode"
                             else fields[field] | 0x400 if field == "st_file_attributes"
                             else fields[field] + 1)
            return SimpleNamespace(**fields)
        return value

    def mutate():
        nonlocal changed
        changed = True

    monkeypatch.setattr(Path, "lstat", lstat)
    after_read(monkeypatch, mutate)
    with pytest.raises(ValueError, match="^unsafe file snapshot$"):
        secure_io.read_file_snapshot(source, 1024)


@pytest.mark.parametrize("limit", [0, 1024])
def test_snapshot_closes_all_opened_descriptors(source, monkeypatch, limit):
    opened, closed = [], []
    original_open, original_close = os.open, os.close

    def open_file(*args, **kwargs):
        descriptor = original_open(*args, **kwargs)
        opened.append(descriptor)
        return descriptor

    def close_file(descriptor):
        closed.append(descriptor)
        return original_close(descriptor)

    monkeypatch.setattr(os, "open", open_file)
    monkeypatch.setattr(os, "close", close_file)
    if limit:
        assert secure_io.read_file_snapshot(source, limit).content == b"synthetic pose"
    else:
        with pytest.raises(ValueError, match="^unsafe file snapshot$"):
            secure_io.read_file_snapshot(source, limit)
    assert opened and closed == list(reversed(opened))
