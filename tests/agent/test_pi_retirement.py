"""Guard the removal boundary without conflating domain rules with the harness."""
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
DOMAIN_FILES = (
    'decision_policy', 'decision_execution', 'decision_inputs', 'decision_clarification',
    'decision_bounds', 'decision_history', 'decision_requirements', 'decision_continuation',
    'ordinary_chat_policy', 'decision_bindings', 'decision_binding_inputs',
    'decision_binding_acceptance', 'decision_binding_continuation',
)


def test_old_generic_harness_has_no_executable_modules():
    remaining = sorted(path.name for path in (ROOT / 'src/agent/harness').glob('*.py'))
    assert not remaining, remaining


def test_scientific_capabilities_have_explicit_preserved_owners():
    for name in DOMAIN_FILES:
        assert (ROOT / 'src/agent/scientific_session' / f'{name}.py').is_file(), name


def test_production_code_does_not_import_retired_backends():
    forbidden = re.compile(r'^\s*(?:from|import)\s+(?:src\.agent\.harness|langgraph)(?:\.|\s)', re.M)
    found = []
    for directory in ('src', 'scripts'):
        for path in (ROOT / directory).rglob('*.py'):
            if forbidden.search(path.read_text(encoding='utf-8-sig')):
                found.append(str(path.relative_to(ROOT)))
    assert not found, found


def test_web_has_no_private_whole_agent_or_direct_model_fallback():
    import ast
    from src.web.chat_handler import ChatHandler
    for retired in ('_process_message', '_process_message_body', '_execute_agent'):
        assert not hasattr(ChatHandler, retired), retired
    module = ast.parse((ROOT / 'src/web/chat_handler.py').read_text(encoding='utf-8-sig'))
    forbidden = {'execute', 'should_use_tools', 'generate', 'generate_async', 'generate_sync', 'stream_generate'}
    assert not [ast.unparse(node.func) for node in ast.walk(module) if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute) and node.func.attr in forbidden]
