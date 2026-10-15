"""Bounded, full-row-verified private reverse-target sources.

The limits below describe logical allocations, not the RSS of NumPy/RDKit.
No disk mapping, caller-owned array or partially verified source is published.
"""

import asyncio
from contextlib import ExitStack
import csv
from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import stat
from types import MappingProxyType
import uuid

import numpy as np
from rdkit import rdBase

from .owned_source import (
    REVISION, SOURCE_ERROR, _OwnedSource, _StrictInputError, _array_hash,
    _read_npy_header, canonical_json, clear_failure_frames, json_sha256,
)


@dataclass(frozen=True)
class CompactSourcePolicy:
    max_snapshot_bytes: int = 768 * 1024**2
    max_acquisition_bytes: int = 1024**3
    max_query_bytes: int = 128 * 1024**2
    max_tsv_bytes: int = 256 * 1024**2
    max_rows: int = 1_000_000
    max_columns: int = 32
    chunk_rows: int = 4096

    def validate(self):
        if type(self) is not CompactSourcePolicy:
            raise ValueError(SOURCE_ERROR)
        for name, ceiling in vars(CompactSourcePolicy()).items():
            value = getattr(self, name)
            if type(value) is not int or not 0 < value <= ceiling:
                raise ValueError(SOURCE_ERROR)
        if self.max_snapshot_bytes > self.max_acquisition_bytes:
            raise ValueError(SOURCE_ERROR)


class _Budget:
    """Conservative capacity reservations, made before bounded allocations."""

    def __init__(self, policy):
        self.policy = policy
        self.ready = 0
        self.scratch = 0
        self.peak = 0

    def reserve(self, size, *, scratch=False):
        ready = self.ready + (0 if scratch else size)
        temporary = self.scratch + (size if scratch else 0)
        if (ready > self.policy.max_snapshot_bytes
                or ready + temporary > self.policy.max_acquisition_bytes):
            raise ValueError(SOURCE_ERROR)
        self.ready, self.scratch = ready, temporary
        self.peak = max(self.peak, ready + temporary)


class _Rows:
    def __init__(self, table):
        self._table = table

    def __getitem__(self, index):
        table = self._table
        if type(index) is not int or not 0 <= index < len(table):
            raise IndexError(index)
        return {name: (float(values[index]) if name == "standard_value"
                      else table._vocabularies[name][int(values[index])])
                for name, values in table._columns.items()}


class CompactTable:
    """Dictionary-coded native strings with original row/column order."""

    def __init__(self, columns, vocabularies, rows):
        self._columns = columns
        self._vocabularies = vocabularies
        self._rows = rows
        self.columns = tuple(columns)
        self.iloc = _Rows(self)
        for values in columns.values():
            values.flags.writeable = False

    def __len__(self):
        return self._rows


class PackedFingerprints:
    """Private bit-packed storage. Unpacking never exceeds the block limit."""

    def __init__(self, packed, width, chunk_rows):
        self._packed = packed
        self.shape = (len(packed), width)
        self._chunk_rows = chunk_rows
        packed.flags.writeable = False

    def block(self, start, stop):
        if (type(start) is not int or type(stop) is not int
                or not 0 <= start <= stop <= self.shape[0]
                or stop - start > self._chunk_rows):
            raise ValueError(SOURCE_ERROR)
        return np.unpackbits(self._packed[start:stop], axis=1,
                             count=self.shape[1], bitorder="big")


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns)


def _open_regular(path, stack):
    path = Path(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or path.is_symlink():
        raise ValueError(SOURCE_ERROR)
    stream = stack.enter_context(open(path, "rb", buffering=0))
    opened = os.fstat(stream.fileno())
    if not stat.S_ISREG(opened.st_mode) or _identity(before) != _identity(opened):
        raise ValueError(SOURCE_ERROR)
    return stream, _identity(opened)


def _check_file(path, stream, identity):
    if (Path(path).is_symlink() or _identity(os.fstat(stream.fileno())) != identity
            or _identity(Path(path).stat()) != identity):
        raise ValueError(SOURCE_ERROR)


class _TSVLines:
    """Incremental universal newlines; CSV controls logical-record boundaries."""

    def __init__(self, stream, policy, check):
        self.stream, self.policy, self.check = stream, policy, check
        self.digest = hashlib.sha256()
        self.total = self.record_bytes = 0
        self.buffer = bytearray()
        self.eof = False
        self.first = True

    def __iter__(self):
        return self

    def __next__(self):
        while True:
            self.check()
            end = None
            for pos, value in enumerate(self.buffer):
                if value == 10:
                    end = pos + 1
                    break
                if value == 13:
                    if pos + 1 == len(self.buffer) and not self.eof:
                        break
                    end = pos + (2 if self.buffer[pos + 1:pos + 2] == b"\n" else 1)
                    break
            if end is not None or self.eof:
                end = len(self.buffer) if end is None else end
                if end == 0:
                    raise StopIteration
                self.record_bytes += end
                if self.record_bytes > 256 * 1024:
                    raise ValueError(SOURCE_ERROR)
                raw = bytes(self.buffer[:end])
                del self.buffer[:end]
                result = raw.decode("utf-8-sig" if self.first else "utf-8")
                self.first = False
                return result
            # Never acquire an unbounded physical line before rejecting it.
            remaining = 256 * 1024 - self.record_bytes - len(self.buffer)
            if remaining < 0:
                raise ValueError(SOURCE_ERROR)
            chunk = self.stream.read(min(64 * 1024, remaining + 1))
            self.check()
            if not chunk:
                self.eof = True
                continue
            self.total += len(chunk)
            if self.total > self.policy.max_tsv_bytes or b"\0" in chunk:
                raise ValueError(SOURCE_ERROR)
            self.digest.update(chunk)
            self.buffer.extend(chunk)


def _records(stream, policy, check):
    lines = _TSVLines(stream, policy, check)
    reader = csv.reader(lines, delimiter="\t", strict=True)

    def records():
        while True:
            lines.record_bytes = 0
            check()
            record = next(reader, None)
            if record is None:
                return
            if (not record or len(record) > policy.max_columns
                    or any(len(cell) > 8192 or len(cell.encode("utf-8")) > 32768
                           for cell in record)):
                raise ValueError(SOURCE_ERROR)
            yield record

    return records(), lines


def _table(stream, policy, budget, check):
    required = {"molecule_chembl_id", "canonical_smiles", "target_name",
                "standard_type", "standard_value", "organism"}
    records, lines = _records(stream, policy, check)
    names = next(records, None)
    if (not names or not required.issubset(names) or len(set(names)) != len(names)
            or any(not name.strip() for name in names)):
        raise ValueError(SOURCE_ERROR)
    # Header strings survive as mapping keys/column names too, independently of
    # the dictionary-coded data cells (and may each contain 8,192 characters).
    budget.reserve(sum(128 + 4 * len(name) for name in names))
    count = 0
    for record in records:
        if len(record) != len(names):
            raise ValueError(SOURCE_ERROR)
        count += 1
        if count > policy.max_rows:
            raise ValueError(SOURCE_ERROR)
    first_digest = lines.digest.hexdigest()
    # Fixed column arrays, packed bits and counts, plus Python container headers.
    budget.reserve(count * ((len(names) - 1) * 4 + 8 + 277 + 4) + 65536)
    columns = {name: np.empty(count, dtype=np.float64 if name == "standard_value"
                             else np.uint32) for name in names}
    vocabs = {name: [] for name in names if name != "standard_value"}
    indexes = {name: {} for name in vocabs}
    stream.seek(0)
    records, lines = _records(stream, policy, check)
    if next(records, None) != names:
        raise ValueError(SOURCE_ERROR)
    index = 0
    for record in records:
        if len(record) != len(names) or index >= count:
            raise ValueError(SOURCE_ERROR)
        for name, value in zip(names, record):
            if name == "standard_value":
                number = float(value)
                if not math.isfinite(number) or number < 0:
                    raise ValueError(SOURCE_ERROR)
                columns[name][index] = number
                continue
            if ((name in required - {"organism"} and not value.strip())
                    or (name == "canonical_smiles" and any(c.isspace() for c in value))):
                raise ValueError(SOURCE_ERROR)
            code = indexes[name].get(value)
            if code is None:
                # Worst-case Unicode storage; amortized list/tuple capacity and
                # integer objects. Hash-table growth lives only during acquisition.
                budget.reserve(128 + len(value) * 4)
                budget.reserve(192, scratch=True)
                code = len(vocabs[name])
                indexes[name][value] = code
                vocabs[name].append(value)
            columns[name][index] = code
        index += 1
    if index != count or lines.digest.hexdigest() != first_digest:
        raise ValueError(SOURCE_ERROR)
    return CompactTable(columns, {name: tuple(values) for name, values in vocabs.items()},
                        count), first_digest


def _mapped(stream, shape, *, counts, stack, check):
    actual, fortran, dtype = _read_npy_header(stream, check)
    if (actual != shape or any(type(d) is not int or d < 0 for d in actual)
            or dtype.hasobject or (dtype.kind not in "iu" if counts else dtype != np.uint8)
            or os.fstat(stream.fileno()).st_size < stream.tell() + math.prod(shape) * dtype.itemsize):
        raise ValueError(SOURCE_ERROR)
    if not math.prod(shape):
        return np.empty(shape, dtype=dtype)
    mapping = np.memmap(stream, dtype=dtype, shape=shape, order="F" if fortran else "C",
                        mode="r", offset=stream.tell())
    stack.callback(mapping._mmap.close)
    return mapping


def _logical_hasher(shape):
    digest = hashlib.sha256(b"MedChat.reverse-array.v1\n")
    digest.update(canonical_json(dict(dtype="uint8", shape=list(shape), order="C")))
    digest.update(b"\n")
    return digest


def load_compact_source(paths, policy, check, fingerprint_fn, *, progress=None):
    """Validate the complete corpus and return an exclusively owned snapshot."""
    try:
        policy.validate()
        budget = _Budget(policy)
        # Parser, conversions, two fingerprint copies, boolean comparisons,
        # pack/unpack and bounded digest copies, including container overhead.
        budget.reserve(8 * 1024**2 + policy.chunk_rows * 2214 * 5, scratch=True)
        with ExitStack() as stack:
            check()
            stream, identity = _open_regular(paths["tsv"], stack)
            if identity[2] > policy.max_tsv_bytes:
                raise ValueError(SOURCE_ERROR)
            pinned = {"tsv": (stream, identity)}
            frame, tsv_hash = _table(stream, policy, budget, check)
            _check_file(paths["tsv"], stream, identity)
            rows = len(frame)
            mapped, packed, counts, digests, origins = {}, {}, {}, {}, {}
            absent = []
            for kind, width in (("morgan", 2048), ("maccs", 166)):
                check()
                fp_stream, fp_identity = _open_regular(paths[kind], stack)
                pinned[kind] = (fp_stream, fp_identity)
                mapped[kind] = _mapped(fp_stream, (rows, width), counts=False,
                                       stack=stack, check=check)
                packed[kind] = np.empty((rows, (width + 7) // 8), dtype=np.uint8)
                counts[kind] = np.empty(rows, dtype=np.uint16)
                digests[kind] = _logical_hasher((rows, width))
                key = kind + "_popcounts"
                if Path(paths[key]).exists():
                    pc_stream, pc_identity = _open_regular(paths[key], stack)
                    pinned[key] = (pc_stream, pc_identity)
                    mapped[key] = _mapped(pc_stream, (rows,), counts=True,
                                          stack=stack, check=check)
                    origins[kind] = "verified_cache"
                else:
                    absent.append(key)
                    origins[kind] = "computed"
            for start in range(0, rows, policy.chunk_rows):
                check()
                stop = min(start + policy.chunk_rows, rows)
                block = {kind: np.array(mapped[kind][start:stop], order="C", copy=True)
                         for kind in ("morgan", "maccs")}
                for index in range(start, stop):
                    check()
                    expected = fingerprint_fn(frame.iloc[index]["canonical_smiles"])
                    check()
                    if type(expected) not in (tuple, list) or len(expected) != 2:
                        raise ValueError(SOURCE_ERROR)
                    for kind, actual in zip(("morgan", "maccs"), expected):
                        if actual is None or not np.array_equal(actual, block[kind][index - start]):
                            raise ValueError(SOURCE_ERROR)
                for kind, width in (("morgan", 2048), ("maccs", 166)):
                    array = block[kind]
                    if np.any((array != 0) & (array != 1)):
                        raise ValueError(SOURCE_ERROR)
                    computed = array.sum(axis=1, dtype=np.uint32)
                    key = kind + "_popcounts"
                    if key in mapped:
                        original = np.array(mapped[key][start:stop], copy=True)
                        if (np.any(original < 0) or np.any(original > width)
                                or not np.array_equal(original, computed)):
                            raise ValueError(SOURCE_ERROR)
                    counts[kind][start:stop] = computed
                    packed[kind][start:stop] = np.packbits(array, axis=1, bitorder="big")
                    # Hash exactly the bytes copied and scientifically verified.
                    view = memoryview(array).cast("B")
                    for offset in range(0, len(view), 1024**2):
                        check()
                        digests[kind].update(view[offset:offset + 1024**2])
                    view.release()
                if progress is not None:
                    progress("validating_rows", stop, rows)
                check()
            descriptor = dict(revision=REVISION, generation_id=uuid.uuid4().hex,
                              source_name=Path(paths["tsv"]).name, tsv_sha256=tsv_hash,
                              row_count=rows, morgan_bits=2048, maccs_bits=166, morgan_radius=2,
                              rdkit_version=rdBase.rdkitVersion, numpy_version=np.__version__,
                              array_codec="reverse-array-v1", row_alignment_verified=True,
                              cache_origins=origins)
            for kind in ("morgan", "maccs"):
                descriptor[kind + "_sha256"] = digests[kind].hexdigest()
                descriptor[kind + "_popcounts_sha256"] = _array_hash(counts[kind], check)
                counts[kind].flags.writeable = False
            frame.allocation_usage = MappingProxyType({
                "snapshot_reserved_bytes": budget.ready,
                "acquisition_peak_reserved_bytes": budget.peak,
            })
            # Nothing invokes a caller callback after the final file checks.
            # The ExitStack retains every fd through hashing and closes all
            # mappings/handles before this return reaches the publication owner.
            check()
            for key, (stream, identity) in pinned.items():
                _check_file(paths[key], stream, identity)
            if any(Path(paths[key]).exists() for key in absent):
                raise ValueError(SOURCE_ERROR)
            return _OwnedSource(frame=frame,
                                morgan=PackedFingerprints(packed["morgan"], 2048, policy.chunk_rows),
                                maccs=PackedFingerprints(packed["maccs"], 166, policy.chunk_rows),
                                morgan_popcounts=counts["morgan"], maccs_popcounts=counts["maccs"],
                                paths=dict(paths), descriptor=descriptor,
                                source_sha256=json_sha256(descriptor))
    except (TimeoutError, asyncio.CancelledError, _StrictInputError) as error:
        clear_failure_frames(error)
        raise
    except Exception as error:
        clear_failure_frames(error)
        raise ValueError(SOURCE_ERROR) from None
    finally:
        frame = mapped = packed = counts = block = array = original = expected = actual = None
        computed = view = pinned = stream = fp_stream = pc_stream = None
