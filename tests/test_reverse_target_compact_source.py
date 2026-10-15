"""Real synthetic files for compact owned-source correctness; no host assets."""

import csv
import gc
import importlib.util
import io
import struct
from dataclasses import replace
from pathlib import Path
import weakref

import numpy as np
import pytest
from rdkit import rdBase

from src.reverse_target.owned_source import _array_hash, operation_check
from src.reverse_target.predictor import ReverseTargetPredictor


def write_source(directory, *, cached=True, fortran=False, columns=None, rows=None):
    directory.mkdir(parents=True, exist_ok=True)
    columns = columns or ["molecule_chembl_id", "canonical_smiles", "target_name",
               "standard_type", "standard_value", "organism", "target_chembl_id"]
    rows = rows if rows is not None else [
        ["CHEMBL1", "CCO", "Synthetic A", "IC50", "1", "Human", "CHEMBL101"],
        ["CHEMBL2", "CCC", "Synthetic A", "Ki", "2", "Mouse", "CHEMBL102"],
        ["CHEMBL3", "CCO", "Synthetic B", "IC50", "3", "Human", "CHEMBL103"],
    ]
    with (directory / "chembl_data_with_fps.tsv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow(columns)
        writer.writerows(rows)
    producer = ReverseTargetPredictor(directory)
    with rdBase.BlockLogs():
        fingerprints = [producer.compute_query_fingerprints(row[1]) for row in rows]
    arrays = {}
    for index, name in enumerate(("morgan", "maccs")):
        array = np.stack([pair[index] for pair in fingerprints])
        if fortran:
            array = np.asfortranarray(array)
        arrays[name] = array
        np.save(getattr(producer, name + "_fp_path"), array)
        if cached:
            np.save(getattr(producer, name + "_popcount_path"),
                    array.sum(axis=1, dtype=np.uint32).astype(np.uint16))
    return producer, arrays


def compact_module():
    assert importlib.util.find_spec("src.reverse_target.compact_source") is not None, (
        "compact source acquisition is not implemented")
    from src.reverse_target import compact_source
    return compact_source


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("fortran", [False, True])
def test_compact_source_matches_all_logical_arrays(tmp_path, cached, fortran):
    producer, arrays = write_source(tmp_path / "source", cached=cached, fortran=fortran)
    module = compact_module()
    check = operation_check(30, None)
    calls = []

    def fingerprints(smiles):
        calls.append(smiles)
        with rdBase.BlockLogs():
            return producer.compute_query_fingerprints(smiles)

    source = module.load_compact_source(producer._resolved_source_paths(),
        module.CompactSourcePolicy(), check, fingerprints)
    assert calls == ["CCO", "CCC", "CCO"]  # Full row verification, no unique-SMILES shortcut.
    assert source.descriptor["row_alignment_verified"] is True
    assert source.descriptor["row_count"] == 3
    assert source.descriptor["array_codec"] == "reverse-array-v1"
    assert source.frame.iloc[1]["organism"] == "Mouse"
    assert source.frame.iloc[0]["standard_value"] == 1.0
    for name, original in arrays.items():
        packed = getattr(source, name)
        assert packed.shape == original.shape
        assert np.array_equal(packed.block(0, 3), original)
        assert source.descriptor[name + "_sha256"] == _array_hash(original, check)
        counts = original.sum(axis=1, dtype=np.uint32).astype(np.uint16)
        assert np.array_equal(getattr(source, name + "_popcounts"), counts)
        assert source.descriptor[name + "_popcounts_sha256"] == _array_hash(counts, check)
        assert source.descriptor["cache_origins"][name] == ("verified_cache" if cached else "computed")
        # Replacing disk bytes after acquisition cannot alter the private source.
        np.save(getattr(producer, name + "_fp_path"), np.zeros_like(original))
        assert np.array_equal(packed.block(0, 3), original)


@pytest.mark.parametrize("kind", ["morgan", "maccs"])
def test_compact_source_rejects_one_corrupt_fingerprint_bit(tmp_path, kind):
    producer, arrays = write_source(tmp_path / "source")
    module = compact_module()
    changed = arrays[kind].copy()
    changed[1, 7] ^= 1
    np.save(getattr(producer, kind + "_fp_path"), changed)
    with pytest.raises(ValueError, match="Invalid reverse-target owned source"):
        module.load_compact_source(producer._resolved_source_paths(), module.CompactSourcePolicy(),
                                  operation_check(30, None), producer.compute_query_fingerprints)


def test_compact_budget_rejects_before_reading_fingerprint_payload(tmp_path, monkeypatch):
    producer, _ = write_source(tmp_path / "source")
    module = compact_module()
    policy = replace(module.CompactSourcePolicy(), max_snapshot_bytes=1)
    original_open = open
    fingerprint_opens = []

    def checked_open(file, *args, **kwargs):
        if Path(file).suffix == ".npy":
            fingerprint_opens.append(file)
        return original_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", checked_open)
    with pytest.raises(ValueError):
        module.load_compact_source(producer._resolved_source_paths(), policy,
                                  operation_check(30, None), producer.compute_query_fingerprints)
    assert not fingerprint_opens, "budget failure must precede fingerprint open"


def load(producer, *, policy=None, fingerprint=None, check=None, progress=None):
    module = compact_module()
    with rdBase.BlockLogs():
        return module.load_compact_source(producer._resolved_source_paths(),
            policy or module.CompactSourcePolicy(), check or operation_check(30, None),
            fingerprint or producer.compute_query_fingerprints, progress=progress)


@pytest.mark.parametrize("damage", ["swapped", "nonbinary", "short", "wrong_width", "float", "object"])
def test_rejects_fingerprint_damage(tmp_path, damage):
    producer, arrays = write_source(tmp_path / "source")
    original = arrays["morgan"].copy()
    if damage == "swapped":
        original[[0, 1]] = original[[1, 0]]
    elif damage == "nonbinary":
        original[0, 0] = 2
    elif damage == "short":
        original = original[:2]
    elif damage == "wrong_width":
        original = original[:, :166]
    else:
        original = original.astype(float if damage == "float" else object)
    np.save(producer.morgan_fp_path, original)
    with pytest.raises(ValueError, match="Invalid reverse-target owned source"):
        load(producer)


@pytest.mark.parametrize("damage", ["negative", "overflow", "float", "misaligned", "wrong_shape"])
def test_rejects_original_cached_counts_before_narrowing(tmp_path, damage):
    producer, arrays = write_source(tmp_path / "source")
    counts = arrays["morgan"].sum(axis=1, dtype=np.int64)
    if damage == "negative":
        counts[0] = -1
    elif damage == "overflow":
        counts[0] += 65536
    elif damage == "float":
        counts = counts.astype(float)
    elif damage == "misaligned":
        counts[[0, 1]] = counts[[1, 0]]
    else:
        counts = counts[:, None]
    np.save(producer.morgan_popcount_path, counts)
    with pytest.raises(ValueError, match="Invalid reverse-target owned source"):
        load(producer)


@pytest.mark.parametrize("damage", ["huge_header", "unsupported_version", "truncated", "wrong_magic"])
def test_rejects_npy_header_without_payload_allocation(tmp_path, damage, monkeypatch):
    producer, _ = write_source(tmp_path / "source")
    module = compact_module()
    path = Path(producer.morgan_fp_path)
    if damage == "huge_header":
        data = b"\x93NUMPY\x02\x00" + struct.pack("<I", 2**30)
    elif damage == "unsupported_version":
        data = b"\x93NUMPY\x04\x00"
    elif damage == "truncated":
        data = path.read_bytes()[:-1]
    else:
        data = b"INVALID!"
    path.write_bytes(data)
    monkeypatch.setattr(module.np, "memmap", lambda *a, **k: pytest.fail("invalid NPY was mapped"))
    with pytest.raises(ValueError, match="Invalid reverse-target owned source"):
        load(producer)


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize("bom", [False, True])
def test_tsv_quoted_multiline_bom_and_final_line_parity(tmp_path, newline, bom):
    producer, _ = write_source(tmp_path / "source")
    path = Path(producer.training_data_path)
    with path.open(encoding="utf-8", newline="") as stream:
        records = list(csv.reader(stream, delimiter="\t"))
    records[1][2] = 'Synthetic "quoted"\tA\r\ncontinuation'
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter="\t", lineterminator=newline)
    writer.writerows(records)
    raw = buffer.getvalue()[:-len(newline)].encode("utf-8-sig" if bom else "utf-8")
    path.write_bytes(raw)
    source = load(producer)
    assert source.frame.iloc[0]["target_name"] == records[1][2]
    import hashlib
    assert source.descriptor["tsv_sha256"] == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize("damage", ["duplicate_header", "empty_header", "nul", "wide", "short",
                                    "overlong_cell", "overlong_record", "invalid_utf8", "bad_quote",
                                    "nonfinite", "negative", "blank_smiles", "smiles_whitespace"])
def test_rejects_tsv_damage(tmp_path, damage):
    producer, _ = write_source(tmp_path / "source")
    path = Path(producer.training_data_path)
    raw = path.read_bytes()
    if damage == "duplicate_header":
        raw = raw.replace(b"target_name", b"organism", 1)
    elif damage == "empty_header":
        raw = raw.replace(b"target_chembl_id", b"", 1)
    elif damage == "nul":
        raw = raw.replace(b"Synthetic A", b"Synthetic\0A", 1)
    elif damage == "wide":
        raw = raw.replace(b"CHEMBL101\n", b"CHEMBL101\textra\n", 1)
    elif damage == "short":
        raw = raw.replace(b"\tCHEMBL101", b"", 1)
    elif damage == "overlong_cell":
        raw = raw.replace(b"Synthetic A", b"x" * 8193, 1)
    elif damage == "overlong_record":
        raw = raw.replace(b"Synthetic A", b'"' + b"x\n" * (128 * 1024) + b'"', 1)
    elif damage == "invalid_utf8":
        raw = raw.replace(b"Synthetic A", b"\xff", 1)
    elif damage == "bad_quote":
        raw += b'"unclosed'
    elif damage in ("nonfinite", "negative"):
        raw = raw.replace(b"\t1\tHuman", b"\t" + (b"NaN" if damage == "nonfinite" else b"-1") + b"\tHuman", 1)
    else:
        raw = raw.replace(b"\tCCO\t", b"\t" + (b"" if damage == "blank_smiles" else b"CC O") + b"\t", 1)
    path.write_bytes(raw)
    with pytest.raises(ValueError, match="Invalid reverse-target owned source"):
        load(producer)


def test_tsv_two_pass_drift_is_rejected(tmp_path, monkeypatch):
    producer, _ = write_source(tmp_path / "source")
    module = compact_module()
    records = module._records
    calls = 0

    def drift(stream, policy, check):
        nonlocal calls
        calls += 1
        if calls == 2:
            path = Path(producer.training_data_path)
            path.write_bytes(path.read_bytes().replace(b"Synthetic A", b"Synthetic Z", 1))
        return records(stream, policy, check)

    monkeypatch.setattr(module, "_records", drift)
    with pytest.raises(ValueError, match="Invalid reverse-target owned source"):
        load(producer)


def test_all_reads_bounded_and_mappings_closed_on_publication(tmp_path, monkeypatch):
    producer, arrays = write_source(tmp_path / "source", fortran=True)
    module = compact_module()
    original_open, original_map = open, np.memmap
    mappings, reads = [], []

    class CheckedFile:
        def __init__(self, stream):
            self.stream = stream
        def __getattr__(self, name):
            return getattr(self.stream, name)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.stream.close()
        def read(self, size=-1):
            assert 0 <= size <= 1024**2
            reads.append(size)
            return self.stream.read(size)

    def mapped(*args, **kwargs):
        result = original_map(*args, **kwargs)
        mappings.append(result)
        return result

    monkeypatch.setattr("builtins.open", lambda *a, **k: CheckedFile(original_open(*a, **k)))
    monkeypatch.setattr(module.np, "memmap", mapped)
    source = load(producer, policy=replace(module.CompactSourcePolicy(), chunk_rows=2))
    assert reads and all(mapping._mmap.closed for mapping in mappings)
    assert np.array_equal(source.morgan.block(0, 2), arrays["morgan"][:2])
    with pytest.raises(ValueError):
        source.morgan.block(0, 3)
    assert not np.any(source.maccs._packed[:, -1] & 3)
    for array in (source.morgan._packed, source.maccs._packed, source.morgan_popcounts):
        assert not array.flags.writeable
        assert not isinstance(array, original_map)


def test_retained_failure_does_not_retain_candidate_arrays(tmp_path, monkeypatch):
    producer, _ = write_source(tmp_path / "source")
    module = compact_module()
    original = np.empty
    references = []

    def allocate(*args, **kwargs):
        result = original(*args, **kwargs)
        references.append(weakref.ref(result))
        return result

    def fail(smiles):
        raise RuntimeError("rejected source text must not escape")

    monkeypatch.setattr(module.np, "empty", allocate)
    with pytest.raises(ValueError, match="Invalid reverse-target owned source") as retained:
        load(producer, fingerprint=fail)
    gc.collect()
    assert references and all(reference() is None for reference in references)
    assert "rejected source text" not in str(retained.value)


def test_compact_source_reports_bounded_logical_allocations(tmp_path):
    producer, _ = write_source(tmp_path / "source")
    source = load(producer)
    usage = source.frame.allocation_usage
    policy = compact_module().CompactSourcePolicy()
    assert 0 < usage["snapshot_reserved_bytes"] <= policy.max_snapshot_bytes
    assert usage["snapshot_reserved_bytes"] <= usage["acquisition_peak_reserved_bytes"] <= policy.max_acquisition_bytes


@pytest.mark.parametrize("length", [0, 1, 3])
def test_fingerprint_producer_must_return_both_exact_arrays(tmp_path, length):
    producer, _ = write_source(tmp_path / "source")
    def incomplete(smiles):
        morgan, maccs = producer.compute_query_fingerprints(smiles)
        return [morgan, maccs, morgan][:length]
    with pytest.raises(ValueError, match="Invalid reverse-target owned source"):
        load(producer, fingerprint=incomplete)


@pytest.mark.parametrize("field,value", [
    ("chunk_rows", True), ("chunk_rows", 4097), ("max_rows", 2),
    ("max_columns", 6), ("max_tsv_bytes", 1), ("max_acquisition_bytes", 1),
])
def test_policy_limits_reject_instead_of_truncating(tmp_path, field, value):
    producer, _ = write_source(tmp_path / "source")
    with pytest.raises(ValueError, match="Invalid reverse-target owned source"):
        load(producer, policy=replace(compact_module().CompactSourcePolicy(), **{field: value}))


def test_cancellation_after_native_fingerprint_releases_private_candidate(tmp_path):
    import asyncio
    producer, _ = write_source(tmp_path / "source")
    cancelled = False
    def fingerprint(smiles):
        nonlocal cancelled
        result = producer.compute_query_fingerprints(smiles)
        cancelled = True
        return result
    with pytest.raises(asyncio.CancelledError):
        load(producer, fingerprint=fingerprint,
             check=operation_check(30, lambda: cancelled))


@pytest.mark.parametrize("version", [(1, 0), (2, 0), (3, 0)])
def test_supported_npy_versions_keep_logical_digest(tmp_path, version):
    producer, arrays = write_source(tmp_path / "source")
    for kind, array in arrays.items():
        with Path(getattr(producer, kind + "_fp_path")).open("wb") as stream:
            np.lib.format.write_array(stream, array, version=version, allow_pickle=False)
    source = load(producer)
    assert source.descriptor["morgan_sha256"] == _array_hash(arrays["morgan"], operation_check(30, None))


def test_long_retained_headers_are_counted_before_column_allocation(tmp_path, monkeypatch):
    import sys
    producer, _ = write_source(tmp_path / "source")
    path = Path(producer.training_data_path)
    with path.open(encoding="utf-8", newline="") as stream:
        records = list(csv.reader(stream, delimiter="\t"))
    headers = [str(index) + "x" * 8190 for index in range(25)]
    assert sum(sys.getsizeof(value) for value in headers) > 128 * 1024
    records[0].extend(headers)
    for row in records[1:]:
        row.extend(["value"] * 25)
    with path.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream, delimiter="\t", lineterminator="\n").writerows(records)
    module = compact_module()
    monkeypatch.setattr(module.np, "empty", lambda *a, **k: pytest.fail("unbudgeted headers retained before arrays"))
    with pytest.raises(ValueError, match="Invalid reverse-target owned source"):
        load(producer, policy=replace(module.CompactSourcePolicy(), max_snapshot_bytes=128 * 1024))


def test_final_identity_check_follows_hash_callbacks(tmp_path, monkeypatch):
    producer, _ = write_source(tmp_path / "source")
    module = compact_module()
    original_hash = module._array_hash
    changed = False
    def drift(array, check):
        nonlocal changed
        digest = original_hash(array, check)
        if not changed:
            changed = True
            path = Path(producer.training_data_path)
            path.write_bytes(path.read_bytes().replace(b"Synthetic A", b"Synthetic Z", 1))
        return digest
    monkeypatch.setattr(module, "_array_hash", drift)
    with pytest.raises(ValueError, match="Invalid reverse-target owned source"):
        load(producer)


def strict_pair(tmp_path, *, columns=None, rows=None, policy=None):
    old, _ = write_source(tmp_path / "source", columns=columns, rows=rows)
    compact = ReverseTargetPredictor(old.data_dir)
    assert callable(getattr(compact, "initialize_compact_strict", None)), (
        "compact initialization is not implemented")
    with rdBase.BlockLogs():
        old.initialize_strict()
        compact.initialize_compact_strict(policy=policy)
    return old, compact


@pytest.mark.parametrize("combine", [False, True])
@pytest.mark.parametrize("threshold", [0.0, 0.6, 1.0])
def test_compact_strict_ranking_and_receipt_parity(tmp_path, combine, threshold):
    old, compact = strict_pair(tmp_path)
    controls = dict(threshold=threshold, top_k=10,
                    combine_by_target=combine, organism_filter="")
    with rdBase.BlockLogs():
        a = old.predict_with_receipt("CCO", **controls)
        b = compact.predict_with_receipt("CCO", **controls)
    assert b["records"] == a["records"]
    assert b["receipt"]["score_dtype"] == a["receipt"]["score_dtype"]
    for key in ("tsv", "morgan", "maccs", "morgan_popcounts", "maccs_popcounts"):
        assert b["receipt"]["source"][key + "_sha256"] == a["receipt"]["source"][key + "_sha256"]
    assert b["receipt"]["source"]["generation_id"] != a["receipt"]["source"]["generation_id"]
    snapshot = compact.capture_prediction_source()
    assert compact.validate_prediction_source(b, smiles="CCO", expected=snapshot, **controls) == b


@pytest.mark.parametrize("combine", [False, True])
@pytest.mark.parametrize("smiles", ["CCO", "[He]", "N#N"])
@pytest.mark.parametrize("weight", [None, "0", "1", "1.25", "-0.25", "bad"])
def test_exact_scoring_parity_across_blocks_and_identity(tmp_path, monkeypatch, combine, smiles, weight):
    if weight is None:
        monkeypatch.delenv("REVERSE_TARGET_MORGAN_WEIGHT", raising=False)
    else:
        monkeypatch.setenv("REVERSE_TARGET_MORGAN_WEIGHT", weight)
    columns = ["molecule_chembl_id", "canonical_smiles", "target_name", "standard_type",
               "standard_value", "organism", "target_chembl_id", "taxon_id", "assay_id", "target_id"]
    rows = [
        ["001", "CCC", "A", "Ki", "1", "Human", "CHEMBL101", "9606", "0001", "raw1"],
        ["002", "CCO", "A", "Ki", "2", "Mouse", "CHEMBL101", "10090", "0002", "raw2"],
        ["003", "CCO", "B", "Ki", "3", "Homo sapiens", "CHEMBL102", "9606", "0003", "raw3"],
        ["004", "CCO", "A", "Ki", "4", "Human", "CHEMBL101", "9606", "0004", "raw4"],
        ["005", "CCO", "B", "Ki", "5", "Homo sapiens", "CHEMBL102", "9606", "0005", "raw5"],
        ["006", "[He]", "C", "Ki", "6", "", "CHEMBL103", "", "0006", "raw6"],
        ["007", "N#N", "D", "Ki", "7", "mouse strain", "", "", "0007", "raw7"],
    ]
    policy = replace(compact_module().CompactSourcePolicy(), chunk_rows=2)
    old, compact = strict_pair(tmp_path, columns=columns, rows=rows, policy=policy)
    with rdBase.BlockLogs():
        for organism in ("", " Human ", "homo sapiens", "Mouse", "strain", "missing"):
            for threshold in (0.0, 0.6, 1.0, float(np.nextafter(np.float32(.6), np.float32(1)))):
                controls = dict(threshold=threshold, top_k=3, combine_by_target=combine, organism_filter=organism)
                a = old.predict_with_receipt(smiles, **controls)
                b = compact.predict_with_receipt(smiles, **controls)
                assert b["records"] == a["records"]
                assert b["receipt"]["score_dtype"] == a["receipt"]["score_dtype"]
                assert b["receipt"]["weights"] == a["receipt"]["weights"]
                assert b["receipt"]["weight_resolution"] == a["receipt"]["weight_resolution"]


def test_only_two_compact_queries_can_own_scratch(tmp_path, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from src.reverse_target.owned_source import ReverseSourceUnavailable
    _, compact = strict_pair(tmp_path)
    original = compact._predict_core
    entered, release, lock = threading.Barrier(3), threading.Event(), threading.Lock()
    calls = 0
    def blocked(*args, **kwargs):
        nonlocal calls
        with lock:
            calls += 1
            number = calls
        if number <= 2:
            entered.wait(timeout=10)
            assert release.wait(timeout=10)
        return original(*args, **kwargs)
    monkeypatch.setattr(compact, "_predict_core", blocked)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(compact.predict_with_receipt, "CCO") for _ in range(2)]
        try:
            entered.wait(timeout=10)
            with pytest.raises(ReverseSourceUnavailable):
                compact.predict_with_receipt("CCO")
            assert compact._strict_active == 2
            with pytest.raises(ReverseSourceUnavailable):
                compact.initialize_compact_strict()
        finally:
            release.set()
        results = [future.result(timeout=10) for future in futures]
    assert results[0]["receipt"]["invocation_id"] != results[1]["receipt"]["invocation_id"]
    assert compact._strict_active == 0


def test_compact_query_budget_rejects_before_unpack(tmp_path, monkeypatch):
    module = compact_module()
    _, compact = strict_pair(tmp_path, policy=replace(module.CompactSourcePolicy(), max_query_bytes=1))
    monkeypatch.setattr(module.PackedFingerprints, "block", lambda *a: pytest.fail("unbudgeted unpack"))
    with pytest.raises(ValueError, match="Invalid reverse-target owned source"):
        compact.predict_with_receipt("CCO")
    assert compact._strict_active == 0


def test_records_are_built_only_for_top_k(tmp_path, monkeypatch):
    _, compact = strict_pair(tmp_path)
    original = compact._similarity_record
    constructed = []
    def record(frame, index, *scores):
        constructed.append(index)
        return original(frame, index, *scores)
    monkeypatch.setattr(compact, "_similarity_record", record)
    result = compact.predict_with_receipt("CCO", threshold=0, top_k=1)
    assert constructed == [0]
    assert len(result["records"]) == 1


@pytest.mark.parametrize("timeout", [True, 0, 119, 3601, float("nan"), float("inf")])
def test_compact_public_prewarm_clock_does_not_accept_request_budgets(tmp_path, timeout):
    producer, _ = write_source(tmp_path / "source")
    with pytest.raises(ValueError, match="Invalid reverse-target strict input"):
        producer.initialize_compact_strict(timeout_seconds=timeout)
    assert not producer._strict_loading


def test_old_strict_clock_still_rejects_extended_budget(tmp_path):
    producer, _ = write_source(tmp_path / "source")
    with pytest.raises(ValueError, match="Invalid reverse-target strict input"):
        producer.initialize_strict(timeout_seconds=1800)


@pytest.mark.parametrize("action", ["close", "path_drift"])
def test_compact_late_acquisition_cannot_publish(tmp_path, action):
    from src.reverse_target.owned_source import ReverseSourceUnavailable
    producer, _ = write_source(tmp_path / "source")
    def progress(stage, done, total):
        if action == "close":
            producer.close_strict()
        else:
            producer.training_data_path = tmp_path / "other.tsv"
    with pytest.raises(ReverseSourceUnavailable), rdBase.BlockLogs():
        producer.initialize_compact_strict(progress=progress)
    assert producer._strict_source is None and not producer._strict_loading


@pytest.mark.parametrize("unicode_keys", [False, True])
def test_query_aggregation_reservations_cover_actual_python_objects(tmp_path, monkeypatch, unicode_keys):
    import sys
    module = compact_module()
    rows = [[f"CHEMBL{index}", "CCO", f"Target {index}", "Ki", "1", "Human", f"CHEMBLT{index}"]
            for index in range(500)]
    if unicode_keys:
        for index, row in enumerate(rows):
            row[2], row[5], row[6] = chr(0x4e00 + index), chr(0x6000 + index), ""
    producer, _ = write_source(tmp_path / "source", rows=rows)
    with rdBase.BlockLogs():
        producer.initialize_compact_strict()
    original = module.heapq.nlargest
    measured = []
    def deep_size(value, seen):
        if id(value) in seen:
            return 0
        seen.add(id(value))
        result = sys.getsizeof(value)
        if type(value) is dict:
            result += sum(deep_size(k, seen) + deep_size(v, seen) for k, v in value.items())
        elif type(value) in (list, tuple):
            result += sum(deep_size(v, seen) for v in value)
        return result
    def checked(*args, **kwargs):
        state = sys._getframe(1).f_locals
        base = state["block_size"] * 2214 * 3 + 2 * 1024**2 + state["top_k"] * 256 * 1024
        measured.append((state["reserved"] - base, deep_size(state["groups"], set())))
        del state
        return original(*args, **kwargs)
    monkeypatch.setattr(module.heapq, "nlargest", checked)
    with rdBase.BlockLogs():
        producer.predict_with_receipt("CCO", top_k=1)
    assert measured and measured[0][0] >= measured[0][1]


def test_cancelled_scoring_clears_private_source_from_retained_traceback(tmp_path, monkeypatch):
    import asyncio
    _, compact = strict_pair(tmp_path)
    source_ref = weakref.ref(compact._strict_source)
    original = compact.batch_tanimoto_similarity
    cancelled = False
    def cancel_after_block(*args, **kwargs):
        nonlocal cancelled
        result = original(*args, **kwargs)
        compact.close_strict()
        cancelled = True
        return result
    monkeypatch.setattr(compact, "batch_tanimoto_similarity", cancel_after_block)
    with pytest.raises(asyncio.CancelledError) as retained:
        compact.predict_with_receipt("CCO", cancelled=lambda: cancelled)
    gc.collect()
    assert retained.value is not None
    assert source_ref() is None
    assert compact._strict_active == 0


def test_compact_snapshot_ignores_later_disk_and_legacy_public_arrays(tmp_path):
    _, compact = strict_pair(tmp_path)
    expected = compact.predict_with_receipt("CCO")
    compact.df = None
    compact.morgan_fps = np.zeros((3, 2048), dtype=np.uint8)
    compact.maccs_fps = np.zeros((3, 166), dtype=np.uint8)
    np.save(compact.morgan_fp_path, compact.morgan_fps)
    np.save(compact.maccs_fp_path, compact.maccs_fps)
    actual = compact.predict_with_receipt("CCO")
    assert actual["records"] == expected["records"]
    assert actual["receipt"]["source_sha256"] == expected["receipt"]["source_sha256"]


def test_compact_path_drift_and_new_generation_reject_old_proofs(tmp_path):
    from src.reverse_target.owned_source import ReverseSourceUnavailable
    _, compact = strict_pair(tmp_path)
    snapshot = compact.capture_prediction_source()
    result = compact.predict_with_receipt("CCO")
    compact.initialize_compact_strict()
    with pytest.raises(ReverseSourceUnavailable):
        compact.validate_prediction_source(result, smiles="CCO", expected=snapshot)
    compact.training_data_path = tmp_path / "changed.tsv"
    with pytest.raises(ReverseSourceUnavailable):
        compact.capture_prediction_source()
    assert compact._strict_source is None


def test_compact_close_during_physical_load_keeps_owner_until_exit(tmp_path, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from src.reverse_target.owned_source import ReverseSourceUnavailable
    module = compact_module()
    producer, _ = write_source(tmp_path / "source")
    original = module.load_compact_source
    entered, release = threading.Event(), threading.Event()
    def held(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)
    monkeypatch.setattr(module, "load_compact_source", held)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(producer.initialize_compact_strict)
        try:
            assert entered.wait(10)
            producer.close_strict()
            assert producer._strict_loading
            with pytest.raises(ReverseSourceUnavailable):
                producer.initialize_compact_strict()
            with pytest.raises(ReverseSourceUnavailable):
                producer.initialize_strict()
        finally:
            release.set()
        with pytest.raises(ReverseSourceUnavailable):
            future.result(timeout=10)
    assert producer._strict_source is None and not producer._strict_loading


def test_compact_prewarm_clock_has_its_own_deadline_and_strict_cancel_type(monkeypatch):
    import asyncio
    module = compact_module()
    now = [0.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    check = module.acquisition_check(1800, None)
    now[0] = 1799
    check()
    now[0] = 1800
    with pytest.raises(TimeoutError):
        check()
    with pytest.raises(ValueError, match="Invalid reverse-target strict input"):
        module.acquisition_check(1800, lambda: 1)
    with pytest.raises(asyncio.CancelledError):
        module.acquisition_check(1800, lambda: True)


@pytest.mark.parametrize("count,limit,rejected", [
    (10, 5 * 1024**2, True), (100, 128 * 1024**2, True), (10, 128 * 1024**2, False),
])
def test_query_reserves_escaped_json_copies_before_serialization(tmp_path, monkeypatch, count, limit, rejected):
    import tracemalloc
    from src.reverse_target import predictor as predictor_module
    module = compact_module()
    columns = ["molecule_chembl_id", "canonical_smiles", "target_name", "standard_type",
               "standard_value", "organism", "target_chembl_id", "assay_id",
               "document_chembl_id", "publication_id", "standard_relation", "standard_units",
               "assay_confidence_score", "data_validity_comment"]
    # JSON expands each control character sixfold; one astral character also
    # makes a CPython JSON string use four-byte Unicode storage throughout.
    long_value = "\U0001f600" + "\x01" * 7999
    rows = [[long_value, "CCO", f"Target {i}", long_value, "1", "Human",
             f"CHEMBLT{i}"] + [long_value] * 7 for i in range(count)]
    producer, _ = write_source(tmp_path / "source", columns=columns, rows=rows)
    policy = replace(module.CompactSourcePolicy(), chunk_rows=1, max_query_bytes=limit)
    with rdBase.BlockLogs():
        producer.initialize_compact_strict(policy=policy)
    original = predictor_module.json_sha256
    serialized_records = []
    def tracked(value):
        if type(value) is list:
            serialized_records.append(True)
        return original(value)
    monkeypatch.setattr(predictor_module, "json_sha256", tracked)
    tracemalloc.start()
    try:
        with rdBase.BlockLogs():
            if rejected:
                with pytest.raises(ValueError, match="Invalid reverse-target owned source"):
                    producer.predict_with_receipt("CCO", top_k=count, combine_by_target=False)
            else:
                result = producer.predict_with_receipt("CCO", top_k=count, combine_by_target=False)
                assert len(result["records"]) == count
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert serialized_records == ([] if rejected else [True])
    assert peak < limit
    if not rejected:
        assert peak < module._serialization_reservation(result)
    assert producer._strict_active == 0
