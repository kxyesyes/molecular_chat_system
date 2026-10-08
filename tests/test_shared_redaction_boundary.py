from pathlib import Path


CORE_MODULES = (
    "src/docking/sandbox_runner.py",
    "src/task_runtime/docking_execution.py",
    "src/task_runtime/completion.py",
    "src/task_runtime/models.py",
    "src/task_runtime/store.py",
    "src/task_runtime/temporal/activities.py",
    "src/reverse_target/predictor.py",
)


def test_tool_result_normalizer_is_system_owned_for_task_runtime():
    from src.agent.tools.base_tool import execute_tool_compat as legacy
    from src.system.tool_adapter import execute_tool_compat as shared

    assert legacy is shared
    source = (
        Path(__file__).parents[1] / "src" / "task_runtime" / "docking_execution.py"
    ).read_text(encoding="utf-8")
    assert "src.agent.tools.base_tool" not in source
    assert "src.system.tool_adapter" in source


def test_redaction_implementation_is_owned_by_system_and_legacy_imports_are_identical():
    from src.agent.persistence import redaction as legacy
    from src.system import redaction as shared

    for name in (
        "looks_like_credential",
        "redact_sensitive",
        "contains_credential",
        "contains_sensitive_text",
        "sanitize_sensitive_text",
        "sanitize_bounded",
    ):
        assert getattr(legacy, name) is getattr(shared, name)
    assert legacy.contains_secret_material("api_key=synthetic") == shared.contains_secret_material(
        "api_key=synthetic"
    )


def test_scientific_and_task_runtime_modules_import_redaction_from_system():
    root = Path(__file__).parents[1]
    for relative_path in CORE_MODULES:
        source = (root / relative_path).read_text(encoding="utf-8")
        assert "src.agent.persistence.redaction" not in source
        assert "src.system.redaction" in source
