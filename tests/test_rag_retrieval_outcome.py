"""Core diagnostics contracts; synthetic data is not scientific evidence.

Controlled search doubles below test malformed output, not FAISS accuracy.
"""
from dataclasses import replace
from fractions import Fraction
import inspect

import faiss
import numpy as np
import pandas as pd
import pytest

from src.rag import retrieval
from src.rag.index import (
    CURRENT_SCHEMA_VERSION,
    RAGIndexCompatibilityError,
    RAGIndexManifest,
    atomic_save_index_pair,
    file_sha256,
    load_manifest,
    manifest_path,
)


@pytest.fixture
def synthetic_index(tmp_path):
    source = tmp_path / "molecules.csv"
    pd.DataFrame({"SMILES": ["CCO", "CCN", "CCC"]}).to_csv(source, index=False)
    index = faiss.IndexFlatIP(2)
    index.add(np.asarray([[1., 0.], [0., 1.]], dtype=np.float32))
    manifest = RAGIndexManifest(
        schema_version=CURRENT_SCHEMA_VERSION,
        source_path=str(source), source_sha256=file_sha256(source),
        index_sha256="", embedding_model="synthetic-test-model",
        vector_dimension=2, vector_count=2, row_mapping=[0, 2],
        created_at="2026-09-25T00:00:00+00:00",
    )
    path = tmp_path / "synthetic.index"
    atomic_save_index_pair(index, path, manifest, faiss_module=faiss)
    return dict(
        index=faiss.read_index(str(path)),
        manifest=load_manifest(manifest_path(path)),
        molecules=pd.read_csv(source), source_path=source,
        index_sha256=file_sha256(path), embedding_model="synthetic-test-model",
        embedding=np.asarray([0., 3.], dtype=np.float32), k=2,
    )


def controlled_search(monkeypatch, kwargs, scores, labels):
    """Counted non-scientific search double on a real synthetic FAISS index."""
    calls = []

    def search(vector, k):
        calls.append((vector.copy(), k))
        return scores, labels

    monkeypatch.setattr(kwargs["index"], "search", search)
    return calls


def test_legacy_filters_invalid_hits(synthetic_index, monkeypatch):
    calls = controlled_search(monkeypatch, synthetic_index,
                              np.asarray([[1., 1.]]), np.asarray([[1, -1]]))
    result = retrieval.search_molecular_index(**synthetic_index)
    assert [row["source_index"] for row in result] == [2]
    assert result[0]["SMILES"] == "CCC"
    assert len(calls) == 1


def test_legacy_truncates_mismatched_rows(synthetic_index, monkeypatch):
    calls = controlled_search(monkeypatch, synthetic_index,
                              np.asarray([[1., .5]]), np.asarray([[1]]))
    result = retrieval.search_molecular_index(**synthetic_index)
    assert [row["source_index"] for row in result] == [2]
    assert result[0]["similarity_score"] == 1.
    assert len(calls) == 1


def test_outcome_preserves_invalid_hit_diagnostics(synthetic_index, monkeypatch):
    assert callable(getattr(retrieval, "search_molecular_index_outcome", None))
    calls = controlled_search(monkeypatch, synthetic_index,
                              np.asarray([[1., 1.]]), np.asarray([[1, -1]]))
    result = retrieval.search_molecular_index_outcome(**synthetic_index)
    assert result["diagnostics"]["status"] == "invalid_discard"
    assert result["diagnostics"]["accepted_count"] == 1
    assert result["diagnostics"]["discarded_count"] == 1
    assert result["records"][0]["source_index"] == 2
    assert len(calls) == 1


def outcome(kwargs):
    api = getattr(retrieval, "search_molecular_index_outcome", None)
    assert callable(api), "missing outcome API"
    return api(**kwargs)


def assert_diagnostics(result, *, status="valid_hits", requested=2, effective=2,
                       searched=True, scores=2, labels=2, accepted=2,
                       discarded=0, reasons=()):
    assert type(result) is dict
    assert set(result) == {"records", "diagnostics"}
    assert type(result["records"]) is list
    assert type(result["diagnostics"]) is dict
    assert result["diagnostics"] == {
        "version": "1", "status": status,
        "requested_k": requested, "effective_k": effective,
        "index_search_executed": searched,
        "score_count": scores, "label_count": labels,
        "accepted_count": accepted, "discarded_count": discarded,
        "reason_codes": list(reasons),
    }
    assert len(result["records"]) == accepted
    assert result["diagnostics"]["index_search_executed"] is searched
    for key in ("requested_k", "effective_k", "score_count", "label_count",
                "accepted_count", "discarded_count"):
        value = result["diagnostics"][key]
        assert value is None or type(value) is int


def persist_synthetic_shape(kwargs, *, mapping):
    """Persist only synthetic temporary test assets through production helpers."""
    index = faiss.IndexFlatIP(2)
    if mapping:
        index.add(np.asarray([[1., 0.]] * len(mapping), dtype=np.float32))
    path = kwargs["source_path"].parent / "reshaped.index"
    manifest = replace(kwargs["manifest"], row_mapping=mapping,
                       vector_count=len(mapping))
    atomic_save_index_pair(index, path, manifest, faiss_module=faiss)
    kwargs.update(index=faiss.read_index(str(path)),
                  manifest=load_manifest(manifest_path(path)),
                  index_sha256=file_sha256(path))


def test_outcome_has_same_keyword_only_signature():
    api = getattr(retrieval, "search_molecular_index_outcome", None)
    assert callable(api)
    assert inspect.signature(api) == inspect.signature(retrieval.search_molecular_index)
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY
               for p in inspect.signature(api).parameters.values())


@pytest.mark.parametrize("k", [1, 2, 100, np.int64(2)])
def test_real_faiss_mapping_score_parity_and_single_dispatch(
    synthetic_index, monkeypatch, k,
):
    kwargs = synthetic_index
    kwargs["k"] = k
    original_embedding = kwargs["embedding"].copy()
    native_search = kwargs["index"].search
    native_validate = retrieval.validate_manifest
    native_normalize = faiss.normalize_L2
    calls, validations, normalizations = [], [], []

    def counted_search(vector, count):
        calls.append((vector.copy(), count))
        return native_search(vector, count)

    def counted_validate(*args, **kw):
        validations.append(kw)
        return native_validate(*args, **kw)

    def counted_normalize(vector):
        normalizations.append(vector.copy())
        return native_normalize(vector)

    monkeypatch.setattr(kwargs["index"], "search", counted_search)
    monkeypatch.setattr(retrieval, "validate_manifest", counted_validate)
    monkeypatch.setattr(faiss, "normalize_L2", counted_normalize)
    result = outcome(kwargs)
    assert len(calls) == len(validations) == len(normalizations) == 1
    legacy = retrieval.search_molecular_index(**kwargs)
    assert len(calls) == len(validations) == len(normalizations) == 2
    n = min(int(k), 2)
    assert result["records"] == legacy
    assert [r["source_index"] for r in legacy] == [2, 0][:n]
    assert [r["SMILES"] for r in legacy] == ["CCC", "CCO"][:n]
    assert [r["similarity_score"] for r in legacy] == [1., 0.][:n]
    manifest = kwargs["manifest"]
    for record, label in zip(legacy, [1, 0]):
        assert record["provenance"] == {
            "source_path": manifest.source_path,
            "source_sha256": manifest.source_sha256,
            "index_sha256": manifest.index_sha256,
            "embedding_model": manifest.embedding_model,
            "manifest_schema_version": manifest.schema_version,
            "builder_version": manifest.builder_version, "vector_label": label,
        }
    for vector, count in calls:
        np.testing.assert_array_equal(vector, [[0., 1.]])
        assert vector.dtype == np.float32
        assert count == n
    np.testing.assert_array_equal(kwargs["embedding"], original_embedding)
    assert_diagnostics(result, requested=int(k), effective=n,
                       scores=n, labels=n, accepted=n)


def test_validated_zero_index_has_zero_counts_without_search(synthetic_index, monkeypatch):
    persist_synthetic_shape(synthetic_index, mapping=[])
    calls = controlled_search(monkeypatch, synthetic_index, None, None)
    result = outcome(synthetic_index)
    assert_diagnostics(result, status="valid_empty", effective=0, searched=False,
                       scores=0, labels=0, accepted=0)
    assert retrieval.search_molecular_index(**synthetic_index) == []
    assert calls == []


@pytest.mark.parametrize("bad,observed", [
    pytest.param(None, None, id="none"),
    pytest.param([[1., 0.]], None, id="list"),
    pytest.param(((1., 0.),), None, id="tuple"),
    pytest.param(np.asarray(1.), None, id="scalar"),
    pytest.param(np.asarray([1., 0.]), None, id="rank-one"),
    pytest.param(np.zeros((1, 1, 2)), None, id="rank-three"),
    pytest.param(np.zeros((0, 2)), None, id="zero-rows"),
    pytest.param(np.zeros((2, 2)), None, id="extra-rows"),
    pytest.param(np.zeros((1, 0)), 0, id="empty-row"),
    pytest.param(np.zeros((1, 1)), 1, id="short-row"),
    pytest.param(np.zeros((1, 3)), 3, id="long-row"),
])
@pytest.mark.parametrize("side", ["scores", "labels"])
def test_invalid_shapes_report_only_observable_counts(
    synthetic_index, monkeypatch, bad, observed, side,
):
    scores, labels = np.asarray([[1., 0.]]), np.asarray([[1, 0]])
    if side == "scores":
        scores = bad
    else:
        labels = bad
    calls = controlled_search(monkeypatch, synthetic_index, scores, labels)
    result = outcome(synthetic_index)
    assert_diagnostics(result, status="invalid_discard", accepted=0, discarded=None,
                       scores=observed if side == "scores" else 2,
                       labels=observed if side == "labels" else 2,
                       reasons=["invalid_result_shape"])
    assert len(calls) == 1


@pytest.mark.parametrize("scores,labels,sc,lc", [
    (None, None, None, None),
    (np.empty((1, 0)), np.empty((1, 0)), 0, 0),
    (np.ones((1, 1)), np.zeros((1, 1), dtype=int), 1, 1),
    (np.ones((1, 3)), np.zeros((1, 3), dtype=int), 3, 3),
])
def test_matching_but_wrong_shapes_are_not_valid_empty(
    synthetic_index, monkeypatch, scores, labels, sc, lc,
):
    calls = controlled_search(monkeypatch, synthetic_index, scores, labels)
    result = outcome(synthetic_index)
    assert_diagnostics(result, status="invalid_discard", accepted=0, discarded=None,
                       scores=sc, labels=lc, reasons=["invalid_result_shape"])
    assert len(calls) == 1


@pytest.mark.parametrize("label", [-1, 2, 99, True, np.bool_(False),
                                  1., "1", None, complex(1, 0)])
def test_invalid_labels_are_discarded_once(synthetic_index, monkeypatch, label):
    calls = controlled_search(monkeypatch, synthetic_index, np.asarray([[1., .5]]),
                              np.asarray([[1, label]], dtype=object))
    result = outcome(synthetic_index)
    assert_diagnostics(result, status="invalid_discard", accepted=1, discarded=1,
                       reasons=["invalid_label"])
    assert result["records"][0]["source_index"] == 2
    assert len(calls) == 1


@pytest.mark.parametrize("score", [float("nan"), float("inf"), -float("inf"),
                                  complex(1, 0), True, np.bool_(False), "1", None])
def test_invalid_scores_are_discarded_once(synthetic_index, monkeypatch, score):
    calls = controlled_search(monkeypatch, synthetic_index,
                              np.asarray([[1., score]], dtype=object), np.asarray([[1, 0]]))
    result = outcome(synthetic_index)
    assert_diagnostics(result, status="invalid_discard", accepted=1, discarded=1,
                       reasons=["invalid_score"])
    assert result["records"][0]["source_index"] == 2
    assert len(calls) == 1


@pytest.mark.parametrize("score", [0, -1., np.int32(1), np.int64(1),
                                  np.float32(.5), np.float64(.5), Fraction(1, 2)])
@pytest.mark.parametrize("label", [0, np.int32(0), np.int64(0), np.uint64(0)])
def test_real_scores_and_integral_labels_are_accepted(
    synthetic_index, monkeypatch, score, label,
):
    controlled_search(monkeypatch, synthetic_index,
                      np.asarray([[1., score]], dtype=object),
                      np.asarray([[1, label]], dtype=object))
    result = outcome(synthetic_index)
    assert_diagnostics(result)
    assert result["records"][1]["similarity_score"] == float(score)


@pytest.mark.parametrize("mapping,labels", [([0, 2], [1, 1]), ([2, 2], [0, 1])])
def test_duplicate_accepted_labels_or_source_rows_are_discarded(
    synthetic_index, monkeypatch, mapping, labels,
):
    synthetic_index["manifest"] = replace(synthetic_index["manifest"], row_mapping=mapping)
    calls = controlled_search(monkeypatch, synthetic_index,
                              np.asarray([[1., .5]]), np.asarray([labels]))
    result = outcome(synthetic_index)
    assert_diagnostics(result, status="invalid_discard", accepted=1, discarded=1,
                       reasons=["duplicate_hit"])
    assert result["records"][0]["source_index"] == 2
    assert len(calls) == 1
    # The legacy list intentionally retains duplicates.
    assert len(retrieval.search_molecular_index(**synthetic_index)) == 2


@pytest.mark.parametrize("scores,labels,reasons", [
    ([float("nan"), float("nan")], [-1, 0], ["invalid_label", "invalid_score"]),
    ([float("nan"), 1.], [0, -1], ["invalid_score", "invalid_label"]),
    ([1., 1.], [-1, -1], ["invalid_label"]),
    ([float("nan"), float("inf")], [0, 1], ["invalid_score"]),
])
def test_all_invalid_counts_once_and_orders_unique_reasons(
    synthetic_index, monkeypatch, scores, labels, reasons,
):
    controlled_search(monkeypatch, synthetic_index,
                      np.asarray([scores]), np.asarray([labels]))
    result = outcome(synthetic_index)
    assert_diagnostics(result, status="invalid_discard", accepted=0, discarded=2,
                       reasons=reasons)


def test_pair_priority_and_first_occurrence_reason_order(synthetic_index, monkeypatch):
    persist_synthetic_shape(synthetic_index, mapping=[0, 2, 1, 0, 2, 1])
    synthetic_index["k"] = 6
    calls = controlled_search(
        monkeypatch, synthetic_index,
        np.asarray([[1., float("nan"), 1., float("nan"), 1., 1.]]),
        np.asarray([[0, 0, 0, -1, -1, 1]]),
    )
    result = outcome(synthetic_index)
    assert_diagnostics(result, status="invalid_discard", requested=6, effective=6,
                       scores=6, labels=6, accepted=2, discarded=4,
                       reasons=["invalid_score", "duplicate_hit", "invalid_label"])
    assert [r["source_index"] for r in result["records"]] == [0, 2]
    assert len(calls) == 1


def test_invalid_score_does_not_reserve_label_or_source_row(synthetic_index, monkeypatch):
    synthetic_index["manifest"] = replace(synthetic_index["manifest"], row_mapping=[2, 2])
    controlled_search(monkeypatch, synthetic_index,
                      np.asarray([[float("nan"), 1.]]), np.asarray([[0, 1]]))
    result = outcome(synthetic_index)
    assert_diagnostics(result, status="invalid_discard", accepted=1, discarded=1,
                       reasons=["invalid_score"])
    assert result["records"][0]["provenance"]["vector_label"] == 1


@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("api_name", ["search_molecular_index", "search_molecular_index_outcome"])
@pytest.mark.parametrize("embedding", [[], [1.], [1., 2., 3.], [[1., 0.]],
                                      [0., 0.], [float("nan"), 1.],
                                      [float("inf"), 1.], [-float("inf"), 1.]])
def test_invalid_queries_propagate_before_any_search(
    synthetic_index, monkeypatch, empty, api_name, embedding,
):
    if empty:
        persist_synthetic_shape(synthetic_index, mapping=[])
    synthetic_index["embedding"] = embedding
    calls = controlled_search(monkeypatch, synthetic_index, None, None)
    api = getattr(retrieval, api_name, None)
    assert callable(api)
    with pytest.raises(ValueError, match="RAG query embedding is empty, invalid or incompatible"):
        api(**synthetic_index)
    assert calls == []


@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("api_name", ["search_molecular_index", "search_molecular_index_outcome"])
@pytest.mark.parametrize("k", [0, -1, True, 1.5, "2", None, np.bool_(True)])
def test_invalid_k_propagates_before_any_search(synthetic_index, monkeypatch, empty, api_name, k):
    if empty:
        persist_synthetic_shape(synthetic_index, mapping=[])
    synthetic_index["k"] = k
    calls = controlled_search(monkeypatch, synthetic_index, None, None)
    api = getattr(retrieval, api_name, None)
    assert callable(api)
    with pytest.raises(ValueError, match="RAG result count must be a positive integer"):
        api(**synthetic_index)
    assert calls == []


@pytest.mark.parametrize("api_name", ["search_molecular_index", "search_molecular_index_outcome"])
@pytest.mark.parametrize("mutation,message", [
    ({"schema_version": 999}, "schema version mismatch"),
    ({"source_sha256": "0" * 64}, "source SHA256 mismatch"),
    ({"index_sha256": "0" * 64}, "index SHA256 mismatch"),
    ({"embedding_model": "other-synthetic-model"}, "embedding model mismatch"),
    ({"vector_dimension": 3}, "vector dimension mismatch"),
    ({"vector_count": 3}, "vector count mismatch"),
    ({"row_mapping": [0]}, "row_mapping length mismatch"),
    ({"row_mapping": [0, -1]}, "out-of-range source row"),
    ({"row_mapping": [0, 3]}, "out-of-range source row"),
    ({"row_mapping": [0, True]}, "out-of-range source row"),
    ({"row_mapping": [0, "2"]}, "out-of-range source row"),
    ({"builder_version": "unknown"}, "generation is unavailable or incompatible"),
])
def test_manifest_failures_propagate_without_search(
    synthetic_index, monkeypatch, api_name, mutation, message,
):
    synthetic_index["manifest"] = replace(synthetic_index["manifest"], **mutation)
    calls = controlled_search(monkeypatch, synthetic_index, None, None)
    api = getattr(retrieval, api_name, None)
    assert callable(api)
    with pytest.raises(RAGIndexCompatibilityError, match=message):
        api(**synthetic_index)
    assert calls == []


@pytest.mark.parametrize("api_name", ["search_molecular_index", "search_molecular_index_outcome"])
@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("failure,message", [
    ("path", "source path mismatch"),
    ("missing", "source file is unavailable"),
    ("changed", "source SHA256 mismatch"),
    ("index_digest", "generation is unavailable or incompatible"),
])
def test_source_and_generation_failures_are_not_empty_results(
    synthetic_index, monkeypatch, api_name, empty, failure, message,
):
    if empty:
        persist_synthetic_shape(synthetic_index, mapping=[])
    if failure == "path":
        synthetic_index["source_path"] = synthetic_index["source_path"].with_name("other.csv")
    elif failure == "missing":
        synthetic_index["source_path"].unlink()
    elif failure == "changed":
        synthetic_index["source_path"].write_text("SMILES\nCCN\n", encoding="utf-8")
    else:
        synthetic_index["index_sha256"] = ""
    calls = controlled_search(monkeypatch, synthetic_index, None, None)
    api = getattr(retrieval, api_name, None)
    assert callable(api)
    with pytest.raises(RAGIndexCompatibilityError, match=message):
        api(**synthetic_index)
    assert calls == []


@pytest.mark.parametrize("api_name", ["search_molecular_index", "search_molecular_index_outcome"])
def test_backend_exception_propagates_unchanged(synthetic_index, monkeypatch, api_name):
    error = RuntimeError("controlled non-scientific backend failure")
    calls = []

    def broken_search(vector, k):
        calls.append(k)
        raise error

    monkeypatch.setattr(synthetic_index["index"], "search", broken_search)
    api = getattr(retrieval, api_name, None)
    assert callable(api)
    with pytest.raises(RuntimeError) as raised:
        api(**synthetic_index)
    assert raised.value is error
    assert calls == [2]


@pytest.mark.parametrize("api_name", ["search_molecular_index", "search_molecular_index_outcome"])
def test_projection_exception_propagates_unchanged(synthetic_index, monkeypatch, api_name):
    error = RuntimeError("controlled non-scientific projection failure")
    projected = []

    def broken_dict(series, *args, **kwargs):
        projected.append(series.name)
        raise error

    calls = controlled_search(monkeypatch, synthetic_index,
                              np.asarray([[1., 0.]]), np.asarray([[1, 0]]))
    monkeypatch.setattr(pd.Series, "to_dict", broken_dict)
    api = getattr(retrieval, api_name, None)
    assert callable(api)
    with pytest.raises(RuntimeError) as raised:
        api(**synthetic_index)
    assert raised.value is error
    assert projected == [2]
    assert len(calls) == 1


def test_only_accepted_pairs_are_projected(synthetic_index, monkeypatch):
    persist_synthetic_shape(synthetic_index, mapping=[0, 2, 1, 0])
    synthetic_index["k"] = 4
    native_dict = pd.Series.to_dict
    projected = []

    def counted_dict(series, *args, **kwargs):
        projected.append(series.name)
        return native_dict(series, *args, **kwargs)

    monkeypatch.setattr(pd.Series, "to_dict", counted_dict)
    controlled_search(monkeypatch, synthetic_index,
                      np.asarray([[1., 1., float("nan"), 1.]]), np.asarray([[1, -1, 0, 1]]))
    result = outcome(synthetic_index)
    assert_diagnostics(result, status="invalid_discard", requested=4, effective=4,
                       scores=4, labels=4, accepted=1, discarded=3,
                       reasons=["invalid_label", "invalid_score", "duplicate_hit"])
    assert projected == [2]


def test_outcome_records_are_deeply_detached_not_a_loaded_source_receipt(synthetic_index):
    frame = synthetic_index["molecules"]
    nested = {"items": [{"tags": ["original"]}]}
    frame["nested"] = [nested, None, nested]
    frame["missing"] = [np.nan, pd.NA, None]
    frame["source_index"] = [-99, -99, -99]
    frame["provenance"] = [{"untrusted": []}] * 3
    result = outcome(synthetic_index)
    assert_diagnostics(result)
    records = result["records"]
    assert all("missing" not in row for row in records)
    assert all(type(row) is dict for row in records)
    assert records[0]["nested"] is not nested
    assert "untrusted" not in records[0]["provenance"]
    nested["items"][0]["tags"].append("source-change")
    synthetic_index["manifest"].row_mapping[:] = [1, 1]
    assert records[0]["nested"]["items"][0]["tags"] == ["original"]
    assert [row["source_index"] for row in records] == [2, 0]
    records[0]["nested"]["items"][0]["tags"].append("result-change")
    records[0]["provenance"]["embedding_model"] = "result-change"
    assert nested["items"][0]["tags"] == ["original", "source-change"]
    assert synthetic_index["manifest"].embedding_model == "synthetic-test-model"


def test_legacy_projection_keeps_nested_alias_and_nan_filter(synthetic_index):
    nested = {"items": []}
    synthetic_index["molecules"]["nested"] = [None, None, nested]
    synthetic_index["molecules"]["missing"] = [np.nan, None, pd.NA]
    records = retrieval.search_molecular_index(**synthetic_index)
    assert records[0]["nested"] is nested
    assert all("missing" not in row for row in records)


@pytest.mark.parametrize("scores,labels,expected", [
    ([1., float("nan")], [1, 0], [2]),
    ([1., float("inf")], [1, 0], [2]),
    ([1.], [1, 0], [2]),
    ([], [], []),
    ([1., 1.], [True, 0], [0]),
    ([True, 1.], [1, 0], [2, 0]),
])
def test_legacy_filtering_and_truncation_remain_unchanged(
    synthetic_index, monkeypatch, scores, labels, expected,
):
    calls = controlled_search(monkeypatch, synthetic_index,
                              np.asarray([scores]), np.asarray([labels], dtype=object))
    result = retrieval.search_molecular_index(**synthetic_index)
    assert [row["source_index"] for row in result] == expected
    assert len(calls) == 1
