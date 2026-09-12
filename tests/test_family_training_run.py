"""Frozen CLI runner contracts. Synthetic data/stubs, never GPU training."""
import builtins
import importlib.util
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest


def module():
    path = Path(__file__).parents[1] / "scripts/train_family_activity_models.py"
    assert path.exists(), "Frozen family training runner missing"
    spec = importlib.util.spec_from_file_location("family_training_run", path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_import_has_no_training_configuration_or_filesystem_effects(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ACTIVITY_MODEL_DIR", "synthetic-original")
    paths = [p for p in sys.path if p != str(Path(__file__).parents[1])]
    monkeypatch.setattr(sys, "path", paths[:])
    original_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        assert not name.startswith(("src.", "torch", "rdkit")), "Eager scientific import"
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    disabled = logging.root.manager.disable
    runner = module()
    assert callable(runner.run)
    assert sys.path == paths
    assert os.environ["ACTIVITY_MODEL_DIR"] == "synthetic-original"
    assert logging.root.manager.disable == disabled
    assert list(tmp_path.iterdir()) == []


def test_frozen_training_configuration():
    runner = module()
    assert runner.OPTIONS == dict(epochs=50, patience=12, batch_size=32, lr=.001,
        num_layers=5, hidden_size=256, dropout=.2, weight_decay=.0001,
        lr_scheduler="Cosine", loss_metric="MSE", random_seed=42)
    assert runner.FAMILIES == ("pde-family", "buche-family")
    assert runner.TASKS == ("classification", "regression")


def test_baselines_use_only_training_values():
    runner = module()
    r = runner.baseline_metrics("regression", [2., 4.], [1., 5.])
    assert r == dict(rmse=2., mae=2., r2=0.)
    # The held-out mean is deliberately different from the training mean.
    assert runner.baseline_metrics("regression", [2., 4.], [8., 10.])["mae"] == 6.
    c = runner.baseline_metrics("classification", [0., 1., 1.], [0., 0., 1., 1.])
    assert c == dict(roc_auc=.5, pr_auc=.5, balanced_accuracy=.5,
                     confusion_matrix=dict(tn=0, fp=2, fn=0, tp=2))
    c = runner.baseline_metrics("classification", [0., 0., 1.], [0., 1., 1., 1.])
    assert c["confusion_matrix"] == dict(tn=1, fp=0, fn=3, tp=0)


@pytest.mark.parametrize("task,values", [
    ("regression", []), ("regression", [float("nan")]),
    ("classification", [1., 1.]), ("classification", [0., 2.]),
    ("unknown", [1., 2.]),
])
def test_baselines_reject_invalid_training_values(task, values):
    with pytest.raises(ValueError):
        module().baseline_metrics(task, values, [0., 1.])


@pytest.mark.parametrize("states,expected", [
    ([], "failed"), (["completed"] * 4, "completed"),
    (["failed"] * 4, "failed"), (["completed", "failed"], "partial"),
])
def test_run_status_never_hides_failed_jobs(states, expected):
    assert module().aggregate_status(states) == expected


def test_poor_metrics_are_not_scientific_success():
    assess = module().assess_quality
    assert assess("regression", dict(rmse=3., mae=2., r2=-1.),
                  dict(rmse=2., mae=1., r2=0.))["status"] == "does_not_improve_baseline"
    assert assess("classification", dict(roc_auc=.8, pr_auc=.8, balanced_accuracy=.5),
                  dict(roc_auc=.5, pr_auc=.6, balanced_accuracy=.5))["status"] == "mixed"
    assert assess("regression", {}, {})["status"] == "unavailable"


@pytest.fixture
def rig(tmp_path, monkeypatch):
    import pandas as pd
    import torch
    # Import consumers before patching their dependencies: they bind these
    # callables at import time, and must not retain a stub after fixture teardown.
    from src.activity import family_dataset, model_card, model_registry, trainer
    from tests.family_model_test_support import make_pair
    runner = module()
    real_load = family_dataset.load_family_dataset
    real_prepared = model_card.load_prepared_training_data
    real_registry = model_registry.ActivityModelRegistry
    monkeypatch.setenv("ACTIVITY_MODEL_DIR", "synthetic-original")
    # Keep the import stack real; only hardware probes and training are stubbed.
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "get_device_name", lambda _i: "synthetic-device")
    monkeypatch.setattr(torch.cuda, "empty_cache", lambda: None)
    calls, bundles, verified = [], [], []

    def load(path):
        family = path.parent.name.removesuffix("-v1")
        verified.append(family)
        return {"family_id": family, "datasets": {t: {"path": t + ".json"} for t in runner.TASKS}}

    monkeypatch.setattr(family_dataset, "load_family_dataset", load)
    monkeypatch.setattr(model_card, "load_prepared_training_data", lambda _p: SimpleNamespace(
        frames={k: pd.DataFrame({"normalized_value": [0., 1.]}) for k in ("train", "test")}))

    class Registry:
        def __init__(self, path):
            assert path.parent == tmp_path / "data/activity/models"
            assert os.environ["ACTIVITY_MODEL_DIR"] == str(path)

        def register_family_bundle(self, **kwargs):
            bundles.append(kwargs)
            return kwargs

        def select_family_bundle(self, *_args):
            pytest.fail("Must not activate family bundles")

        def select(self, *_args):
            pytest.fail("Must not activate individual models")

    monkeypatch.setattr(model_registry, "ActivityModelRegistry", Registry)

    def train(path, job_id, progress):
        assert verified == list(runner.FAMILIES), "All packages must pass before any training"
        calls.append(job_id)
        progress(2, 1, {"val_loss": 1.}, best_epoch=1)
        return dict(state="completed", model_id=job_id, best_epoch=1,
                    epochs_completed=1, best_metrics={}, test_metrics={})

    monkeypatch.setattr(runner, "train_job", train)
    return SimpleNamespace(runner=runner, torch=torch, calls=calls, bundles=bundles,
                           verified=verified, train=train, load=load, Registry=Registry,
                           real_load=real_load, real_prepared=real_prepared, real_registry=real_registry,
                           make_pair=make_pair)


def report_file(root, run_id="synthetic"):
    return root / "outputs/activity_training" / run_id / "report.json"


@pytest.mark.parametrize("failure", [None, "job", "bundle"])
def test_serial_run_progress_partial_status_and_no_activation(rig, tmp_path, monkeypatch, failure):
    runner = rig.runner
    snapshots = []
    write = runner.write_report

    def record(path, data):
        write(path, data)
        snapshots.append(json.loads(path.read_text(encoding="utf-8")))

    monkeypatch.setattr(runner, "write_report", record)

    def train(path, job_id, progress):
        status = rig.train(path, job_id, progress)
        if failure == "job" and len(rig.calls) == 2:
            raise RuntimeError("private-fixture-must-not-be-printed")
        return status

    monkeypatch.setattr(runner, "train_job", train)
    register = rig.Registry.register_family_bundle

    def bundle(self, **kwargs):
        if failure == "bundle" and kwargs["bundle_id"].endswith("pde-family"):
            raise RuntimeError("private-registration-text")
        return register(self, **kwargs)

    monkeypatch.setattr(rig.Registry, "register_family_bundle", bundle)
    disabled = logging.root.manager.disable
    report = runner.run("synthetic", root=tmp_path)
    assert rig.calls == [f"synthetic-{family}-{task}" for family in runner.FAMILIES for task in runner.TASKS]
    assert report["status"] == ("completed" if failure is None else "partial")
    assert len(rig.bundles) == (2 if failure is None else 1)
    assert report["activated"] is False
    if failure == "bundle":
        assert report["bundle_errors"] == [dict(family="pde-family", error_type="RuntimeError")]
    assert all(sum(j["status"] == "running" for j in s["jobs"]) <= 1 for s in snapshots)
    assert [s["jobs"][-1]["family"] + "-" + s["jobs"][-1]["task"] for s in snapshots
            if s["jobs"] and s["jobs"][-1].get("epoch") == 1
            and s["jobs"][-1]["status"] == "running"] == [
                f"{family}-{task}" for family in runner.FAMILIES for task in runner.TASKS]
    assert json.loads(report_file(tmp_path).read_text(encoding="utf-8")) == report
    assert "private-" not in json.dumps(report)
    assert os.environ["ACTIVITY_MODEL_DIR"] == "synthetic-original"
    assert logging.root.manager.disable == disabled
    with pytest.raises(FileExistsError):
        runner.run("synthetic", root=tmp_path)
    assert len(rig.calls) == 4


@pytest.mark.parametrize("run_id", ["", "../escape", "x/y", "x\\y", "a" * 65, "x.", "x ",
                                   "NUL", "con", "COM1", "LPT9", None, 42])
def test_invalid_run_id_has_no_writes_or_train_calls(rig, tmp_path, run_id):
    with pytest.raises(ValueError):
        rig.runner.run(run_id, root=tmp_path)
    assert rig.calls == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("directory", ["outputs/activity_training", "data/activity/models"])
def test_reused_run_id_in_either_namespace_is_rejected(rig, tmp_path, directory):
    (tmp_path / directory / "synthetic").mkdir(parents=True)
    before = sorted(str(p) for p in tmp_path.rglob("*"))
    with pytest.raises(FileExistsError):
        rig.runner.run("synthetic", root=tmp_path)
    assert sorted(str(p) for p in tmp_path.rglob("*")) == before
    assert rig.calls == []


@pytest.mark.parametrize("failure", ["cuda", "torch", "graph_dependency", "missing", "corrupt", "family"])
def test_preflight_failure_prevents_every_train_call(rig, tmp_path, monkeypatch, failure, capsys):
    from src.activity import family_dataset
    if failure == "cuda":
        monkeypatch.setattr(rig.torch.cuda, "is_available", lambda: False)
    elif failure in {"torch", "graph_dependency"}:
        name = "torch" if failure == "torch" else "src.activity.rg_mpnn.Nets.ReduceGNN"
        monkeypatch.setitem(sys.modules, name, None)
    else:
        def load(path):
            result = rig.load(path)
            if result["family_id"] == "buche-family":
                if failure == "family":
                    result["family_id"] = "pde-family"
                else:
                    raise (FileNotFoundError if failure == "missing" else ValueError)("PRIVATE_SOURCE_LABELS")
            return result
        monkeypatch.setattr(family_dataset, "load_family_dataset", load)
    disabled = logging.root.manager.disable
    report = rig.runner.run("synthetic", root=tmp_path)
    assert report["status"] == "failed"
    assert report["jobs"] == rig.calls == rig.bundles == []
    assert report["error_type"]
    assert os.environ["ACTIVITY_MODEL_DIR"] == "synthetic-original"
    assert logging.root.manager.disable == disabled
    out = capsys.readouterr()
    assert "PRIVATE_SOURCE_LABELS" not in json.dumps(report) + out.out + out.err


@pytest.mark.parametrize("dependency", [
    "sklearn.metrics", "scipy.special", "torch_geometric.data",
    "torch.optim", "torch.optim.lr_scheduler", "src.activity.prepared_training",
    "src.activity.predictor", "src.activity.trainer",
    "src.activity.rg_mpnn.molecular_network.mol_feature.atom_feature",
    "src.activity.rg_mpnn.molecular_network.mol_feature.bond_feature",
    "src.activity.rg_mpnn.molecular_network.mol_feature.reduceGraph_feature",
    "src.activity.rg_mpnn.molecular_network.util.wash",
])
def test_delayed_dependency_failure_prevents_every_train_call(
    rig, tmp_path, monkeypatch, capsys, dependency,
):
    original_import = builtins.__import__
    blocked = []

    def guarded(name, *args, **kwargs):
        if name == dependency:
            blocked.append(name)
            raise ImportError("SYNTHETIC_PRIVATE_DEPENDENCY")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    report = rig.runner.run("synthetic", root=tmp_path)
    captured = capsys.readouterr()
    assert rig.calls == [], "A missing runtime dependency must fail before any training"
    assert blocked, "The actual dependency must be imported, not just discoverable"
    assert report["status"] == "failed" and report["error_type"] == "ImportError"
    assert report["jobs"] == report["bundles"] == rig.bundles == []
    assert json.loads(report_file(tmp_path).read_text(encoding="utf-8")) == report
    assert "SYNTHETIC_PRIVATE" not in json.dumps(report) + captured.out + captured.err


def test_training_adapter_preserves_main_owner_and_fixed_cuda(rig, tmp_path, monkeypatch):
    from src.activity import prepared_training
    runner = module()  # Unpatched train_job, but no real training.
    calls = []
    callback = lambda *_args, **_kwargs: None

    def train(owner, path, **options):
        assert owner.has_torch is True and owner.device.type == "cuda"
        assert owner.job_id == "synthetic-job" and owner.status == {}
        assert owner._update_progress is callback
        assert path == tmp_path / "manifest.json" and options == runner.OPTIONS
        calls.append(path)
        owner.status.update(state="completed")

    monkeypatch.setattr(prepared_training, "run_prepared_training", train)
    assert runner.train_job(tmp_path / "manifest.json", "synthetic-job", callback) == {"state": "completed"}
    assert len(calls) == 1


@pytest.mark.parametrize("state", ["failed", "running", "missing_id"])
def test_incomplete_trainer_status_cannot_register_bundle(rig, tmp_path, monkeypatch, state):
    def train(*args):
        result = rig.train(*args)
        if state == "missing_id":
            result.pop("model_id")
        else:
            result["state"] = state
        return result
    monkeypatch.setattr(rig.runner, "train_job", train)
    report = rig.runner.run("synthetic", root=tmp_path)
    assert report["status"] == "failed" and rig.bundles == []


def test_reports_and_events_allow_only_aggregate_metrics(rig, tmp_path, monkeypatch, capsys):
    secret = "PRIVATE_MOLECULE_C1CCCCC1_LABEL_8.123456"

    def train(path, job_id, progress):
        # Third-party diagnostics and arbitrary fields must not become report data.
        print(secret)
        print(secret, file=sys.stderr)
        logging.error(secret)
        progress(2, 1, {"val_loss": 1., "smiles": secret, "labels": [8.123456],
                        secret: 8.123456}, best_epoch=1, error=secret)
        if "regression" in job_id:
            raise RuntimeError(secret)
        return dict(state="completed", model_id=job_id, best_epoch=1, epochs_completed=1,
                    best_metrics={"val_loss": 1., "raw_molecule": secret},
                    test_metrics={"roc_auc": .5, "labels": [8.123456]}, error=secret)

    monkeypatch.setattr(rig.runner, "train_job", train)
    report = rig.runner.run("synthetic", root=tmp_path)
    out = capsys.readouterr()
    encoded = json.dumps(report) + report_file(tmp_path).read_text(encoding="utf-8") + out.out + out.err
    assert secret not in encoded and "8.123456" not in encoded and "labels" not in encoded
    assert report["status"] == "partial"
    assert report["jobs"][0]["validation_metrics"] == {"val_loss": 1.}
    assert [json.loads(line)["event"] for line in out.out.splitlines()].count("epoch") == 4


@pytest.mark.parametrize("field,value", [("epoch", "PRIVATE_LABEL"), ("val_loss", "PRIVATE_LABEL"),
                                         ("val_loss", float("nan")), ("model_id", "PRIVATE/CCO")])
def test_malformed_trainer_fields_fail_without_leaking(rig, tmp_path, monkeypatch, capsys, field, value):
    def train(path, job_id, progress):
        progress(2, value if field == "epoch" else 1,
                 {"val_loss": value if field == "val_loss" else 1.}, best_epoch=1)
        return dict(state="completed", model_id=value if field == "model_id" else job_id,
                    best_epoch=1, epochs_completed=1, best_metrics={}, test_metrics={})
    monkeypatch.setattr(rig.runner, "train_job", train)
    report = rig.runner.run("synthetic", root=tmp_path)
    assert report["status"] == "failed"
    captured = capsys.readouterr()
    assert "PRIVATE" not in json.dumps(report) + captured.out + captured.err


@pytest.mark.parametrize("present", [False, True])
@pytest.mark.parametrize("interruption", [KeyboardInterrupt(), SystemExit(0),
                                         SystemExit("SYNTHETIC_PRIVATE_INTERRUPTION")])
def test_process_overrides_restored_after_interruption(rig, tmp_path, monkeypatch, present, interruption):
    if not present:
        monkeypatch.delenv("ACTIVITY_MODEL_DIR")
    old = logging.root.manager.disable
    monkeypatch.setattr(logging.root.manager, "disable", logging.ERROR)

    def interrupt(*_args):
        raise interruption

    monkeypatch.setattr(rig.runner, "train_job", interrupt)
    streams = sys.stdout, sys.stderr
    with pytest.raises(type(interruption)) as raised:
        rig.runner.run("synthetic", root=tmp_path)
    assert raised.value is interruption  # Low-level callers retain interruption semantics.
    assert (sys.stdout, sys.stderr) == streams
    assert not rig.runner._RUN_LOCK.locked()
    assert os.environ.get("ACTIVITY_MODEL_DIR") == ("synthetic-original" if present else None)
    assert logging.root.manager.disable == logging.ERROR
    report = json.loads(report_file(tmp_path).read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["jobs"][0]["status"] == "failed"
    logging.disable(old)


def _cli_interrupt_probe(root, kind, fail_at):
    """Subprocess-only harness: real CLI/run/report, synthetic trainer and packages."""
    root = Path(root)
    with pytest.MonkeyPatch.context() as patches:
        harness = rig.__wrapped__(root, patches)
        runner = harness.runner
        original_run = runner.run
        patches.setattr(runner, "run", lambda run_id: original_run(run_id, root=root))
        private = "SYNTHETIC_PRIVATE_INTERRUPTION"
        interruption = {"zero": SystemExit(0), "string": SystemExit(private),
                        "keyboard": KeyboardInterrupt(private)}[kind]

        def train(*args):
            result = harness.train(*args)
            if len(harness.calls) == fail_at:
                print(private)
                print(private, file=sys.stderr)
                raise interruption
            return result

        patches.setattr(runner, "train_job", train)
        streams = sys.stdout, sys.stderr
        disabled = logging.root.manager.disable
        try:
            return runner.main(["--run-id", "synthetic"])
        finally:
            assert len(harness.calls) == fail_at
            assert (sys.stdout, sys.stderr) == streams
            assert logging.root.manager.disable == disabled
            assert os.environ["ACTIVITY_MODEL_DIR"] == "synthetic-original"
            assert not runner._RUN_LOCK.locked()


def _isolated_cli_process(code, *arguments):
    # Do not read/copy the host environment or forward credentials to the probe.
    python_dir = Path(sys.executable).parent
    return subprocess.run([sys.executable, "-B", "-c", code, *map(str, arguments)],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=90,
        env={"SYSTEMROOT": "C:/Windows", "WINDIR": "C:/Windows",
             "PATH": os.pathsep.join(map(str, (python_dir, python_dir / "Library/bin",
                                               Path("C:/Windows/System32"))))})


@pytest.mark.parametrize("kind,error_type", [("zero", "SystemExit"), ("string", "SystemExit"),
                                          ("keyboard", "KeyboardInterrupt")])
@pytest.mark.parametrize("fail_at", [1, 2])
def test_cli_training_interruptions_exit_nonzero_without_private_output(tmp_path, kind, error_type, fail_at):
    child = _isolated_cli_process(
        "import sys; from tests.test_family_training_run import _cli_interrupt_probe; "
        "raise SystemExit(_cli_interrupt_probe(sys.argv[1], sys.argv[2], int(sys.argv[3])))",
        tmp_path, kind, fail_at)
    persisted = report_file(tmp_path).read_text(encoding="utf-8")
    report = json.loads(persisted)
    assert report["status"] == ("failed" if fail_at == 1 else "partial")
    assert report["error_type"] == error_type
    assert len(report["jobs"]) == fail_at and report["jobs"][-1]["status"] == "failed"
    assert report["jobs"][-1]["error_type"] == error_type
    assert report["bundles"] == [] and report["activated"] is False
    assert "SYNTHETIC_PRIVATE" not in child.stdout + child.stderr + persisted
    assert child.stderr == ""
    assert child.returncode == 1
    summary = json.loads(child.stdout.splitlines()[-1])
    assert summary == dict(status="failed", activated=False, error_type=error_type)


def test_cli_help_still_exits_zero_without_scientific_imports():
    child = _isolated_cli_process("""
import builtins
from tests.test_family_training_run import module
runner = module()
original = builtins.__import__
def guarded(name, *args, **kwargs):
    assert not name.startswith(("src.", "torch", "rdkit", "sklearn"))
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
runner.main(["--help"])
""")
    assert child.returncode == 0 and child.stderr == ""
    assert "--run-id" in child.stdout and "usage:" in child.stdout


def test_same_module_concurrent_run_rejected_before_global_overrides(rig, tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    reports, errors = [], []

    def train(*args):
        entered.set()
        assert release.wait(10)
        assert os.environ["ACTIVITY_MODEL_DIR"] == str(tmp_path / "data/activity/models/first")
        return rig.train(*args)

    monkeypatch.setattr(rig.runner, "train_job", train)

    def first():
        try:
            reports.append(rig.runner.run("first", root=tmp_path))
        except BaseException as exc:
            errors.append(type(exc).__name__)

    worker = threading.Thread(target=first)
    worker.start()
    try:
        assert entered.wait(10)
        with pytest.raises(RuntimeError, match="already running"):
            rig.runner.run("second", root=tmp_path)
        assert not (tmp_path / "outputs/activity_training/second").exists()
        assert not (tmp_path / "data/activity/models/second").exists()
    finally:
        release.set()
        worker.join(15)
    assert not worker.is_alive() and errors == []
    assert reports[0]["status"] == "completed"
    assert os.environ["ACTIVITY_MODEL_DIR"] == "synthetic-original"


@pytest.mark.parametrize("failure", ["encode", "write", "replace"])
def test_atomic_report_failure_preserves_previous_file_and_cleans_temp(tmp_path, monkeypatch, failure):
    runner = module()
    path = tmp_path / "report.json"
    runner.write_report(path, {"status": "running"})
    before = path.read_bytes()
    payload = {"status": "completed"}
    if failure == "encode":
        payload["invalid"] = float("nan")
    elif failure == "replace":
        def fail_replace(*_args):
            raise OSError("synthetic replace failure")
        monkeypatch.setattr(runner.os, "replace", fail_replace)
    else:
        original = runner.tempfile.NamedTemporaryFile

        def fail_write(*args, **kwargs):
            handle = original(*args, **kwargs)
            def write(_data):
                raise OSError("synthetic write failure")
            handle.write = write
            return handle
        monkeypatch.setattr(runner.tempfile, "NamedTemporaryFile", fail_write)
    with pytest.raises((ValueError, OSError)):
        runner.write_report(path, payload)
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("when", ["initial", "progress", "final"])
def test_report_io_failure_restores_overrides_and_never_returns_completed(rig, tmp_path, monkeypatch, when):
    original = rig.runner.write_report
    failed = []

    def write(path, report):
        trigger = (when == "initial" or when == "final" and report["status"] == "completed"
                   or when == "progress" and any(j.get("epoch") for j in report["jobs"]))
        if trigger and not failed:
            failed.append(True)
            raise OSError("PRIVATE_REPORT_PATH")
        return original(path, report)

    monkeypatch.setattr(rig.runner, "write_report", write)
    disabled = logging.root.manager.disable
    try:
        result = rig.runner.run("synthetic", root=tmp_path)
    except OSError as exc:
        assert "PRIVATE_REPORT_PATH" not in str(exc)
    else:
        assert result["status"] in {"failed", "partial"}
    assert failed
    assert os.environ["ACTIVITY_MODEL_DIR"] == "synthetic-original"
    assert logging.root.manager.disable == disabled
    if report_file(tmp_path).exists():
        assert json.loads(report_file(tmp_path).read_bytes())["status"] != "completed"
    if when == "initial":
        assert rig.calls == []
    # Failure releases the same-module lock, too.
    monkeypatch.setattr(rig.runner, "write_report", original)
    rig.verified.clear()
    assert rig.runner.run("recovery", root=tmp_path)["status"] == "completed"


@pytest.fixture
def packages(tmp_path, monkeypatch):
    from src.activity import family_contract, family_dataset
    real_builder = family_contract.build_family_manifest

    def small(family, dataset_id, task_type):
        return real_builder(family, dataset_id, task_type,
                            minimum_unique_molecules=3, minimum_scaffolds=3)

    monkeypatch.setattr(family_contract, "build_family_manifest", small)
    source = tmp_path / "synthetic.csv"
    rows = ["Smiles,pIC50"]
    for ring in ("c1ccccc1", "c1ccncc1", "C1CCCCC1"):
        rows.extend(f"{prefix}{ring},{value}" for prefix, value in
                    zip(("", "C", "CC", "CCC"), (4, 4.5, 5, 6)))
    source.write_text("\n".join(rows) + "\n", encoding="utf-8")
    paths = []
    for family in ("pde-family", "buche-family"):
        family_dataset.prepare_family_dataset(source, family=family, package_id=family + "-v1",
            output_dir=tmp_path / "data/activity/prepared", validate_only=False)
        paths.append(tmp_path / "data/activity/prepared" / (family + "-v1") / "family_dataset.json")
    return paths


@pytest.mark.parametrize("tamper", ["source", "threshold", "split", "child", "wrong_family"])
def test_real_package_integrity_preflight_before_training(packages, rig, tmp_path, monkeypatch, tamper):
    from src.activity import family_dataset
    real_load = rig.real_load
    monkeypatch.setattr(family_dataset, "load_family_dataset", real_load)
    path = packages[1]
    descriptor = json.loads(path.read_bytes())
    if tamper == "source":
        (path.parent / "source.csv").write_text("PRIVATE_CORRUPTION", encoding="utf-8")
    elif tamper == "child":
        child = path.parent / descriptor["datasets"]["regression"]["path"]
        child.write_text("{}", encoding="utf-8")
    elif tamper == "wrong_family":
        # A genuinely verified package for the wrong loop family, not forged metadata.
        monkeypatch.setattr(family_dataset, "load_family_dataset", lambda _p: real_load(packages[0]))
    else:
        descriptor["label_threshold" if tamper == "threshold" else "assignment_sha256"] = 6 if tamper == "threshold" else "0" * 64
        path.write_text(json.dumps(descriptor), encoding="utf-8")
    report = rig.runner.run("synthetic", root=tmp_path)
    assert report["status"] == "failed" and report["jobs"] == []
    assert rig.calls == rig.bundles == []


def test_main_registry_rejects_fabricated_completed_models(packages, rig, tmp_path, monkeypatch):
    from src.activity import family_dataset, model_card, model_registry
    monkeypatch.setattr(family_dataset, "load_family_dataset", rig.real_load)
    monkeypatch.setattr(model_card, "load_prepared_training_data", rig.real_prepared)
    monkeypatch.setattr(model_registry, "ActivityModelRegistry", rig.real_registry)

    def train(path, job_id, progress):
        return dict(state="completed", model_id=job_id, best_epoch=1,
                    epochs_completed=1, best_metrics={}, test_metrics={})

    monkeypatch.setattr(rig.runner, "train_job", train)
    report = rig.runner.run("synthetic", root=tmp_path)
    assert report["status"] != "completed" and report["bundles"] == []
    assert len(report["bundle_errors"]) == 2
    registry = model_registry.ActivityModelRegistry(tmp_path / "data/activity/models/synthetic")
    assert all(registry.get_active_family_bundle(f) is None for f in rig.runner.FAMILIES)


@pytest.mark.parametrize("damage", [None, "model_contract_key", "dataset_sha256", "label_threshold", "weights"])
def test_main_registry_binds_real_synthetic_artifacts_without_activation(
    packages, rig, tmp_path, monkeypatch, damage,
):
    from src.activity import family_dataset, model_card, model_registry, trainer
    monkeypatch.setattr(family_dataset, "load_family_dataset", rig.real_load)
    monkeypatch.setattr(model_card, "load_prepared_training_data", rig.real_prepared)
    monkeypatch.setattr(model_registry, "ActivityModelRegistry", rig.real_registry)
    for method in ("select", "select_family_bundle"):
        monkeypatch.setattr(rig.real_registry, method, lambda *_a: pytest.fail("Must not activate"))
    pairs, calls = {}, []

    def train(manifest, job_id, progress):
        family = next(f for f in rig.runner.FAMILIES if f in job_id)
        task = manifest.parent.name.rsplit("-", 1)[1]
        registry = trainer.get_model_registry()  # Main dynamically resolves the run's environment.
        assert registry.models_dir == tmp_path / "data/activity/models/synthetic"
        if family not in pairs:
            changes = None
            if family == "pde-family" and damage in {"model_contract_key", "dataset_sha256", "label_threshold"}:
                changes = {"classification": {damage: 6. if damage == "label_threshold" else "0" * 64}}
            path = next(p for p in packages if p.parent.name == family + "-v1")
            pairs[family] = rig.make_pair(registry, path, family, overrides=changes)
            if family == "pde-family" and damage == "weights":
                (registry.models_dir / pairs[family]["classification"]["weights_file"]).write_bytes(b"CORRUPT SYNTHETIC")
        item = pairs[family][task]
        calls.append((family, task))
        progress(2, 1, {"val_loss": 1.}, best_epoch=1)
        return dict(state="completed", model_id=item["model_id"], best_epoch=1,
                    epochs_completed=1, best_metrics=item["metrics"], test_metrics=item["test_metrics"])

    monkeypatch.setattr(rig.runner, "train_job", train)
    report = rig.runner.run("synthetic", root=tmp_path)
    assert calls == [(f, t) for f in rig.runner.FAMILIES for t in rig.runner.TASKS]
    assert report["status"] == ("completed" if damage is None else "partial")
    assert report["bundles"] == (["synthetic-pde-family", "synthetic-buche-family"]
                                  if damage is None else ["synthetic-buche-family"])
    state_path = tmp_path / "data/activity/models/synthetic/registry.json"
    registry = rig.real_registry(state_path.parent)
    state = json.loads(registry.state_path.read_bytes())
    assert state["active_family_bundles"] == state["active_models_by_endpoint"] == {}
    assert state["active_model_id"] is None


@pytest.mark.parametrize("arguments", [["--run-id", "PRIVATE/CCO"], ["--unknown", "PRIVATE_CCO"],
                                      ["--run-id", "synthetic", "--unknown", "PRIVATE_CCO"]])
def test_cli_invalid_arguments_are_redacted_without_training(rig, arguments, capsys):
    assert rig.runner.main(arguments) != 0
    captured = capsys.readouterr()
    assert "PRIVATE" not in captured.out + captured.err
    assert json.loads(captured.out)["status"] == "failed"
    assert rig.calls == []


@pytest.mark.parametrize("status,expected", [("completed", 0), ("partial", 1), ("failed", 1)])
def test_cli_exit_code_matches_operational_status(monkeypatch, capsys, status, expected):
    runner = module()
    monkeypatch.setattr(runner, "run", lambda run_id: dict(status=status, activated=False))
    assert runner.main(["--run-id", "synthetic"]) == expected
    assert json.loads(capsys.readouterr().out)["status"] == status


def test_custom_exception_class_name_is_not_reported(rig, tmp_path, monkeypatch, capsys):
    private_error = type("PRIVATE_CCO_LABEL_6", (Exception,), {})
    monkeypatch.setattr(rig.runner, "train_job", lambda *_a: (_ for _ in ()).throw(private_error("PRIVATE_CCO")))
    report = rig.runner.run("synthetic", root=tmp_path)
    captured = capsys.readouterr()
    assert report["status"] == "failed"
    assert "PRIVATE" not in json.dumps(report) + captured.out + captured.err
