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
    metadata = {"requested_count": 10}
    outputs = {"target": [{"gene_symbol": "PDE5A", "source": "UniProt"}]}

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
            "target": [{"gene_symbol": "PDE5A", "source": "UniProt"}]
        },
    }
    assert value["metadata"] is not metadata
    assert value["outputs"] is not outputs
    value["metadata"]["requested_count"] = 3
    value["outputs"]["extra"] = "new"
    assert metadata == {"requested_count": 10}
    assert "extra" not in outputs


def test_binding_resolver_rejects_untrusted_selector_syntax():
    with pytest.raises(BindingResolutionError, match="Unsupported"):
        BindingResolver().resolve(
            "$.outputs[*].secret",
            "identity",
            request={},
            outputs={},
        )
