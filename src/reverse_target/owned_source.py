"""Private RAM snapshots and detached, non-authenticating reverse proofs.

Logical allocation limits are not process RSS limits. No legacy cache ownership,
filesystem persistence, or consumer authorization is conferred by these proofs.
"""

import asyncio
import csv
from dataclasses import dataclass
import hashlib
import io
import json
import math
from pathlib import Path
import re
import time
import traceback
import uuid

import numpy as np
import pandas as pd
from rdkit import rdBase


REVISION = "reverse-owned-v1"
WEIGHT_REVISION = "reverse-weights-v1"
INPUT_ERROR = "Invalid reverse-target strict input."
SOURCE_ERROR = "Invalid reverse-target owned source."
PROOF_ERROR = "Invalid reverse-target prediction proof."


class _StrictInputError(ValueError):
    def __init__(self):
        super().__init__(INPUT_ERROR)


class ReverseSourceUnavailable(ValueError):
    """No eligible current source (also used for stale historical evidence)."""

    def __init__(self):
        super().__init__("Reverse-target source unavailable.")


@dataclass(frozen=True)
class ReverseSourceSnapshot:
    generation_id: str
    source_sha256: str
    configuration_sha256: str


@dataclass(frozen=True)
class _OwnedSource:
    frame: pd.DataFrame
    morgan: np.ndarray
    maccs: np.ndarray
    morgan_popcounts: np.ndarray
    maccs_popcounts: np.ndarray
    paths: dict
    descriptor: dict
    source_sha256: str


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def json_sha256(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


def detached(value):
    return json.loads(canonical_json(value))


def clear_failure_frames(error):
    """Release unwound failure frames, including suppressed contexts/causes.

    Traceback locations and exception types remain intact. Executing frames are
    deliberately skipped by clear_frames; owning callers clear their own large
    locals before releasing lifecycle ownership. Never used on successful calls.
    """
    pending, seen = [error], set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        traceback.clear_frames(current.__traceback__)
        for linked in (current.__cause__, current.__context__):
            if linked is not None:
                pending.append(linked)


def operation_check(timeout_seconds, cancelled):
    if (type(timeout_seconds) not in (int, float)
            or not 0 < timeout_seconds <= 300 or not math.isfinite(timeout_seconds)
            or (cancelled is not None and not callable(cancelled))):
        raise ValueError(INPUT_ERROR)
    deadline = time.monotonic() + timeout_seconds

    def check_deadline():
        if time.monotonic() >= deadline:
            raise TimeoutError("Reverse-target operation timed out.")

    def check():
        if cancelled is not None:
            result = cancelled()
            if type(result) is not bool:
                raise _StrictInputError()
            if result:
                raise asyncio.CancelledError()
        check_deadline()
    # Final path revalidation must not invoke a mutating callback afterwards.
    check.deadline = check_deadline
    check()
    return check


def validate_controls(smiles, threshold, top_k, combine_by_target, organism_filter):
    if (type(smiles) is not str or not 0 < len(smiles) <= 8192
            or any(c.isspace() for c in smiles)
            or type(threshold) not in (int, float) or not 0 <= threshold <= 1
            or not math.isfinite(threshold)
            or type(top_k) is not int or not 1 <= top_k <= 100
            or type(combine_by_target) is not bool
            or type(organism_filter) is not str or len(organism_filter) > 256):
        raise ValueError(INPUT_ERROR)
    try:
        smiles.encode("utf-8")
        organism_filter.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError(INPUT_ERROR) from None
    return dict(threshold=threshold, top_k=top_k,
                combine_by_target=combine_by_target, organism_filter=organism_filter)


def resolve_weights(raw):
    defaulted = raw is None
    try:
        parsed = float(raw) if raw is not None else 0.7
    except ValueError:
        parsed, defaulted = 0.7, True
    morgan = min(1.0, max(0.0, parsed))
    return ({"morgan": morgan, "maccs": 1.0 - morgan},
            {"defaulted": defaulted, "clamped": parsed != morgan})


def configuration_sha256(paths, weights, resolution):
    return json_sha256(dict(paths=paths, weights=weights,
                            weight_resolution_revision=WEIGHT_REVISION,
                            weight_resolution=resolution))


def validate_popcount_rows(fingerprints, counts, *, chunk_size, check=None):
    """Pure original arithmetic; validate cached values before narrowing casts."""
    fingerprint_error = "Invalid reverse-target fingerprints."
    cache_error = "Invalid reverse-target popcount cache."
    if (not isinstance(fingerprints, np.ndarray) or fingerprints.ndim != 2
            or fingerprints.dtype.kind not in "biu"
            or not 1 <= fingerprints.shape[1] <= np.iinfo(np.uint16).max):
        raise ValueError(fingerprint_error)
    n_samples, width = fingerprints.shape
    cached = counts is not None
    if cached and (not isinstance(counts, np.ndarray) or counts.ndim != 1
                   or counts.shape[0] != n_samples or counts.dtype.kind not in "iu"):
        raise ValueError(cache_error)
    popcounts = counts if cached else np.zeros(n_samples, dtype=np.uint16)
    for start in range(0, n_samples, chunk_size):
        if check:
            check()
        end = min(start + chunk_size, n_samples)
        chunk = fingerprints[start:end]
        if np.any((chunk != 0) & (chunk != 1)):
            raise ValueError(fingerprint_error)
        expected = np.sum(chunk, axis=1, dtype=np.uint32)
        if cached:
            values = popcounts[start:end]
            if (np.any(values < 0) or np.any(values > width)
                    or not np.array_equal(values, expected)):
                raise ValueError(cache_error)
        else:
            popcounts[start:end] = expected
        if check:
            check()
    return popcounts.astype(np.uint16, copy=False) if cached else popcounts


def _array_hash(array, check):
    digest = hashlib.sha256(b"MedChat.reverse-array.v1\n")
    digest.update(canonical_json(dict(dtype=array.dtype.name, shape=list(array.shape), order="C")))
    digest.update(b"\n")
    flat = array.reshape(-1)
    for start in range(0, flat.size, 1024 * 1024 // array.itemsize):
        check()
        digest.update(flat[start:start + 1024 * 1024 // array.itemsize].astype(array.dtype.newbyteorder("<"), copy=False).tobytes(order="C"))
        check()
    return digest.hexdigest()


def _read_npy_header(stream, check):
    """Preflight declared bytes before asking NumPy to parse a bounded header.

    NumPy's max_header_size alone checks *after* reading the declared body. Its
    existing parser is still authoritative for accepted NPY 1/2/3 syntax, with
    only the length-prefixed <=16KiB header passed through an in-memory stream.
    """
    check()
    version = np.lib.format.read_magic(stream)  # Exactly eight prefix bytes.
    check()
    length_size = {(1, 0): 2, (2, 0): 4, (3, 0): 4}.get(version)
    if length_size is None:
        raise ValueError(SOURCE_ERROR)
    length_bytes = stream.read(length_size)
    check()
    if len(length_bytes) != length_size:
        raise ValueError(SOURCE_ERROR)
    length = int.from_bytes(length_bytes, "little")
    if length > 16 * 1024:
        raise ValueError(SOURCE_ERROR)
    body = stream.read(length)
    check()
    if len(body) != length:
        raise ValueError(SOURCE_ERROR)
    # This internal helper is NumPy's shared parser for all three versions;
    # the public v1/v2 helpers do not cover v3's UTF-8 header encoding.
    result = np.lib.format._read_array_header(
        io.BytesIO(length_bytes + body), version, max_header_size=16 * 1024)
    check()
    return result


def _read_array(path, shape, *, counts=False, check):
    """Only this function owns the local mapping; no alias escapes its finally."""
    loaded = None
    try:
        check()
        # Unbuffered prefix reads cannot prefetch a rejected header body. Parsing
        # and mapping use this same fd: no reopen-path TOCTOU after validation.
        with open(path, "rb", buffering=0) as stream:
            check()
            actual_shape, fortran, dtype = _read_npy_header(stream, check)
            if (actual_shape != shape or any(type(d) is not int or d < 0 for d in actual_shape)
                    or dtype.hasobject or (dtype.kind not in "iu" if counts else dtype != np.uint8)):
                raise ValueError(SOURCE_ERROR)
            # The loader checked the row-based logical budget before opening;
            # exact shape/dtype checks precede any mapping/private allocation.
            loaded = np.memmap(stream, dtype=dtype, shape=shape,
                               order="F" if fortran else "C", mode="r", offset=stream.tell())
            check()
            # Preserve original count values until validated-before-cast.
            result = np.empty(shape, dtype=dtype, order="C")
            for start in range(0, shape[0], 4096):
                check()
                result[start:start + 4096] = loaded[start:start + 4096]
                check()
            return result
    except BaseException as error:
        clear_failure_frames(error)
        raise
    finally:
        if isinstance(loaded, np.memmap):
            loaded._mmap.close()
        elif hasattr(loaded, "close"):
            loaded.close()
        loaded = result = None


def _validate_tsv_structure(raw, check):
    """Count logical records before pandas can repair headers or discard rows.

    Retain only the header and current record, not another table. The existing
    raw-byte cap bounds physical lines; check even inside quoted multiline cells.
    newline='' preserves quoted CR/LF, and utf-8-sig matches pandas' BOM handling.
    Failure frames are cleared by the acquisition owner, including this parser.
    """
    with io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8-sig", newline="") as stream:
        def checked_lines():
            while True:
                check()
                line = stream.readline()
                check()
                if not line:
                    return
                yield line

        reader = csv.reader(checked_lines(), delimiter="\t", strict=True)
        columns = next(reader, None)
        check()
        if (not columns or any(not column.strip() for column in columns)
                or len(set(columns)) != len(columns)):
            raise ValueError(SOURCE_ERROR)
        rows = 0
        for record in reader:
            check()
            if len(record) != len(columns):
                raise ValueError(SOURCE_ERROR)
            rows += 1
        check()
        return columns, rows


def load_owned_source(paths, max_snapshot_bytes, check, fingerprint_fn):
    """Bounded acquisition of one private generation. Never repairs source files."""
    try:
        limit = min(32 * 1024**2, max_snapshot_bytes // 8)
        check()
        with open(paths["tsv"], "rb") as source:
            check()
            buffer = bytearray()
            while True:
                check()
                chunk = source.read(min(1024**2, limit + 1 - len(buffer)))
                check()
                buffer.extend(chunk)
                if len(buffer) > limit:
                    raise ValueError(SOURCE_ERROR)
                if not chunk:
                    break
        check()
        raw = bytes(buffer)
        columns, rows = _validate_tsv_structure(raw, check)
        frame = pd.read_csv(io.BytesIO(raw), sep="\t", encoding="utf-8", dtype=str,
                            keep_default_na=False)
        check()
        required = {"molecule_chembl_id", "canonical_smiles", "target_name", "standard_type", "standard_value", "organism"}
        if (not required <= set(frame.columns) or list(frame.columns) != columns
                or len(frame) != rows or not frame.index.equals(pd.RangeIndex(rows))):
            raise ValueError(SOURCE_ERROR)
        if len(raw) + int(frame.memory_usage(deep=True).sum()) + rows * (2 * 2214 + 32) > max_snapshot_bytes:
            raise ValueError(SOURCE_ERROR)
        for _, row in frame.iterrows():
            check()
            for key in required - {"standard_value"}:
                value = row[key]
                if type(value) is not str or len(value) > 8192 or (key != "organism" and not value.strip()):
                    raise ValueError(SOURCE_ERROR)
            if any(c.isspace() for c in row["canonical_smiles"]):
                raise ValueError(SOURCE_ERROR)
            value = float(row["standard_value"])
            if not math.isfinite(value) or value < 0:
                raise ValueError(SOURCE_ERROR)
        frame["standard_value"] = frame["standard_value"].astype(float)
        check()
        morgan = _read_array(paths["morgan"], (rows, 2048), check=check)
        maccs = _read_array(paths["maccs"], (rows, 166), check=check)
        for index, smiles in enumerate(frame["canonical_smiles"]):
            check()
            expected_morgan, expected_maccs = fingerprint_fn(smiles)
            check()
            if not np.array_equal(morgan[index], expected_morgan) or not np.array_equal(maccs[index], expected_maccs):
                raise ValueError(SOURCE_ERROR)
        arrays = dict(morgan=morgan, maccs=maccs)
        origins = {}
        for kind in ("morgan", "maccs"):
            check()
            path = Path(paths[kind + "_popcounts"])
            exists = path.exists()
            check()
            counts = _read_array(path, (rows,), counts=True, check=check) if exists else None
            arrays[kind + "_popcounts"] = validate_popcount_rows(arrays[kind], counts, chunk_size=4096, check=check)
            origins[kind] = "verified_cache" if exists else "computed"
        descriptor = dict(revision=REVISION, generation_id=uuid.uuid4().hex,
                          source_name=Path(paths["tsv"]).name, tsv_sha256=hashlib.sha256(raw).hexdigest(),
                          row_count=rows, morgan_bits=2048, maccs_bits=166, morgan_radius=2,
                          rdkit_version=rdBase.rdkitVersion, numpy_version=np.__version__,
                          array_codec="reverse-array-v1", row_alignment_verified=True,
                          cache_origins=origins)
        for name, array in arrays.items():
            descriptor[name + "_sha256"] = _array_hash(array, check)
            array.flags.writeable = False
        check()
        return _OwnedSource(frame=frame, paths=dict(paths), descriptor=descriptor,
                            source_sha256=json_sha256(descriptor), **arrays)
    except (TimeoutError, asyncio.CancelledError, _StrictInputError) as error:
        clear_failure_frames(error)
        raise
    except Exception as error:
        clear_failure_frames(error)
        raise ValueError(SOURCE_ERROR) from None
    finally:
        # clear_frames cannot clear this executing owner. Do not let a retained
        # exception keep a failed dataframe, source arrays, counts or copy scratch.
        frame = row = morgan = maccs = counts = arrays = array = None
        expected_morgan = expected_maccs = raw = buffer = chunk = columns = None


def _closed(value, keys):
    if type(value) is not dict or set(value) != set(keys.split()):
        raise ValueError(PROOF_ERROR)


def _hex(value, length):
    return type(value) is str and re.fullmatch("[0-9a-f]{%d}" % length, value) is not None


def _number(value, low, high=math.inf):
    return type(value) in (int, float) and math.isfinite(value) and low <= value <= high


def _native_bounded(value):
    """Validate before copying/hashing: exact native types and active-path cycles."""
    nodes, size, active = 0, 0, set()

    def visit(item, depth):
        nonlocal nodes, size
        nodes += 1
        if depth > 8 or nodes > 10000:
            raise ValueError(PROOF_ERROR)
        kind = type(item)
        if kind is str:
            size += len(item.encode("utf-8"))
            if size > 1024**2:
                raise ValueError(PROOF_ERROR)
        elif kind in (dict, list):
            if id(item) in active:
                raise ValueError(PROOF_ERROR)
            active.add(id(item))
            if kind is dict:
                for key, child in item.items():
                    if type(key) is not str:
                        raise ValueError(PROOF_ERROR)
                    visit(key, depth + 1)
                    visit(child, depth + 1)
            else:
                for child in item:
                    visit(child, depth + 1)
            active.remove(id(item))
        elif kind is float:
            if not math.isfinite(item):
                raise ValueError(PROOF_ERROR)
        elif kind not in (int, bool, type(None)):
            raise ValueError(PROOF_ERROR)
    visit(value, 0)


def validate_snapshot(expected):
    if (type(expected) is not ReverseSourceSnapshot
            or not _hex(expected.generation_id, 32)
            or not _hex(expected.source_sha256, 64)
            or not _hex(expected.configuration_sha256, 64)):
        raise ValueError(PROOF_ERROR)


def validate_envelope(envelope, *, smiles, controls, expected):
    """Pure structural/content checks, NOT authentication or scientific rescoring."""
    try:
        validate_snapshot(expected)
        _native_bounded(envelope)
        _closed(envelope, "records receipt")
        r = envelope["receipt"]
        _closed(r, "schema_version validation_revision invocation_id source source_sha256 input_sha256 configuration_sha256 controls weights weight_resolution_revision weight_resolution score_dtype result_sha256 status record_count")
        if r["schema_version"] != "1" or r["validation_revision"] != REVISION or not _hex(r["invocation_id"], 32):
            raise ValueError(PROOF_ERROR)
        source = r["source"]
        _closed(source, "revision generation_id source_name tsv_sha256 row_count morgan_sha256 maccs_sha256 morgan_popcounts_sha256 maccs_popcounts_sha256 morgan_bits maccs_bits morgan_radius rdkit_version numpy_version array_codec row_alignment_verified cache_origins")
        if source["revision"] != REVISION or not _hex(source["generation_id"], 32):
            raise ValueError(PROOF_ERROR)
        for key in ("tsv", "morgan", "maccs", "morgan_popcounts", "maccs_popcounts"):
            if not _hex(source[key + "_sha256"], 64):
                raise ValueError(PROOF_ERROR)
        for key, value in (("morgan_bits", 2048), ("maccs_bits", 166), ("morgan_radius", 2)):
            if type(source[key]) is not int or source[key] != value:
                raise ValueError(PROOF_ERROR)
        for key in ("source_name", "rdkit_version", "numpy_version"):
            if type(source[key]) is not str or not source[key].strip() or len(source[key]) > 8192:
                raise ValueError(PROOF_ERROR)
        if any(c in source["source_name"] for c in ("/", "\\", ":")) or source["source_name"] in (".", ".."):
            raise ValueError(PROOF_ERROR)
        rows = source["row_count"]
        if type(rows) is not int or rows < 0 or source["array_codec"] != "reverse-array-v1" or source["row_alignment_verified"] is not True:
            raise ValueError(PROOF_ERROR)
        _closed(source["cache_origins"], "morgan maccs")
        if any(v not in ("computed", "verified_cache") for v in source["cache_origins"].values()):
            raise ValueError(PROOF_ERROR)
        _closed(r["controls"], "threshold top_k combine_by_target organism_filter")
        actual_controls = validate_controls(smiles, **r["controls"])
        if canonical_json(actual_controls) != canonical_json(controls):
            raise ValueError(PROOF_ERROR)
        _closed(r["weights"], "morgan maccs")
        w = r["weights"]
        if not all(_number(v, 0, 1) for v in w.values()) or w["maccs"] != 1.0 - w["morgan"]:
            raise ValueError(PROOF_ERROR)
        _closed(r["weight_resolution"], "defaulted clamped")
        resolution = r["weight_resolution"]
        if any(type(v) is not bool for v in resolution.values()) or r["weight_resolution_revision"] != WEIGHT_REVISION:
            raise ValueError(PROOF_ERROR)
        if resolution["defaulted"] and (w["morgan"] != .7 or resolution["clamped"]):
            raise ValueError(PROOF_ERROR)
        if resolution["clamped"] and w["morgan"] not in (0., 1.):
            raise ValueError(PROOF_ERROR)
        for key in ("source_sha256", "input_sha256", "configuration_sha256", "result_sha256"):
            if not _hex(r[key], 64):
                raise ValueError(PROOF_ERROR)
        if r["source_sha256"] != json_sha256(source) or r["input_sha256"] != json_sha256(dict(smiles=smiles, controls=r["controls"])):
            raise ValueError(PROOF_ERROR)
        records = envelope["records"]
        if type(records) is not list or len(records) > controls["top_k"] or type(r["record_count"]) is not int or r["record_count"] != len(records):
            raise ValueError(PROOF_ERROR)
        if r["status"] != ("verified_hits" if records else "verified_empty") or r["score_dtype"] not in ("float32", "float64"):
            raise ValueError(PROOF_ERROR)
        indices, targets, total, previous = set(), set(), 0, math.inf
        strings = "target_name organism canonical_smiles molecule_chembl_id standard_type chembl_search_url uniprot_search_url".split()
        for record in records:
            _closed(record, " ".join(strings) + " standard_value morgan_similarity maccs_similarity final_similarity row_index similar_count")
            for key in strings:
                v = record[key]
                if type(v) is not str or len(v) > 8192 or (key != "organism" and not v.strip()):
                    raise ValueError(PROOF_ERROR)
            if not _number(record["standard_value"], 0):
                raise ValueError(PROOF_ERROR)
            if any(not _number(record[key], 0, 1) for key in ("morgan_similarity", "maccs_similarity", "final_similarity")):
                raise ValueError(PROOF_ERROR)
            idx, count, score = record["row_index"], record["similar_count"], record["final_similarity"]
            if type(idx) is not int or not 0 <= idx < rows or idx in indices or type(count) is not int or not 1 <= count <= rows:
                raise ValueError(PROOF_ERROR)
            if score > previous or (controls["combine_by_target"] and record["target_name"] in targets) or (not controls["combine_by_target"] and count != 1):
                raise ValueError(PROOF_ERROR)
            indices.add(idx)
            targets.add(record["target_name"])
            total += count
            previous = score
        if total > rows or not np.all(np.asarray([v["final_similarity"] for v in records], dtype=r["score_dtype"]) >= controls["threshold"]):
            raise ValueError(PROOF_ERROR)
        if r["result_sha256"] != json_sha256(records):
            raise ValueError(PROOF_ERROR)
        return detached(envelope)
    except Exception:
        raise ValueError(PROOF_ERROR) from None
