from __future__ import annotations

import errno
import importlib.util
import os
import stat
import threading
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = ROOT / "deployment" / "libexec" / "validate-temporal-worker-env.py"
RUN_TRUST_INTEGRATION = (
    os.name == "posix"
    and hasattr(os, "geteuid")
    and os.geteuid() == 0
    and os.environ.get("MEDCHAT_RUN_TEMPORAL_TRUST_INTEGRATION") == "1"
)
VALID_PAYLOAD = (
    b"MEDCHAT_TASK_BACKEND=temporal_canary\n"
    b"MEDCHAT_TEMPORAL_CANARY_PERCENT=5\n"
)


def _load_helper():
    spec = importlib.util.spec_from_file_location("temporal_worker_env_helper_linux", HELPER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("helper_import_failed")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _remove_tree_no_follow(root: Path) -> None:
    root_text = os.fspath(root)
    if not root_text.startswith("/root/medchat-temporal-trust-"):
        raise RuntimeError("cleanup_root_outside_fixture")

    def remove(path: str) -> None:
        metadata = os.lstat(path)
        if stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
            with os.scandir(path) as entries:
                children = [entry.path for entry in entries]
            for child in children:
                remove(child)
            os.rmdir(path)
        else:
            os.unlink(path)

    try:
        remove(root_text)
    except FileNotFoundError:
        return


@unittest.skipUnless(
    RUN_TRUST_INTEGRATION,
    "requires Linux root and MEDCHAT_RUN_TEMPORAL_TRUST_INTEGRATION=1",
)
class TemporalWorkerTrustLinuxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.helper = _load_helper()
        self.fixture_root = Path(f"/root/medchat-temporal-trust-{os.getpid()}-{time.time_ns()}")
        self.secure_parent = self.fixture_root / "etc" / "medchat"
        self.secure_parent.mkdir(parents=True, mode=0o700)
        os.chmod(self.fixture_root, 0o700)
        os.chmod(self.fixture_root / "etc", 0o700)
        os.chmod(self.secure_parent, 0o700)
        self.environment_file = self.secure_parent / "temporal-worker.env"
        self._write_regular(self.environment_file, VALID_PAYLOAD)

    def tearDown(self) -> None:
        _remove_tree_no_follow(self.fixture_root)

    @staticmethod
    def _write_regular(path: Path, payload: bytes, mode: int = 0o600) -> None:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        try:
            os.write(descriptor, payload)
        finally:
            os.close(descriptor)
        os.chmod(path, mode)
        os.chown(path, 0, 0)

    def _assert_code(self, expected: str, path: Path | None = None) -> None:
        with self.assertRaises(self.helper.TrustBoundaryError) as raised:
            self.helper.validate_environment_file(path or self.environment_file)
        self.assertEqual(raised.exception.code, expected)
        self.assertEqual(str(raised.exception), expected)

    def test_secure_root_owned_regular_file_passes(self) -> None:
        result = self.helper.validate_environment_file(self.environment_file)
        self.assertEqual(result["MEDCHAT_TASK_BACKEND"], "temporal_canary")
        self.assertLessEqual(result.keys(), self.helper.ALLOWED_KEYS)

    def test_parent_symlink_fails_closed(self) -> None:
        alternate = self.fixture_root / "alternate"
        alternate.mkdir(mode=0o700)
        self._write_regular(alternate / "temporal-worker.env", VALID_PAYLOAD)
        os.symlink(alternate, self.fixture_root / "linked-parent")
        self._assert_code(
            "environment_path_invalid",
            self.fixture_root / "linked-parent" / "temporal-worker.env",
        )

    def test_leaf_symlink_fails_closed(self) -> None:
        target = self.secure_parent / "target.env"
        self._write_regular(target, VALID_PAYLOAD)
        self.environment_file.unlink()
        os.symlink(target, self.environment_file)
        self._assert_code("environment_path_invalid")

    def test_group_or_world_writable_parent_fails(self) -> None:
        for mode in (0o720, 0o702):
            with self.subTest(mode=oct(mode)):
                os.chmod(self.secure_parent, mode)
                self._assert_code("environment_parent_untrusted")
                os.chmod(self.secure_parent, 0o700)

    def test_wrong_parent_owner_fails(self) -> None:
        os.chown(self.secure_parent, 65534, 65534)
        self._assert_code("environment_parent_untrusted")

    def test_wrong_leaf_owner_fails(self) -> None:
        os.chown(self.environment_file, 65534, 65534)
        self._assert_code("environment_file_wrong_owner")

    def test_wrong_leaf_modes_fail(self) -> None:
        for mode in (0o640, 0o666):
            with self.subTest(mode=oct(mode)):
                os.chmod(self.environment_file, mode)
                self._assert_code("environment_file_wrong_mode")
                os.chmod(self.environment_file, 0o600)

    def test_fifo_fails_without_blocking(self) -> None:
        self.environment_file.unlink()
        os.mkfifo(self.environment_file, 0o600)
        self._assert_code("environment_file_not_regular")

    def test_device_node_fails_closed_when_mknod_is_available(self) -> None:
        self.environment_file.unlink()
        try:
            os.mknod(
                self.environment_file,
                stat.S_IFCHR | 0o600,
                os.makedev(1, 3),
            )
        except OSError as exc:
            if exc.errno in {errno.EPERM, errno.EACCES, errno.ENOSYS, errno.EOPNOTSUPP}:
                self.skipTest("mknod is unavailable in this Linux root environment")
            raise
        self._assert_code("environment_file_not_regular")

    def test_oversize_file_fails(self) -> None:
        self._write_regular(
            self.environment_file,
            b"MEDCHAT_TASK_BACKEND=" + b"x" * self.helper.MAX_ENVIRONMENT_BYTES,
        )
        self._assert_code("environment_file_too_large")

    def test_mutation_during_read_fails_identity_check(self) -> None:
        original_read = self.helper.os.read
        mutated = False

        def mutating_read(descriptor: int, amount: int) -> bytes:
            nonlocal mutated
            chunk = original_read(descriptor, amount)
            if chunk and not mutated:
                mutated = True
                with open(self.environment_file, "ab", buffering=0) as stream:
                    stream.write(b"# changed\n")
            return chunk

        with mock.patch.object(self.helper.os, "read", side_effect=mutating_read):
            self._assert_code("environment_file_changed")

    def test_replacement_race_never_returns_unapproved_keys(self) -> None:
        attacker_file = self.secure_parent / "attacker.env"
        self._write_regular(attacker_file, b"UNDECLARED=payload\n")
        stop = threading.Event()

        def replace_loop() -> None:
            regular = self.secure_parent / "regular.next"
            symlink = self.secure_parent / "symlink.next"
            while not stop.is_set():
                try:
                    self._write_regular(regular, VALID_PAYLOAD)
                    os.replace(regular, self.environment_file)
                    os.symlink(attacker_file, symlink)
                    os.replace(symlink, self.environment_file)
                except FileExistsError:
                    continue
                except FileNotFoundError:
                    continue
            for leftover in (regular, symlink):
                try:
                    os.unlink(leftover)
                except FileNotFoundError:
                    pass

        racer = threading.Thread(target=replace_loop, daemon=True)
        racer.start()
        try:
            for _ in range(250):
                try:
                    result = self.helper.validate_environment_file(self.environment_file)
                except self.helper.TrustBoundaryError:
                    continue
                self.assertLessEqual(result.keys(), self.helper.ALLOWED_KEYS)
                self.assertNotIn("UNDECLARED", result)
        finally:
            stop.set()
            racer.join(timeout=5)
        self.assertFalse(racer.is_alive())

    def test_cleanup_unlinks_symlink_instead_of_following_it(self) -> None:
        outside = Path(f"/root/medchat-temporal-trust-outside-{os.getpid()}-{time.time_ns()}")
        outside.mkdir(mode=0o700)
        marker = outside / "marker"
        marker.write_text("keep", encoding="utf-8")
        os.symlink(outside, self.fixture_root / "cleanup-link")
        _remove_tree_no_follow(self.fixture_root)
        self.assertTrue(marker.is_file())
        marker.unlink()
        outside.rmdir()


if __name__ == "__main__":
    unittest.main()
