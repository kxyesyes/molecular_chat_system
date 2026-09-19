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
