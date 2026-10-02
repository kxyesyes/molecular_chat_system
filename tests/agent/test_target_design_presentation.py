from src.agent.contracts import AgentErrorCode, AgentResult, ToolResult
from src.agent.reporting.target_design import present_target_design_result


def test_target_design_presentation_filters_canonical_candidates_and_reports_boundaries():
    result = AgentResult.from_tool_results(
        trace_id="target-design-test",
        skill_name="target_driven_design",
        tool_results=[
            ToolResult.success_result(
                "target_database_search",
                data={"target": "PDE5A", "uniprot_id": "O76074"},
                message="PDE5A target evidence found",
                formatted="RAW TARGET FORMATTER SHOULD NOT BE USED",
                evidence=[{"source": "local_target_db", "id": "O76074"}],
                quality={"step_id": "target_search"},
            ),
            ToolResult.success_result(
                "llm_molecular_generator",
                data=[
                    {
                        "smiles": "CCO",
                        "model": "gmm-llama:latest",
                        "source": "llm",
                    },
                    {
                        "smiles": "OCC",
                        "model": "gmm-llama:latest",
                        "source": "llm",
                    },
                    {
                        "smiles": "PDE5A",
                        "model": "gmm-llama:latest",
                        "source": "llm",
                    },
                    {
                        "smiles": "CCN",
                        "model": "gmm-llama:latest",
                        "source": "llm",
                    },
                ],
                message="generated candidates",
                formatted="RAW GENERATOR FORMATTER SHOULD NOT BE USED",
                quality={
                    "step_id": "molecule_generation",
                    "model": "gmm-llama:latest",
                },
            ),
            ToolResult.success_result(
                "property_calculator",
                data=[
                    {
                        "smiles": "CCO",
                        "properties": {
                            "molecular_weight": 46.07,
                            "logp": -0.001,
                            "tpsa": 20.23,
                            "qed": 0.407,
                        },
                    },
                    {
                        "smiles": "CCN",
                        "properties": {
                            "molecular_weight": 45.08,
                            "logp": -0.035,
                            "tpsa": 26.02,
                            "qed": 0.406,
                        },
                    },
                ],
                message="properties calculated with RDKit",
                formatted="RAW PROPERTY FORMATTER SHOULD NOT BE USED",
                quality={"step_id": "properties", "method": "RDKit"},
            ),
            ToolResult.error_result(
                "admet_predictor",
                AgentErrorCode.TOOL_UNAVAILABLE,
                "ADMET model unavailable",
                warnings=["No production ADMET model was loaded"],
                quality={"step_id": "admet"},
            ),
            ToolResult.error_result(
                "activity_predictor",
                AgentErrorCode.TOOL_UNAVAILABLE,
                "RG-MPNN weights unavailable",
                warnings=["demo mode is not accepted"],
                quality={"step_id": "activity"},
            ),
        ],
        final_answer="legacy concatenated output",
    )

    present_target_design_result(
        result,
        {
            "target_hint": "PDE5A",
            "requested_count": 10,
            "docking_top_n": 3,
        },
    )

    presentation = result.metadata["presentation"]
    molecules = presentation["molecules"]
    assert [item["smiles"] for item in molecules] == ["CCO", "CCN"]
    assert all(item["validation"] == {"valid": True, "method": "RDKit"} for item in molecules)
    assert molecules[0]["properties"]["qed"] == 0.407
    assert presentation["requested_count"] == 10
    assert presentation["actual_count"] == 2
    assert presentation["complete"] is False

    report = result.final_answer
    assert "PDE5A 候选分子设计结果" in report
    assert "请求候选数" in report and "10" in report
    assert "有效且唯一候选数" in report and "2" in report
    assert "gmm-llama:latest" in report
    assert "RDKit" in report
    assert "ADMET model unavailable" in report
    assert "RG-MPNN weights unavailable" in report
    assert "计算生成的候选假设" in report
    assert "对接结合能" not in report
    assert "pIC50" not in report
    assert "RAW TARGET FORMATTER" not in report
    assert "RAW GENERATOR FORMATTER" not in report
    assert "RAW PROPERTY FORMATTER" not in report
    assert (
        "| molecule_generation | llm_molecular_generator | 部分完成 |"
        in report
    )


def test_target_design_report_uses_requested_target_in_scientific_boundary():
    result = AgentResult.from_tool_results(
        trace_id="egfr-target-design",
        skill_name="target_driven_design",
        tool_results=[
            ToolResult.success_result(
                "llm_molecular_generator",
                data=[
                    {
                        "smiles": "CCO",
                        "model": "gmm-llama:latest",
                        "source": "llm",
                    }
                ],
                message="generated one candidate",
                quality={"step_id": "molecule_generation"},
            )
        ],
    )

    present_target_design_result(
        result,
        {"target_hint": "EGFR", "requested_count": 1},
    )

    assert "已验证的 EGFR 抑制剂" in result.final_answer
    assert "已验证的 PDE5A 抑制剂" not in result.final_answer
