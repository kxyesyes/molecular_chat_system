"""Load-time arithmetic checks using only synthetic temporary databases."""

import numpy as np
import pytest

from src.reverse_target.predictor import ReverseTargetPredictor


CACHE_ERROR = "Invalid reverse-target popcount cache."
FINGERPRINT_ERROR = "Invalid reverse-target fingerprints."


@pytest.fixture(autouse=True)
def controlled_popcount_environment(monkeypatch):
    monkeypatch.setenv("REVERSE_TARGET_POPCOUNT_CHUNK_SIZE", "1")
    monkeypatch.setenv("REVERSE_TARGET_MORGAN_WEIGHT", "0.7")


def close_fixture_mmaps(predictor):
    for name in ("morgan_fps", "maccs_fps", "morgan_popcounts", "maccs_popcounts"):
        handle = getattr(getattr(predictor, name, None), "_mmap", None)
        if handle is not None:
            handle.close()


def make_database(directory, *, cached=True):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "chembl_data_with_fps.tsv").write_text(
        "molecule_chembl_id\tcanonical_smiles\ttarget_name\tstandard_type\tstandard_value\torganism\n"
        "CHEMBL1\tCCO\tSynthetic target A\tIC50\t1\tHuman\n"
        "CHEMBL2\tCCC\tSynthetic target B\tIC50\t2\tHuman\n",
        encoding="utf-8",
    )
    predictor = ReverseTargetPredictor(directory)
    pairs = [predictor.compute_query_fingerprints(smiles) for smiles in ("CCO", "CCC")]
    for index, kind, width in ((0, "morgan", 2048), (1, "maccs", 166)):
        fingerprints = np.stack([pair[index] for pair in pairs])
        assert fingerprints.shape == (2, width)
        np.save(getattr(predictor, f"{kind}_fp_path"), fingerprints)
        if cached:
            np.save(
                getattr(predictor, f"{kind}_popcount_path"),
                fingerprints.sum(axis=1, dtype=np.uint32).astype(np.uint16),
            )
    return predictor


def test_actual_load_rejects_same_length_wrong_cache(tmp_path):
    predictor = make_database(tmp_path)
    try:
        predictor.load()
        assert predictor.predict("CCO")[0]["final_similarity"] == 1.0
    finally:
        close_fixture_mmaps(predictor)

    counts = np.load(predictor.morgan_popcount_path)
    counts[0] += 1
    np.save(predictor.morgan_popcount_path, counts)
    original = predictor.morgan_popcount_path.read_bytes()
    fresh = ReverseTargetPredictor(tmp_path)
    try:
        with pytest.raises(ValueError):
            fresh.load()
        assert not fresh._loaded
        assert fresh.morgan_popcount_path.read_bytes() == original
    finally:
        close_fixture_mmaps(fresh)


def test_actual_cached_and_missing_predictions_match(tmp_path):
    cached = make_database(tmp_path / "cached")
    missing = make_database(tmp_path / "missing", cached=False)
    original = {
        path: path.read_bytes()
        for path in (cached.morgan_popcount_path, cached.maccs_popcount_path)
    }
    try:
        cached.load()
        missing.load()
        for smiles in ("CCO", "CCC"):
            for combine in (True, False):
                expected = cached.predict(smiles, threshold=0, combine_by_target=combine)
                assert expected[0]["final_similarity"] == 1.0
                assert len(expected) == 2
                assert missing.predict(smiles, threshold=0, combine_by_target=combine) == expected
        for kind in ("morgan", "maccs"):
            expected = getattr(cached, f"{kind}_popcounts")
            computed = getattr(missing, f"{kind}_popcounts")
            np.testing.assert_array_equal(computed, expected)
            assert computed.dtype == np.uint16
            np.testing.assert_array_equal(np.load(getattr(missing, f"{kind}_popcount_path")), expected)
        assert all(path.read_bytes() == before for path, before in original.items())
    finally:
        close_fixture_mmaps(cached)
        close_fixture_mmaps(missing)


@pytest.mark.parametrize("kind", ["morgan", "maccs"])
def test_actual_load_rejects_swapped_cache_counts(tmp_path, kind):
    predictor = make_database(tmp_path)
    other = "maccs" if kind == "morgan" else "morgan"
    path = getattr(predictor, f"{kind}_popcount_path")
    swapped = np.load(getattr(predictor, f"{other}_popcount_path"))
    assert not np.array_equal(np.load(path), swapped)
    np.save(path, swapped)
    original = {p: p.read_bytes() for p in tmp_path.iterdir()}
    try:
        with pytest.raises(ValueError, match=f"^{CACHE_ERROR.replace('.', '[.]')}$"):
            predictor.load()
        assert not predictor._loaded
        assert {p: p.read_bytes() for p in tmp_path.iterdir()} == original
    finally:
        close_fixture_mmaps(predictor)


def test_bad_first_cache_does_not_create_missing_sibling(tmp_path):
    predictor = make_database(tmp_path, cached=False)
    predictor.morgan_popcount_path.write_bytes(b"corrupt synthetic cache")
    original = {p: p.read_bytes() for p in tmp_path.iterdir()}
    try:
        with pytest.raises(ValueError) as error:
            predictor.load()
        assert str(error.value) == CACHE_ERROR
        assert not predictor._loaded
        assert not predictor.maccs_popcount_path.exists()
        assert {p: p.read_bytes() for p in tmp_path.iterdir()} == original
    finally:
        close_fixture_mmaps(predictor)


@pytest.mark.parametrize(
    "counts",
    [
        np.array(2, dtype=np.uint16),
        np.array([[2], [1]], dtype=np.uint16),
        np.array([2], dtype=np.uint16),
        np.array([2, 1, 0], dtype=np.uint16),
        np.array([True, True]),
        np.array([2.0, 1.0]),
        np.array([2, 1], dtype=object),
        np.array(["2", "1"]),
        np.array([2, 1], dtype=np.complex128),
        np.array([-1, 1], dtype=np.int64),
        np.array([4, 1], dtype=np.uint16),
        np.array([2, 2], dtype=np.uint16),
        np.array([1, 2], dtype=np.uint16),  # Same total, different rows.
        np.array([2, np.iinfo(np.uint64).max], dtype=np.uint64),
    ],
    ids=["scalar", "rank2", "short", "long", "bool", "float", "object", "string",
         "complex", "negative", "over-width", "late-wrong", "same-total", "uint64-max"],
)
def test_invalid_existing_cache_is_rejected_without_writes(tmp_path, counts):
    predictor = ReverseTargetPredictor(tmp_path)
    path = tmp_path / "counts.npy"
    np.save(path, counts)
    original = path.read_bytes()
    fingerprints = np.array([[1, 0, 1], [0, 1, 0]], dtype=np.uint8)
    result = None
    try:
        with pytest.raises(ValueError) as error:
            result = predictor._load_or_compute_popcounts(fingerprints, path)
        assert str(error.value) == CACHE_ERROR
        assert path.read_bytes() == original
    finally:
        handle = getattr(result, "_mmap", None)
        if handle is not None:
            handle.close()


@pytest.mark.parametrize("format_name", ["corrupt", "truncated", "npz", "directory"])
def test_unreadable_or_non_array_cache_is_safe_and_unchanged(tmp_path, format_name):
    predictor = ReverseTargetPredictor(tmp_path)
    path = tmp_path / "private-counts.npy"
    if format_name == "corrupt":
        path.write_bytes(b"not a numpy file")
    elif format_name == "truncated":
        np.save(path, np.array([1], dtype=np.uint16))
        path.write_bytes(path.read_bytes()[:-1])
    elif format_name == "npz":
        with path.open("wb") as output:
            np.savez(output, counts=np.array([1], dtype=np.uint16))
    else:
        path.mkdir()
    original = None if path.is_dir() else path.read_bytes()
    with pytest.raises(ValueError) as error:
        predictor._load_or_compute_popcounts(np.array([[1, 0]], dtype=np.uint8), path)
    assert str(error.value) == CACHE_ERROR
    assert error.value.__suppress_context__
    assert path.is_dir() if original is None else path.read_bytes() == original


@pytest.mark.parametrize("cached", [False, True], ids=["missing", "cached"])
@pytest.mark.parametrize(
    "fingerprints",
    [
        [[1, 0]],
        np.array(1, dtype=np.uint8),
        np.array([1, 0], dtype=np.uint8),
        np.ones((2, 1, 1), dtype=np.uint8),
        np.empty((2, 0), dtype=np.uint8),
        np.zeros((2, 65536), dtype=np.uint8),
        np.array([[1, 0], [0, 1]], dtype=float),
        np.array([[1, 0], [0, 1]], dtype=object),
        np.array([[1, 0], [0, 1]], dtype=complex),
        np.array([["1", "0"], ["0", "1"]]),
        np.array([[1, 0], [2, 0]], dtype=np.uint8),
        np.array([[1, 0], [-1, 1]], dtype=np.int16),
        np.array([[1, 0], [0, np.iinfo(np.uint64).max]], dtype=np.uint64),
    ],
    ids=["list", "scalar", "rank1", "rank3", "zero-width", "wide", "float", "object",
         "complex", "string", "late-two", "late-negative", "late-uint64-max"],
)
def test_invalid_fingerprints_never_write(tmp_path, fingerprints, cached):
    predictor = ReverseTargetPredictor(tmp_path)
    path = tmp_path / "counts.npy"
    if cached:
        np.save(path, np.array([1, 1], dtype=np.uint16))
    original = path.read_bytes() if cached else None
    result = None
    try:
        with pytest.raises(ValueError) as error:
            result = predictor._load_or_compute_popcounts(fingerprints, path)
        assert str(error.value) == FINGERPRINT_ERROR
        if cached:
            assert path.read_bytes() == original
        else:
            assert not path.exists()
    finally:
        handle = getattr(result, "_mmap", None)
        if handle is not None:
            handle.close()


@pytest.mark.parametrize("cached", [False, True], ids=["missing", "cached"])
@pytest.mark.parametrize("width", [1, 166, 2048, 65535])
@pytest.mark.parametrize("dtype", [np.bool_, np.uint8, np.int64, np.uint64])
def test_binary_fingerprints_compute_exact_counts(tmp_path, cached, width, dtype):
    predictor = ReverseTargetPredictor(tmp_path)
    path = tmp_path / "counts.npy"
    fingerprints = np.ones((3, width), dtype=dtype)
    fingerprints[0] = 0
    fingerprints[1, ::2] = 0
    expected = np.array([0, width // 2, width], dtype=np.uint16)
    if cached:
        np.save(path, expected)
    result = predictor._load_or_compute_popcounts(fingerprints, path)
    try:
        np.testing.assert_array_equal(result, expected)
        assert result.dtype == np.uint16
        np.testing.assert_array_equal(np.load(path), expected)
    finally:
        handle = getattr(result, "_mmap", None)
        if handle is not None:
            handle.close()


@pytest.mark.parametrize("cached", [False, True], ids=["missing", "cached"])
@pytest.mark.parametrize("dtype", [np.bool_, np.uint8, np.int64])
def test_zero_rows_are_valid(tmp_path, cached, dtype):
    path = tmp_path / "counts.npy"
    if cached:
        np.save(path, np.empty(0, dtype=np.uint16))
    result = ReverseTargetPredictor(tmp_path)._load_or_compute_popcounts(
        np.empty((0, 166), dtype=dtype), path
    )
    try:
        assert result.shape == (0,)
        assert result.dtype == np.uint16
        assert np.load(path).shape == (0,)
    finally:
        handle = getattr(result, "_mmap", None)
        if handle is not None:
            handle.close()


@pytest.mark.parametrize("dtype", [np.int8, np.int16, np.int32, np.int64,
                                         np.uint8, np.uint16, np.uint32, np.uint64])
def test_integer_cache_dtypes_return_canonical_uint16(tmp_path, dtype):
    path = tmp_path / "counts.npy"
    np.save(path, np.array([2, 1], dtype=dtype))
    result = ReverseTargetPredictor(tmp_path)._load_or_compute_popcounts(
        np.array([[1, 1], [1, 0]], dtype=np.uint8), path
    )
    try:
        assert result.dtype == np.uint16
        np.testing.assert_array_equal(result, [2, 1])
    finally:
        handle = getattr(result, "_mmap", None)
        if handle is not None:
            handle.close()


@pytest.mark.parametrize("cached", [False, True], ids=["missing", "cached"])
@pytest.mark.parametrize("setting,expected_sizes", [
    ("1", [1, 1, 1]), ("2", [2, 1]), ("0", [1, 1, 1]),
    ("-2", [1, 1, 1]), ("invalid", [3]), (None, [3]),
])
def test_chunk_setting_and_uint32_sums_are_preserved(tmp_path, monkeypatch, cached, setting, expected_sizes):
    path = tmp_path / "counts.npy"
    fingerprints = np.array([[1, 0], [0, 1], [1, 1]], dtype=np.uint8)
    if cached:
        np.save(path, np.array([1, 1, 2], dtype=np.uint16))
    if setting is None:
        monkeypatch.delenv("REVERSE_TARGET_POPCOUNT_CHUNK_SIZE")
    else:
        monkeypatch.setenv("REVERSE_TARGET_POPCOUNT_CHUNK_SIZE", setting)
    real_sum = np.sum
    sizes = []

    def bounded_sum(array, axis=None, dtype=None, **kwargs):
        assert axis == 1
        assert dtype == np.uint32
        assert np.shares_memory(array, fingerprints)
        sizes.append(array.shape[0])
        return real_sum(array, axis=axis, dtype=dtype, **kwargs)

    monkeypatch.setattr(np, "sum", bounded_sum)
    result = ReverseTargetPredictor(tmp_path)._load_or_compute_popcounts(fingerprints, path)
    try:
        np.testing.assert_array_equal(result, [1, 1, 2])
        assert sizes == expected_sizes
    finally:
        handle = getattr(result, "_mmap", None)
        if handle is not None:
            handle.close()


def test_missing_cache_save_oserror_is_best_effort(tmp_path, monkeypatch):
    path = tmp_path / "counts.npy"
    attempts = []

    def failing_save(destination, counts):
        attempts.append((destination, counts.copy()))
        raise OSError("synthetic read-only destination")

    monkeypatch.setattr(np, "save", failing_save)
    result = ReverseTargetPredictor(tmp_path)._load_or_compute_popcounts(
        np.array([[1, 1, 0], [0, 0, 0]], dtype=np.uint8), path
    )
    np.testing.assert_array_equal(result, [2, 0])
    assert result.dtype == np.uint16
    assert len(attempts) == 1
    assert attempts[0][0] == path
    np.testing.assert_array_equal(attempts[0][1], result)
    assert not path.exists()


@pytest.mark.parametrize("failure", [OSError, ValueError, EOFError, RuntimeError])
def test_cache_load_errors_are_safe(tmp_path, monkeypatch, failure):
    path = tmp_path / "private-counts.npy"
    path.write_bytes(b"synthetic existing file")

    def failing_load(*args, **kwargs):
        raise failure(f"private raw error: {path}")

    monkeypatch.setattr(np, "load", failing_load)
    with pytest.raises(ValueError) as error:
        ReverseTargetPredictor(tmp_path)._load_or_compute_popcounts(np.ones((1, 2), dtype=np.uint8), path)
    assert str(error.value) == CACHE_ERROR
    assert error.value.__suppress_context__
    assert path.read_bytes() == b"synthetic existing file"


def test_cache_load_does_not_swallow_cancellation(tmp_path, monkeypatch):
    path = tmp_path / "counts.npy"
    path.write_bytes(b"synthetic existing file")

    def cancelled_load(*args, **kwargs):
        raise KeyboardInterrupt("synthetic cancellation")

    monkeypatch.setattr(np, "load", cancelled_load)
    with pytest.raises(KeyboardInterrupt):
        ReverseTargetPredictor(tmp_path)._load_or_compute_popcounts(np.ones((1, 2), dtype=np.uint8), path)


@pytest.mark.parametrize("case", ["valid", "rank2", "scalar", "float", "wrong", "bad-fingerprint", "copy-error"])
def test_helper_closes_only_owned_cache_handles(tmp_path, monkeypatch, case):
    fp_path = tmp_path / "fingerprints.npy"
    path = tmp_path / "counts.npy"
    matrix = np.array([[1, 0], [1, 1]], dtype=np.uint8)
    if case == "bad-fingerprint":
        matrix[-1, -1] = 2
    np.save(fp_path, matrix)
    counts = np.array([1, 2], dtype=np.uint16)
    if case == "rank2":
        counts = counts[:, None]
    elif case == "scalar":
        counts = np.array(1, dtype=np.uint16)
    elif case == "float":
        counts = counts.astype(float)
    elif case == "wrong":
        counts[-1] = 1
    np.save(path, counts)
    original = path.read_bytes()
    caller_fps = np.load(fp_path, mmap_mode="r")
    opened = []
    real_load = np.load
    real_array = np.array

    def tracked_load(source, *args, **kwargs):
        assert source == path
        assert kwargs["mmap_mode"] == "r"
        loaded = real_load(source, *args, **kwargs)
        opened.append(loaded)
        return loaded

    def tracked_array(value, *args, **kwargs):
        if isinstance(value, np.ndarray):
            assert value.ndim == 1, "must not copy the fingerprint matrix"
            if case == "copy-error":
                raise OSError("synthetic cache copy error")
        return real_array(value, *args, **kwargs)

    monkeypatch.setattr(np, "load", tracked_load)
    monkeypatch.setattr(np, "array", tracked_array)
    try:
        if case == "valid":
            result = ReverseTargetPredictor(tmp_path)._load_or_compute_popcounts(caller_fps, path)
            assert type(result) is np.ndarray
            assert result.flags.owndata
        else:
            with pytest.raises(ValueError) as error:
                ReverseTargetPredictor(tmp_path)._load_or_compute_popcounts(caller_fps, path)
            assert str(error.value) == (FINGERPRINT_ERROR if case == "bad-fingerprint" else CACHE_ERROR)
        assert len(opened) == 1
        assert opened[0]._mmap.closed
        assert not caller_fps._mmap.closed
        np.testing.assert_array_equal(caller_fps, matrix)
        assert path.read_bytes() == original
        if case == "valid":
            # This overwrite also verifies Windows no longer has a cache mapping.
            np.save(path, np.zeros(2, dtype=np.uint16))
            np.testing.assert_array_equal(result, [1, 2])
    finally:
        for array in opened:
            array._mmap.close()
        caller_fps._mmap.close()


def test_missing_cache_does_not_close_caller_mmap(tmp_path):
    fp_path = tmp_path / "fingerprints.npy"
    np.save(fp_path, np.array([[1, 0], [1, 1]], dtype=np.uint8))
    caller_fps = np.load(fp_path, mmap_mode="r")
    try:
        result = ReverseTargetPredictor(tmp_path)._load_or_compute_popcounts(caller_fps, tmp_path / "counts.npy")
        np.testing.assert_array_equal(result, [1, 2])
        assert not caller_fps._mmap.closed
    finally:
        caller_fps._mmap.close()


@pytest.mark.parametrize("width", [166, 2048])
@pytest.mark.parametrize("dtype,on_bits", [(np.int8, 100), (np.uint8, 128)], ids=["int8", "uint8"])
def test_actual_batch_narrow_integer_cache_matches_missing(tmp_path, dtype, on_bits, width):
    """Correct narrow counts must not overflow before Tanimoto subtraction."""
    predictor = ReverseTargetPredictor(tmp_path)
    fingerprints = np.zeros((1, width), dtype=np.uint8)
    fingerprints[0, :on_bits] = 1
    query = fingerprints[0].copy()
    assert on_bits <= np.iinfo(dtype).max < 2 * on_bits

    cached_path = tmp_path / "narrow_counts.npy"
    np.save(cached_path, np.array([on_bits], dtype=dtype))
    original = cached_path.read_bytes()
    missing_counts = predictor._load_or_compute_popcounts(
        fingerprints, tmp_path / "missing_counts.npy"
    )
    cached_counts = predictor._load_or_compute_popcounts(fingerprints, cached_path)
    try:
        missing_score = predictor.batch_tanimoto_similarity(query, fingerprints, missing_counts)
        assert missing_score[0] == 1.0
        no_popcounts_score = predictor.batch_tanimoto_similarity(query, fingerprints)
        assert no_popcounts_score[0] == 1.0
        np.testing.assert_array_equal(missing_score, no_popcounts_score)
        np.testing.assert_array_equal(cached_counts, missing_counts)
        assert cached_path.read_bytes() == original

        cached_score = predictor.batch_tanimoto_similarity(query, fingerprints, cached_counts)
        assert cached_score[0] == 1.0
        np.testing.assert_array_equal(cached_score, missing_score)
        np.testing.assert_array_equal(cached_score, no_popcounts_score)
    finally:
        for counts in (missing_counts, cached_counts):
            handle = getattr(counts, "_mmap", None)
            if handle is not None:
                handle.close()
