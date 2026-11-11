"""Contracts for the portable offline B1 campaign."""
import json
from pathlib import Path
from subprocess import CompletedProcess
from types import SimpleNamespace

import pytest

from scripts import run_b1_acceptance as campaign


def test_all_historical_rows_have_existing_current_targets():
    assert len(campaign.ROWS) == 57
    for targets in campaign.ROWS:
        assert targets
        for node in targets:
            path = Path(node.split("::", 1)[0])
            assert path.parts[0] == "tests" and ".." not in path.parts
            assert (campaign.ROOT / path).is_file()


def test_row_subset_does_not_silently_change_the_campaign_order():
    assert campaign.parse_rows("2,1,3-4") == [1, 2, 3, 4]
    for invalid in ("0", "58", "2-1", "../other", "1,1", "1-3,3"):
        with pytest.raises(ValueError):
            campaign.parse_rows(invalid)


def test_collection_or_skips_cannot_claim_execution():
    record = dict(row=1, campaign_sha256="hash", collected=2, passed=0, skipped=0, failures=[], timings_ns={}, pytest_exit=0)
    assert campaign.validate_receipt(record, 1, True, 0, "hash") == "collected"
    assert campaign.validate_receipt(record, 1, False, 0, "hash") == "failed"
    assert campaign.validate_receipt(dict(record, skipped=2), 1, False, 0, "hash") == "failed"
    assert campaign.validate_receipt(dict(record, passed=1, skipped=1), 1, False, 0, "hash") == "failed"
    assert campaign.validate_receipt(dict(record, passed=2), 1, False, 0, "hash") == "passed"
    with pytest.raises(ValueError):
        campaign.validate_receipt(record, 2, False, 0, "hash")
    with pytest.raises(ValueError):
        campaign.validate_receipt(record, 1, False, 1, "hash")
    with pytest.raises(campaign.CampaignChanged):
        campaign.validate_receipt(record, 1, True, 0, "different")
    results = campaign.RowResults()
    results.pytest_deselected([object()])
    assert campaign.validate_receipt(dict(record, failures=results.failures, passed=2), 1, False, 0, "hash") == "failed"
    results = campaign.RowResults()
    results.pytest_collectreport(SimpleNamespace(skipped=True, failed=False, nodeid="tests/missing.py"))
    assert results.failures == [["tests/missing.py", "collection"]]
    assert campaign.validate_receipt(dict(record, failures=results.failures), 1, True, 0, "hash") == "failed"


def test_row_seals_owner_before_releasing_temporary_directory(tmp_path, monkeypatch):
    commands = campaign.command_owner_module()
    monkeypatch.setattr(campaign, "command_owner_module", lambda: commands)

    monkeypatch.setattr(campaign.tempfile, "tempdir", str(tmp_path))

    def report(self, args, *, cwd, timeout):
        receipt = {"row": 1, "campaign_sha256": "hash", "collected": 1, "passed": 1, "skipped": 0, "failures": [],
                   "pytest_exit": 0, "timings_ns": {}}
        (Path(cwd) / "receipt.json").write_text(json.dumps(receipt), encoding="utf8")
        return CompletedProcess(args, 0)

    monkeypatch.setattr(commands.CommandAdapter, "run", report)
    assert campaign.run_row(1, False, "hash")["status"] == "passed"
    assert list(tmp_path.iterdir()) == []


def test_uncertain_owner_retains_its_directory_and_stops_the_campaign(tmp_path, monkeypatch):
    commands = campaign.command_owner_module()
    monkeypatch.setattr(campaign, "command_owner_module", lambda: commands)

    events = []
    monkeypatch.setattr(campaign.tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(campaign.sys, "argv", ["run_b1_acceptance.py", "--rows", "1-2"])
    def fail(*args, **kwargs):
        events.append("run")
        raise RuntimeError("child failed")
    def unresolved(scope):
        events.append("settle")
        return False
    monkeypatch.setattr(commands.CommandAdapter, "run", fail)
    monkeypatch.setattr(commands.CommandOwnershipScope, "seal", lambda scope: events.append("seal"))
    monkeypatch.setattr(commands.CommandOwnershipScope, "settle", unresolved)
    assert campaign.main() == 1
    assert events == ["run", "seal", "settle"]
    assert len(list(tmp_path.iterdir())) == 1
