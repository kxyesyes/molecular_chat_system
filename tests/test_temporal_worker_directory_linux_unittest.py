from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "deployment/libexec/prepare-temporal-worker-directories.py"
RUN = (
    os.name == "posix"
    and hasattr(os, "geteuid")
    and os.geteuid() == 0
    and os.environ.get("MEDCHAT_RUN_TEMPORAL_DIRECTORY_INTEGRATION") == "1"
)
TEST_UID = 65534
TEST_GID = 65534


def _load_helper():
    spec = importlib.util.spec_from_file_location("temporal_directory_helper_linux", HELPER)
    if spec is None or spec.loader is None:
        raise RuntimeError("helper_import_failed")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _remove_no_follow(root: Path) -> None:
    root_text = os.fspath(root)
    if not root_text.startswith("/root/medchat-temporal-directory-"):
        raise RuntimeError("cleanup_root_outside_fixture")

    def remove(path: str) -> None:
        metadata = os.lstat(path)
        if stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
            for entry in list(os.scandir(path)):
                remove(entry.path)
            os.rmdir(path)
        else:
            os.unlink(path)

    try:
        remove(root_text)
    except FileNotFoundError:
        pass


@unittest.skipUnless(
    RUN,
    "requires Linux root and MEDCHAT_RUN_TEMPORAL_DIRECTORY_INTEGRATION=1",
)
class TemporalWorkerDirectoryLinuxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.helper = _load_helper()
        self.fixture = Path(
            f"/root/medchat-temporal-directory-{os.getpid()}-{time.time_ns()}"
        )
        self.project = self.fixture / "opt/medchat/molecular_chat_system"
        self.project.mkdir(parents=True, mode=0o700)
        for parent in (
            self.fixture,
            self.fixture / "opt",
            self.fixture / "opt/medchat",
            self.project,
        ):
            os.chown(parent, 0, 0)
            os.chmod(parent, 0o700)
        self.root_fd = os.open(
            self.fixture,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )

    def tearDown(self) -> None:
        os.close(self.root_fd)
        _remove_no_follow(self.fixture)

    def prepare(self) -> None:
        self.helper._prepare_runtime_directories(self.root_fd, TEST_UID, TEST_GID)

    def assert_code(self, code: str) -> None:
        with self.assertRaises(self.helper.DirectoryPreparationError) as raised:
            self.prepare()
        self.assertEqual(raised.exception.code, code)
        self.assertEqual(str(raised.exception), code)

    def test_empty_deployment_creates_exact_directories(self) -> None:
        self.prepare()
        for relative in self.helper.RUNTIME_DIRECTORIES:
            metadata = os.lstat(self.fixture / relative)
            self.assertTrue(stat.S_ISDIR(metadata.st_mode))
            self.assertFalse(stat.S_ISLNK(metadata.st_mode))
            self.assertEqual((metadata.st_uid, metadata.st_gid), (TEST_UID, TEST_GID))
            self.assertEqual(stat.S_IMODE(metadata.st_mode), 0o700)

    def test_existing_exact_directories_are_idempotent(self) -> None:
        self.prepare()
        identities = {
            relative: os.lstat(self.fixture / relative).st_ino
            for relative in self.helper.RUNTIME_DIRECTORIES
        }
        self.prepare()
        self.assertEqual(
            identities,
            {
                relative: os.lstat(self.fixture / relative).st_ino
                for relative in self.helper.RUNTIME_DIRECTORIES
            },
        )

    def test_leaf_symlink_preserves_sentinel(self) -> None:
        sentinel = self.fixture / "sentinel"
        sentinel.mkdir(mode=0o755)
        os.chown(sentinel, 0, 0)
        before = os.lstat(sentinel)
        os.symlink(sentinel, self.project / "scratch")
        self.assert_code("directory_path_invalid")
        after = os.lstat(sentinel)
        self.assertEqual(
            (before.st_dev, before.st_ino, before.st_uid, before.st_gid,
             stat.S_IMODE(before.st_mode)),
            (after.st_dev, after.st_ino, after.st_uid, after.st_gid,
             stat.S_IMODE(after.st_mode)),
        )

    def test_systemd_259_legacy_tmpfiles_symlink_regression_is_eliminated(self) -> None:
        version = subprocess.run(
            ["systemd-tmpfiles", "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
        if version.returncode != 0 or not version.stdout.startswith("systemd 259 "):
            self.skipTest("requires systemd-tmpfiles 259 regression fixture")
        sentinel = self.fixture / "sentinel"
        sentinel.mkdir(mode=0o755)
        os.chown(sentinel, 0, 0)
        os.chmod(sentinel, 0o755)
        os.symlink("../../../sentinel", self.project / "scratch")
        policy = self.fixture / "legacy-tmpfiles.conf"
        policy.write_text(
            "d /opt/medchat/molecular_chat_system/scratch 0700 root root -\n",
            encoding="ascii",
        )
        legacy = subprocess.run(
            [
                "systemd-tmpfiles",
                f"--root={self.fixture}",
                "--create",
                os.fspath(policy),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(legacy.returncode, 0, legacy.stderr)
        self.assertIn("not a directory", legacy.stderr.lower())
        before = os.lstat(sentinel)
        self.assertEqual(stat.S_IMODE(before.st_mode), 0o755)
        self.assert_code("directory_path_invalid")
        after = os.lstat(sentinel)
        self.assertEqual(
            (before.st_dev, before.st_ino, before.st_uid, before.st_gid,
             stat.S_IMODE(before.st_mode)),
            (after.st_dev, after.st_ino, after.st_uid, after.st_gid,
             stat.S_IMODE(after.st_mode)),
        )

    def test_parent_symlink_fails(self) -> None:
        alternate = self.fixture / "alternate"
        alternate.mkdir(mode=0o700)
        os.rename(self.fixture / "opt/medchat", self.fixture / "opt/medchat.real")
        os.symlink(alternate, self.fixture / "opt/medchat")
        self.assert_code("directory_path_invalid")

    def test_fifo_and_regular_leaf_fail(self) -> None:
        for kind in ("fifo", "regular"):
            with self.subTest(kind=kind):
                leaf = self.project / "scratch"
                if kind == "fifo":
                    os.mkfifo(leaf, 0o600)
                else:
                    leaf.write_text("sentinel", encoding="utf-8")
                self.assert_code("directory_entry_not_directory")
                leaf.unlink()

    def test_existing_wrong_owner_or_mode_fails_without_repair(self) -> None:
        leaf = self.project / "scratch"
        for owner, mode, code in (
            ((0, 0), 0o700, "directory_entry_wrong_owner"),
            ((TEST_UID, TEST_GID), 0o755, "directory_entry_wrong_mode"),
        ):
            with self.subTest(code=code):
                leaf.mkdir(mode=0o700)
                os.chown(leaf, *owner)
                os.chmod(leaf, mode)
                self.assert_code(code)
                metadata = os.lstat(leaf)
                self.assertEqual((metadata.st_uid, metadata.st_gid), owner)
                self.assertEqual(stat.S_IMODE(metadata.st_mode), mode)
                leaf.rmdir()

    def test_concurrent_entry_replacement_fails_identity_check(self) -> None:
        original_stat = self.helper.os.stat
        replaced = False

        def replacing_stat(path, *args, **kwargs):
            nonlocal replaced
            if path == "scratch" and kwargs.get("follow_symlinks") is False and not replaced:
                replaced = True
                old = self.project / "scratch.old"
                os.rename(self.project / "scratch", old)
                replacement = self.project / "scratch"
                replacement.mkdir(mode=0o700)
                os.chown(replacement, TEST_UID, TEST_GID)
                os.chmod(replacement, 0o700)
            return original_stat(path, *args, **kwargs)

        with mock.patch.object(self.helper.os, "stat", side_effect=replacing_stat):
            self.assert_code("directory_entry_changed")

    def test_missing_medchat_identity_is_stable(self) -> None:
        with mock.patch.object(
            self.helper.pwd,
            "getpwnam",
            side_effect=KeyError("private identity"),
        ):
            with self.assertRaises(self.helper.DirectoryPreparationError) as raised:
                self.helper.prepare_runtime_directories()
        self.assertEqual(raised.exception.code, "directory_identity_unavailable")


if __name__ == "__main__":
    unittest.main()
