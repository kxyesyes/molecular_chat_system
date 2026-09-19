"""Opt-in acceptance helpers; import/config parsing never discovers assets.

Snapshot entry points are only for authorized children or synthetic fixtures.
Hash/type/size checks are integrity gates, NOT a sandbox for untrusted weights.
"""
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path, PureWindowsPath
import stat
from typing import Mapping


@dataclass(frozen=True, repr=False)
class AcceptanceConfig:
    source: Path
    pde_bundle_id: str
    buche_bundle_id: str


def read_config(env: Mapping[str, str]) -> AcceptanceConfig | None:
    """Parse explicit inputs without I/O, environment defaults or asset validation.

    Only the literal string '1' opens the gate. Existence, file types and sealed
    model evidence belong to a later snapshot stage, not configuration parsing.
    """
    if env.get("MEDCHAT_RUN_FAMILY_REAL_ACCEPTANCE") != "1":
        return None

    # Reuse the registry's lexical contract, without constructing a registry.
    from src.activity.model_registry import ActivityModelRegistry

    try:
        pde_id = ActivityModelRegistry._validate_model_id(
            env.get("MEDCHAT_FAMILY_ACCEPTANCE_PDE_BUNDLE_ID"))
        buche_id = ActivityModelRegistry._validate_model_id(
            env.get("MEDCHAT_FAMILY_ACCEPTANCE_BUCHE_BUNDLE_ID"))
        source = env.get("MEDCHAT_FAMILY_ACCEPTANCE_MODELS_DIR")
        if (pde_id == buche_id or not isinstance(source, str) or not source
                or "\x00" in source or source.replace("\\", "/").startswith("//")
                or PureWindowsPath(source).drive.startswith("\\")
                or not Path(source).is_absolute()):
            raise ValueError
    except ValueError:
        # Do not expose configured source paths or identifiers in error output.
        raise ValueError("invalid_configuration") from None
    return AcceptanceConfig(Path(source), pde_id, buche_id)


REGISTRY_LIMIT = 16 << 20
CARD_LIMIT = 2 << 20
WEIGHTS_LIMIT = 512 << 20
READ_CHUNK = 1 << 20
JSON_DEPTH = 32
_TASKS = ("classification", "regression")


@dataclass(frozen=True, repr=False)
class FamilySnapshot:
    models_dir: Path
    family_id: str
    bundle_id: str
    expected_models: dict
    source_digests: dict


class _AcceptanceError(ValueError):
    """Internal marker so boundary handlers preserve only fixed public codes."""


def _fail(code):
    raise _AcceptanceError(code) from None


def _local_path(path):
    from src.activity.model_registry import _artifact_basename

    path = Path(path)
    if (not path.is_absolute() or str(path).replace("\\", "/").startswith("//")
            or PureWindowsPath(str(path)).drive.startswith("\\")):
        _fail("unsafe_source")
    try:
        for part in path.parts[1:]:
            _artifact_basename(part, "path")
    except ValueError:
        _fail("unsafe_source")
    return path


def _identity(info, *, directory=False):
    identity = (info.st_dev, info.st_ino, info.st_mode)
    return identity if directory else identity + (info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink)


def _safe_type(info, *, directory=False):
    if (stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400
            or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            or (not directory and info.st_nlink != 1)):
        _fail("unsafe_source")


def _checked_path(path, *, directory=False):
    """Check un-resolved ancestors AND leaf; resolving first would hide links."""
    identities = []
    for component in (*reversed(path.parents), path):
        info = component.lstat()
        is_directory = component != path or directory
        _safe_type(info, directory=is_directory)
        identities.append(_identity(info, directory=is_directory))
    return identities, info


def _bounded_file(path, limit, *, destination=None, retain=False):
    before, info = _checked_path(path)
    if info.st_size > limit:
        _fail("asset_limit_exceeded")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with ExitStack() as stack:
        source = stack.enter_context(os.fdopen(os.open(path, flags), "rb"))
        opened = os.fstat(source.fileno())
        _safe_type(opened)
        if opened.st_size > limit:
            _fail("asset_limit_exceeded")
        if _identity(opened) != before[-1] or _checked_path(path)[0] != before:
            _fail("source_changed")
        output = None
        if destination is not None:
            _checked_path(destination.parent, directory=True)
            fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
            output = stack.enter_context(os.fdopen(fd, "wb"))
        digest, size, content = hashlib.sha256(), 0, bytearray()
        while True:
            chunk = source.read(READ_CHUNK)
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                _fail("asset_limit_exceeded")
            digest.update(chunk)
            if retain:
                content.extend(chunk)
            if output is not None:
                output.write(chunk)
        if (size != info.st_size or _identity(os.fstat(source.fileno())) != before[-1]
                or _checked_path(path)[0] != before):
            _fail("source_changed")
    return digest.hexdigest(), bytes(content)


def _strict_json(content):
    from src.activity.dataset_contract import _reject_duplicate_object_members

    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError
        return result

    def nonfinite(value):
        raise ValueError

    try:
        text = content.decode("utf-8")
        # Bound nesting before the decoder or deepcopy can recurse. Ignore
        # braces inside quoted strings, including escaped quotes/backslashes.
        depth, quoted, escaped = 0, False, False
        for char in text:
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char in "[{":
                depth += 1
                if depth > JSON_DEPTH:
                    raise ValueError
            elif char in "]}":
                depth -= 1
        value = json.loads(text, object_pairs_hook=_reject_duplicate_object_members,
                           parse_constant=nonfinite, parse_float=finite_float)
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (ValueError, UnicodeError, RecursionError):
        _fail("invalid_registry")


def _selection(config, family_id, state):
    from src.activity.family_models import require_pinned_record, validate_bundle_mappings
    from src.activity.model_registry import ActivityModelRegistry, _artifact_basename, validate_endpoint_metadata

    if (type(state.get("version")) is not int or state["version"] != 3
            or not isinstance(state.get("models"), dict)
            or not isinstance(state.get("family_bundles"), dict)):
        _fail("invalid_registry")
    bundle_id = {"pde-family": config.pde_bundle_id, "buche-family": config.buche_bundle_id}.get(family_id)
    try:
        ActivityModelRegistry._validate_model_id(bundle_id)
        bundle = state["family_bundles"][bundle_id]
        validate_bundle_mappings({bundle_id: bundle}, {})
        if bundle["family_id"] != family_id:
            raise ValueError
        models = {}
        for task in _TASKS:
            pinned = bundle["models"][task]
            model_id = ActivityModelRegistry._validate_model_id(pinned["model_id"])
            model = state["models"][model_id]
            if model["model_id"] != model_id or model["task_type"] != task:
                raise ValueError
            require_pinned_record(model, pinned)
            models[task] = model
        if models[_TASKS[0]]["model_id"] == models[_TASKS[1]]["model_id"]:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        _fail("bundle_mismatch")

    # Reserve all selected sidecar names even though we never copy sidecars.
    reserved = {"registry_state.json", "registry_state.lock", "active_model.json"}
    reserved.update(f"{item['model_id']}_info.json".casefold() for item in models.values())
    assets = {}
    for task, model in models.items():
        for kind, field, limit in (("weights", "weights_file", WEIGHTS_LIMIT), ("card", "model_card_file", CARD_LIMIT)):
            try:
                name = _artifact_basename(model[field], field)
                if name.casefold() in reserved:
                    raise ValueError
            except (KeyError, ValueError):
                _fail("unsafe_source")
            reserved.add(name.casefold())
            assets[f"{task}.{kind}"] = (name, limit, model.get("weights_sha256" if kind == "weights" else "model_card_sha256"))
        try:
            validate_endpoint_metadata(model)
        except ValueError:
            _fail("bundle_mismatch")
    # The sealed descriptor is JSON inside a string; bound it before the
    # production pinned-evidence validator parses it independently in the copy.
    try:
        _strict_json(bundle["family_dataset_snapshot"].encode("utf-8"))
    except (KeyError, AttributeError, UnicodeError):
        _fail("bundle_mismatch")
    return bundle, models, assets


def _source_baseline(config, family_id):
    from src.activity.model_registry import REGISTRY_STATE_FILE

    _local_path(config.source)
    digest, content = _bounded_file(config.source / REGISTRY_STATE_FILE, REGISTRY_LIMIT, retain=True)
    bundle, models, assets = _selection(config, family_id, _strict_json(content))
    digests = {"registry": digest}
    for logical, (name, limit, expected) in assets.items():
        actual, content = _bounded_file(config.source / name, limit, retain=logical.endswith(".card"))
        if actual != expected:
            _fail("asset_digest_mismatch")
        if logical.endswith(".card"):
            _strict_json(content)
        digests[logical] = actual
    return bundle, models, assets, digests


def _recheck_source(config, assets, digests):
    """Bracket pinned asset reads with manifest checks, not an atomic snapshot."""
    from src.activity.model_registry import REGISTRY_STATE_FILE

    checks = {"registry": (REGISTRY_STATE_FILE, REGISTRY_LIMIT, None), **assets}
    for logical, (name, limit, _) in checks.items():
        if _bounded_file(config.source / name, limit)[0] != digests.get(logical):
            _fail("source_changed")
    if _bounded_file(config.source / REGISTRY_STATE_FILE, REGISTRY_LIMIT)[0] != digests.get("registry"):
        _fail("source_changed")


def _destination(source, destination):
    source, destination = _local_path(source), _local_path(destination)
    left, right = tuple(p.casefold() for p in source.parts), tuple(p.casefold() for p in destination.parts)
    if left == right[:len(left)] or right == left[:len(right)]:
        _fail("unsafe_source")
    # A fresh directory is required. Do not enumerate an existing destination.
    try:
        destination.lstat()
    except FileNotFoundError:
        pass
    else:
        _fail("unsafe_source")
    source_identity = _checked_path(source, directory=True)[0][-1]
    for parent in destination.parents:
        try:
            ancestors, _ = _checked_path(parent, directory=True)
            # Also reject directory aliases, not just lexical containment.
            if source_identity in ancestors:
                _fail("unsafe_source")
            break
        except FileNotFoundError:
            continue
    return destination


def snapshot_family(config, family_id, destination):
    """Read only five selected source files; validate sealed evidence in a copy.

    The caller owns the fresh destination and cleanup, including on failure.
    No model is deserialized, no source registry constructed, no dataset opened.
    """
    try:
        from src.activity.family_models import require_pinned_record
        from src.activity.model_registry import ActivityModelRegistry, REGISTRY_STATE_FILE

        destination = _destination(config.source, destination)
        bundle, models, assets, digests = _source_baseline(config, family_id)
        destination.mkdir(parents=True, exist_ok=False)
        _checked_path(destination, directory=True)
        for logical, (name, limit, _) in assets.items():
            actual, _ = _bounded_file(config.source / name, limit, destination=destination / name)
            if actual != digests[logical]:
                _fail("source_changed")
        state = dict(version=3, models=deepcopy({m["model_id"]: m for m in models.values()}),
                     active_model_id=None, active_models_by_endpoint={},
                     family_bundles={bundle["bundle_id"]: deepcopy(bundle)}, active_family_bundles={})
        with (destination / REGISTRY_STATE_FILE).open("x", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, allow_nan=False)
        _recheck_source(config, assets, digests)
        # Only the copied registry may acquire locks or mutate active selection.
        registry = ActivityModelRegistry(destination)
        selected = registry.select_family_bundle(bundle["bundle_id"])
        require_pinned_record(selected, bundle)
        _recheck_source(config, assets, digests)
        return FamilySnapshot(destination, family_id, bundle["bundle_id"], deepcopy(models), digests)
    except _AcceptanceError:
        raise
    except ImportError:
        _fail("dependency_unavailable")
    except OSError:
        _fail("unsafe_source")
    except (ValueError, TypeError, KeyError, RecursionError):
        _fail("bundle_mismatch")


def verify_source(config, snapshot):
    """Verify pinned assets without following a changed source manifest."""
    try:
        from src.activity.family_models import require_pinned_record
        from src.activity.model_registry import REGISTRY_STATE_FILE

        _local_path(config.source)
        registry_path = config.source / REGISTRY_STATE_FILE
        digest, content = _bounded_file(registry_path, REGISTRY_LIMIT, retain=True)
        if digest != snapshot.source_digests["registry"]:
            _fail("source_changed")
        # Parse ONLY these hash-matched bytes, never a subsequent registry read.
        # Selection is pure; bind every filename/identity to the snapshot before
        # opening any asset. Later registry reads below are digest checks only.
        bundle, models, assets = _selection(config, snapshot.family_id, _strict_json(content))
        if bundle["bundle_id"] != snapshot.bundle_id:
            _fail("source_changed")
        require_pinned_record(models, snapshot.expected_models)
        digests = {"registry": digest, **{key: item[2] for key, item in assets.items()}}
        if digests != snapshot.source_digests:
            _fail("source_changed")
        _recheck_source(config, assets, digests)
        return True
    except ImportError:
        _fail("dependency_unavailable")
    except (ValueError, TypeError, KeyError, OSError, RecursionError):
        _fail("source_changed")


# Publication is deliberately separate from the private Task5 evidence schema.
REPORT_LIMIT = 2 << 20
PUBLIC_ERRORS = frozenset({
    'invalid_configuration', 'unsafe_source', 'asset_limit_exceeded', 'invalid_registry',
    'bundle_mismatch', 'asset_digest_mismatch', 'source_changed', 'child_timeout',
    'child_failed', 'ownership_uncertain', 'invalid_report', 'dependency_unavailable', 'chain_mismatch',
})
ENTRIES = ('predictor', 'api_single', 'api_batch', 'tool', 'decision', 'websocket', 'dom')
MODEL_KEYS = ('model_id', 'target_id', 'task_type', 'weights_sha256', 'model_card_sha256',
              'demo_mode', 'fallback_used')
CHECK_KEYS = ('entry_validated', 'formatted_numbers', 'observed_evidence', 'answer_from_tool',
              'input_digest', 'terminal', 'rejected_without_service', 'public_matches_actual', 'baseline_matches')
DIGEST_KEYS = ('registry', 'classification.weights', 'classification.card', 'regression.weights', 'regression.card')
FAMILY_TARGETS = {'pde-family': ('PDE', 'PDE5A'), 'buche-family': ('BuChE', 'BChE')}


def _pick(value, keys):
    if not isinstance(value, dict):
        return {}
    return {key: value[key] for key in keys if key in value}


def _public_identity(value):
    result = _pick(value, ('family_id', 'bundle_id'))
    if isinstance(value, dict):
        result['models'] = {task: _pick(value.get('models', {}).get(task), MODEL_KEYS) for task in _TASKS}
    return result


def _valid_identity(value):
    import re
    if (value.get('family_id') not in ('pde-family', 'buche-family')
            or not isinstance(value.get('bundle_id'), str) or not value['bundle_id']):
        return False
    models = value.get('models', {})
    for task in _TASKS:
        model = models.get(task, {})
        if (set(model) != set(MODEL_KEYS) or model['task_type'] != task
                or model['demo_mode'] is not False or model['fallback_used'] is not False
                or not isinstance(model['model_id'], str) or not model['model_id']
                or not isinstance(model['target_id'], str) or not model['target_id']
                or any(not isinstance(model[key], str) or not re.fullmatch('[0-9a-f]{64}', model[key])
                       for key in ('weights_sha256', 'model_card_sha256'))):
            return False
    return models['classification']['model_id'] != models['regression']['model_id']


def _public_row(row):
    result = _pick(row, ('smiles', 'requested_target', 'status', 'success', 'family_id', 'bundle_id',
        'activity_probability', 'predicted_pIC50', 'activity_class', 'label_threshold',
        'probability_threshold', 'units', 'classification_regression_consistent', 'warnings'))
    result['models'] = _public_identity(row).get('models', {})
    result['errors'] = _pick(row.get('errors'), ('input', 'bundle', 'classification', 'regression'))
    return result


def _finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _row_contract(row, requested_target):
    """Independent obligations, not values learned from a possibly corrupt baseline."""
    required = {'label_threshold', 'probability_threshold', 'units', 'requested_target', 'activity_class'}
    probability = row.get('activity_probability')
    classification = (None if probability is None else
        '有活性' if _finite_number(probability) and probability >= .5 else '无活性')
    return (required.issubset(row) and requested_target is not None
        and row['requested_target'] == requested_target and row['units'] == 'pIC50'
        and _finite_number(row['label_threshold']) and row['label_threshold'] == 5.0
        and _finite_number(row['probability_threshold']) and row['probability_threshold'] == .5
        and (probability is None or _finite_number(probability) and 0 <= probability <= 1)
        and row['activity_class'] == classification)


def _available_stages(rows):
    """Available evidence is independent of the whole-row acceptance verdict."""
    available = set()
    for row in rows:
        if row.get('status') not in ('passed', 'partial'):
            continue
        for task, field in (('classification', 'activity_probability'), ('regression', 'predicted_pIC50')):
            value = row.get(field)
            if (row.get('models', {}).get(task) and task not in row.get('errors', {})
                    and _finite_number(value) and (task != 'classification' or 0 <= value <= 1)):
                available.add(task)
    return [task for task in _TASKS if task in available]


def _case_record(stage, entry, kind, expected, actual):
    """Closed projection; never traverse arbitrary metadata or message payloads."""
    stage = stage if isinstance(stage, dict) else {}
    checks = _pick(stage.get('checks'), CHECK_KEYS)
    rows = stage.get('rows', [])
    rows = [_public_row(row) for row in rows] if isinstance(rows, list) else []
    trace = [_pick(item, ('tool_name', 'status', 'evidence_id', 'input_digest', 'output_digest', 'error', 'warnings'))
             for item in stage.get('tool_trace', [])]
    events = [{**_pick(item, ('event', 'trace_id', 'timestamp', 'tool', 'progress')),
               'payload': _pick(item.get('payload'), ('round', 'decision_id', 'action', 'success', 'status', 'partial'))}
              for item in stage.get('event_trace', [])]
    latency = stage.get('latency_ms')
    valid = (stage.get('status') == 'passed' and checks.get('entry_validated') is True
             and type(latency) in (int, float) and math.isfinite(latency) and latency >= 0)
    rejected = kind in ('invalid_smiles', 'unknown_target')
    target, alias = FAMILY_TARGETS.get(expected.get('family_id'), (None, None))
    requested_target = ('AChE' if kind == 'unknown_target' else
        target if kind == 'valid' and entry in ('predictor', 'api_single') else alias)
    result_status = stage.get('scientific_status', stage.get('agent_status', stage.get('observation_status')))
    if rows:
        successful = [row for row in rows if row.get('status') == 'passed']
        if rejected:
            valid = valid and not successful
        actual = _public_identity(successful[0] if successful else rows[0])
        if result_status is None:
            result_status = ('passed' if len(successful) == len(rows) else
                'partial' if successful or any(row.get('status') == 'partial' for row in rows) else 'failed')
        for row in rows:
            valid = valid and _row_contract(row, requested_target)
            if row.get('status') == 'passed':
                probability, pic50 = row.get('activity_probability'), row.get('predicted_pIC50')
                valid = valid and (_public_identity(row) == expected and row.get('success') is True
                    and type(probability) in (int, float) and math.isfinite(probability) and 0 <= probability <= 1
                    and type(pic50) in (int, float) and math.isfinite(pic50))
            else:
                valid = valid and (row.get('status') == 'failed' and row.get('success') is False
                    and row.get('activity_probability') is None and row.get('predicted_pIC50') is None
                    and row.get('errors', {}).get('input') in ('invalid_smiles', 'unknown_or_ambiguous_family'))
        required_count = 1 if rejected else 4 if kind == 'mixed' else 2 if entry in ('tool', 'decision', 'websocket') else 3
        valid = valid and len(rows) == required_count
    elif entry not in ('dom', 'decision', 'websocket') and not (rejected and entry == 'tool'):
        valid = False
    if entry == 'dom':
        valid = valid and (type(stage.get('rows')) is int and stage['rows'] == (4 if kind == 'mixed' else 3)
            and stage.get('exit_code') == 0 and stage.get('ownership_released') is True
            and stage.get('cleanup_complete') is True)
        result_status = ('partial' if kind == 'mixed' else 'passed') if stage.get('status') == 'passed' else None
        if not stage:
            actual = {}
    if entry == 'tool':
        valid = valid and stage.get('observation_status') == ('invalid_input' if rejected else 'succeeded')
        if rejected:
            valid = valid and stage.get('error') == 'invalid_input' and stage.get('data') is None
        else:
            valid = valid and checks.get('formatted_numbers') is True
    if entry in ('decision', 'websocket'):
        valid = valid and bool(stage.get('trace_id')) and bool(events)
        valid = valid and [item.get('event') for item in events] == stage.get('events')
        valid = valid and all(item.get('trace_id') == stage.get('trace_id') for item in events)
        if rejected:
            valid = valid and checks.get('rejected_without_service') is True
            valid = valid and (len(trace) == (0 if kind == 'unknown_target' else 1))
            if trace:
                valid = valid and trace[0].get('status') == trace[0].get('error') == 'invalid_input'
        else:
            valid = valid and len(rows) == 2
            valid = valid and all(checks.get(key) is True for key in ('observed_evidence', 'answer_from_tool', 'input_digest'))
            valid = valid and len(trace) == 1 and all(trace[0].get(key) for key in ('evidence_id', 'input_digest', 'output_digest'))
        if entry == 'websocket':
            valid = valid and checks.get('terminal') is True
        valid = valid and all(item.get('tool_name') == 'activity_predictor' for item in trace)
        valid = valid and _event_integrity(events, trace, result_status)
    if rejected:
        valid = valid and result_status in ('failed', 'rejected')
    elif kind == 'mixed':
        valid = valid and result_status == 'partial'
    else:
        valid = valid and result_status in ('passed', 'completed', 'succeeded')
    artifacts = stage.get('artifacts', [])
    valid = valid and artifacts == []
    return {'case_id': kind + '.' + entry, 'entry': entry, 'status': 'passed' if valid else 'failed',
        'expected_identity': expected, 'actual_identity': actual, 'latency_ms': latency,
        'checks': checks, 'result_status': result_status,
        'error_code': None if valid else 'chain_mismatch',
        'scientific_error_code': stage.get('error') if stage.get('error') in ('invalid_input',) else None,
        'actual_tools': [item.get('tool_name') for item in trace] if entry in ('decision', 'websocket') else
                        ['activity_predictor'] if entry == 'tool' and stage.get('observation_status') else [],
        'trace_id': stage.get('trace_id'), 'events': stage.get('events', []),
        'event_trace': events, 'tool_trace': trace, 'rows': rows,
        'warnings': stage.get('warnings', []), 'available_stages': _available_stages(rows), 'artifacts': [],
        'state_ref': 'temporary_agent_state' if stage.get('trace_id') else None}


def _event_integrity(events, trace, status):
    if not events or status not in ('completed', 'failed', 'rejected'):
        return False
    names = [event.get('event') for event in events]
    tool_end = 'tool_completed' if status == 'completed' else 'tool_failed'
    core = ['task_started', 'planning_started', 'planning_completed']
    if trace:
        core += ['tool_started', tool_end, 'planning_started', 'planning_completed']
    core += ['task_' + status]
    optional = ('tool_progress', 'validation_warning')
    if [name for name in names if name not in optional] != core or names[-1] != core[-1]:
        return False
    for index, event in enumerate(events):
        stamp = event.get('timestamp')
        if type(stamp) not in (int, float) or not math.isfinite(stamp):
            return False
        if event.get('event') in optional:
            if not trace or not names.index('tool_started') < index < names.index(tool_end):
                return False
        if event.get('event') in optional or event.get('event', '').startswith('tool_'):
            if event.get('tool') != 'activity_predictor':
                return False
    planning = [event['payload'] for event in events if event['event'].startswith('planning_')]
    for index in range(0, len(planning), 2):
        start, end = planning[index:index + 2]
        if (start.get('round') != index // 2 + 1 or start.get('round') != end.get('round')
                or not start.get('decision_id') or start['decision_id'] != end.get('decision_id')
                or end.get('action') != ('tool' if index == 0 else 'finish')):
            return False
    terminal = events[-1]['payload']
    return (terminal.get('status') == status and terminal.get('success') is (status == 'completed')
            and terminal.get('partial') is False)


def _matching_rows(cases):
    """Independently recheck ordered scientific rows against the CPU baseline."""
    baseline = cases[0]['rows']
    if len(baseline) != 3 or [row.get('smiles') for row in baseline] != ['CCO', 'CCN', 'CCO']:
        return False
    def matches(expected, actual):
        if len(expected) != len(actual):
            return False
        for left, right in zip(expected, actual):
            for key in left:
                if key in ('predicted_pIC50', 'activity_probability'):
                    value = right.get(key)
                    if type(value) not in (int, float) or not math.isclose(left[key], value, rel_tol=1e-6, abs_tol=1e-6):
                        return False
                # Targets intentionally differ by entry; _row_contract checks
                # each independently before this cross-entry comparison.
                elif key != 'requested_target' and left[key] != right.get(key):
                    return False
        return True
    all_matched = True
    for case in cases:
        if case['case_id'].startswith('valid.') and case['entry'] != 'dom':
            expected = baseline[:2] if case['entry'] in ('tool', 'decision', 'websocket') else baseline
        elif case['case_id'] == 'mixed.api_batch':
            if len(case['rows']) != 4 or case['rows'][1] != cases[7]['rows'][0]:
                return False
            expected = baseline
        else:
            continue
        actual = [row for row in case['rows'] if row.get('status') == 'passed']
        matched = matches(expected, actual)
        case['checks']['baseline_matches'] = matched
        if not matched:
            case.update(status='failed', error_code='chain_mismatch')
        all_matched = all_matched and matched
    return all_matched


def _scope(source_check):
    return {'external_model': 'not_run', 'production_selection':
            {'passed': 'unchanged', 'changed': 'changed'}.get(source_check, 'not_verified')}


def _failed_report(mode, reason='invalid_report', source_check='not_completed'):
    return {'status': 'failed', 'mode': mode, 'decision_model_kind': 'scripted',
        'scope': _scope(source_check), 'source_check': source_check, 'error_code': reason, 'cases': []}


def _sanitize_report(report):
    from src.agent.persistence.redaction import sanitize_bounded
    clean, changed = sanitize_bounded(report, max_depth=16, max_items=128, max_text_chars=512)
    # Any alteration can destroy necessary evidence; never recompute a pass rate
    # from a shortened list or silently claim unchanged source after truncation.
    if changed:
        clean['status'], clean['error_code'] = 'failed', 'invalid_report'
    encoded = json.dumps(clean, ensure_ascii=False, allow_nan=False)
    if len(encoded.encode('utf-8')) > REPORT_LIMIT:
        raise ValueError
    return clean


def _validate_public_schema(report):
    """A second closed boundary prevents unprojected nested fields being written."""
    scalar = None
    model = dict.fromkeys(MODEL_KEYS, scalar)
    identity = {'family_id': scalar, 'bundle_id': scalar, 'models': dict.fromkeys(_TASKS, model)}
    row = dict.fromkeys(('smiles', 'requested_target', 'status', 'success', 'family_id', 'bundle_id',
        'activity_probability', 'predicted_pIC50', 'activity_class', 'label_threshold',
        'probability_threshold', 'units', 'classification_regression_consistent'), scalar)
    row.update(models=dict.fromkeys(_TASKS, model), warnings=[scalar],
               errors=dict.fromkeys(('input', 'bundle', 'classification', 'regression'), scalar))
    event = dict.fromkeys(('event', 'trace_id', 'timestamp', 'tool', 'progress'), scalar)
    event['payload'] = dict.fromkeys(('round', 'decision_id', 'action', 'success', 'status', 'partial'), scalar)
    trace = dict.fromkeys(('tool_name', 'status', 'evidence_id', 'input_digest', 'output_digest', 'error'), scalar)
    trace['warnings'] = [scalar]
    case = dict.fromkeys(('case_id', 'entry', 'status', 'latency_ms', 'result_status', 'error_code',
        'scientific_error_code', 'trace_id', 'state_ref'), scalar)
    case.update(expected_identity=identity, actual_identity=identity, checks=dict.fromkeys(CHECK_KEYS, scalar),
        actual_tools=[scalar], events=[scalar], event_trace=[event], tool_trace=[trace], rows=[row],
        warnings=[scalar], available_stages=[scalar], artifacts=[scalar])
    root = dict.fromkeys(('status', 'mode', 'decision_model_kind', 'source_check', 'error_code'), scalar)
    root.update(scope=dict.fromkeys(('external_model', 'production_selection'), scalar),
        expected_identity=identity, actual_identity=identity, cases=[case], source_digests=dict.fromkeys(DIGEST_KEYS, scalar))
    family = {**root, **dict.fromkeys(('family_id', 'exit_code', 'ownership_released',
        'process_cleanup_complete', 'cleanup_complete', 'latency_ms', 'cleanup_latency_ms'), scalar)}
    root['families'] = [family]
    def visit(value, schema, depth=0):
        if depth > 16:
            raise ValueError
        if schema is None:
            if value is not None and type(value) not in (str, bool, int, float):
                raise ValueError
        elif isinstance(schema, list):
            if not isinstance(value, list) or len(value) > 128:
                raise ValueError
            for item in value:
                visit(item, schema[0], depth + 1)
        else:
            if not isinstance(value, dict) or set(value) - set(schema):
                raise ValueError
            for key, item in value.items():
                visit(item, schema[key], depth + 1)
    visit(report, root)


def public_report(report, mode=None):
    """Project Task5 evidence, then sanitize; a passed label alone has no authority."""
    if not isinstance(report, dict):
        return _failed_report(mode if mode in ('trained_weights', 'synthetic_fixture') else 'trained_weights')
    mode = mode or report.get('mode', 'trained_weights')
    if mode not in ('trained_weights', 'synthetic_fixture'):
        return _failed_report('trained_weights', 'invalid_configuration')
    source_check = report.get('source_check', 'not_completed')
    if source_check not in ('passed', 'changed', 'not_completed'):
        source_check = 'not_completed'
    try:
        expected = _public_identity(report.get('expected_identity'))
        actual = _public_identity(report.get('actual_identity'))
        cases = [_case_record(report.get('stages', {}).get(entry), entry, 'valid', expected, actual) for entry in ENTRIES]
        for kind in ('invalid_smiles', 'unknown_target'):
            stages = report.get('rejections', {}).get(kind, {}).get('stages', {})
            cases.extend(_case_record(stages.get(entry), entry, kind, expected, {}) for entry in ENTRIES[:-1])
        mixed = report.get('mixed', {})
        cases.append(_case_record(mixed, 'api_batch', 'mixed', expected, actual))
        cases.append(_case_record(mixed.get('dom'), 'dom', 'mixed', expected, actual))
        import re
        digests = _pick(report.get('source_digests'), DIGEST_KEYS)
        valid_digests = set(digests) == set(DIGEST_KEYS) and all(
            isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) for value in digests.values())
        valid_digests = valid_digests and all(digests.get(task + '.' + kind) == expected.get('models', {}).get(task, {}).get(field)
            for task in _TASKS for kind, field in (('weights', 'weights_sha256'), ('card', 'model_card_sha256')))
        passed = (report.get('status') == 'passed' and report.get('mode') == mode
            and report.get('decision_model') == 'scripted' and source_check == 'passed'
            and _valid_identity(expected) and expected == actual
            and valid_digests and all(case['status'] == 'passed' for case in cases) and _matching_rows(cases))
        reason = report.get('reason')
        result = {'status': 'passed' if passed else 'failed', 'mode': mode,
            'decision_model_kind': 'scripted', 'scope': _scope(source_check), 'source_check': source_check,
            'error_code': None if passed else reason if reason in PUBLIC_ERRORS else 'chain_mismatch',
            'expected_identity': expected, 'actual_identity': actual, 'cases': cases,
            'source_digests': digests}
        _validate_public_schema(result)
        return _sanitize_report(result)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return _failed_report(mode, source_check=source_check)


def write_report(path, report):
    """Only an already-projected, finite, bounded document may reach exclusive open."""
    from scripts.run_decision_chat_acceptance import open_report
    try:
        _validate_public_schema(report)
        required = {'status', 'mode', 'decision_model_kind', 'source_check', 'scope', 'error_code'}
        if (not required.issubset(report) or report['mode'] not in ('synthetic_fixture', 'trained_weights')
                or report['decision_model_kind'] != 'scripted'
                or report['status'] not in ('passed', 'partial', 'failed', 'skipped')
                or report['source_check'] not in ('passed', 'changed', 'not_completed')
                or report['scope'] != _scope(report['source_check'])
                or report['error_code'] is not None and report['error_code'] not in PUBLIC_ERRORS):
            raise ValueError
        if report['status'] in ('passed', 'partial'):
            families = report.get('families', [])
            if [f.get('family_id') for f in families] != ['pde-family', 'buche-family']:
                raise ValueError
            passed = 0
            for family in families:
                if family['status'] != 'passed':
                    continue
                if (family['source_check'] != 'passed' or type(family.get('exit_code')) is not int
                        or family['exit_code'] != 0 or family.get('ownership_released') is not True
                        or family.get('process_cleanup_complete') is not True or family.get('cleanup_complete') is not True
                        or len(family['cases']) != 21 or any(case['status'] != 'passed' for case in family['cases'])):
                    raise ValueError
                passed += 1
            if passed != (2 if report['status'] == 'passed' else 1):
                raise ValueError
        clean = _sanitize_report(report)
        if clean != report or set(report) - {
                'status', 'mode', 'decision_model_kind', 'scope', 'source_check', 'error_code',
                'expected_identity', 'actual_identity', 'cases', 'source_digests', 'families'}:
            raise ValueError
        payload = json.dumps(clean, ensure_ascii=False, allow_nan=False)
        path = _local_path(path)
        with open_report(path) as output:
            output.write(payload)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        _fail('invalid_report')


def _cleanup_owned(directory, original_identity, *, timeout=2):
    """Bound the caller even if a local filesystem operation stalls.

    A late remover cannot upgrade its returned failure. There is no GC finalizer;
    the private directory is never reused, and the remover checks its deadline
    before every subsequent operation. The child tree must already be released.
    """
    import threading
    import time
    if (not Path(directory).is_absolute() or type(timeout) not in (int, float)
            or not math.isfinite(timeout) or not 0 < timeout <= 2):
        return False
    deadline = time.monotonic() + timeout
    done, result = threading.Event(), []
    def remove():
        try:
            result.append(_remove_owned_tree(directory, original_identity, deadline))
        finally:
            done.set()
    try:
        threading.Thread(target=remove, daemon=True).start()
        finished = done.wait(max(0, deadline - time.monotonic()))
        return finished and time.monotonic() <= deadline and result == [True]
    except RuntimeError:
        return False


def _remove_owned_tree(directory, original_identity, deadline):
    """Delete only a proven owned tree, with a separate cooperative deadline.

    No rmtree/GC finalizer: uncertainty retains the directory. Every ancestor and
    leaf is checked again before deletion; no recursive delete crosses a link.
    """
    import time
    try:
        if _checked_path(directory, directory=True)[0][-1] != original_identity:
            return False
        files, folders = [], []
        def inspect(folder):
            if time.monotonic() >= deadline:
                raise TimeoutError
            _checked_path(folder, directory=True)
            with os.scandir(folder) as entries:
                for item in entries:
                    if time.monotonic() >= deadline:
                        raise TimeoutError
                    path = Path(item.path)
                    info = path.lstat()
                    is_dir = stat.S_ISDIR(info.st_mode)
                    _safe_type(info, directory=is_dir)
                    if is_dir:
                        inspect(path)
                    else:
                        files.append((path, _identity(info)))
            folders.append((folder, _checked_path(folder, directory=True)[0][-1]))
        inspect(directory)
        for path, expected in files + folders:
            if time.monotonic() >= deadline:
                return False
            if _checked_path(directory, directory=True)[0][-1] != original_identity:
                return False
            is_dir = len(expected) == 3
            if _checked_path(path, directory=is_dir)[0][-1] != expected:
                return False
            path.rmdir() if is_dir else path.unlink()
        return not directory.exists()
    except (OSError, ValueError, RecursionError):
        return False


def run_acceptance(env, *, repo_dir, temporary_root=None, runner=None, mode='trained_weights'):
    """Parent owns only fresh temp dirs, process envelopes and public projections.

    Source paths are opaque argv here: never stat/open/recheck source in parent.
    mkdtemp has no finalizer that could race an uncertain process tree.
    """
    import sys
    import tempfile
    import time
    from tests.family_acceptance_process_support import child_environment, run_owned_child
    try:
        config = read_config(env)
    except (ValueError, ImportError):
        return _failed_report(mode, 'invalid_configuration')
    if config is None:
        return {**_failed_report(mode), 'status': 'skipped', 'error_code': None, 'families': []}
    runner = runner or run_owned_child
    families = []
    for family, bundle in (('pde-family', config.pde_bundle_id), ('buche-family', config.buche_bundle_id)):
        directory = None
        released = process_clean = deleted = False
        started = time.monotonic()
        family_report = _failed_report(mode, 'child_failed')
        exit_code = None
        try:
            directory = Path(tempfile.mkdtemp(prefix='family-acceptance-', dir=temporary_root)).absolute()
            root_identity = _checked_path(directory, directory=True)[0][-1]
            child = runner([sys.executable, '-B', '-m', 'tests.family_acceptance_chain_support',
                '--source', str(config.source), '--family', family, '--bundle', bundle,
                '--work-dir', str(directory), '--mode', mode],
                env=child_environment(env, directory), cwd=repo_dir, environment_dir=directory, timeout=120)
            released, process_clean, exit_code = child.ownership_released, child.cleanup_complete, child.exit_code
            if child.report is not None and child.status == 'passed':
                family_report = public_report(child.report.get('scientific_report', {}), mode=mode)
                expected = family_report.get('expected_identity', {})
                if expected and (expected.get('family_id') != family or expected.get('bundle_id') != bundle):
                    family_report.update(status='failed', error_code='bundle_mismatch')
            else:
                family_report = _failed_report(mode, child.reason if child.reason in PUBLIC_ERRORS else 'child_failed')
            if child.status != 'passed' or type(exit_code) is not int or exit_code != 0:
                family_report.update(status='failed', error_code=child.reason if child.reason in PUBLIC_ERRORS else 'child_failed')
        except Exception:
            # A raised runner has not proven ownership release. Keep its temp.
            family_report = _failed_report(mode, 'ownership_uncertain' if directory else 'child_failed')
        finally:
            elapsed = (time.monotonic() - started) * 1000
            cleaning = time.monotonic()
            if directory is not None and released is True and process_clean is True:
                deleted = _cleanup_owned(directory, root_identity)
            cleanup_ms = (time.monotonic() - cleaning) * 1000
        if released is not True or process_clean is not True or deleted is not True:
            family_report.update(status='failed', error_code='ownership_uncertain')
        family_report.update(family_id=family, exit_code=exit_code, ownership_released=released is True,
            process_cleanup_complete=process_clean is True, cleanup_complete=deleted is True,
            latency_ms=elapsed, cleanup_latency_ms=cleanup_ms)
        families.append(family_report)
    checks = [family['source_check'] for family in families]
    source_check = 'changed' if 'changed' in checks else 'passed' if checks == ['passed', 'passed'] else 'not_completed'
    passed = sum(family['status'] == 'passed' for family in families)
    result = {'status': 'passed' if passed == 2 else 'partial' if passed else 'failed',
        'mode': mode, 'decision_model_kind': 'scripted', 'scope': _scope(source_check),
        'source_check': source_check, 'error_code': None if passed == 2 else 'chain_mismatch', 'families': families}
    try:
        return _sanitize_report(result)
    except (ValueError, TypeError, RecursionError):
        return _failed_report(mode, source_check=source_check)
