from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_IMPORT = "src.agent." + "skills"
FORBIDDEN_RELATIVE_IMPORT = "from ." + "skills"
LEGACY_SYMBOLS = ("Base" + "Skill", "Skill" + "Registry")


def test_legacy_skill_package_is_removed():
    assert not (ROOT / "src" / "agent" / "skills").exists()


def test_python_sources_do_not_import_legacy_skill_package():
    offenders = []
    for base in (ROOT / "src", ROOT / "scripts", ROOT / "tests"):
        for path in base.rglob("*.py"):
            if path == Path(__file__).resolve():
                continue
            content = path.read_text(encoding="utf-8")
            if (
                FORBIDDEN_IMPORT in content
                or FORBIDDEN_RELATIVE_IMPORT in content
                or any(symbol in content for symbol in LEGACY_SYMBOLS)
            ):
                offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []
