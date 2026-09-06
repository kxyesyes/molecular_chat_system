import pytest

from src.agent.planning import BindingResolutionError, BindingResolver


def test_binding_resolver_reads_upstream_output_as_smiles_text():
    value = BindingResolver().resolve(
        "$.outputs.molecules",
        "smiles_text",
        request={"query": "design"},
        outputs={
            "molecules": [
                {"smiles": "CCO"},
                {"smiles": "OCC"},
                {"smiles": "CCN"},
            ]
        },
    )
    assert value == "CCO\nOCC\nCCN"


def test_binding_resolver_preserves_raw_target_records():
    targets = [{"gene_symbol": "EGFR", "similarity": 0.91}]
    value = BindingResolver().resolve(
        "$.outputs.targets",
        "identity",
        request={"query": "analyze"},
        outputs={"targets": targets},
    )
    assert value == targets


def test_binding_resolver_returns_isolated_structured_workflow_payload():
    metadata = {
        "requested_count": 10,
        "capabilities": {"scientific_tools": True},
        "secret": "must-not-bind",
    }
    outputs = {
        "target": [
            {
                "gene_symbol": "PDE5A",
                "source": "UniProt",
                "quality": {"status": "official"},
            }
        ],
        "unrelated": {"secret": "must-not-bind"},
    }

    value = BindingResolver().resolve(
        "$.workflow",
        "identity",
        request={"query": "设计 10 个", "metadata": metadata},
        outputs=outputs,
    )

    assert value == {
        "query": "设计 10 个",
        "metadata": {"requested_count": 10},
        "outputs": {
            "target": [
                {
                    "gene_symbol": "PDE5A",
                    "source": "UniProt",
                    "quality": {"status": "official"},
                }
            ]
        },
    }
    assert value["metadata"] is not metadata
    assert value["outputs"] is not outputs
    value["metadata"]["requested_count"] = 3
    value["outputs"]["target"][0]["quality"]["status"] = "mutated"
    assert metadata["requested_count"] == 10
    assert outputs["target"][0]["quality"]["status"] == "official"


def test_workflow_binding_can_expose_only_explicit_scientific_output_keys():
    outputs = {
        "target": {"gene_symbol": "PDE5A"},
        "molecules": [{"smiles": "CCO"}],
        "properties": [{"smiles": "CCO", "properties": {"qed": 0.7}}],
        "admet": [],
        "activity": [],
        "secret": {"token": "must-not-bind"},
    }

    value = BindingResolver().resolve(
        "$.workflow",
        "identity",
        request={"query": "rank", "metadata": {"docking_top_n": 3}},
        outputs=outputs,
        workflow_output_keys=("molecules", "properties", "admet", "activity"),
        workflow_metadata_keys=("docking_top_n",),
    )

    assert value == {
        "query": "rank",
        "metadata": {"docking_top_n": 3},
        "outputs": {
            "molecules": [{"smiles": "CCO"}],
            "properties": [
                {"smiles": "CCO", "properties": {"qed": 0.7}}
            ],
            "admet": [],
            "activity": [],
        },
    }
    assert "secret" not in value["outputs"]


def test_workflow_binding_materializes_missing_optional_outputs_as_empty_lists():
    value = BindingResolver().resolve(
        "$.workflow",
        "identity",
        request={"query": "rank", "metadata": {"docking_top_n": 2}},
        outputs={
            "molecules": [{"smiles": "CCO"}],
            "properties": [{"smiles": "CCO", "properties": {"qed": 0.7}}],
        },
        workflow_output_keys=("molecules", "properties", "admet", "activity"),
        workflow_optional_output_keys=("admet", "activity"),
        workflow_metadata_keys=("docking_top_n",),
    )

    assert value["outputs"]["admet"] == []
    assert value["outputs"]["activity"] == []


def test_workflow_binding_preserves_only_recursive_output_trust_signals():
    outputs = {
        "target": {"gene_symbol": "PDE5A", "source": "UniProt"},
        "quality": {
            "provider": {"fallback_used": True, "debug": "drop-me"},
            "request_id": "drop-me",
        },
        "unrelated": {"fallback_used": True},
    }

    value = BindingResolver().resolve(
        "$.workflow",
        "identity",
        request={
            "query": "Generate 2 molecules",
            "metadata": {
                "requested_count": 2,
                "quality": {
                    "provider": {"fallback_used": True, "debug": "drop-me"}
                },
            },
            "quality": {
                "provider": {"fallback_used": True, "debug": "drop-me"}
            },
        },
        outputs=outputs,
    )

    assert value == {
        "query": "Generate 2 molecules",
        "metadata": {
            "requested_count": 2,
            "quality": {"provider": {"fallback_used": True}},
        },
        "quality": {"provider": {"fallback_used": True}},
        "outputs": {
            "target": {"gene_symbol": "PDE5A", "source": "UniProt"},
            "quality": {"provider": {"fallback_used": True}},
        },
        "trust_envelope": {"signals": ({"fallback_used": True},)},
    }
    with pytest.raises(TypeError, match="immutable"):
        value["outputs"]["quality"]["provider"]["fallback_used"] = False
    with pytest.raises(TypeError, match="immutable"):
        value["trust_envelope"]["signals"][0]["fallback_used"] = False


def test_ordinary_target_selector_preserves_legacy_raw_shape():
    target = {
        "data": [{"gene_symbol": "PDE5A", "source": "UniProt"}],
        "quality": {"provider": {"fallback_used": False}},
    }

    value = BindingResolver().resolve(
        "$.outputs.target",
        "identity",
        request={"query": "design"},
        outputs={"target": target},
    )

    assert value is target


def test_binding_resolver_rejects_untrusted_selector_syntax():
    with pytest.raises(BindingResolutionError, match="Unsupported"):
        BindingResolver().resolve(
            "$.outputs[*].secret",
            "identity",
            request={},
            outputs={},
        )
