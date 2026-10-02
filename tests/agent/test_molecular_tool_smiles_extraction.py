from src.agent.tools.property_calculator import PropertyCalculator


def test_line_delimited_workflow_smiles_do_not_create_overlapping_fragments():
    expected = [
        "COc1ccc(Cl)cc1C(=O)NCC(O)c1cccc2nncn12",
        "CC(NS(C)(=O)=O)c1cn(-c2ccccc2)nn1",
        "CN(C)S(=O)(=O)Cc1ccc(Cl)c(F)c1",
        "COc1ccc(NC(=O)C2CCC(=O)OC2)cc1",
        "Cc1nnc(SCC#N)n1-c1cccc(F)c1",
        "COC(=O)c1cc(Cl)ccc1Br",
        "CN(C)S(=O)(=O)Cc1cccs1",
        "N#CCSc1nnc(N)n1-c1cccc(F)c1",
        "CCc1nc(-c2ccc(OC(C)C)cc2)no1",
        "CN(C)c1cccc(NC(=O)Nc2nccs2)c1",
    ]

    actual = PropertyCalculator().extract_smiles("\n".join(expected))

    assert actual == expected
    assert "C(=O)NCC(O)" not in actual


def test_line_delimited_workflow_smiles_are_canonicalized_before_deduplication():
    actual = PropertyCalculator().extract_smiles("CCO\nOCC\nCCN")

    assert actual == ["CCO", "CCN"]
