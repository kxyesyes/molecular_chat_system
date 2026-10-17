"""Docking-owned synchronous adapter for the real AutoDock Vina service.

This module is deliberately independent from the Agent package.  The Agent
module keeps a compatibility export, while task runtimes and other domain
code can use this adapter without importing Agent implementation details.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List

from src.system.molecular_input import BaseMolecularTool
from src.system.redaction import redact_sensitive
from src.system.scientific_contracts import ToolProvenance

logger = logging.getLogger(__name__)
MOLECULAR_DOCKING_ADAPTER_VERSION = "molecular-docking-adapter-1"
_DOCKING_STATUSES = frozenset({
    "succeeded", "partial", "failed", "timeout", "unavailable",
    "not_calculated", "invalid_input", "rejected", "cancelled",
})


def _status_from_docking_result(docking_result: Any, *, success: bool) -> str:
    """Translate service terminal codes into the shared observation contract."""

    error_code = docking_result.get("error_code") if isinstance(docking_result, dict) else None
    normalized = str(error_code).strip().lower() if error_code is not None else ""
    # A provider can accidentally leave ``success`` set while also reporting a
    # terminal failure.  The explicit terminal code is more informative and
    # must win; otherwise the adapter would turn a timeout/cancellation into a
    # scientifically valid docking result.
    if success and normalized in _DOCKING_STATUSES and normalized != "succeeded":
        return normalized
    if success:
        return "succeeded"
    return normalized if normalized in _DOCKING_STATUSES else "failed"


def _run_coroutine_sync(factory: Callable[[], Any]) -> Any:
    """Run an async service from sync tool code, including loop-owning callers."""
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())

    outcome: Dict[str, Any] = {}

    def invoke() -> None:
        try:
            outcome["result"] = asyncio.run(factory())
        except BaseException as error:
            outcome["error"] = error

    worker = threading.Thread(target=invoke, name="molecular-docking-async-bridge", daemon=False)
    worker.start()
    worker.join()
    if "error" in outcome:
        raise outcome["error"]
    return outcome["result"]


def _safe_environment_diagnostics(values: Any) -> Dict[str, Any]:
    diagnostics = values if isinstance(values, dict) else {}
    safe: Dict[str, Any] = {"ok": bool(diagnostics.get("ok"))}
    for source, label in (
        ("vina", "vina_available"),
        ("adfr_prepare_receptor", "receptor_preparation_available"),
        ("mk_prepare_ligand", "ligand_preparation_available"),
    ):
        item = diagnostics.get(source)
        safe[label] = bool(item.get("exists")) if isinstance(item, dict) else False
    python_env = diagnostics.get("python_env")
    safe["rdkit_available"] = bool(python_env.get("has_rdkit")) if isinstance(python_env, dict) else False
    return safe


def _normalize_warning_strings(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    warnings: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        warning = redact_sensitive(value.strip())
        if isinstance(warning, str) and warning and warning not in warnings:
            warnings.append(warning)
    return warnings


def _sha256_file(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return f"sha256:{digest.hexdigest()}"
    except (OSError, ValueError):
        return None


def _sha256_text(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _runtime_model_version(service: Any) -> str | None:
    executable = getattr(service, "vina_exe", None)
    if not executable:
        return None
    digest = _sha256_file(Path(str(executable)))
    return f"vina-executable:{digest}" if digest else None


def _docking_lineage(*, receptor_path: Path, ligand_input: str, input_type: str,
                     center: tuple[float, float, float], size: tuple[float, float, float],
                     config: Any, docking_data: Dict[str, Any],
                     runtime_model_version: str | None = None) -> tuple[Dict[str, Any], list[Dict[str, Any]]]:
    receptor_digest = _sha256_file(receptor_path)
    if input_type == "file":
        ligand_record = {"kind": "ligand_file", "sha256": _sha256_file(Path(ligand_input))}
    else:
        ligand_record = {"kind": "smiles", "sha256": _sha256_text(ligand_input)}
    input_structure = {
        "receptor": {"kind": "receptor_file", "sha256": receptor_digest},
        "ligand": ligand_record,
    }
    params = {
        "center": list(center), "size": list(size),
        "exhaustiveness": getattr(config, "exhaustiveness", None),
        "num_modes": getattr(config, "num_modes", None),
        "energy_range": getattr(config, "energy_range", None),
        "manual_center": getattr(config, "manual_center", None),
    }
    manifest = {"input_structure": input_structure, "parameters": params}
    data_version = _sha256_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")))
    provider = docking_data.get("provenance")
    provider = provider if isinstance(provider, dict) else {}
    model_version = provider.get("vina_version") or docking_data.get("vina_version") or runtime_model_version
    evidence = {
        "type": "vina_output", "source": "autodock_vina", "model_version": model_version,
        "data_version": data_version, "input_structure": input_structure, "parameters": params,
    }
    quality = {
        "source": "local_autodock_vina", "model_version": model_version,
        "data_version": data_version, "input_structure": input_structure,
    }
    return quality, [evidence]


class MolecularDocking(BaseMolecularTool):
    """Synchronous domain adapter around the real docking service."""

    def __init__(self, *, command_scope=None, allowed_output_root=None, chat_toolchain=None):
        if chat_toolchain is not None:
            from src.docking.chat_toolchain import ChatDockingToolchain
            from src.task_runtime.docking_consent import DockingConsentError
            if type(chat_toolchain) is not ChatDockingToolchain:
                raise DockingConsentError("consent_policy_unavailable")
            chat_toolchain.require_current()
        self._chat_toolchain = chat_toolchain
        self._chat_toolchain_at_construction = chat_toolchain
        self._command_scope = command_scope
        self._allowed_output_root = None
        if command_scope is not None or allowed_output_root is not None:
            from src.docking.adapters.base import CommandOwnershipScope
            self._allowed_output_root = CommandOwnershipScope._bound_output_root(command_scope, allowed_output_root)
        super().__init__("molecular_docking", "Perform molecular docking between ligands and protein targets")
        self.trigger_words = [
            "docking", "dock", "binding", "affinity", "receptor", "ligand",
            "对接", "结合", "亲和力", "受体", "配体", "分子对接", "autodock", "vina",
            "glide", "binding site", "结合位点", "protein-ligand", "蛋白配体", "target", "靶点",
        ]

    def should_use(self, query: str) -> bool:
        query_lower = query.lower()
        has_trigger = any(word in query_lower for word in self.trigger_words)
        smiles_list = self.extract_smiles(query)
        has_target = any(target in query_lower for target in ("protein", "target", "receptor", "蛋白", "靶点", "受体"))
        result = has_trigger and (bool(smiles_list) or has_target)
        if result:
            logger.info("MolecularDocking triggered: found %s valid SMILES, trigger=%s, target=%s", len(smiles_list), has_trigger, has_target)
        return result

    def execute(self, query: str | Dict[str, Any], *, job_id=None,
                progress_callback=None, cancel_event=None) -> Dict[str, Any]:
        safe_query: Any = query
        if isinstance(query, dict):
            safe_query = {
                "request_type": "structured_docking",
                "receptor_provided": bool(query.get("receptor_path")),
                "ligand_mode": "smiles" if query.get("smiles") else "file",
            }
        result = self._create_base_result(safe_query)
        result["status"] = "not_calculated"
        if not isinstance(query, dict):
            result["message"] = "Docking requires receptor and ligand inputs plus docking box center/size parameters; no binding energy was calculated."
            result["reasoning"] = "A target name and SMILES alone are insufficient for a real AutoDock Vina run."
            return result
        required = {"receptor_path", "center", "size"}
        missing = sorted(required - set(query))
        if not query.get("ligand_path") and not query.get("smiles"):
            missing.append("ligand_path or smiles")
        if missing:
            result["status"] = "invalid_input"
            result["message"] = "Missing docking inputs: " + ", ".join(missing) + ". No binding energy was calculated."
            result["reasoning"] = "Docking precondition validation failed."
            return result
        try:
            from src.docking.molecular_docking_service import DockingConfig, MolecularDockingService
            receptor_path = Path(str(query["receptor_path"])).expanduser().resolve()
            if not receptor_path.is_file():
                result["status"] = "invalid_input"
                result["message"] = "Receptor file does not exist. No binding energy was calculated."
                return result
            center = tuple(float(value) for value in query["center"])
            size = tuple(float(value) for value in query["size"])
            if len(center) != 3 or len(size) != 3:
                result["status"] = "invalid_input"
                result["message"] = "Docking center and size must each contain 3 values."
                return result
            config = DockingConfig(
                center_x=center[0], center_y=center[1], center_z=center[2],
                size_x=size[0], size_y=size[1], size_z=size[2],
                exhaustiveness=int(query.get("exhaustiveness", 8)),
                num_modes=int(query.get("num_modes", 10)),
                energy_range=float(query.get("energy_range", 3.0)), manual_center=True,
            )
            ownership = {}
            if self._command_scope is not None:
                ownership = {"command_scope": self._command_scope, "allowed_output_root": self._allowed_output_root}
            toolchain = self._chat_toolchain_at_construction
            if self._chat_toolchain is not toolchain:
                from src.task_runtime.docking_consent import DockingConsentError
                raise DockingConsentError("consent_policy_unavailable")
            if toolchain is not None:
                # A structured/browser payload is never toolchain authority.
                # Recreate config from the trusted constructor binding only.
                toolchain.require_current()
                runtime_config = toolchain.runtime_config()
                ownership["chat_toolchain"] = toolchain
            else:
                runtime_config = query.get("runtime_config")
            service = MolecularDockingService(config=runtime_config, **ownership)
            if toolchain is not None:
                # configure() can resolve optional tools from ambient state.
                # Fence the ACTUAL instance, not just a preflight resolver,
                # before environment probes or any scientific commands.
                toolchain.assert_service(service)
                toolchain.require_current()
            if not service.verify_environment():
                result["status"] = "unavailable"
                result["message"] = "AutoDock Vina docking environment is unavailable. No binding energy was calculated."
                result["data"] = {"diagnostics": _safe_environment_diagnostics(service.env_diagnostics())}
                return result
            ligand_input = query.get("smiles")
            input_type = "smiles"
            if not ligand_input:
                ligand_path = Path(str(query["ligand_path"])).expanduser().resolve()
                if not ligand_path.is_file():
                    result["status"] = "invalid_input"
                    result["message"] = "Ligand file does not exist. No binding energy was calculated."
                    return result
                ligand_input, input_type = str(ligand_path), "file"
            reference_pose = query.get("reference_pose")
            if reference_pose is not None:
                reference_path = Path(str(reference_pose)).expanduser().resolve()
                if not reference_path.is_file():
                    result["status"] = "invalid_input"
                    result["message"] = "Reference pose file does not exist. No binding energy was calculated."
                    return result
                reference_pose = str(reference_path)
            redocking_rmsd_threshold = query.get("redocking_rmsd_threshold")
            if redocking_rmsd_threshold is not None:
                try:
                    if isinstance(redocking_rmsd_threshold, bool):
                        raise ValueError("boolean is not a threshold")
                    redocking_rmsd_threshold = float(redocking_rmsd_threshold)
                except (TypeError, ValueError):
                    result["status"] = "invalid_input"
                    result["message"] = "Redocking RMSD threshold must be a finite positive number."
                    return result
                if not math.isfinite(redocking_rmsd_threshold) or redocking_rmsd_threshold <= 0:
                    result["status"] = "invalid_input"
                    result["message"] = "Redocking RMSD threshold must be a finite positive number."
                    return result
            control = {
                key: value
                for key, value in (
                    ("job_id", job_id),
                    ("progress_callback", progress_callback),
                    ("cancel_event", cancel_event),
                )
                if value is not None
            }
            if reference_pose is not None:
                control["reference_pose"] = reference_pose
            if redocking_rmsd_threshold is not None:
                control["redocking_rmsd_threshold"] = redocking_rmsd_threshold
            docking_result = _run_coroutine_sync(lambda: service.perform_docking(
                receptor_file=str(receptor_path), ligand_input=str(ligand_input), config=config,
                input_type=input_type,
                **control,
            ))
            warnings = _normalize_warning_strings(docking_result.get("warnings"))
            docking_data = dict(docking_result)
            docking_data["warnings"] = warnings
            provider_success = bool(docking_result.get("success"))
            result["status"] = _status_from_docking_result(
                docking_result, success=provider_success
            )
            result["success"] = result["status"] == "succeeded"
            # Never expose a provider payload containing pose/energy claims
            # after the execution has entered a non-success terminal state.
            # Keep only the non-scientific terminal diagnostic required by the
            # synchronous compatibility contract; the Agent validator still
            # removes this safe data before a failed result is persisted or
            # presented as a scientific observation.
            result["data"] = (
                docking_data
                if result["success"]
                else {
                    "error_code": result["status"],
                    "status": result["status"],
                }
            )
            result["warnings"] = warnings
            execution_status = "completed" if result["success"] else str(docking_result.get("error_code") or "failed")
            result["quality"] = {
                "engine": "AutoDock Vina",
                "real_execution": bool(result["success"]),
                "execution_status": execution_status,
                "scientific_usable": bool(result["success"]),
            }
            # Keep failed validation diagnostics separate from rejected
            # scientific result data. Only stable codes and finite RMSD
            # numbers are allowed across this boundary, never raw exceptions.
            scientific_quality = docking_result.get("scientific_quality")
            if isinstance(scientific_quality, dict):
                safe_quality = {"status": "failed"}
                if scientific_quality.get("status") in ("passed", "failed"):
                    safe_quality["status"] = scientific_quality["status"]
                rmsd = scientific_quality.get("redocking_rmsd")
                if isinstance(rmsd, dict) and rmsd.get("status") in ("passed", "failed", "not_run"):
                    safe_rmsd = {"status": rmsd["status"]}
                    if rmsd.get("reason_code") in (
                        "reference_pose_not_supplied", "threshold_not_supplied", "threshold_invalid",
                        "candidate_pose_unavailable", "reference_pose_invalid",
                        "rmsd_within_threshold", "rmsd_exceeds_threshold",
                    ):
                        safe_rmsd["reason_code"] = rmsd["reason_code"]
                    for key in ("value_angstrom", "threshold_angstrom"):
                        value = rmsd.get(key)
                        if type(value) in (int, float) and math.isfinite(value) and value >= 0:
                            safe_rmsd[key] = value
                    safe_quality["redocking_rmsd"] = safe_rmsd
                result["quality"]["scientific_quality"] = safe_quality
            if isinstance(docking_result.get("quality"), dict):
                result["quality"]["service_quality"] = dict(docking_result["quality"])
            if not result["success"]:
                error_codes = {
                    "cancelled": "cancelled",
                    "timeout": "tool_timeout",
                    "unavailable": "tool_unavailable",
                    "invalid_input": "invalid_input",
                    "rejected": "invalid_output",
                    "not_calculated": "empty_result",
                    "partial": "provider_error",
                }
                result["error"] = {
                    "code": error_codes.get(result["status"], "internal_error"),
                    "message": str(docking_result.get("error") or "Docking failed."),
                }
            if result["success"]:
                lineage_quality, evidence = _docking_lineage(
                    receptor_path=receptor_path, ligand_input=str(ligand_input), input_type=input_type,
                    center=center, size=size, config=config, docking_data=docking_data,
                    runtime_model_version=_runtime_model_version(service),
                )
                result["quality"].update(lineage_quality)
                result["evidence"] = evidence
                result["provenance"] = ToolProvenance(
                    tool_name="molecular_docking", tool_version=MOLECULAR_DOCKING_ADAPTER_VERSION,
                    model_name="AutoDock Vina", model_version=lineage_quality["model_version"],
                    demo_mode=False, fallback_used=False,
                ).to_dict()
                result["quality"]["docking_inputs"] = {
                    "receptor_provided": True, "ligand_provided": True, "ligand_mode": input_type,
                    "center": list(center), "size": list(size),
                }
            result["message"] = (
                "Real AutoDock Vina docking completed." if result["success"] else
                ("Docking was cancelled before a scientific result was produced." if execution_status == "cancelled" else docking_result.get("error", "Docking failed."))
            )
            if result["success"]:
                best_pose = docking_data.get("best_pose") if isinstance(docking_data.get("best_pose"), dict) else {}
                binding_energy = best_pose.get("binding_energy")
                if binding_energy is None:
                    binding_energy = docking_data.get("binding_energy")
                pose_file = docking_data.get("pose_file") or best_pose.get("pose_file")
                formatted_lines = [f"Docking job: {docking_result.get('job_id')}", f"Poses: {docking_result.get('total_poses', 0)}"]
                if isinstance(binding_energy, (int, float)) and not isinstance(binding_energy, bool):
                    formatted_lines.append(f"Binding energy: {binding_energy:g} kcal/mol")
                if isinstance(pose_file, str) and pose_file.strip():
                    formatted_lines.append(f"Pose file: {pose_file}")
                result["formatted"] = "\n".join(formatted_lines)
            else:
                result["formatted"] = ""
            result["reasoning"] = "Results were parsed from an actual Vina output file." if result["success"] else "No scientific docking result was accepted."
        except Exception:
            logger.error("Molecular docking failed (%s)", "adapter_error")
            result["status"] = "failed"
            result["message"] = "Docking failed before a scientific result was produced."
            result["reasoning"] = "The real docking pipeline raised an error."
        return result


__all__ = [
    "MOLECULAR_DOCKING_ADAPTER_VERSION", "MolecularDocking", "_docking_lineage",
    "_normalize_warning_strings", "_runtime_model_version", "_safe_environment_diagnostics",
    "_status_from_docking_result",
]
