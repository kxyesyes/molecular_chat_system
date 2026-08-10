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


def test_binding_resolver_rejects_untrusted_selector_syntax():
    with pytest.raises(BindingResolutionError, match="Unsupported"):
        BindingResolver().resolve(
            "$.outputs[*].secret",
            "identity",
            request={},
            outputs={},
        )
