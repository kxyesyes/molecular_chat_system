"""Pure opt-in configuration; importing this module never discovers model assets."""
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
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
