#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分子对接工具 - 用于蛋白质-配体分子对接预测
"""

from typing import Dict, Any, Callable
import hashlib
import json
import logging
import threading
from pathlib import Path

from src.agent.persistence.redaction import redact_sensitive
from src.agent.contracts import ToolProvenance

from .base_tool import BaseMolecularTool

logger = logging.getLogger(__name__)
MOLECULAR_DOCKING_ADAPTER_VERSION = "molecular-docking-adapter-1"


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

    worker = threading.Thread(
        target=invoke,
        name="molecular-docking-async-bridge",
        daemon=False,
    )
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
    safe["rdkit_available"] = (
        bool(python_env.get("has_rdkit")) if isinstance(python_env, dict) else False
    )
    return safe


def _normalize_warning_strings(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    warnings = []
    for value in values:
        if not isinstance(value, str):
            continue
        warning = value.strip()
        if not warning:
            continue
        warning = redact_sensitive(warning)
        if isinstance(warning, str) and warning not in warnings:
            warnings.append(warning)
    return warnings


def _sha256_file(path: Path) -> str | None:
    """Return a content digest without exposing the local path."""
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
    """Identify the configured Vina executable without exposing its path."""
    executable = getattr(service, "vina_exe", None)
    if not executable:
        return None
    digest = _sha256_file(Path(str(executable)))
    return f"vina-executable:{digest}" if digest else None


def _docking_lineage(
    *,
    receptor_path: Path,
    ligand_input: str,
    input_type: str,
    center: tuple[float, float, float],
    size: tuple[float, float, float],
    config: Any,
    docking_data: Dict[str, Any],
    runtime_model_version: str | None = None,
) -> tuple[Dict[str, Any], list[Dict[str, Any]]]:
    """Build path-free provenance for a real Vina result.

    The input fingerprint is intentionally separate from ToolProvenance's
    strict compatibility shape. It records exactly what was docked without
    leaking machine-specific paths into Agent output.
    """
    receptor_digest = _sha256_file(receptor_path)
    if input_type == "file":
        ligand_digest = _sha256_file(Path(ligand_input))
        ligand_record = {"kind": "ligand_file", "sha256": ligand_digest}
    else:
        ligand_digest = _sha256_text(ligand_input)
        ligand_record = {"kind": "smiles", "sha256": ligand_digest}

    input_structure = {
        "receptor": {"kind": "receptor_file", "sha256": receptor_digest},
        "ligand": ligand_record,
    }
    params = {
        "center": list(center),
        "size": list(size),
        "exhaustiveness": getattr(config, "exhaustiveness", None),
        "num_modes": getattr(config, "num_modes", None),
        "energy_range": getattr(config, "energy_range", None),
        "manual_center": getattr(config, "manual_center", None),
    }
    manifest = {"input_structure": input_structure, "parameters": params}
    data_version = _sha256_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")))
    provider_provenance = docking_data.get("provenance")
    provider_provenance = provider_provenance if isinstance(provider_provenance, dict) else {}
    model_version = (
        provider_provenance.get("vina_version")
        or docking_data.get("vina_version")
        or runtime_model_version
    )
    evidence = {
        "type": "vina_output",
        "source": "autodock_vina",
        "model_version": model_version,
        "data_version": data_version,
        "input_structure": input_structure,
        "parameters": params,
    }
    quality = {
        "source": "local_autodock_vina",
        "model_version": model_version,
        "data_version": data_version,
        "input_structure": input_structure,
    }
    return quality, [evidence]


class MolecularDocking(BaseMolecularTool):
    """分子对接工具"""

    def __init__(self, *, command_scope=None, allowed_output_root=None):
        self._command_scope = command_scope
        self._allowed_output_root = None
        if command_scope is not None or allowed_output_root is not None:
            from src.docking.adapters.base import CommandOwnershipScope

            self._allowed_output_root = CommandOwnershipScope._bound_output_root(
                command_scope, allowed_output_root,
            )
        super().__init__(
            name="molecular_docking",
            description="Perform molecular docking between ligands and protein targets"
        )

        # 触发关键词
        self.trigger_words = [
            'docking', 'dock', 'binding', 'affinity', 'receptor', 'ligand',
            '对接', '结合', '亲和力', '受体', '配体', '分子对接',
            'autodock', 'vina', 'glide', 'binding site', '结合位点',
            'protein-ligand', '蛋白配体', 'target', '靶点'
        ]

    def should_use(self, query: str) -> bool:
        """判断是否应该使用此工具"""
        query_lower = query.lower()

        # 检查触发词
        has_trigger = any(word in query_lower for word in self.trigger_words)

        # 检查是否有SMILES或提到了蛋白质/靶点
        smiles_list = self.extract_smiles(query)
        has_smiles = len(smiles_list) > 0

        # 检查是否提到了蛋白质或靶点
        has_target = any(target in query_lower for target in ['protein', 'target', 'receptor', '蛋白', '靶点', '受体'])

        result = has_trigger and (has_smiles or has_target)

        if result:
            logger.info(f"MolecularDocking triggered: found {len(smiles_list)} valid SMILES, trigger={has_trigger}, target={has_target}")

        return result

    def execute(
        self,
        query: str | Dict[str, Any],
        *,
        job_id=None,
        progress_callback=None,
        cancel_event=None,
    ) -> Dict[str, Any]:
        """Run real docking only for structured requests with complete inputs."""
        safe_query: Any = query
        if isinstance(query, dict):
            safe_query = {
                "request_type": "structured_docking",
                "receptor_provided": bool(query.get("receptor_path")),
                "ligand_mode": "smiles" if query.get("smiles") else "file",
            }
        result = self._create_base_result(safe_query)

        if not isinstance(query, dict):
            result["message"] = (
                "Docking requires receptor and ligand inputs plus docking box "
                "center/size parameters; no binding energy was calculated."
            )
            result["reasoning"] = (
                "A target name and SMILES alone are insufficient for a real "
                "AutoDock Vina run."
            )
            return result

        required = {"receptor_path", "center", "size"}
        missing = sorted(required - set(query))
        if not query.get("ligand_path") and not query.get("smiles"):
            missing.append("ligand_path or smiles")
        if missing:
            result["message"] = (
                "Missing docking inputs: "
                + ", ".join(missing)
                + ". No binding energy was calculated."
            )
            result["reasoning"] = "Docking precondition validation failed."
            return result

        try:
            from pathlib import Path

            from src.docking.molecular_docking_service import (
                DockingConfig,
                MolecularDockingService,
            )

            receptor_path = Path(str(query["receptor_path"])).expanduser().resolve()
            if not receptor_path.is_file():
                result["message"] = (
                    "Receptor file does not exist. No binding energy was calculated."
                )
                return result
            center = tuple(float(value) for value in query["center"])
            size = tuple(float(value) for value in query["size"])
            if len(center) != 3 or len(size) != 3:
                result["message"] = "Docking center and size must each contain 3 values."
                return result
            config = DockingConfig(
                center_x=center[0],
                center_y=center[1],
                center_z=center[2],
                size_x=size[0],
                size_y=size[1],
                size_z=size[2],
                exhaustiveness=int(query.get("exhaustiveness", 8)),
                num_modes=int(query.get("num_modes", 10)),
                energy_range=float(query.get("energy_range", 3.0)),
                manual_center=True,
            )
            ownership = {}
            if self._command_scope is not None:
                ownership = {
                    "command_scope": self._command_scope,
                    "allowed_output_root": self._allowed_output_root,
                }
            service = MolecularDockingService(config=query.get("runtime_config"), **ownership)
            if not service.verify_environment():
                result["message"] = (
                    "AutoDock Vina docking environment is unavailable. "
                    "No binding energy was calculated."
                )
                result["data"] = {
                    "diagnostics": _safe_environment_diagnostics(
                        service.env_diagnostics()
                    )
                }
                return result
            ligand_input = query.get("smiles")
            input_type = "smiles"
            if not ligand_input:
                ligand_path = Path(str(query["ligand_path"])).expanduser().resolve()
                if not ligand_path.is_file():
                    result["message"] = (
                        "Ligand file does not exist. No binding energy was calculated."
                    )
                    return result
                ligand_input = str(ligand_path)
                input_type = "file"
            control = {}
            if job_id is not None:
                control["job_id"] = job_id
            if progress_callback is not None:
                control["progress_callback"] = progress_callback
            if cancel_event is not None:
                control["cancel_event"] = cancel_event
            docking_result = _run_coroutine_sync(
                lambda: service.perform_docking(
                    receptor_file=str(receptor_path),
                    ligand_input=str(ligand_input),
                    config=config,
                    input_type=input_type,
                    **control,
                )
            )
            warnings = _normalize_warning_strings(docking_result.get("warnings"))
            docking_data = dict(docking_result)
            docking_data["warnings"] = warnings
            result["success"] = bool(docking_result.get("success"))
            result["data"] = docking_data
            result["warnings"] = warnings
            execution_status = (
                "completed"
                if result["success"]
                else str(docking_result.get("error_code") or "failed")
            )
            result["quality"] = {
                "engine": "AutoDock Vina",
                "real_execution": bool(result["success"]),
                "execution_status": execution_status,
            }
            if result["success"]:
                lineage_quality, evidence = _docking_lineage(
                    receptor_path=receptor_path,
                    ligand_input=str(ligand_input),
                    input_type=input_type,
                    center=center,
                    size=size,
                    config=config,
                    docking_data=docking_data,
                    runtime_model_version=_runtime_model_version(service),
                )
                result["quality"].update(lineage_quality)
                result["evidence"] = evidence
                result["provenance"] = ToolProvenance(
                    tool_name="molecular_docking",
                    tool_version=MOLECULAR_DOCKING_ADAPTER_VERSION,
                    model_name="AutoDock Vina",
                    model_version=lineage_quality["model_version"],
                    demo_mode=False,
                    fallback_used=False,
                ).to_dict()
                result["quality"]["docking_inputs"] = {
                    "receptor_provided": True,
                    "ligand_provided": True,
                    "ligand_mode": input_type,
                    "center": list(center),
                    "size": list(size),
                }
            result["message"] = (
                "Real AutoDock Vina docking completed."
                if result["success"]
                else (
                    "Docking was cancelled before a scientific result was produced."
                    if execution_status == "cancelled"
                    else docking_result.get("error", "Docking failed.")
                )
            )
            if result["success"]:
                best_pose = docking_data.get("best_pose")
                best_pose = best_pose if isinstance(best_pose, dict) else {}
                binding_energy = best_pose.get("binding_energy")
                if binding_energy is None:
                    binding_energy = docking_data.get("binding_energy")
                pose_file = (
                    docking_data.get("pose_file")
                    or best_pose.get("pose_file")
                )
                formatted_lines = [
                    f"Docking job: {docking_result.get('job_id')}",
                    f"Poses: {docking_result.get('total_poses', 0)}",
                ]
                if (
                    isinstance(binding_energy, (int, float))
                    and not isinstance(binding_energy, bool)
                ):
                    formatted_lines.append(
                        f"Binding energy: {binding_energy:g} kcal/mol"
                    )
                if isinstance(pose_file, str) and pose_file.strip():
                    formatted_lines.append(f"Pose file: {pose_file}")
                result["formatted"] = "\n".join(formatted_lines)
            else:
                result["formatted"] = ""
            result["reasoning"] = (
                "Results were parsed from an actual Vina output file."
                if result["success"]
                else "No scientific docking result was accepted."
            )
        except Exception as e:
            logger.error("Molecular docking failed (%s)", type(e).__name__)
            result["message"] = "Docking failed before a scientific result was produced."
            result["reasoning"] = "The real docking pipeline raised an error."

        return result
