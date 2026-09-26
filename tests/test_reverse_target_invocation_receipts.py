"""Owned reverse producer: synthetic writer files, never host assets."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import copy
from dataclasses import fields, FrozenInstanceError
import gc
import hashlib
import json
import threading
import weakref

import numpy as np
import pytest
from rdkit import Chem

from src.reverse_target.generate_fingerprints import FingerprintGenerator
from src.reverse_target.predictor import ReverseTargetPredictor
from src.reverse_target import owned_source as owned


@pytest.fixture(autouse=True)
def controlled_environment(monkeypatch):
    monkeypatch.setenv("REVERSE_TARGET_MORGAN_WEIGHT", "0.7")
    monkeypatch.setenv("REVERSE_TARGET_POPCOUNT_CHUNK_SIZE", "1")


def close_fixture_mmaps(predictor):
    for name in ("morgan_fps", "maccs_fps", "morgan_popcounts", "maccs_popcounts"):
        handle = getattr(getattr(predictor, name, None), "_mmap", None)
        if handle is not None:
            handle.close()


def make_writer_database(directory, *, cached=True, smiles=("CCO", "CCC")):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "chembl_data_with_fps.tsv"
    path.write_text(
        "molecule_chembl_id\tcanonical_smiles\ttarget_name\tstandard_type\tstandard_value\torganism\n"
        + "".join(f"CHEMBL{i+1}\t{s}\tSynthetic {chr(65+i)}\tIC50\t{i+1}\tHuman\n"
                  for i, s in enumerate(smiles)), encoding="utf-8")
    writer = FingerprintGenerator(path)
    predictor = ReverseTargetPredictor(directory)
    for kind, width in (("morgan", 2048), ("maccs", 166)):
        generate = getattr(writer, f"generate_{kind}_fingerprint")
        array = np.stack([generate(Chem.MolFromSmiles(s)) for s in smiles]) if smiles else np.empty((0, width), dtype=np.uint8)
        np.save(getattr(predictor, f"{kind}_fp_path"), array)
        if cached:
            np.save(getattr(predictor, f"{kind}_popcount_path"), array.sum(axis=1, dtype=np.uint32).astype(np.uint16))
    return predictor


@pytest.mark.parametrize("corruption", ["extra-leading-column", "blank-record"])
def test_strict_tsv_rejects_silently_normalized_records(tmp_path, corruption):
    p = make_writer_database(tmp_path)
    lines = p.training_data_path.read_text(encoding="utf-8").splitlines()
    if corruption == "extra-leading-column":
        lines[1:] = ["unexpected\t" + line for line in lines[1:]]
    else:
        lines.insert(2, "")
    p.training_data_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="^Invalid reverse-target owned source\\.$"):
        p.initialize_strict()
    with pytest.raises(owned.ReverseSourceUnavailable):
        p.capture_prediction_source()


@pytest.mark.parametrize("quoted", [False, True], ids=["unquoted", "quoted"])
@pytest.mark.parametrize("field,original,corrupt", [
    ("canonical_smiles", "CCO", "CCO\x00junk"),
    ("target_name", "Synthetic A", "Synthetic A\x00forged"),
    ("standard_value", "1", "1\x00junk"),
], ids=["canonical_smiles", "target_name", "standard_value"])
def test_strict_tsv_rejects_nul_cells_before_truncation(tmp_path, field, original, corrupt, quoted):
    p = make_writer_database(tmp_path)
    lines = p.training_data_path.read_text(encoding="utf-8").splitlines()
    column = lines[0].split("\t").index(field)
    cells = lines[1].split("\t")
    assert cells[column] == original
    cells[column] = '"' + corrupt + '"' if quoted else corrupt
    lines[1] = "\t".join(cells)
    raw = ("\n".join(lines) + "\n").encode("utf-8")
    assert raw.count(b"\x00") == 1
    # Only the TSV changes; retain the actual writer's original RDKit arrays.
    p.training_data_path.write_bytes(raw)
    with pytest.raises(ValueError, match="^Invalid reverse-target owned source\\.$"):
        p.initialize_strict()
    with pytest.raises(owned.ReverseSourceUnavailable):
        p.capture_prediction_source()


@pytest.mark.parametrize("target", ["Synthetic\tA", "Synthetic\nA", 'Synthetic "A"', "Synthetic\r\n\nA"])
def test_strict_tsv_preserves_quoted_fields_and_empty_organism(tmp_path, target):
    p = make_writer_database(tmp_path)
    text = p.training_data_path.read_text(encoding="utf-8")
    text = text.replace("Synthetic A", '"' + target.replace('"', '""') + '"')
    text = text.replace("\tHuman", "\t")
    p.training_data_path.write_bytes(text.encode("utf-8"))
    p.initialize_strict()
    envelope = p.predict_with_receipt("CCO", threshold=0, combine_by_target=False)
    records = envelope["records"]
    assert [(r["row_index"], r["canonical_smiles"]) for r in records] == [(0, "CCO"), (1, "CCC")]
    assert records[0]["target_name"] == target
    assert records[0]["final_similarity"] == 1.0
    assert all(r["organism"] == "" for r in records)
    assert envelope["receipt"]["source"]["row_count"] == 2
    assert envelope["receipt"]["source"]["tsv_sha256"] == hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("corruption", [
    "duplicate-header", "empty-header", "whitespace-header", "short-record",
    "leading-blank", "trailing-blank", "whitespace-record", "unterminated-quote",
    "trailing-quote-junk",
])
def test_strict_tsv_structure_rejected_before_pandas(tmp_path, monkeypatch, corruption):
    p = make_writer_database(tmp_path)
    lines = p.training_data_path.read_text(encoding="utf-8").splitlines()
    if corruption in ("duplicate-header", "empty-header", "whitespace-header"):
        lines[0] += "\t" + {"duplicate-header": "target_name", "empty-header": "", "whitespace-header": " "}[corruption]
        lines[1:] = [line + "\textra" for line in lines[1:]]
    elif corruption == "short-record":
        lines[1] = lines[1].rsplit("\t", 1)[0]  # Missing organism is not an empty cell.
    elif corruption == "leading-blank":
        lines.insert(0, "")
    elif corruption == "trailing-blank":
        lines.append("")
    elif corruption == "whitespace-record":
        lines.insert(2, "   ")
    elif corruption == "unterminated-quote":
        lines[1] = lines[1].replace("Synthetic A", '"Synthetic A')
    else:
        lines[1] = lines[1].replace("Synthetic A", '"Synthetic A"junk')
    p.training_data_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    normalized = []
    original = owned.pd.read_csv
    def tracked(*args, **kwargs):
        normalized.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(owned.pd, "read_csv", tracked)
    with pytest.raises(ValueError, match="^Invalid reverse-target owned source\\.$"):
        p.initialize_strict()
    assert normalized == [], "structural rejection must precede pandas normalization"
    with pytest.raises(owned.ReverseSourceUnavailable):
        p.capture_prediction_source()


@pytest.mark.parametrize("failure", ["cancel", "deadline", "bad-return", "structure"])
def test_strict_tsv_parser_cleanup_and_multiline_cancellation(tmp_path, monkeypatch, failure):
    p = make_writer_database(tmp_path)
    text = p.training_data_path.read_text(encoding="utf-8")
    if failure == "structure":
        text += "\n"
    else:
        text = text.replace("Synthetic A", '"' + "Synthetic\n" * 100 + 'A"')
    p.training_data_path.write_bytes(text.encode("utf-8"))
    streams, reads, normalized = [], [], []
    original_wrapper = owned.io.TextIOWrapper
    original_csv = owned.pd.read_csv
    class TrackedText(original_wrapper):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            streams.append(weakref.ref(self))
        def readline(self, *args, **kwargs):
            result = super().readline(*args, **kwargs)
            reads.append(len(result))
            return result
    def tracked_csv(*args, **kwargs):
        normalized.append(True)
        return original_csv(*args, **kwargs)
    def cancelled():
        if len(reads) < 3 or failure == "structure":
            return False
        if failure == "deadline":
            raise TimeoutError("controlled parser deadline")
        return 1 if failure == "bad-return" else True
    monkeypatch.setattr(owned.io, "TextIOWrapper", TrackedText)
    monkeypatch.setattr(owned.pd, "read_csv", tracked_csv)
    expected = {"cancel": asyncio.CancelledError, "deadline": TimeoutError,
                "bad-return": ValueError, "structure": ValueError}[failure]
    with pytest.raises(expected) as caught:
        p.initialize_strict(cancelled=cancelled)
    assert normalized == []
    if failure != "structure":
        assert len(reads) == 3, "cancellation must be observed within a multiline record"
    else:
        assert str(caught.value) == owned.SOURCE_ERROR
    gc.collect()
    assert streams and all(ref() is None for ref in streams), "retained error pinned the parser stream"
    pending, seen, parser_frames = [caught.value], set(), []
    while pending:
        error = pending.pop()
        if id(error) in seen:
            continue
        seen.add(id(error))
        tb = error.__traceback__
        while tb:
            if tb.tb_frame.f_code.co_name in ("_validate_tsv_structure", "checked_lines"):
                parser_frames.append(tb.tb_frame)
                assert not tb.tb_frame.f_locals
            tb = tb.tb_next
        pending.extend(e for e in (error.__context__, error.__cause__) if e is not None)
    assert parser_frames
    with pytest.raises(owned.ReverseSourceUnavailable):
        p.capture_prediction_source()


@pytest.mark.parametrize("change", ["drop-row", "add-row", "replace-index", "rename-header"])
def test_strict_tsv_normalized_shape_matches_preflight_before_arrays(tmp_path, monkeypatch, change):
    p = make_writer_database(tmp_path)
    original = owned.pd.read_csv
    opened = []
    def normalized(*args, **kwargs):
        frame = original(*args, **kwargs)
        if change == "drop-row":
            return frame.iloc[:1]
        if change == "add-row":
            return owned.pd.concat([frame, frame.iloc[:1]], ignore_index=True)
        if change == "replace-index":
            frame.index = ["unexpected", "index"]
        else:
            frame["renamed"] = "extra"
        return frame
    def forbidden(*args, **kwargs):
        opened.append(True)
        raise AssertionError("normalization mismatch reached array acquisition")
    monkeypatch.setattr(owned.pd, "read_csv", normalized)
    monkeypatch.setattr(owned, "_read_array", forbidden)
    with pytest.raises(ValueError, match="^Invalid reverse-target owned source\\.$"):
        p.initialize_strict()
    assert opened == []


@pytest.mark.parametrize("ending", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize("bom", [False, True])
def test_strict_tsv_preserves_bom_line_endings_and_unterminated_final_record(tmp_path, ending, bom):
    p = make_writer_database(tmp_path)
    text = p.training_data_path.read_text(encoding="utf-8").rstrip("\n")
    text = text.replace("\n", ending)
    p.training_data_path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8"))
    p.initialize_strict()
    envelope = p.predict_with_receipt("CCO", threshold=0, combine_by_target=False)
    assert envelope["receipt"]["source"]["row_count"] == 2
    assert [(r["row_index"], r["canonical_smiles"]) for r in envelope["records"]] == [(0, "CCO"), (1, "CCC")]


def test_strict_source_rejects_swapped_rows(tmp_path):
    baseline = make_writer_database(tmp_path)
    try:
        result = baseline.predict("CCO")[0]
        assert (result["canonical_smiles"], result["target_name"], result["final_similarity"]) == ("CCO", "Synthetic A", 1.0)
    finally:
        close_fixture_mmaps(baseline)
    path = baseline.training_data_path
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join([lines[0], lines[2], lines[1]]) + "\n", encoding="utf-8")
    fresh = ReverseTargetPredictor(tmp_path)
    try:
        with pytest.raises(ValueError):
            fresh.initialize_strict()
            fresh.predict_with_receipt("CCO")
    finally:
        close_fixture_mmaps(fresh)


@pytest.mark.parametrize("cached", [True, False])
@pytest.mark.parametrize("smiles", [("CCO", "CCC", "CCO"), ()])
def test_owned_parity_and_detached_proof(tmp_path, cached, smiles):
    p = make_writer_database(tmp_path, cached=cached, smiles=smiles)
    p.initialize_strict()
    snapshot = p.capture_prediction_source()
    try:
        for grouped in (True, False):
            for organism in ("", "human", "mouse"):
                for threshold in (0, 0.6, 1.0):
                    controls = dict(combine_by_target=grouped, organism_filter=organism, threshold=threshold, top_k=2)
                    envelope = p.predict_with_receipt("CCO", **controls)
                    assert envelope["records"] == p.predict("CCO", **controls)
                    assert p.validate_prediction_source(envelope, smiles="CCO", expected=snapshot, **controls) == envelope
                    assert envelope["receipt"]["source"]["cache_origins"] == dict.fromkeys(("morgan", "maccs"), "verified_cache" if cached else "computed")
                    copy = p.validate_prediction_source(envelope, smiles="CCO", expected=snapshot, **controls)
                    copy["receipt"]["source"]["row_count"] = 999
                    assert envelope["receipt"]["source"]["row_count"] == len(smiles)
    finally:
        close_fixture_mmaps(p)


def test_unavailable_close_reload_and_sticky_paths(tmp_path):
    p = make_writer_database(tmp_path)
    with pytest.raises(ValueError):
        p.predict_with_receipt("CCO")
    p.initialize_strict()
    old = p.capture_prediction_source()
    envelope = p.predict_with_receipt("CCO")
    p.close_strict()
    with pytest.raises(ValueError):
        p.capture_prediction_source()
    p.initialize_strict()
    assert p.capture_prediction_source() != old
    with pytest.raises(ValueError):
        p.validate_prediction_source(envelope, smiles="CCO", expected=old)
    healthy = p.capture_prediction_source()
    assert healthy == p.capture_prediction_source()
    original = p.training_data_path
    p.training_data_path = tmp_path / "different.tsv"
    with pytest.raises(ValueError):
        p.capture_prediction_source()
    p.training_data_path = original
    with pytest.raises(ValueError):
        p.capture_prediction_source()
    p.initialize_strict()


def test_owned_disk_and_public_fields_are_not_authority(tmp_path, monkeypatch):
    p = make_writer_database(tmp_path, cached=False)
    def forbidden(*args, **kwargs):
        raise AssertionError("strict must not save or unpickle")
    monkeypatch.setattr(np, "save", forbidden)
    import pickle
    monkeypatch.setattr(pickle, "load", forbidden)
    p.metadata_path.write_bytes(b"never read")
    p.initialize_strict()
    snapshot = p.capture_prediction_source()
    before = p.predict_with_receipt("CCO")
    for path in tmp_path.iterdir():
        path.unlink()
    p.df = "not authority"
    p.morgan_fps = None
    assert p.predict_with_receipt("CCO")["records"] == before["records"]
    monkeypatch.setattr(p, "compute_query_fingerprints", forbidden)
    assert p.validate_prediction_source(before, smiles="CCO", expected=snapshot) == before
    with pytest.raises(ValueError):
        p.initialize_strict()
    with pytest.raises(ValueError):
        p.capture_prediction_source()


@pytest.mark.parametrize("change", ["bits", "rows", "cache", "negative", "nonfinite", "blank", "suffix", "invalid", "dtype", "rank", "width"])
def test_reject_corrupt_source(tmp_path, change):
    p = make_writer_database(tmp_path)
    if change in ("bits", "rows", "dtype", "rank", "width"):
        a = np.load(p.morgan_fp_path)
        if change == "bits":
            on, off = np.flatnonzero(a[0]), np.flatnonzero(a[0] == 0)
            a[0, on[0]], a[0, off[0]] = 0, 1
        elif change == "rows":
            a = a[::-1]
        elif change == "dtype":
            a = a.astype(float)
        elif change == "rank":
            a = a.ravel()
        else:
            a = a[:, :-1]
        np.save(p.morgan_fp_path, a)
    elif change == "cache":
        np.save(p.morgan_popcount_path, np.array([1, 1], dtype=np.uint16))
    else:
        text = p.training_data_path.read_text()
        replacements = {"negative": ("\t1\tHuman", "\t-1\tHuman"), "nonfinite": ("\t1\tHuman", "\tinf\tHuman"), "blank": ("Synthetic A", " "), "suffix": ("CCO", "CCO name"), "invalid": ("CCO", "not-smiles")}
        a, b = replacements[change]
        p.training_data_path.write_text(text.replace(a, b))
    with pytest.raises(ValueError):
        p.initialize_strict()
    with pytest.raises(ValueError):
        p.capture_prediction_source()


@pytest.mark.parametrize("kwargs", [{"max_snapshot_bytes": True}, {"max_snapshot_bytes": 0}, {"max_snapshot_bytes": 100}, {"timeout_seconds": 0}, {"timeout_seconds": float("nan")}, {"timeout_seconds": 301}, {"cancelled": 1}, {"cancelled": lambda: 1}])
def test_acquisition_limits(tmp_path, kwargs):
    p = make_writer_database(tmp_path)
    with pytest.raises(ValueError):
        p.initialize_strict(**kwargs)


@pytest.mark.parametrize("kwargs", [{"smiles": "CCO name"}, {"smiles": ""}, {"top_k": True}, {"top_k": 101}, {"threshold": True}, {"threshold": float("nan")}, {"combine_by_target": 1}, {"organism_filter": None}, {"timeout_seconds": 301}, {"cancelled": lambda: 1}])
def test_strict_input_checks(tmp_path, kwargs):
    p = make_writer_database(tmp_path)
    p.initialize_strict()
    with pytest.raises(ValueError):
        p.predict_with_receipt(**({"smiles": "CCO"} | kwargs))


@pytest.mark.parametrize("setting,weight,defaulted,clamped", [(None, .7, True, False), ("bad", .7, True, False), ("-1", 0., False, True), ("2", 1., False, True), ("nan", 0., False, True), ("inf", 1., False, True), ("-inf", 0., False, True), ("0.25", .25, False, False)])
def test_weight_resolution(tmp_path, monkeypatch, setting, weight, defaulted, clamped):
    p = make_writer_database(tmp_path)
    p.initialize_strict()
    if setting is None:
        monkeypatch.delenv("REVERSE_TARGET_MORGAN_WEIGHT", raising=False)
    else:
        monkeypatch.setenv("REVERSE_TARGET_MORGAN_WEIGHT", setting)
    snapshot = p.capture_prediction_source()
    envelope = p.predict_with_receipt("CCO", threshold=0)
    r = envelope["receipt"]
    assert r["weights"] == {"morgan": weight, "maccs": 1.-weight}
    assert r["weight_resolution"] == {"defaulted": defaulted, "clamped": clamped}
    assert p.validate_prediction_source(envelope, smiles="CCO", threshold=0, expected=snapshot) == envelope
    monkeypatch.setenv("REVERSE_TARGET_MORGAN_WEIGHT", "0.9")
    with pytest.raises(ValueError):
        p.validate_prediction_source(envelope, smiles="CCO", threshold=0, expected=snapshot)


def test_writer_query_bit_parity(tmp_path):
    smiles = ("CCO", "CCC", "c1ccccc1", "C[C@H](O)C(=O)O", "[Na+].[Cl-]", "C#N", "[He]")
    p = make_writer_database(tmp_path, smiles=smiles)
    arrays = [np.load(p.morgan_fp_path), np.load(p.maccs_fp_path)]
    for row, s in enumerate(smiles):
        for actual, matrix in zip(p.compute_query_fingerprints(s), arrays):
            np.testing.assert_array_equal(actual, matrix[row])
    p.initialize_strict()


def test_loading_owner_releases_after_path_error(tmp_path, monkeypatch):
    p = make_writer_database(tmp_path)
    original = p._resolved_source_paths
    def fail():
        raise OSError("private path")
    monkeypatch.setattr(p, "_resolved_source_paths", fail)
    with pytest.raises(ValueError):
        p.initialize_strict()
    assert not p._strict_loading
    monkeypatch.setattr(p, "_resolved_source_paths", original)
    p.initialize_strict()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def test_exact_identities_and_frozen_snapshot(tmp_path):
    p = make_writer_database(tmp_path)
    p.initialize_strict()
    snapshot = p.capture_prediction_source()
    assert [f.name for f in fields(snapshot)] == ["generation_id", "source_sha256", "configuration_sha256"]
    with pytest.raises(FrozenInstanceError):
        snapshot.generation_id = "0" * 32
    e = p.predict_with_receipt("CCO")
    r, source = e["receipt"], e["receipt"]["source"]
    assert source["tsv_sha256"] == hashlib.sha256(p.training_data_path.read_bytes()).hexdigest()
    for key in ("morgan", "maccs", "morgan_popcounts", "maccs_popcounts"):
        path = getattr(p, key + ("_path" if "popcounts" in key else "_fp_path"), None)
        if "popcounts" in key:
            path = getattr(p, key.replace("popcounts", "popcount") + "_path")
        array = np.load(path)
        framing = b"MedChat.reverse-array.v1\n" + json.dumps(dict(dtype=array.dtype.name, shape=list(array.shape), order="C"), sort_keys=True, separators=(",", ":")).encode() + b"\n"
        assert source[key + "_sha256"] == hashlib.sha256(framing + array.astype(array.dtype.newbyteorder("<")).tobytes()).hexdigest()
    assert r["source_sha256"] == digest(source) == snapshot.source_sha256
    assert r["input_sha256"] == digest(dict(smiles="CCO", controls=r["controls"]))
    assert r["configuration_sha256"] == digest(dict(paths=p._resolved_source_paths(), weights=r["weights"], weight_resolution_revision=r["weight_resolution_revision"], weight_resolution=r["weight_resolution"]))
    assert r["result_sha256"] == digest(e["records"])
    assert str(tmp_path) not in json.dumps(e)
    for name in ("morgan", "maccs", "morgan_popcounts", "maccs_popcounts"):
        a = getattr(p._strict_source, name)
        assert a.flags.owndata and a.flags.c_contiguous and not a.flags.writeable
    assert p.df is None and p.morgan_fps is None and not p._loaded


@pytest.mark.parametrize("phase", ["loading", "scoring"])
@pytest.mark.parametrize("mutation", ["close", "path"])
def test_busy_nonowner_and_late_work_cannot_publish(tmp_path, monkeypatch, phase, mutation):
    p = make_writer_database(tmp_path)
    entered, release = threading.Event(), threading.Event()
    if phase == "scoring":
        p.initialize_strict()
    original = p.compute_query_fingerprints
    def blocked(smiles):
        entered.set()
        assert release.wait(10), "barrier release missing"
        return original(smiles)
    monkeypatch.setattr(p, "compute_query_fingerprints", blocked)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(p.initialize_strict if phase == "loading" else p.predict_with_receipt, *(() if phase == "loading" else ("CCO",)))
        try:
            assert entered.wait(10), "worker did not enter barrier"
            before = p.capture_prediction_source() if phase == "scoring" else None
            with pytest.raises(owned.ReverseSourceUnavailable):
                p.initialize_strict()
            assert p._strict_loading == (phase == "loading")
            if before:
                assert before == p.capture_prediction_source()
            if mutation == "close":
                p.close_strict()
            else:
                p.maccs_fp_path = tmp_path / "new.npy"
            if phase == "scoring":
                assert p._strict_active == 1
            with pytest.raises(owned.ReverseSourceUnavailable):
                p.initialize_strict()
        finally:
            release.set()
        with pytest.raises(owned.ReverseSourceUnavailable):
            future.result(timeout=10)
    assert p._strict_active == 0 and not p._strict_loading
    with pytest.raises(owned.ReverseSourceUnavailable):
        p.capture_prediction_source()
    p.maccs_fp_path = tmp_path / "maccs_fingerprints.npy"
    p.initialize_strict()


def test_concurrent_calls_keep_separate_controls_and_receipts(tmp_path, monkeypatch):
    p = make_writer_database(tmp_path)
    p.initialize_strict()
    barrier = threading.Barrier(2)
    original = p._predict_core
    calls = []
    def blocked(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(args[0])
        barrier.wait(timeout=10)
        return result
    monkeypatch.setattr(p, "_predict_core", blocked)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(p.predict_with_receipt, "CCO", threshold=1., top_k=1)
        b = pool.submit(p.predict_with_receipt, "CCC", threshold=0., top_k=2)
        first, second = a.result(timeout=10), b.result(timeout=10)
    assert sorted(calls) == ["CCC", "CCO"]
    assert first["receipt"]["invocation_id"] != second["receipt"]["invocation_id"]
    assert first["receipt"]["input_sha256"] != second["receipt"]["input_sha256"]
    assert first["records"][0]["canonical_smiles"] == "CCO"
    assert second["records"][0]["canonical_smiles"] == "CCC"
    assert first["receipt"]["controls"]["top_k"] == 1
    assert second["receipt"]["controls"]["top_k"] == 2
    assert p._strict_active == 0


def test_weights_changed_after_scoring_are_historical_not_recaptured(tmp_path, monkeypatch):
    p = make_writer_database(tmp_path)
    p.initialize_strict()
    old = p.capture_prediction_source()
    original = p._predict_core
    calls = []
    def mutate(*args, **kwargs):
        calls.append(kwargs["weights"].copy())
        result = original(*args, **kwargs)
        monkeypatch.setenv("REVERSE_TARGET_MORGAN_WEIGHT", "0.2")
        return result
    monkeypatch.setattr(p, "_predict_core", mutate)
    e = p.predict_with_receipt("CCO", threshold=0)
    assert calls == [{"morgan": .7, "maccs": 1. - .7}]
    assert e["receipt"]["weights"] == calls[0]
    assert e["receipt"]["configuration_sha256"] == old.configuration_sha256
    with pytest.raises(owned.ReverseSourceUnavailable):
        p.validate_prediction_source(e, smiles="CCO", threshold=0, expected=old)
    assert p.capture_prediction_source().generation_id == old.generation_id


@pytest.mark.parametrize("phase", ["loading", "scoring"])
@pytest.mark.parametrize("control", ["cancel", "deadline", "bad-return"])
def test_cooperative_controls_release_ownership(tmp_path, monkeypatch, phase, control):
    p = make_writer_database(tmp_path)
    if phase == "scoring":
        p.initialize_strict()
    original = p.compute_query_fingerprints
    state = {"past": False}
    def after_fingerprint(smiles):
        result = original(smiles)
        state["past"] = True
        return result
    monkeypatch.setattr(p, "compute_query_fingerprints", after_fingerprint)
    kwargs = {}
    if control == "deadline":
        monkeypatch.setattr(owned.time, "monotonic", lambda: 1000. if state["past"] else 0.)
        error = TimeoutError
    else:
        kwargs["cancelled"] = lambda: (1 if control == "bad-return" else True) if state["past"] else False
        error = ValueError if control == "bad-return" else asyncio.CancelledError
    action = p.initialize_strict if phase == "loading" else lambda **kw: p.predict_with_receipt("CCO", **kw)
    with pytest.raises(error) as caught:
        action(**kwargs)
    if control == "bad-return":
        assert str(caught.value) == owned.INPUT_ERROR
    assert not p._strict_loading and p._strict_active == 0
    if phase == "loading":
        with pytest.raises(owned.ReverseSourceUnavailable):
            p.capture_prediction_source()
    else:
        assert p.capture_prediction_source()


@pytest.mark.parametrize("change", ["extra", "unknown_source", "hash", "score", "row", "count", "status", "dtype", "weight", "resolution", "control_bool", "cycle", "depth", "nodes", "bytes", "numpy", "nan", "path", "order", "duplicate", "sum", "threshold", "blank", "bool_score"])
def test_proof_rejects_malformed_without_mutation_or_chemistry(tmp_path, monkeypatch, change):
    p = make_writer_database(tmp_path)
    p.initialize_strict()
    snapshot = p.capture_prediction_source()
    e = p.predict_with_receipt("CCO", threshold=0)
    r = e["receipt"]
    if change == "extra": e["extra"] = 1
    elif change == "unknown_source": r["source"]["extra"] = 1
    elif change == "hash": r["result_sha256"] = "0" * 64
    elif change == "score": e["records"][0]["final_similarity"] = 2.
    elif change == "row": e["records"][0]["row_index"] = True
    elif change == "count": r["record_count"] = True
    elif change == "status": r["status"] = "verified_empty"
    elif change == "dtype": r["score_dtype"] = "float16"
    elif change == "weight": r["weights"]["morgan"] = True
    elif change == "resolution": r["weight_resolution"]["clamped"] = True
    elif change == "control_bool": r["controls"]["top_k"] = True
    elif change == "cycle": e["extra"] = e
    elif change == "depth": e["extra"] = [[[[[[[[[1]]]]]]]]]
    elif change == "nodes": e["extra"] = [0] * 10001
    elif change == "bytes": e["extra"] = "a" * (1024**2 + 1)
    elif change == "numpy": r["record_count"] = np.int64(2)
    elif change == "nan": e["records"][0]["standard_value"] = float("nan")
    elif change == "path": r["source"]["source_name"] = "C:\\private\\source.tsv"
    elif change == "order": e["records"].reverse()
    elif change == "duplicate": e["records"][1]["row_index"] = 0
    elif change == "sum": e["records"][0]["similar_count"] = 2
    elif change == "threshold": e["records"][0]["final_similarity"] = -0.1
    elif change == "blank": e["records"][0]["target_name"] = ""
    elif change == "bool_score": e["records"][0]["morgan_similarity"] = True
    before = repr(e)
    def forbidden(*args, **kwargs):
        raise AssertionError("proof validation must be pure")
    monkeypatch.setattr(p, "compute_query_fingerprints", forbidden)
    monkeypatch.setattr(p, "_predict_core", forbidden)
    monkeypatch.setattr(np, "load", forbidden)
    with pytest.raises(ValueError) as caught:
        p.validate_prediction_source(e, smiles="CCO", threshold=0, expected=snapshot)
    assert type(caught.value) is ValueError
    assert str(caught.value) == owned.PROOF_ERROR
    assert repr(e) == before
    assert p.capture_prediction_source() == snapshot


def test_self_consistent_forgery_not_claimed_authenticated(tmp_path):
    p = make_writer_database(tmp_path)
    p.initialize_strict()
    snapshot = p.capture_prediction_source()
    e = p.predict_with_receipt("CCO")
    e["records"][0]["target_name"] = "Self-consistent fabrication, not authenticated"
    e["receipt"]["result_sha256"] = digest(e["records"])
    assert p.validate_prediction_source(e, smiles="CCO", expected=snapshot) == e


def test_float32_threshold_boundary_parity(tmp_path):
    p = make_writer_database(tmp_path)
    p.initialize_strict()
    e = p.predict_with_receipt("CCO", threshold=0, combine_by_target=False)
    score = e["records"][1]["final_similarity"]
    threshold = float(np.nextafter(score, 1.))
    assert score < threshold and np.asarray([score], dtype=np.float32)[0:1] >= threshold
    try:
        actual = p.predict_with_receipt("CCO", threshold=threshold, combine_by_target=False)
        assert actual["records"] == p.predict("CCO", threshold=threshold, combine_by_target=False)
        assert len(actual["records"]) == 2
        assert actual["receipt"]["score_dtype"] == "float32"
        assert p.validate_prediction_source(actual, smiles="CCO", threshold=threshold, combine_by_target=False, expected=p.capture_prediction_source()) == actual
    finally:
        close_fixture_mmaps(p)


@pytest.mark.parametrize("kind", ["morgan", "maccs"])
@pytest.mark.parametrize("variant", ["missing", "int8", "uint64", "wrong", "negative", "float", "bool", "object", "rank", "length", "npz", "truncated", "huge-header"])
def test_strict_cache_variants_and_owned_handles(tmp_path, monkeypatch, kind, variant):
    p = make_writer_database(tmp_path)
    path = getattr(p, f"{kind}_popcount_path")
    counts = np.load(path)
    if variant == "missing": path.unlink()
    elif variant in ("int8", "uint64", "float", "bool", "object"):
        np.save(path, counts.astype({"float": float, "bool": bool, "object": object}.get(variant, variant)))
    elif variant == "wrong": np.save(path, counts[::-1])
    elif variant == "negative": np.save(path, np.array([-1, 1], dtype=np.int64))
    elif variant == "rank": np.save(path, counts[:, None])
    elif variant == "length": np.save(path, counts[:1])
    elif variant == "npz":
        with path.open("wb") as f: np.savez(f, counts=counts)
    elif variant == "truncated": path.write_bytes(path.read_bytes()[:-1])
    elif variant == "huge-header": np.save(path, np.zeros(2, dtype=[("x" * 17000, "u1")]))
    original_files = {f.name: f.read_bytes() for f in tmp_path.iterdir()}
    real_memmap, opened, handles, load_attempts = np.memmap, [], [], []
    class TrackedMap(real_memmap):
        def __new__(cls, source, *args, **kwargs):
            handles.append(source)
            assert hasattr(source, "read"), "mapping must retain the header fd"
            loaded = super().__new__(cls, source, *args, **kwargs)
            opened.append(loaded)
            return loaded
    def forbidden_load(*args, **kwargs):
        load_attempts.append(True)
        raise AssertionError("strict must not fall back to path-reopening np.load")
    monkeypatch.setattr(np, "memmap", TrackedMap)
    monkeypatch.setattr(np, "load", forbidden_load)
    if variant in ("missing", "int8", "uint64"):
        p.initialize_strict()
        assert p.predict_with_receipt("CCO")["records"][0]["final_similarity"] == 1.
    else:
        with pytest.raises(ValueError): p.initialize_strict()
    assert len(opened) >= 2  # Real fingerprint mappings; never vacuous all([]).
    assert all(a._mmap.closed for a in opened)
    assert handles and all(handle.closed for handle in handles)
    assert not load_attempts
    assert {f.name: f.read_bytes() for f in tmp_path.iterdir()} == original_files


def test_invalid_query_error_is_safe(tmp_path):
    p = make_writer_database(tmp_path)
    p.initialize_strict()
    with pytest.raises(ValueError) as caught:
        p.predict_with_receipt("private-invalid-smiles")
    assert str(caught.value) == owned.INPUT_ERROR


@pytest.mark.parametrize("phase", ["loading", "scoring"])
def test_deadline_expiring_during_final_path_resolution(tmp_path, monkeypatch, phase):
    p = make_writer_database(tmp_path)
    if phase == "scoring":
        p.initialize_strict()
    state = {"clock": 0., "paths": 0}
    monkeypatch.setattr(owned.time, "monotonic", lambda: state["clock"])
    original = p._resolved_source_paths
    def paths():
        result = original()
        state["paths"] += 1
        if state["paths"] == 2:
            state["clock"] = 1000.
        return result
    monkeypatch.setattr(p, "_resolved_source_paths", paths)
    with pytest.raises(TimeoutError):
        if phase == "loading": p.initialize_strict()
        else: p.predict_with_receipt("CCO")
    assert not p._strict_loading and p._strict_active == 0


@pytest.mark.parametrize("kwargs", [{"timeout_seconds": 10**1000}, {"threshold": 10**1000}])
def test_huge_native_number_safe_rejection(tmp_path, kwargs):
    p = make_writer_database(tmp_path)
    p.initialize_strict()
    with pytest.raises(ValueError) as error:
        p.predict_with_receipt("CCO", **kwargs)
    assert str(error.value) == owned.INPUT_ERROR


@pytest.mark.parametrize("phase", ["loading", "scoring"])
@pytest.mark.parametrize("mutation", ["close", "path", "cancel"])
def test_final_callback_mutations_cannot_publish(tmp_path, monkeypatch, phase, mutation):
    p = make_writer_database(tmp_path)
    if phase == "scoring": p.initialize_strict()
    state = {"ready": False, "mutated": False}
    if phase == "loading":
        from src.reverse_target import predictor as module
        original = module.load_owned_source
        def mark(*args, **kwargs):
            result = original(*args, **kwargs)
            state["ready"] = True
            return result
        monkeypatch.setattr(module, "load_owned_source", mark)
    else:
        original = p._predict_core
        def mark(*args, **kwargs):
            result = original(*args, **kwargs)
            state["ready"] = True
            return result
        monkeypatch.setattr(p, "_predict_core", mark)
    def callback():
        if state["ready"] and not state["mutated"]:
            state["mutated"] = True
            if mutation == "close": p.close_strict()
            elif mutation == "path": p.morgan_fp_path = tmp_path / "new.npy"
            else: return True
        return False
    with pytest.raises(asyncio.CancelledError if mutation == "cancel" else owned.ReverseSourceUnavailable):
        if phase == "loading": p.initialize_strict(cancelled=callback)
        else: p.predict_with_receipt("CCO", cancelled=callback)
    assert state["mutated"] and not p._strict_loading and p._strict_active == 0


def test_grouping_ties_filters_and_actual_dtype(tmp_path):
    p = make_writer_database(tmp_path, smiles=("CCO", "CCO", "CCO", "[He]"))
    text = p.training_data_path.read_text().replace("Synthetic B", "Synthetic A").replace("4\tHuman", "4\tMouse")
    p.training_data_path.write_text(text)
    p.initialize_strict()
    try:
        e = p.predict_with_receipt("CCO", threshold=1.)
        assert [r["row_index"] for r in e["records"]] == [0, 2]
        assert [r["similar_count"] for r in e["records"]] == [2, 1]
        assert e["records"] == p.predict("CCO", threshold=1.)
        for s in ("CCO", "[He]"):
            for organism in ("human", "HOMO SAPIENS", "Mouse", "mou"):
                controls = dict(threshold=0, combine_by_target=False, organism_filter=organism)
                e = p.predict_with_receipt(s, **controls)
                assert e["records"] == p.predict(s, **controls)
                assert p.validate_prediction_source(e, smiles=s, expected=p.capture_prediction_source(), **controls) == e
        # Do not infer dtype from the molecule name: compare actual legacy batch
        # vectors and the original formula (He's MACCS is not an all-zero vector).
        for s in ("CCO", "[He]", "C"):
            morgan, maccs = p.compute_query_fingerprints(s)
            m = p.batch_tanimoto_similarity(morgan, p.morgan_fps, p.morgan_popcounts)
            a = p.batch_tanimoto_similarity(maccs, p.maccs_fps, p.maccs_popcounts)
            assert p.predict_with_receipt(s)["receipt"]["score_dtype"] == (m * .7 + a * (1. - .7)).dtype.name
    finally:
        close_fixture_mmaps(p)


def test_empty_organism_and_numeric_looking_string_cells(tmp_path):
    p = make_writer_database(tmp_path)
    text = p.training_data_path.read_text().replace("Synthetic A", "123").replace("CHEMBL1", "456").replace("\tHuman", "\t")
    p.training_data_path.write_text(text)
    p.initialize_strict()
    e = p.predict_with_receipt("CCO")
    assert e["records"][0]["organism"] == ""
    assert e["records"][0]["target_name"] == "123"
    assert p.validate_prediction_source(e, smiles="CCO", expected=p.capture_prediction_source()) == e


@pytest.mark.parametrize("snapshot", [None, {}, ("0" * 32, "0" * 64, "0" * 64), owned.ReverseSourceSnapshot("bad", "0" * 64, "0" * 64)])
def test_exact_expected_snapshot_types(tmp_path, snapshot):
    p = make_writer_database(tmp_path)
    p.initialize_strict()
    e = p.predict_with_receipt("CCO")
    with pytest.raises(ValueError) as error:
        p.validate_prediction_source(e, smiles="CCO", expected=snapshot)
    assert type(error.value) is ValueError and str(error.value) == owned.PROOF_ERROR


@pytest.mark.parametrize("change", ["source", "weights", "configuration", "generation"])
def test_consistent_but_stale_proof_does_not_revoke_current(tmp_path, change):
    p = make_writer_database(tmp_path)
    p.initialize_strict()
    snapshot = p.capture_prediction_source()
    e = p.predict_with_receipt("CCO")
    if change == "source": e["receipt"]["source"]["tsv_sha256"] = "0" * 64
    elif change == "generation": e["receipt"]["source"]["generation_id"] = "0" * 32
    elif change == "configuration": e["receipt"]["configuration_sha256"] = "0" * 64
    else:
        e["receipt"]["weights"] = {"morgan": .5, "maccs": .5}
        # This older case is explicitly self-consistent but ineligible, unlike
        # the unrepaired numeric-identity tampering regressions below.
        e["receipt"]["configuration_sha256"] = owned.configuration_sha256(
            p._resolved_source_paths(), e["receipt"]["weights"], e["receipt"]["weight_resolution"])
    e["receipt"]["source_sha256"] = digest(e["receipt"]["source"])
    before = copy.deepcopy(e)
    with pytest.raises(ValueError if change == "configuration" else owned.ReverseSourceUnavailable) as error:
        p.validate_prediction_source(e, smiles="CCO", expected=snapshot)
    if change == "configuration":
        assert type(error.value) is ValueError and str(error.value) == owned.PROOF_ERROR
    assert e == before and p.capture_prediction_source() == snapshot


def test_explicit_reload_adopts_new_content_without_touching_legacy(tmp_path):
    p = make_writer_database(tmp_path)
    try:
        baseline = p.predict("CCO")
        p.initialize_strict()
        old = p.capture_prediction_source()
        path = p.training_data_path
        path.write_text(path.read_text().replace("Synthetic A", "Synthetic replacement"))
        assert p.predict_with_receipt("CCO")["records"] == baseline
        p.initialize_strict()
        new = p.capture_prediction_source()
        assert new.generation_id != old.generation_id and new.source_sha256 != old.source_sha256
        assert p.predict_with_receipt("CCO")["records"][0]["target_name"] == "Synthetic replacement"
        assert p.predict("CCO") == baseline and p._loaded
        p.close_strict()
        assert p.predict("CCO") == baseline
    finally:
        close_fixture_mmaps(p)


def test_acquisition_callback_is_not_retained(tmp_path):
    p = make_writer_database(tmp_path)
    state = {"done": False}
    def callback():
        assert not state["done"], "acquisition callback leaked into published source"
        return False
    p.initialize_strict(cancelled=callback)
    state["done"] = True
    assert p.predict_with_receipt("CCO")["records"]


def test_logical_budget_rejected_before_fingerprint_open(tmp_path, monkeypatch):
    import builtins
    p = make_writer_database(tmp_path)
    attempts = []
    real_open = builtins.open
    def forbidden(*args, **kwargs):
        attempts.append("np.load")
        raise AssertionError("array opened before budget check")
    def tracked_open(path, *args, **kwargs):
        if str(path).endswith(".npy"):
            attempts.append("open")
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(builtins, "open", tracked_open)
    monkeypatch.setattr(np, "load", forbidden)
    with pytest.raises(ValueError) as error:
        p.initialize_strict(max_snapshot_bytes=4096)
    assert str(error.value) == owned.SOURCE_ERROR
    assert attempts == [], "array access preceded logical budget rejection"


def test_tsv_actual_read_is_bounded(tmp_path, monkeypatch):
    p = make_writer_database(tmp_path)
    import builtins
    real_open = builtins.open
    read_sizes = []
    class Growing:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, count):
            read_sizes.append(count)
            assert 0 < count <= 1024**2
            return b"x" * count
    def open_tsv(path, *args, **kwargs):
        if str(path) == str(p.training_data_path.resolve()): return Growing()
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(builtins, "open", open_tsv)
    with pytest.raises(ValueError): p.initialize_strict(max_snapshot_bytes=4096)
    assert sum(read_sizes) == 4096 // 8 + 1


def test_array_copy_and_popcounts_are_chunk_bounded(tmp_path, monkeypatch):
    p = make_writer_database(tmp_path, smiles=("CCO",) * 4097)
    sums = []
    original_sum = np.sum
    def bounded_sum(a, *args, **kwargs):
        if getattr(a, "ndim", 0) == 2:
            sums.append(len(a))
            assert len(a) <= 4096
        return original_sum(a, *args, **kwargs)
    monkeypatch.setattr(np, "sum", bounded_sum)
    p.initialize_strict()
    assert sums == [4096, 1, 4096, 1]


def test_producer_never_returns_out_of_schema_link_strings(tmp_path):
    p = make_writer_database(tmp_path)
    p.training_data_path.write_text(p.training_data_path.read_text().replace("Synthetic A", "x" * 8192))
    p.initialize_strict()
    with pytest.raises(ValueError) as error:
        p.predict_with_receipt("CCO")
    assert str(error.value) == owned.PROOF_ERROR
    assert p._strict_active == 0


@pytest.mark.parametrize("original,replacement", [(0.0, 0), (0, 0.0), (0.0, -0.0), (1.0, 1)])
def test_receipt_control_numeric_identity_tampering(tmp_path, original, replacement):
    p = make_writer_database(tmp_path)
    p.initialize_strict()
    snapshot = p.capture_prediction_source()
    e = p.predict_with_receipt("CCO", threshold=original)
    assert p.validate_prediction_source(e, smiles="CCO", threshold=original, expected=snapshot) == e
    controls = copy.deepcopy(e["receipt"]["controls"])
    e["receipt"]["controls"]["threshold"] = replacement
    assert e["receipt"]["controls"] == controls  # Python equality hides the change.
    assert digest(e["receipt"]["controls"]) != digest(controls)
    assert e["receipt"]["input_sha256"] == digest(dict(smiles="CCO", controls=controls))
    before = copy.deepcopy(e)
    for caller_threshold in (original, replacement):
        with pytest.raises(ValueError) as error:
            p.validate_prediction_source(e, smiles="CCO", threshold=caller_threshold, expected=snapshot)
        assert type(error.value) is ValueError and str(error.value) == owned.PROOF_ERROR
        # Matching the tampered controls still cannot repair the original hash.
        caller_controls = dict(controls, threshold=caller_threshold)
        with pytest.raises(ValueError) as error:
            owned.validate_envelope(e, smiles="CCO", controls=caller_controls, expected=snapshot)
        assert type(error.value) is ValueError and str(error.value) == owned.PROOF_ERROR
    assert owned.canonical_json(e) == owned.canonical_json(before)
    assert p.capture_prediction_source() == snapshot


@pytest.mark.parametrize("weight,key,replacement", [("0", "morgan", 0), ("1", "morgan", 1), ("0", "maccs", 1), ("1", "maccs", 0), ("0", "morgan", -0.0)])
def test_receipt_weight_numeric_identity_tampering(tmp_path, monkeypatch, weight, key, replacement):
    monkeypatch.setenv("REVERSE_TARGET_MORGAN_WEIGHT", weight)
    p = make_writer_database(tmp_path)
    p.initialize_strict()
    snapshot = p.capture_prediction_source()
    e = p.predict_with_receipt("CCO")
    assert p.validate_prediction_source(e, smiles="CCO", expected=snapshot) == e
    weights = copy.deepcopy(e["receipt"]["weights"])
    e["receipt"]["weights"][key] = replacement
    assert e["receipt"]["weights"] == weights
    assert digest(e["receipt"]["weights"]) != digest(weights)
    assert e["receipt"]["configuration_sha256"] == snapshot.configuration_sha256
    before = copy.deepcopy(e)
    with pytest.raises(ValueError) as error:
        p.validate_prediction_source(e, smiles="CCO", expected=snapshot)
    assert type(error.value) is ValueError and str(error.value) == owned.PROOF_ERROR
    assert owned.canonical_json(e) == owned.canonical_json(before)
    assert p.capture_prediction_source() == snapshot


@pytest.mark.parametrize("threshold", [0, 0.0, -0.0, 1, 1.0])
def test_native_threshold_identity_preserved_with_legacy_parity(tmp_path, threshold):
    p = make_writer_database(tmp_path)
    p.initialize_strict()
    try:
        e = p.predict_with_receipt("CCO", threshold=threshold)
        assert e["records"] == p.predict("CCO", threshold=threshold)
        assert owned.canonical_json(e["receipt"]["controls"]["threshold"]) == owned.canonical_json(threshold)
        assert p.validate_prediction_source(e, smiles="CCO", threshold=threshold, expected=p.capture_prediction_source()) == e
    finally:
        close_fixture_mmaps(p)


@pytest.mark.parametrize("version,length_bytes", [((1, 0), 2), ((2, 0), 4), ((3, 0), 4)])
def test_npy_declared_header_cap_precedes_body_read(tmp_path, monkeypatch, version, length_bytes):
    import builtins
    p = make_writer_database(tmp_path)
    # Only a tiny prefix exists: no large synthetic file is needed to expose an
    # unbounded read request from an attacker-controlled declaration.
    p.morgan_fp_path.write_bytes(np.lib.format.magic(*version) + (20000).to_bytes(length_bytes, "little"))
    real_open, reads, handles = builtins.open, [], []
    class Tracked:
        def __init__(self, handle): self.handle = handle
        def __enter__(self): return self
        def __exit__(self, *args): self.handle.close()
        def __getattr__(self, name): return getattr(self.handle, name)
        def read(self, size=-1):
            reads.append((self.handle.tell(), size))
            return self.handle.read(size)
    def tracked(path, *args, **kwargs):
        handle = real_open(path, *args, **kwargs)
        if str(path) == str(p.morgan_fp_path):
            handles.append(handle)
            return Tracked(handle)
        return handle
    monkeypatch.setattr(builtins, "open", tracked)
    with pytest.raises(ValueError): p.initialize_strict()
    assert reads and all(size >= 0 and offset + size <= 8 + length_bytes for offset, size in reads), reads
    assert all(handle.closed for handle in handles)


@pytest.mark.parametrize("version", [(1, 0), (2, 0), (3, 0)])
@pytest.mark.parametrize("fortran", [False, True])
def test_npy_header_and_mapping_share_open_identity(tmp_path, monkeypatch, version, fortran):
    import builtins
    p = make_writer_database(tmp_path)
    original = np.load(p.morgan_fp_path)
    with p.morgan_fp_path.open("wb") as stream:
        np.lib.format.write_array(stream, np.asfortranarray(original) if fortran else original, version=version, allow_pickle=False)
    replacement = tmp_path / "replacement.npy"
    np.save(replacement, original[::-1])
    real_open, opens, handles = builtins.open, [], []
    def replaced_path_on_reopen(path, *args, **kwargs):
        if str(path) == str(p.morgan_fp_path):
            # Deterministic path-replacement seam, including on Windows where
            # renaming an open file may be denied. Both files are real writer data.
            opens.append(str(path))
            handle = real_open(path if len(opens) == 1 else replacement, *args, **kwargs)
            handles.append(handle)
            return handle
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(builtins, "open", replaced_path_on_reopen)
    array = owned._read_array(p.morgan_fp_path, original.shape, check=lambda: None)
    np.testing.assert_array_equal(array, original)
    assert len(opens) == 1
    assert array.flags.owndata and array.flags.c_contiguous
    assert all(handle.closed for handle in handles)


def _generation_weakrefs(p):
    return [weakref.ref(p._strict_source), weakref.ref(p._strict_source.frame)] + [
        weakref.ref(getattr(p._strict_source, key))
        for key in ("morgan", "maccs", "morgan_popcounts", "maccs_popcounts")]


@pytest.mark.parametrize("failure", ["late-close", "core-chain", "core-cancel"])
def test_failed_scoring_future_releases_retired_arrays(tmp_path, monkeypatch, failure):
    p = make_writer_database(tmp_path)
    p.initialize_strict()
    refs = _generation_weakrefs(p)
    if failure == "late-close":
        original = p._predict_core
        def fail(*args, **kwargs):
            result = original(*args, **kwargs)
            p.close_strict()
            return result
        monkeypatch.setattr(p, "_predict_core", fail)
    else:
        original = p.batch_tanimoto_similarity
        def fail(*args, **kwargs):
            scores = original(*args, **kwargs)
            refs.append(weakref.ref(scores))
            p.close_strict()
            if failure == "core-cancel": raise asyncio.CancelledError()
            try:
                raise ValueError("synthetic inner error")
            except ValueError as inner:
                raise RuntimeError("synthetic outer error") from inner
        monkeypatch.setattr(p, "batch_tanimoto_similarity", fail)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(p.predict_with_receipt, "CCO")
        error = future.exception(timeout=10)  # Deliberately retain Future + chain.
    assert isinstance(error, asyncio.CancelledError if failure == "core-cancel" else Exception)
    assert p._strict_active == 0
    p.initialize_strict()
    healthy = p.capture_prediction_source()
    gc.collect()
    assert all(ref() is None for ref in refs), "retired generation/core arrays survived failed Future"
    assert future.exception() is error and p.capture_prediction_source() == healthy


@pytest.mark.parametrize("failure", ["late-candidate", "loader-row", "loader-cancel"])
def test_failed_acquisition_exception_releases_owned_arrays(tmp_path, monkeypatch, failure):
    from src.reverse_target import predictor as module
    p = make_writer_database(tmp_path)
    refs = []
    original_read_csv = owned.pd.read_csv
    def track_frame(*args, **kwargs):
        frame = original_read_csv(*args, **kwargs)
        refs.append(weakref.ref(frame))
        return frame
    monkeypatch.setattr(owned.pd, "read_csv", track_frame)
    if failure == "late-candidate":
        original = module.load_owned_source
        def fail(*args, **kwargs):
            candidate = original(*args, **kwargs)
            refs.extend([weakref.ref(candidate), weakref.ref(candidate.frame), weakref.ref(candidate.morgan), weakref.ref(candidate.maccs)])
            p.close_strict()
            return candidate
        monkeypatch.setattr(module, "load_owned_source", fail)
    else:
        original = owned._read_array
        def track(*args, **kwargs):
            result = original(*args, **kwargs)
            refs.append(weakref.ref(result))
            return result
        monkeypatch.setattr(owned, "_read_array", track)
        original_query = p.compute_query_fingerprints
        def fail(smiles):
            result = original_query(smiles)
            if failure == "loader-cancel": raise asyncio.CancelledError()
            raise ValueError("synthetic row failure")
        monkeypatch.setattr(p, "compute_query_fingerprints", fail)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(p.initialize_strict)
        error = future.exception(timeout=10)
    assert isinstance(error, asyncio.CancelledError if failure == "loader-cancel" else ValueError)
    assert not p._strict_loading and p._strict_active == 0
    # End fault injection, retain only weak references and the failed evidence.
    if failure == "late-candidate": monkeypatch.setattr(module, "load_owned_source", original)
    else:
        monkeypatch.setattr(owned, "_read_array", original)
        monkeypatch.setattr(p, "compute_query_fingerprints", original_query)
    monkeypatch.setattr(owned.pd, "read_csv", original_read_csv)
    p.initialize_strict()
    gc.collect()
    assert refs and all(ref() is None for ref in refs), "failed acquisition traceback retained owned arrays"
    assert future.exception() is error


@pytest.mark.parametrize("field", ["smiles", "organism_filter"])
@pytest.mark.parametrize("value", ["\ud800", "\udfff", "CCO\ud800"])
def test_unpaired_surrogate_rejected_before_scoring(tmp_path, monkeypatch, field, value):
    p = make_writer_database(tmp_path)
    p.initialize_strict()
    original, calls = p._predict_core, []
    def track(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(p, "_predict_core", track)
    with pytest.raises(ValueError) as error:
        p.predict_with_receipt(**({"smiles": "CCO"} | {field: value}))
    assert not calls, "invalid Unicode reached scientific scoring"
    assert type(error.value) is ValueError and str(error.value) == owned.INPUT_ERROR
    assert p._strict_active == 0


def test_budget_guard_detects_normalized_boundary_violation(tmp_path, monkeypatch):
    p = make_writer_database(tmp_path)
    def early_array_open(*args, **kwargs):
        # Fault injection demonstrates the test guard, not a scientific positive:
        # the loader normalizes this failure to exactly SOURCE_ERROR.
        np.load(p.morgan_fp_path)
    monkeypatch.setattr(owned.pd, "read_csv", early_array_open)
    with pytest.raises(AssertionError):
        test_logical_budget_rejected_before_fingerprint_open(tmp_path, monkeypatch)


@pytest.mark.parametrize("action", ["capture", "validate"])
def test_failed_source_accessor_future_does_not_pin_retired_generation(tmp_path, action):
    p = make_writer_database(tmp_path)
    p.initialize_strict()
    refs = _generation_weakrefs(p)
    expected = p.capture_prediction_source()
    envelope = p.predict_with_receipt("CCO")
    if action == "capture": p.morgan_fp_path = tmp_path / "changed.npy"
    else: envelope["receipt"]["configuration_sha256"] = "0" * 64
    with ThreadPoolExecutor(max_workers=1) as pool:
        if action == "capture": future = pool.submit(p.capture_prediction_source)
        else: future = pool.submit(p.validate_prediction_source, envelope, smiles="CCO", expected=expected)
        error = future.exception(timeout=10)
    assert isinstance(error, ValueError)
    p.close_strict()
    p.morgan_fp_path = tmp_path / "morgan_fingerprints.npy"
    p.initialize_strict()
    gc.collect()
    assert all(ref() is None for ref in refs), "failed accessor pinned retired source"
    assert future.exception() is error


@pytest.mark.parametrize("version,length_bytes", [((1, 0), 2), ((2, 0), 4), ((3, 0), 4)])
def test_valid_npy_at_header_cap_and_read_only_fd(tmp_path, monkeypatch, version, length_bytes):
    import builtins
    p = make_writer_database(tmp_path)
    original = np.load(p.morgan_fp_path)
    description = repr(dict(descr="|u1", fortran_order=False, shape=original.shape)).encode("ascii")
    body = description + b" " * (16384 - len(description) - 1) + b"\n"
    p.morgan_fp_path.write_bytes(np.lib.format.magic(*version) + len(body).to_bytes(length_bytes, "little") + body + original.tobytes())
    real_open, requests, handles = builtins.open, [], []
    class Tracked:
        def __init__(self, handle): self.handle = handle
        def __enter__(self): return self
        def __exit__(self, *args): self.handle.close()
        def __getattr__(self, name): return getattr(self.handle, name)
        def read(self, size=-1):
            requests.append(size)
            return self.handle.read(size)
    def track(path, *args, **kwargs):
        handle = real_open(path, *args, **kwargs)
        if str(path) == str(p.morgan_fp_path):
            handles.append(handle)
            assert not handle.writable()
            return Tracked(handle)
        return handle
    monkeypatch.setattr(builtins, "open", track)
    p.initialize_strict()
    assert requests == [8, length_bytes, 16384]
    assert len(handles) == 1 and handles[0].closed
    assert p.predict_with_receipt("CCO")["records"][0]["final_similarity"] == 1.


@pytest.mark.parametrize("invalid", ["rank", "shape", "dtype", "object", "npz", "version"])
def test_bad_npy_metadata_precedes_mapping_and_private_allocation(tmp_path, monkeypatch, invalid):
    p = make_writer_database(tmp_path)
    path = p.morgan_fp_path
    if invalid == "rank": np.save(path, np.zeros(4096, dtype=np.uint8))
    elif invalid == "shape": np.save(path, np.zeros((2, 2047), dtype=np.uint8))
    elif invalid == "dtype": np.save(path, np.zeros((2, 2048), dtype=float))
    elif invalid == "object": np.save(path, np.zeros((2, 2048), dtype=object))
    elif invalid == "npz":
        with path.open("wb") as stream: np.savez(stream, fp=np.zeros((2, 2048), dtype=np.uint8))
    else: path.write_bytes(np.lib.format.magic(4, 0))
    calls = []
    real_memmap = np.memmap
    class ForbiddenMap(real_memmap):
        def __new__(cls, *args, **kwargs):
            calls.append("mapping")
            raise AssertionError("mapping invalid metadata")
    def forbidden_empty(*args, **kwargs):
        calls.append("allocation")
        raise AssertionError("allocating invalid metadata")
    monkeypatch.setattr(np, "memmap", ForbiddenMap)
    monkeypatch.setattr(np, "empty", forbidden_empty)
    with pytest.raises(ValueError):
        owned._read_array(path, (2, 2048), check=lambda: None)
    assert calls == []


def test_actual_file_replacement_adopted_only_on_explicit_reload(tmp_path):
    p = make_writer_database(tmp_path)
    p.initialize_strict()
    first = p.capture_prediction_source()
    before = p.predict_with_receipt("CCO")["records"]
    # Overwrite all writer files after acquisition; this also proves no owned
    # file/mapping locks were left open on Windows.
    make_writer_database(tmp_path, smiles=("CCC", "CCO"))
    assert p.predict_with_receipt("CCO")["records"] == before
    assert p.capture_prediction_source() == first
    p.initialize_strict()
    assert p.capture_prediction_source().generation_id != first.generation_id
    record = p.predict_with_receipt("CCO")["records"][0]
    assert (record["canonical_smiles"], record["target_name"], record["final_similarity"]) == ("CCO", "Synthetic B", 1.)


def test_failed_copy_releases_partial_owned_allocation(tmp_path, monkeypatch):
    p = make_writer_database(tmp_path)
    original_empty, refs = np.empty, []
    armed = {"fail": False}
    def track(shape, *args, **kwargs):
        array = original_empty(shape, *args, **kwargs)
        if shape == (2, 2048):
            refs.append(weakref.ref(array))
            armed["fail"] = True
        return array
    monkeypatch.setattr(np, "empty", track)
    def cancelled(): return armed["fail"]
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(p.initialize_strict, cancelled=cancelled)
        error = future.exception(timeout=10)
    assert isinstance(error, asyncio.CancelledError)
    assert not p._strict_loading
    monkeypatch.setattr(np, "empty", original_empty)
    p.initialize_strict()
    gc.collect()
    assert refs and all(ref() is None for ref in refs)
    assert future.exception() is error


def test_failure_cleanup_preserves_healthy_publication_and_caller_locals(tmp_path, monkeypatch):
    p = make_writer_database(tmp_path)
    p.initialize_strict()
    source_refs = _generation_weakrefs(p)
    snapshot = p.capture_prediction_source()
    caller_array = np.arange(4)
    caller_ref = weakref.ref(caller_array)
    original = p._predict_core
    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("synthetic failure without retiring publication")
    monkeypatch.setattr(p, "_predict_core", fail)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(p.predict_with_receipt, "CCO")
        error = future.exception(timeout=10)
    assert isinstance(error, RuntimeError)
    assert p.capture_prediction_source() == snapshot and p._strict_active == 0
    assert all(ref() is not None for ref in source_refs)
    assert caller_ref() is caller_array
    np.testing.assert_array_equal(caller_array, [0, 1, 2, 3])
    p.close_strict()
    p.initialize_strict()
    gc.collect()
    assert all(ref() is None for ref in source_refs)
    assert future.exception() is error


def test_predict_pre_admission_path_drift_future_releases_source(tmp_path, monkeypatch):
    p = make_writer_database(tmp_path)
    p.initialize_strict()
    refs = _generation_weakrefs(p)
    original, calls = p._predict_core, []
    def track(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(p, "_predict_core", track)
    original_path = p.morgan_fp_path
    p.morgan_fp_path = tmp_path / "drifted.npy"
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(p.predict_with_receipt, "CCO")
        error = future.exception(timeout=10)
    assert isinstance(error, owned.ReverseSourceUnavailable)
    assert not calls and p._strict_active == 0 and p._strict_source is None
    p.morgan_fp_path = original_path
    p.initialize_strict()
    gc.collect()
    assert all(ref() is None for ref in refs), "pre-admission failure retained revoked source"
    assert future.exception() is error


def test_pre_admission_failure_never_releases_other_calls_active_slot(tmp_path, monkeypatch):
    p = make_writer_database(tmp_path)
    p.initialize_strict()
    refs = _generation_weakrefs(p)
    entered, release = threading.Event(), threading.Event()
    original = p._predict_core
    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(10), "owner release missing"
        return original(*args, **kwargs)
    monkeypatch.setattr(p, "_predict_core", blocked)
    original_path = p.morgan_fp_path
    with ThreadPoolExecutor(max_workers=2) as pool:
        owner = pool.submit(p.predict_with_receipt, "CCO")
        try:
            assert entered.wait(10), "owner failed to acquire slot"
            assert p._strict_active == 1
            p.morgan_fp_path = tmp_path / "drifted.npy"
            rejected = pool.submit(p.predict_with_receipt, "CCC")
            rejected_error = rejected.exception(timeout=10)
            assert isinstance(rejected_error, owned.ReverseSourceUnavailable)
            assert p._strict_active == 1, "nonowner decremented the active owner"
            p.morgan_fp_path = original_path
            with pytest.raises(owned.ReverseSourceUnavailable): p.initialize_strict()
            assert p._strict_active == 1
        finally:
            release.set()
        owner_error = owner.exception(timeout=10)
    assert isinstance(owner_error, owned.ReverseSourceUnavailable)
    assert p._strict_active == 0
    p.initialize_strict()
    gc.collect()
    assert all(ref() is None for ref in refs)
    assert owner.exception() is owner_error and rejected.exception() is rejected_error
