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


def write_source(directory, *, cached=True, fortran=False):
    directory.mkdir(parents=True, exist_ok=True)
    columns = ["molecule_chembl_id", "canonical_smiles", "target_name",
               "standard_type", "standard_value", "organism", "target_chembl_id"]
    rows = [
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
