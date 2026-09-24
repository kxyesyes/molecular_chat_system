"""Regressions for the target tests' own fixture and subprocess boundaries."""

import json
import os
import unittest
from unittest.mock import patch

import pytest

# Import modules, not TestCase aliases: pytest must not collect their tests twice.
from tests import test_target_db_validation as validation_tests
from tests import test_target_search as search_tests


CASE_TYPES = (
    search_tests.TargetSearchDemoTest,
    validation_tests.TargetDatabaseValidationTest,
)
TARGET_KEYS = ("TARGET_DB_PATH", "TARGET_CACHE_DIR")


@pytest.mark.parametrize("case_type", CASE_TYPES)
def test_cases_use_distinct_roots_and_restore_hostile_paths(case_type, tmp_path, monkeypatch):
    from src.target_search.database import get_cache_dir, get_connection, get_db_path
    from src.target_search.seed import seed_database

    outer = {
        "TARGET_DB_PATH": str(tmp_path / "outside.sqlite"),
        "TARGET_CACHE_DIR": str(tmp_path / "outside-cache"),
    }
    for key, value in outer.items():
        monkeypatch.setenv(key, value)
    roots = []
    for index in range(2):
        case = case_type(methodName="runTest")
        try:
            try:
                case.setUp()
                roots.append(case.root)
                assert get_db_path(case.root) == case.root / "data/target_db/target_database.sqlite"
                assert get_cache_dir(case.root) == case.root / "data/target_db/cache"
                seed_database(project_root=case.root)
                conn = get_connection(case.root)
                try:
                    assert conn.execute("SELECT COUNT(*) AS n FROM targets").fetchone()["n"] == 15
                    assert conn.execute(
                        "SELECT id FROM targets WHERE gene_symbol = 'ISOLATION_SENTINEL'"
                    ).fetchone() is None
                    if index == 0:
                        conn.execute("INSERT INTO targets (gene_symbol) VALUES ('ISOLATION_SENTINEL')")
                        conn.commit()
                finally:
                    conn.close()
                cached = get_cache_dir(case.root) / "isolation-sentinel.cif"
                assert not cached.exists()
                cached.parent.mkdir(parents=True, exist_ok=True)
                cached.write_text("data_isolation\n", encoding="utf-8")
            finally:
                case.doCleanups()
            assert {key: os.environ.get(key) for key in outer} == outer
            assert not case.root.exists()
        finally:
            # Also clean up on RED against the old, tearDown-only implementation.
            if hasattr(case, "temp_dir"):
                case.temp_dir.cleanup()
    assert roots[0] != roots[1]
    assert not (tmp_path / "outside.sqlite").exists()
    assert not (tmp_path / "outside-cache").exists()


@pytest.mark.parametrize("case_type", CASE_TYPES)
@pytest.mark.parametrize("initially_present", [True, False])
def test_unittest_failure_restores_only_target_overrides(
    case_type, initially_present, tmp_path, monkeypatch
):
    outer = dict(zip(TARGET_KEYS, (str(tmp_path / "outer.sqlite"), str(tmp_path / "outer-cache"))))
    for key, value in outer.items():
        if initially_present:
            monkeypatch.setenv(key, value)
        else:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TARGET_ISOLATION_UNRELATED", "before")
    observed = {}

    class FailingCase(case_type):
        def runTest(self):
            observed.update({key: os.environ.get(key) for key in TARGET_KEYS})
            observed["unrelated"] = os.environ["TARGET_ISOLATION_UNRELATED"]
            monkeypatch.setenv("TARGET_ISOLATION_UNRELATED", "changed-during-test")
            self.fail("synthetic assertion failure")

    case = FailingCase(methodName="runTest")
    result = unittest.TestResult()
    try:
        case.run(result)
        assert result.testsRun == 1
        assert not result.errors
        assert len(result.failures) == 1
        assert "synthetic assertion failure" in result.failures[0][1]
        assert observed == {**dict.fromkeys(TARGET_KEYS), "unrelated": "before"}
        assert {key: os.environ.get(key) for key in TARGET_KEYS} == (
            outer if initially_present else dict.fromkeys(TARGET_KEYS)
        )
        assert os.environ["TARGET_ISOLATION_UNRELATED"] == "changed-during-test"
        assert not case.root.exists()
    finally:
        case.doCleanups()
        if hasattr(case, "temp_dir"):
            case.temp_dir.cleanup()


def test_real_validation_cli_uses_utf8_for_chinese_json(monkeypatch):
    monkeypatch.setenv("PYTHONIOENCODING", "ascii")
    case = validation_tests.TargetDatabaseValidationTest(
        methodName="test_validation_cli_outputs_json_and_strict_fails_on_missing_pde"
    )
    real_run = validation_tests.subprocess.run
    original_seed = case._write_minimal_pde_seed
    chinese_path = "data/target_db/cache/rcsb/PDE5A/中文结构.cif"

    def write_chinese_fixture():
        original_seed()
        structures = case.root / "data/target_db/pde_structures.csv"
        structures.write_text(
            structures.read_text(encoding="utf-8").replace(
                "data/target_db/cache/rcsb/PDE5A/1T9R.cif", chinese_path
            ),
            encoding="utf-8",
        )

    def checked_run(command, **kwargs):
        assert kwargs.get("encoding") == "utf-8"
        assert kwargs["env"]["PYTHONIOENCODING"] == "utf-8"
        completed = real_run(command, **kwargs)
        assert completed.returncode == 1
        assert "中文结构.cif" in completed.stdout
        report = json.loads(completed.stdout)
        assert report["pde_coverage"]["missing_expected_genes"] == ["PDE6D"]
        assert chinese_path in [item["local_file_path"] for item in report["cache"]["missing_files"]]
        assert "PDE 预期靶点缺失：PDE6D" in report["warnings"]
        return completed

    try:
        case.setUp()
        with patch.object(case, "_write_minimal_pde_seed", side_effect=write_chinese_fixture), patch.object(
            validation_tests.subprocess, "run", side_effect=checked_run
        ) as run:
            case.test_validation_cli_outputs_json_and_strict_fails_on_missing_pde()
        run.assert_called_once()
        assert os.environ["PYTHONIOENCODING"] == "ascii"
    finally:
        case.doCleanups()


@pytest.mark.parametrize("method_name", [
    "test_local_download_returns_cached_file_without_network",
    "test_send_to_docking_uses_readable_chinese_message",
])
def test_cache_hit_guard_rejects_missing_cache_before_transport(method_name):
    case = search_tests.TargetSearchDemoTest(methodName=method_name)
    try:
        case.setUp()
        # Exercise the real cache-hit test with a misplaced file. The lower guard
        # keeps RED offline; GREEN must fail at the test's requests.get guard.
        with patch(
            "src.target_search.downloader.StructureDownloader._absolute_cache_path",
            return_value=case.root / "missing-cache" / "missing.cif",
        ), patch(
            "requests.adapters.HTTPAdapter.send",
            side_effect=AssertionError("unguarded HTTP reached transport"),
        ) as transport:
            with pytest.raises(AssertionError, match="cache hit must not access HTTP"):
                getattr(case, method_name)()
            transport.assert_not_called()
    finally:
        case.doCleanups()
