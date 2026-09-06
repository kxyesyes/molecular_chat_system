import ast
import warnings
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LEGACY_MODULE = "src.agent." + "skills"
LEGACY_SYMBOLS = ("Base" + "Skill", "Skill" + "Registry")


def test_legacy_skill_package_is_removed():
    assert not (ROOT / "src" / "agent" / "skills").exists()
    assert not (ROOT / "src" / "agent" / "skills.py").exists()


def _legacy_references(path: Path, content: str) -> list[str]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        warnings.simplefilter("ignore", SyntaxWarning)
        tree = ast.parse(content.removeprefix("\ufeff"), filename=str(path))
    relative_parts = path.relative_to(ROOT).parts
    inside_agent_package = relative_parts[:2] == ("src", "agent")
    references = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == LEGACY_MODULE or alias.name.startswith(
                    LEGACY_MODULE + "."
                ):
                    references.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imported_names = {alias.name for alias in node.names}
            if node.level == 0 and (
                module == LEGACY_MODULE
                or module.startswith(LEGACY_MODULE + ".")
                or (module == "src.agent" and "skills" in imported_names)
            ):
                references.append(module or "src.agent")
            elif node.level > 0 and inside_agent_package and (
                module == "skills"
                or module.startswith("skills.")
                or (not module and "skills" in imported_names)
            ):
                references.append("." * node.level + module)
        elif isinstance(node, ast.Name) and node.id in LEGACY_SYMBOLS:
            references.append(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in LEGACY_SYMBOLS:
            references.append(node.attr)
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in LEGACY_SYMBOLS:
                references.append(node.name)

    return references


def test_ast_guard_detects_legacy_imports_without_false_substring_matches():
    agent_path = ROOT / "src" / "agent" / "probe.py"
    detected = (
        f"import {LEGACY_MODULE}",
        f"from {LEGACY_MODULE} import {LEGACY_SYMBOLS[0]}",
        "from src.agent import " + "skills",
        "from ." + "skills import " + LEGACY_SYMBOLS[1],
    )
    allowed = (
        "import src.agent." + "skills_v2",
        "from ." + "skills_helpers import helper",
        f"# import {LEGACY_MODULE}\nvalue = '{LEGACY_SYMBOLS[1]}'",
    )

    assert all(_legacy_references(agent_path, source) for source in detected)
    assert all(not _legacy_references(agent_path, source) for source in allowed)


def test_python_sources_do_not_import_legacy_skill_package():
    offenders = []
    for base in (ROOT / "src", ROOT / "scripts", ROOT / "tests"):
        for path in base.rglob("*.py"):
            if path == Path(__file__).resolve():
                continue
            content = path.read_text(encoding="utf-8")
            if _legacy_references(path, content):
                offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []
