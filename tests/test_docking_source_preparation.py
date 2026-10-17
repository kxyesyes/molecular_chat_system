"""Source-worker lifecycle/transport contracts, not real scientific acceptance."""

import asyncio
import importlib
import importlib.util
import math
import os
import threading
import time

import pytest

from src.docking import chat_preparation as lifecycle
from tests.test_rcsb_source import sample


def api():
    name = "src.docking.source_preparation"
    assert importlib.util.find_spec(name) is not None, "retained source worker is missing"
    return importlib.import_module(name)


def _echo_child(pdb_id, accession, taxonomy, deadline):
    # Synthetic lifecycle probe; it makes no network or scientific claim.
    return {"ok": True, "identity": (pdb_id, accession, taxonomy),
            "remaining": deadline - time.monotonic(),
            "credential_present": "SOURCE_WORKER_TEST_SECRET" in os.environ}


def _sleeping_child(*args):
    time.sleep(30)
    return {"ok": True}


def _fixture_pipeline_child(pdb_id, accession, taxonomy, deadline):
    # Only provider transport is synthetic. Actual CIF/CCD comparison executes
    # inside the real isolated child. This is not RCSB authentication evidence.
    from src.docking.source_preparation import _source_candidate
    from src.target_search import downloader
    pdb, cif, components = sample.__wrapped__()
    downloader.fetch_rcsb_source_pair = lambda selected, **kw: {
        "pdb_bytes": pdb, "cif_bytes": cif, "pdb_id": selected}
    downloader.fetch_rcsb_component_files = lambda ids, **kw: components
    return _source_candidate(pdb_id, accession, taxonomy, deadline)


def arguments(**overrides):
    return {"pdb_id": "1ABC", "target": {"uniprot_id": "P12345", "taxonomy_id": 9606},
            "deadline": time.monotonic() + 20, "owner": lifecycle.PreparationOwner(), **overrides}


def test_source_worker_callable_and_offline_map_remains_closed():
    assert callable(api().run_source_preparation)
    assert "source" not in lifecycle._INPUTS
    assert "source_prepare" not in lifecycle._INPUTS


@pytest.mark.parametrize("change", [
    {"pdb_id": "1abc"}, {"pdb_id": "https://files.rcsb.org/1ABC"},
    {"pdb_id": b"1ABC"}, {"pdb_id": "../x"},
    {"target": {"uniprot_id": "P12345", "taxonomy_id": True}},
    {"target": {"uniprot_id": "P12345", "taxonomy_id": 0}},
    {"target": {"uniprot_id": "P12345", "taxonomy_id": 2**31}},
    {"target": {"uniprot_id": "bad", "taxonomy_id": 9606}},
    {"target": {"uniprot_id": "P12345", "taxonomy_id": 9606, "url": "x"}},
    {"target": {}}, {"target": None}, {"deadline": True},
    {"deadline": math.inf}, {"deadline": math.nan}, {"deadline": "1"}, {"owner": None},
])
def test_invalid_envelope_never_constructs_child(monkeypatch, change):
    module = api()
    monkeypatch.setattr(lifecycle, "IsolatedProcess", lambda *a, **k: pytest.fail("constructed"))
    result = asyncio.run(module.run_source_preparation(**arguments(**change)))
    assert result["code"] == "PREPARATION_INPUT_INVALID"


@pytest.mark.parametrize("entry", ["source", "offline"])
def test_large_native_integer_deadline_is_fixed_rejection(monkeypatch, entry):
    module = api()
    monkeypatch.setattr(lifecycle, "IsolatedProcess", lambda *a, **k: pytest.fail("constructed"))
    supplied = arguments(deadline=10**400)
    if entry == "source":
        coroutine = module.run_source_preparation(**supplied)
    else:
        coroutine = lifecycle.run_preparation("ligand", inputs={"smiles": "CC"},
            deadline=supplied["deadline"], owner=supplied["owner"])
    assert asyncio.run(coroutine)["code"] == "PREPARATION_INPUT_INVALID"


def test_native_target_is_detached_before_background_handoff(monkeypatch):
    module = api()
    original = lifecycle.IsolatedProcess
    target = {"uniprot_id": "P12345", "taxonomy_id": 9606}

    class ChangedTargetProcess(original):
        def __init__(self, producer, *, args):
            target.clear()
            target.update(uniprot_id="Q99999", taxonomy_id=10090)
            super().__init__(producer, args=args)

    monkeypatch.setattr(lifecycle, "IsolatedProcess", ChangedTargetProcess)
    monkeypatch.setattr(module, "_source_candidate", _echo_child)
    monkeypatch.setenv("SOURCE_WORKER_TEST_SECRET", "synthetic-not-a-credential")
    supplied = arguments(target=target)
    result = asyncio.run(module.run_source_preparation(**supplied))
    assert result["ok"] and result["identity"] == ("1ABC", "P12345", 9606)
    assert result["credential_present"] is False
    assert supplied["owner"].snapshot()["settled"]
    assert supplied["owner"].process._parent.closed
    assert supplied["owner"].process._child.closed


def test_expired_root_never_constructs_child(monkeypatch):
    module = api()
    monkeypatch.setattr(lifecycle, "IsolatedProcess", lambda *a, **k: pytest.fail("constructed"))
    supplied = arguments(deadline=time.monotonic() - 1)
    result = asyncio.run(module.run_source_preparation(**supplied))
    assert result["code"] == "PREPARATION_TIMEOUT"
    assert supplied["owner"].snapshot()["settled"]


def test_one_owner_cannot_restart_or_switch_offline_operation(monkeypatch):
    module = api()
    monkeypatch.setattr(module, "_source_candidate", _echo_child)

    async def run():
        supplied = arguments()
        first = await module.run_source_preparation(**supplied)
        assert first["ok"]
        assert (await module.run_source_preparation(**supplied))["code"] == "PREPARATION_INPUT_INVALID"
        assert (await lifecycle.run_preparation("ligand", inputs={"smiles": "CC"},
                deadline=supplied["deadline"], owner=supplied["owner"]))["code"] == "PREPARATION_INPUT_INVALID"

    asyncio.run(run())


@pytest.mark.parametrize("cancel", [False, True])
def test_source_worker_owns_real_blocked_process_until_stopped(monkeypatch, cancel):
    module = api()
    original = lifecycle.IsolatedProcess

    async def run():
        loop, entered = asyncio.get_running_loop(), asyncio.Event()

        class ObservedProcess(original):
            def wait(self, timeout=None):
                loop.call_soon_threadsafe(entered.set)
                return super().wait(timeout=timeout)

        monkeypatch.setattr(lifecycle, "IsolatedProcess", ObservedProcess)
        monkeypatch.setattr(module, "_source_candidate", _sleeping_child)
        supplied = arguments(deadline=time.monotonic() + (10 if cancel else 2))
        owner = supplied["owner"]
        task = asyncio.create_task(module.run_source_preparation(**supplied))
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            if cancel:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                assert (await task)["code"] == "PREPARATION_TIMEOUT"
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            for _ in range(500):
                if owner.snapshot()["settled"]:
                    break
                await asyncio.sleep(0.01)
        assert owner.snapshot()["settled"]
        assert owner.process.pid is not None and not owner.process.is_alive()

    asyncio.run(run())


def test_source_child_receives_same_capped_absolute_deadline(monkeypatch):
    module = api()
    monkeypatch.setattr(module, "_source_candidate", _echo_child)
    supplied = arguments(deadline=time.monotonic() + 180)
    result = asyncio.run(module.run_source_preparation(**supplied))
    assert result["ok"] and 0 < result["remaining"] < 60


def test_actual_comparison_pipeline_runs_inside_owned_child(monkeypatch, sample):
    from src.docking import rcsb_source
    module = api()
    monkeypatch.setattr(module, "_source_candidate", _fixture_pipeline_child)
    monkeypatch.setattr(rcsb_source, "_compile", lambda *a, **k: pytest.fail("parent compiled"))
    supplied = arguments(target={"uniprot_id": "P06276", "taxonomy_id": 9606})
    result = asyncio.run(module.run_source_preparation(**supplied))
    assert result["ok"] and result["pdb_bytes"] == sample[0]
    assert result["input_ready"] is False and result["execution_authorized"] is False
    assert result["source_authentication"] == "requires_live_owner_binding"
    assert supplied["owner"].snapshot()["settled"]


def test_unsafe_native_subclasses_never_run_hooks(monkeypatch):
    module = api()
    calls = []

    class Text(str):
        def __eq__(self, other):
            calls.append("eq")
            return super().__eq__(other)
        __hash__ = str.__hash__

    class Mapping(dict):
        def items(self):
            calls.append("items")
            return super().items()

    monkeypatch.setattr(lifecycle, "IsolatedProcess", lambda *a, **k: pytest.fail("constructed"))
    for change in ({"pdb_id": Text("1ABC")},
                   {"target": Mapping(uniprot_id="P12345", taxonomy_id=9606)},
                   {"target": {Text("uniprot_id"): "P12345", "taxonomy_id": 9606}},
                   {"target": {"uniprot_id": Text("P12345"), "taxonomy_id": 9606}}):
        assert asyncio.run(module.run_source_preparation(**arguments(**change)))["code"] == "PREPARATION_INPUT_INVALID"
    assert calls == []


def mock_downloads(monkeypatch, sample, *, fail=None):
    from src.target_search import downloader
    calls = []

    def pair(pdb_id, *, deadline):
        calls.append(("pair", pdb_id, deadline))
        if fail == "pair":
            raise downloader.StructureDownloadError("synthetic-private-diagnostic")
        return {"pdb_id": pdb_id, "pdb_bytes": sample[0], "cif_bytes": sample[1],
                "pdb_sha256": "not-used-as-authentication", "cif_sha256": "not-used-as-authentication"}

    def components(ids, *, deadline):
        calls.append(("components", ids, deadline))
        if fail == "components":
            raise downloader.StructureDownloadError("synthetic-private-diagnostic")
        return dict(sample[2])

    monkeypatch.setattr(downloader, "fetch_rcsb_source_pair", pair)
    monkeypatch.setattr(downloader, "fetch_rcsb_component_files", components)
    return calls


def test_real_comparison_of_downloaded_synthetic_bytes_not_authenticated(monkeypatch, sample):
    module = api()
    calls = mock_downloads(monkeypatch, sample)
    deadline = time.monotonic() + 10
    result = module._source_candidate("1ABC", "P06276", 9606, deadline)
    assert result["ok"], result
    assert result["pdb_bytes"] == sample[0] and result["cif_bytes"] == sample[1]
    assert result["component_files"] == sample[2]
    assert result["source"]["target"] == {"uniprot_id": "P06276", "taxonomy_id": 9606}
    assert result["input_ready"] is False
    assert result["source_authentication"] == "requires_live_owner_binding"
    assert result["execution_authorized"] is False
    assert calls == [("pair", "1ABC", deadline), ("components", ("ALA", "HOH", "LIG"), deadline)]


@pytest.mark.parametrize("failure", ["pair", "components"])
def test_transport_errors_are_fixed_and_publish_no_partial_bytes(monkeypatch, sample, failure):
    module = api()
    calls = mock_downloads(monkeypatch, sample, fail=failure)
    result = module._source_candidate("1ABC", "P06276", 9606, time.monotonic() + 10)
    assert result == {"ok": False, "code": "SOURCE_PREPARATION_FAILED",
                      "message": "Official source preparation failed."}
    assert len(calls) == (1 if failure == "pair" else 2)


@pytest.mark.parametrize("stage", ["before", "pair", "discovery", "components", "compile"])
def test_one_absolute_deadline_checked_at_every_child_stage(monkeypatch, sample, stage):
    from src.docking import rcsb_source
    from src.target_search import downloader
    module = api()
    calls = mock_downloads(monkeypatch, sample)
    now = [100.0 if stage != "before" else 110.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    seams = {"pair": (downloader, "fetch_rcsb_source_pair"),
             "discovery": (rcsb_source, "_discover_components"),
             "components": (downloader, "fetch_rcsb_component_files"),
             "compile": (rcsb_source, "_compile")}
    if stage != "before":
        host, name = seams[stage]
        original = getattr(host, name)
        def expired(*args, **kwargs):
            result = original(*args, **kwargs)
            now[0] = 110.0
            return result
        monkeypatch.setattr(host, name, expired)
    result = module._source_candidate("1ABC", "P06276", 9606, 105.0)
    assert result["code"] == "PREPARATION_TIMEOUT"
    assert "pdb_bytes" not in result
    assert len(calls) == (0 if stage == "before" else 1 if stage in {"pair", "discovery"} else 2)


def test_shared_logical_ledger_discovery_compile_and_raw_ipc(monkeypatch, sample):
    from src.docking import rcsb_source, receptor_projection_pdb
    module = api()
    mock_downloads(monkeypatch, sample)
    seen = []
    discover, compile_source = rcsb_source._discover_components, rcsb_source._compile

    def discovery(*args, **kwargs):
        budget = kwargs["budget"]
        seen.append(("discovery", budget, budget.used))
        result = discover(*args, **kwargs)
        seen.append(("after_discovery", budget, budget.used))
        return result

    def compile(*args, **kwargs):
        budget = kwargs["budget"]
        seen.append(("compile", budget, budget.used))
        result = compile_source(*args, **kwargs)
        seen.append(("after_compile", budget, budget.used))
        return result

    monkeypatch.setattr(rcsb_source, "_discover_components", discovery)
    monkeypatch.setattr(rcsb_source, "_compile", compile)
    assert module._source_candidate("1ABC", "P06276", 9606, time.monotonic() + 10)["ok"]
    assert all(type(row[1]) is receptor_projection_pdb.AllocationBudget for row in seen)
    assert len({id(row[1]) for row in seen}) == 1
    assert 0 < seen[0][2] < seen[1][2] <= seen[2][2] < seen[3][2] < seen[3][1].used <= 512 * 1024**2


def test_shared_budget_exhaustion_prevents_final_compilation(monkeypatch, sample):
    from src.docking import rcsb_source
    module = api()
    calls = mock_downloads(monkeypatch, sample)
    original = rcsb_source._discover_components

    def exhausted(*args, **kwargs):
        result = original(*args, **kwargs)
        kwargs["budget"].reserve(512 * 1024**2)
        return result

    monkeypatch.setattr(rcsb_source, "_discover_components", exhausted)
    result = module._source_candidate("1ABC", "P06276", 9606, time.monotonic() + 10)
    assert result == {"ok": False, "code": "RCSB_SOURCE_INVALID", "reason": "resource_limit"}
    assert len(calls) == 1


@pytest.mark.parametrize("change", ["revision", "atom", "target", "component"])
def test_download_success_does_not_bypass_independent_compilation(monkeypatch, sample, change):
    pdb, cif, components = sample
    accession = "P06276"
    if change == "revision":
        cif = cif.replace(b"2024-01-01", b"2024-01-02")
    elif change == "atom":
        cif = cif.replace(b"1.000 2.000 3.000", b"1.001 2.000 3.000")
    elif change == "target":
        accession = "P12345"
    else:
        components = {key: value.replace(b"LIG C1 O1 SING N N", b"LIG C1 O1 DOUB N N")
                      for key, value in components.items()}
    mock_downloads(monkeypatch, (pdb, cif, components))
    result = api()._source_candidate("1ABC", accession, 9606, time.monotonic() + 10)
    assert result["ok"] is False and result["code"] == "RCSB_SOURCE_INVALID"
    assert set(result) == {"ok", "code", "reason"}


@pytest.mark.parametrize("malformed", ["cif_quote", "revision_date"])
def test_ordinary_parser_errors_keep_safe_compiler_reason(monkeypatch, sample, malformed):
    from src.docking import rcsb_source
    pdb, cif, components = sample
    cif = (b"data_1ABC\n_entry.id 'unterminated\n" if malformed == "cif_quote"
           else cif.replace(b"2024-01-01", b"2024-99-99"))
    expected = rcsb_source.compile_rcsb_source(pdb, cif, components, pdb_id="1ABC",
        target={"uniprot_id": "P06276", "taxonomy_id": 9606})
    assert expected == {"ok": False, "code": "RCSB_SOURCE_INVALID", "reason": "cif_invalid"}
    mock_downloads(monkeypatch, (pdb, cif, components))
    assert api()._source_candidate("1ABC", "P06276", 9606, time.monotonic() + 10) == expected
