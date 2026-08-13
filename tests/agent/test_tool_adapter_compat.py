from src.agent.contracts import AgentErrorCode, ToolProvenance, ToolResult
from src.agent.tools.base_tool import execute_tool_compat
from src.agent.tools.molecular_docking import MolecularDocking


class DummyLegacyTool:
    name = "dummy"

    def execute(self, query):
        return {
            "success": True,
            "message": "ok",
            "data": {"value": 1},
            "formatted": "OK",
        }


class DummyToolResultTool:
    name = "already_standard"

    def execute(self, query):
        return ToolResult.success_result(
            tool_name=self.name,
            data={"value": 2},
            message="standard",
        )


class FailingLegacyTool:
    name = "failing"

    def execute(self, query):
        raise RuntimeError("boom")


class StructuredLegacyTool:
    name = "structured"

    def execute(self, query):
        return {
            "success": True,
            "message": "structured ok",
            "data": {"value": 3},
            "formatted": "STRUCTURED",
            "warnings": ["low confidence"],
            "evidence": [{"source": "unit-test", "value": 3}],
            "artifacts": [
                {
                    "artifact_type": "report",
                    "path": "outputs/report.json",
                    "label": "normalized report",
                    "mime_type": "application/json",
                    "metadata": {"validated": True},
                }
            ],
            "quality": {"validated": True, "score": 0.9},
        }


class StructuredFailingLegacyTool:
    name = "structured_failure"

    def execute(self, query):
        return {
            "success": False,
            "message": "invalid scientific output",
            "error": {
                "code": "invalid_output",
                "message": "binding energy lacks evidence",
                "details": {"claim": "binding_energy"},
            },
            "warnings": ["claim removed"],
            "evidence": [{"source": "validator"}],
            "quality": {"validated": False},
        }


class ProvenanceLegacyTool:
    name = "molecular_docking"

    def __init__(self, provenance):
        self.provenance = provenance

    def execute(self, query):
        return {
            "success": True,
            "message": "real docking",
            "data": {"total_poses": 1},
            "quality": {"real_execution": True},
            "provenance": self.provenance,
        }


def test_legacy_tool_is_wrapped_as_tool_result():
    wrapped = execute_tool_compat(DummyLegacyTool(), "CCO")

    assert wrapped.success is True
    assert wrapped.tool_name == "dummy"
    assert wrapped.data == {"value": 1}
    assert wrapped.formatted == "OK"
    assert isinstance(wrapped.elapsed_ms, int)


def test_standard_tool_result_passes_through_with_elapsed_time():
    wrapped = execute_tool_compat(DummyToolResultTool(), "CCO")

    assert wrapped.success is True
    assert wrapped.tool_name == "already_standard"
    assert wrapped.data == {"value": 2}
    assert isinstance(wrapped.elapsed_ms, int)


def test_legacy_tool_exception_becomes_internal_error():
    wrapped = execute_tool_compat(FailingLegacyTool(), "CCO")

    assert wrapped.success is False
    assert wrapped.tool_name == "failing"
    assert wrapped.error.code == AgentErrorCode.INTERNAL_ERROR
    assert "boom" in wrapped.message


def test_legacy_tool_preserves_structured_fields():
    wrapped = execute_tool_compat(StructuredLegacyTool(), "CCO")

    assert wrapped.warnings == ["low confidence"]
    assert wrapped.evidence == [{"source": "unit-test", "value": 3}]
    assert wrapped.quality == {"validated": True, "score": 0.9}
    assert len(wrapped.artifacts) == 1
    assert wrapped.artifacts[0].artifact_type == "report"
    assert wrapped.artifacts[0].path == "outputs/report.json"
    assert wrapped.artifacts[0].metadata == {"validated": True}


def test_failed_legacy_tool_preserves_error_and_validation_fields():
    wrapped = execute_tool_compat(StructuredFailingLegacyTool(), "CCO")

    assert wrapped.success is False
    assert wrapped.error.code == AgentErrorCode.INVALID_OUTPUT
    assert wrapped.error.details == {"claim": "binding_energy"}
    assert wrapped.warnings == ["claim removed"]
    assert wrapped.evidence == [{"source": "validator"}]
    assert wrapped.quality == {"validated": False}


def test_failed_legacy_tool_accepts_string_error_field():
    class StringErrorLegacyTool:
        name = "rag_search"

        def execute(self, query):
            return {"success": False, "error": "RAG system not available globally"}

    wrapped = execute_tool_compat(StringErrorLegacyTool(), "query")

    assert wrapped.success is False
    assert wrapped.tool_name == "rag_search"
    assert wrapped.message == "RAG system not available globally"


def test_legacy_tool_strictly_normalizes_complete_provenance():
    raw = {
        "tool_name": "molecular_docking",
        "tool_version": "molecular-docking-adapter-1",
        "model_name": "AutoDock Vina",
        "model_version": None,
        "demo_mode": False,
        "fallback_used": False,
        "input_digest": None,
        "output_digest": None,
    }

    wrapped = execute_tool_compat(ProvenanceLegacyTool(raw), {})

    assert wrapped.success is True
    assert wrapped.provenance == ToolProvenance.from_dict(raw)


def test_legacy_tool_invalid_present_provenance_fails_closed():
    wrapped = execute_tool_compat(
        ProvenanceLegacyTool(
            {
                "tool_name": "molecular_docking",
                "tool_version": "molecular-docking-adapter-1",
                "demo_mode": False,
            }
        ),
        {},
    )

    assert wrapped.success is False
    assert wrapped.error.code == AgentErrorCode.INVALID_OUTPUT
    assert wrapped.provenance is None
    assert "provenance" in wrapped.message.lower()


def test_nullable_legacy_provenance_round_trips_as_unprovided():
    legacy = ToolResult.success_result(
        "legacy_scientific_tool",
        data={"value": 1.0},
        provenance=None,
    ).to_legacy_dict()
    assert "provenance" in legacy
    assert legacy["provenance"] is None

    class NullableProvenanceLegacyTool:
        name = "legacy_scientific_tool"

        def execute(self, _query):
            return legacy

    wrapped = execute_tool_compat(NullableProvenanceLegacyTool(), {})

    assert wrapped.success is True
    assert wrapped.data == {"value": 1.0}
    assert wrapped.provenance is None


def test_real_molecular_docking_success_provenance_survives_compat(
    tmp_path,
    monkeypatch,
):
    from src.docking import molecular_docking_service as service_module

    receptor = tmp_path / "receptor.pdb"
    ligand = tmp_path / "ligand.sdf"
    receptor.write_text("ATOM\n", encoding="utf-8")
    ligand.write_text("$$$$\n", encoding="utf-8")

    class FakeService:
        def __init__(self, config=None):
            pass

        def verify_environment(self):
            return True

        async def perform_docking(self, **kwargs):
            return {
                "success": True,
                "job_id": "job-1",
                "total_poses": 1,
                "best_pose": {"binding_energy": -7.2},
            }

    monkeypatch.setattr(service_module, "MolecularDockingService", FakeService)

    wrapped = execute_tool_compat(
        MolecularDocking(),
        {
            "receptor_path": str(receptor),
            "ligand_path": str(ligand),
            "center": [1, 2, 3],
            "size": [20, 20, 20],
        },
    )

    assert wrapped.success is True
    assert wrapped.provenance is not None
    assert wrapped.provenance.tool_name == "molecular_docking"
    assert wrapped.provenance.tool_version == "molecular-docking-adapter-1"
    assert wrapped.provenance.model_name == "AutoDock Vina"
    assert wrapped.provenance.model_version is None
    assert wrapped.provenance.demo_mode is False
    assert wrapped.provenance.fallback_used is False
