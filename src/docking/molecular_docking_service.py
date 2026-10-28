"""
分子对接服务模块
集成AutoDock Vina进行蛋白质-配体对接计算
"""

import os
import subprocess
import logging
import math
import inspect
import re
import hashlib
import json
import tempfile
from typing import Dict, List, Any, Tuple, Optional
import shutil
from dataclasses import dataclass
from pathlib import Path
import sys

from .adapters import ADFRAdapter, MeekoAdapter, OpenBabelAdapter, VinaAdapter
from .chat_receptor_validation import (
    ReceptorPreservationError, approved_heavy_atoms, validate_prepared_receptor,
)
from .adapters.base import (
    CommandAdapter,
    CommandCancelledError,
    CommandOwnershipScope,
    CommandOwnershipUncertainError,
)

logger = logging.getLogger(__name__)

_SAFE_JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DOCKING_PHASES = (
    ("receptor_preparation", 10),
    ("ligand_preparation", 35),
    ("vina_running", 60),
    ("scientific_validation", 90),
)


class _ProgressCallbackError(RuntimeError):
    pass

@dataclass
class DockingResult:
    """对接结果数据类"""
    ligand_id: str
    binding_energy: float
    rmsd_lb: float
    rmsd_ub: float
    pose_data: str
    pose_index: int = 0

@dataclass
class DockingConfig:
    """对接配置参数"""
    center_x: float = 0.0
    center_y: float = 0.0
    center_z: float = 0.0
    size_x: float = 20.0
    size_y: float = 20.0
    size_z: float = 20.0
    exhaustiveness: int = 8
    num_modes: int = 10
    energy_range: float = 3.0
    manual_center: bool = False
    blind_docking: bool = False
    random_seed: Optional[int] = None

    def __post_init__(self) -> None:
        self.validate_random_seed()

    def validate_random_seed(self) -> None:
        if self.random_seed is None:
            return
        if (
            type(self.random_seed) is not int
            or self.random_seed <= 0
            or self.random_seed > 2**31 - 1
        ):
            raise ValueError("random_seed must be an integer between 1 and 2147483647")


def _ligand_efficiency(binding_energy: float | None, heavy_atom_count: int) -> float | None:
    """Return LE = -ΔG / heavy-atom count, or None when it is undefined."""
    if (
        type(binding_energy) not in (int, float)
        or not math.isfinite(float(binding_energy))
        or type(heavy_atom_count) is not int
        or heavy_atom_count <= 0
    ):
        return None
    return round(-float(binding_energy) / heavy_atom_count, 3)

class MolecularDockingService:
    """分子对接服务类"""

    def __init__(self, config: Optional[Dict[str, Any]] = None, *,
                 command_scope=None, allowed_output_root=None, chat_toolchain=None):
        self._chat_toolchain = chat_toolchain
        self._chat_toolchain_at_construction = chat_toolchain
        self._chat_command_scope_at_construction = command_scope
        if chat_toolchain is not None:
            from .chat_toolchain import ChatDockingToolchain
            if type(chat_toolchain) is not ChatDockingToolchain:
                raise ValueError("consent_policy_unavailable")
        self._command_scope = command_scope
        self._allowed_output_root = CommandOwnershipScope._bound_output_root(
            command_scope, allowed_output_root,
        )
        # ===== 软件路径配置（根据实际安装位置修改）=====
        # AutoDock Vina 可执行文件
        self.vina_exe = ""
        # ADFRsuite bin 目录（用于蛋白准备）
        self.adfrsuite_path = ""
        # 可直接指定 prepare_receptor 命令；优先于 ADFRsuite bin 自动推断
        self.prepare_receptor_cmd = ""
        # reduce 用于为相互作用分析准备带显式氢的拓扑受体；失败时不阻断对接
        self.reduce_cmd = ""
        # Meeko 命令行工具（用于配体准备，来自 Anaconda 环境）
        self.prepare_ligand_cmd = ""
        # 保留兼容字段
        self.python_exe = sys.executable

        # 创建临时工作目录
        self.work_dir = self._allowed_output_root or os.path.abspath(
            os.path.join(os.getcwd(), "temp_docking")
        )
        os.makedirs(self.work_dir, exist_ok=True)
        ownership = self._adapter_ownership()
        self.vina_adapter = VinaAdapter(**ownership)
        self.receptor_adapter = ADFRAdapter(**ownership)
        # Instantiate lazily so environments without reduce do not alter the
        # existing command-adapter lifecycle or ownership probes.
        self.reduce_adapter = None
        self.ligand_adapter = MeekoAdapter(**ownership)
        self.openbabel_adapter = OpenBabelAdapter(**ownership)
        self._require_command_scope()
        self.configure(config)

        logger.info(f"分子对接服务初始化完成，工作目录: {self.work_dir}")

    def _require_chat_toolchain(self):
        toolchain = self._chat_toolchain_at_construction
        if self._chat_toolchain is not toolchain:
            raise ValueError("consent_policy_unavailable")
        if toolchain is not None:
            if self._command_scope is not self._chat_command_scope_at_construction:
                raise CommandOwnershipUncertainError()
            self._require_command_scope()
            toolchain.require_current()
            toolchain.assert_service(self)
        return toolchain

    def _refresh_adapters(self) -> None:
        ownership = self._adapter_ownership()
        self.vina_adapter = VinaAdapter(self.vina_exe, **ownership)
        self.receptor_adapter = ADFRAdapter(self.prepare_receptor_cmd, **ownership)
        self.reduce_adapter = None
        self.ligand_adapter = MeekoAdapter(self.prepare_ligand_cmd, **ownership)
        self.openbabel_adapter = OpenBabelAdapter(shutil.which("obabel") or "", **ownership)
        self._require_command_scope()

    def _adapter_ownership(self):
        return {} if self._command_scope is None else {"ownership_scope": self._command_scope}

    def _require_command_scope(self):
        scope = self._command_scope
        if scope is None:
            return
        if os.path.normcase(os.path.abspath(self.work_dir)) != os.path.normcase(self._allowed_output_root):
            raise CommandOwnershipUncertainError()
        adapters = (
            self.vina_adapter, self.receptor_adapter, self.reduce_adapter,
            self.ligand_adapter, self.openbabel_adapter,
        )
        if any(
            adapter is not None and getattr(adapter, "_ownership_scope", None) is not scope
            for adapter in adapters
        ):
            raise CommandOwnershipUncertainError()

    def _require_finished_commands(self):
        self._require_command_scope()
        scope = self._command_scope
        if scope is not None:
            with scope._condition:
                if scope._sealed or scope._own_job_cleanup is not None:
                    raise CommandOwnershipUncertainError()
            if not scope._settle_finished_commands():
                raise CommandOwnershipUncertainError()

    def _first_existing_path(self, *candidates: Optional[str]) -> str:
        for candidate in candidates:
            if candidate and os.path.exists(candidate):
                return os.path.abspath(candidate)
        for candidate in candidates:
            if candidate:
                return os.path.abspath(candidate)
        return ""

    def _wrap_command(self, executable: str, *args: str) -> List[str]:
        if executable.lower().endswith(".bat"):
            return ["cmd", "/c", executable, *args]
        return [executable, *args]

    def configure(self, config: Optional[Dict[str, Any]] = None) -> None:
        """Configure docking tool paths from config or environment."""
        raw_config = config or {}
        docking_config = raw_config.get("docking", raw_config)

        raw_vina_timeout = docking_config.get("vina_timeout_seconds")
        if raw_vina_timeout is None:
            raw_vina_timeout = os.environ.get(
                "MOLECULAR_DOCKING_VINA_TIMEOUT_SECONDS",
                "300",
            )
        try:
            vina_timeout_seconds = float(raw_vina_timeout)
            if not math.isfinite(vina_timeout_seconds) or vina_timeout_seconds <= 0:
                raise ValueError("timeout must be a positive finite number")
        except (TypeError, ValueError):
            logger.warning(
                "Invalid Vina timeout %r; using the 300 second default",
                raw_vina_timeout,
            )
            vina_timeout_seconds = 300.0
        self.vina_timeout_seconds = vina_timeout_seconds

        root_dir = docking_config.get("root_dir") or os.environ.get("MOLECULAR_DOCKING_ROOT", "")
        root_dir = os.path.abspath(root_dir) if root_dir else ""
        env_scripts_dir = os.path.join(os.path.dirname(self.python_exe), "Scripts")
        vina_in_path = shutil.which("vina") or shutil.which("vina.exe")
        prepare_receptor_in_path = (
            shutil.which("prepare_receptor")
            or shutil.which("prepare_receptor.bat")
            or shutil.which("prepare_receptor.py")
        )
        prepare_ligand_in_path = (
            shutil.which("mk_prepare_ligand.py")
            or shutil.which("mk_prepare_ligand.exe")
            or shutil.which("mk_prepare_ligand")
        )

        adfr_bin_from_path = os.path.dirname(prepare_receptor_in_path) if prepare_receptor_in_path else ""
        inferred_roots: List[str] = []
        if root_dir:
            inferred_roots.append(root_dir)
        if adfr_bin_from_path:
            adfrsuite_dir = os.path.dirname(adfr_bin_from_path)
            inferred_roots.extend([os.path.dirname(adfrsuite_dir), adfrsuite_dir])
        unique_roots = []
        for path in inferred_roots:
            if path and path not in unique_roots:
                unique_roots.append(path)

        self.vina_exe = self._first_existing_path(
            docking_config.get("vina_exe"),
            os.environ.get("MOLECULAR_DOCKING_VINA"),
            *[os.path.join(base, "Vina", "vina.exe") for base in unique_roots],
            *[os.path.join(base, "vina.exe") for base in unique_roots],
            *[os.path.join(base, "bin", "vina") for base in unique_roots],
            *[os.path.join(base, "bin", "vina.exe") for base in unique_roots],
            vina_in_path,
        )
        self.adfrsuite_path = self._first_existing_path(
            docking_config.get("adfrsuite_bin"),
            os.environ.get("MOLECULAR_DOCKING_ADFR_BIN"),
            adfr_bin_from_path,
            os.path.join(root_dir, "ADFRsuite", "bin") if root_dir else None,
            os.path.join(root_dir, "ADFRsuite-1.0", "bin") if root_dir else None,
            os.path.join(root_dir, "bin") if root_dir else None,
        )
        self.prepare_receptor_cmd = self._first_existing_path(
            docking_config.get("prepare_receptor_cmd"),
            os.environ.get("MOLECULAR_DOCKING_PREPARE_RECEPTOR"),
            prepare_receptor_in_path,
            os.path.join(self.adfrsuite_path, "prepare_receptor.bat") if self.adfrsuite_path else None,
            os.path.join(self.adfrsuite_path, "prepare_receptor") if self.adfrsuite_path else None,
            os.path.join(self.adfrsuite_path, "prepare_receptor.py") if self.adfrsuite_path else None,
        )
        self.reduce_cmd = self._first_existing_path(
            docking_config.get("reduce_cmd"),
            os.environ.get("MOLECULAR_DOCKING_REDUCE"),
            os.path.join(self.adfrsuite_path, "reduce.exe") if self.adfrsuite_path else None,
            os.path.join(self.adfrsuite_path, "reduce.bat") if self.adfrsuite_path else None,
            os.path.join(self.adfrsuite_path, "reduce") if self.adfrsuite_path else None,
            shutil.which("reduce") or shutil.which("reduce.exe"),
        )
        self.prepare_ligand_cmd = self._first_existing_path(
            docking_config.get("prepare_ligand_cmd"),
            os.environ.get("MOLECULAR_DOCKING_PREPARE_LIGAND"),
            os.path.join(env_scripts_dir, "mk_prepare_ligand.exe"),
            os.path.join(env_scripts_dir, "mk_prepare_ligand.py"),
            os.path.join(self.adfrsuite_path, "prepare_ligand.bat") if self.adfrsuite_path else None,
            prepare_ligand_in_path,
        )
        self._refresh_adapters()

    @staticmethod
    def _pdb_text_has_explicit_hydrogens(text: str) -> bool:
        """Return whether PDB text contains at least one explicit H atom."""
        for line in text.splitlines():
            if not line.startswith(("ATOM", "HETATM")):
                continue
            element = line[76:78].strip().upper() if len(line) >= 78 else ""
            atom_name = line[12:16].strip().upper() if len(line) >= 16 else ""
            if element == "H" or atom_name.startswith("H"):
                return True
        return False

    @classmethod
    def _pdb_has_explicit_hydrogens(cls, path: str) -> bool:
        """Return whether a PDB contains at least one explicit hydrogen atom."""
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as stream:
                return cls._pdb_text_has_explicit_hydrogens(stream.read())
        except OSError:
            return False

    def prepare_analysis_receptor(
        self,
        source_path: str,
        output_path: str,
        *,
        cancel_event=None,
        trace: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Preserve a topology-bearing receptor for interaction analysis.

        ProLIF requires explicit protein hydrogens for defensible geometry.  The
        docking receptor preparation output is PDBQT and is therefore not used
        as a topology fallback.  ``reduce`` is best-effort here: if it is not
        installed or fails, the original receptor is still preserved and the
        analyzer will report an unavailable result rather than inventing claims.
        """
        source_path = os.path.abspath(source_path)
        output_path = os.path.abspath(output_path)
        trace = trace if trace is not None else {}
        trace.setdefault("analysis_receptor_source", os.path.basename(source_path))
        try:
            self._raise_if_cancelled(cancel_event)
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            suffix = Path(source_path).suffix.lower()
            if suffix != ".pdb" or self._pdb_has_explicit_hydrogens(source_path):
                self._copy_file_cooperatively(source_path, output_path, cancel_event)
                trace.update({
                    "analysis_receptor_method": "copy",
                    "analysis_receptor_hydrogens": "explicit" if suffix == ".pdb" else "not_verified",
                })
                return True

            if self.reduce_cmd:
                adapter = getattr(self, "reduce_adapter", None)
                adapter_executable = getattr(adapter, "executable", None)
                if adapter is None or (
                    adapter_executable is not None
                    and adapter_executable != os.path.abspath(self.reduce_cmd)
                ):
                    adapter = CommandAdapter(self.reduce_cmd, **self._adapter_ownership())
                    self.reduce_adapter = adapter
                result = adapter.run(
                    adapter.wrap_command("-NOFLIP", "-Quiet", "-DB", "dict", source_path),
                    cwd=os.path.dirname(source_path) or self.work_dir,
                    timeout=60,
                    cancel_event=cancel_event,
                )
                stdout = result.stdout or ""
                if isinstance(stdout, bytes):
                    stdout = stdout.decode("utf-8", errors="replace")
                if result.returncode == 0 and stdout:
                    temporary = f"{output_path}.tmp"
                    with open(temporary, "w", encoding="utf-8", newline="\n") as stream:
                        stream.write(stdout)
                        stream.flush()
                        os.fsync(stream.fileno())
                    if self._pdb_has_explicit_hydrogens(temporary):
                        os.replace(temporary, output_path)
                        trace.update({
                            "analysis_receptor_method": "reduce",
                            "analysis_receptor_hydrogens": "explicit",
                        })
                        return True
                    try:
                        os.unlink(temporary)
                    except OSError:
                        pass
                logger.warning("reduce did not produce an explicit-H receptor (returncode=%s)", result.returncode)
            else:
                logger.info("reduce unavailable; preserving original receptor for fail-closed analysis")
        except (CommandCancelledError, CommandOwnershipUncertainError):
            raise
        except Exception:
            logger.warning("Unable to add explicit hydrogens to analysis receptor", exc_info=True)

        self._copy_file_cooperatively(source_path, output_path, cancel_event)
        trace.update({
            "analysis_receptor_method": "copy_unavailable",
            "analysis_receptor_hydrogens": "unavailable",
        })
        return True

    def _prepare_receptor_candidates(self) -> List[str]:
        candidates = []
        if self.prepare_receptor_cmd:
            candidates.append(self.prepare_receptor_cmd)
        if self.adfrsuite_path:
            candidates.extend(
                [
                    os.path.join(self.adfrsuite_path, "prepare_receptor.bat"),
                    os.path.join(self.adfrsuite_path, "prepare_receptor"),
                    os.path.join(self.adfrsuite_path, "prepare_receptor.py"),
                ]
            )
        return candidates

    def _run_prepare_ligand(self, input_path: str, output_path: str,
                               job_dir: str, log_label: str,
                               log_on_failure: bool = True, *,
                               cancel_event=None) -> Tuple[bool, str]:
        """Run ligand preparation and return success flag plus CLI output."""
        self._require_finished_commands()
        input_path = os.path.abspath(input_path)
        output_path = os.path.abspath(output_path)
        job_dir = os.path.abspath(job_dir)
        logger.info("Executing %s command", log_label)
        adapter_control = {}
        if cancel_event is not None:
            adapter_control["cancel_event"] = cancel_event
        # Fence every initial/retry command, after in-process ligand work and
        # progress callbacks. The later Vina check cannot undo a prep launch.
        self._require_chat_toolchain()
        result = self.ligand_adapter.prepare_ligand(
            input_path,
            output_path,
            job_dir,
            timeout=60,
            **adapter_control,
        )
        combined_output = (result.stderr or "") + ("\n" if result.stderr and result.stdout else "") + (result.stdout or "")

        if result.returncode == 0 and os.path.exists(output_path):
            logger.info("%s succeeded", log_label)
            return True, combined_output

        if log_on_failure:
            logger.error("%s failed (returncode=%s)", log_label, result.returncode)
        return False, combined_output

    def _load_rdkit_mol_from_file(self, ligand_path: str):
        """Load the first molecule from a ligand file via RDKit."""
        from rdkit import Chem

        src_ext = os.path.splitext(ligand_path)[1].lower()
        if src_ext == '.sdf':
            supplier = Chem.SDMolSupplier(ligand_path, removeHs=False)
            for mol in supplier:
                if mol is not None:
                    return mol
            return None
        if src_ext == '.mol':
            return Chem.MolFromMolFile(ligand_path, removeHs=False)
        if src_ext == '.mol2':
            return Chem.MolFromMol2File(ligand_path, removeHs=False)
        if src_ext == '.pdb':
            return Chem.MolFromPDBFile(ligand_path, removeHs=False)
        return None

    @staticmethod
    def _prepare_3d_molecule(mol, *, trace: Optional[Dict[str, Any]] = None):
        """Embed and optimize a molecule, failing closed on invalid geometry."""
        from rdkit.Chem import AllChem

        if trace is not None:
            trace.update(
                {
                    "status": "in_progress",
                    "embedding": {"attempts": []},
                    "force_field": None,
                    "optimization_status": None,
                    "converged": False,
                }
            )

        def record_embedding(method: str, result: int) -> None:
            if trace is not None:
                trace["embedding"]["attempts"].append(
                    {
                        "method": method,
                        "return_code": int(result),
                        "status": "completed" if result >= 0 else "failed",
                    }
                )

        def fail(reason: str):
            if trace is not None:
                trace["status"] = "failed"
                trace["failure_reason"] = reason
            return None

        try:
            embedded = AllChem.EmbedMolecule(mol, AllChem.ETKDGv3())
            record_embedding("ETKDGv3", embedded)
            if embedded < 0:
                embedded = AllChem.EmbedMolecule(mol, AllChem.ETKDGv2())
                record_embedding("ETKDGv2", embedded)
            if embedded < 0 or mol.GetNumConformers() == 0:
                logger.error("RDKit failed to generate a 3D conformer")
                return fail("embedding_failed")

            if AllChem.MMFFHasAllMoleculeParams(mol):
                force_field = "MMFF"
                optimization_status = AllChem.MMFFOptimizeMolecule(mol)
                if optimization_status != 0 and AllChem.UFFHasAllMoleculeParams(mol):
                    logger.warning(
                        "RDKit MMFF optimization did not converge; retrying with UFF"
                    )
                    force_field = "UFF"
                    optimization_status = AllChem.UFFOptimizeMolecule(mol)
            elif AllChem.UFFHasAllMoleculeParams(mol):
                force_field = "UFF"
                optimization_status = AllChem.UFFOptimizeMolecule(mol)
            else:
                logger.error("No complete MMFF or UFF parameters are available")
                return fail("force_field_parameters_missing")

            if trace is not None:
                trace["force_field"] = force_field
                trace["optimization_status"] = int(optimization_status)

            if optimization_status != 0:
                logger.error(
                    "RDKit %s optimization did not converge (status=%s)",
                    force_field,
                    optimization_status,
                )
                return fail("force_field_optimization_not_converged")
            if trace is not None:
                trace["status"] = "completed"
                trace["converged"] = True
            return mol
        except Exception as exc:
            logger.error("RDKit 3D preparation failed (%s)", type(exc).__name__)
            return fail(type(exc).__name__)

    def _prepare_ligand_file_with_explicit_hs(
        self,
        ligand_path: str,
        output_path: str,
        job_dir: str,
        *,
        cancel_event=None,
        trace: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Normalize ligand files through RDKit so Meeko receives explicit hydrogens."""
        try:
            from rdkit import Chem
            from rdkit.Chem import AllChem

            mol = self._load_rdkit_mol_from_file(ligand_path)
            if mol is None:
                logger.error("RDKit could not read the ligand input")
                return False

            mol = Chem.AddHs(mol, addCoords=True)
            if mol.GetNumConformers() == 0:
                geometry_trace = None if trace is None else trace.setdefault("ligand_geometry", {})
                mol = self._prepare_3d_molecule(mol, trace=geometry_trace)
            else:
                # Existing coordinates are still not accepted without a
                # converged force-field check.
                if not AllChem.MMFFHasAllMoleculeParams(mol):
                    if not AllChem.UFFHasAllMoleculeParams(mol):
                        logger.error("No complete force-field parameters are available")
                        return False
                    optimization_status = AllChem.UFFOptimizeMolecule(mol)
                    if trace is not None:
                        trace["ligand_geometry"] = {
                            "status": "completed" if optimization_status == 0 else "failed",
                            "embedding": {"attempts": [], "source": "input_conformer"},
                            "force_field": "UFF",
                            "optimization_status": int(optimization_status),
                            "converged": optimization_status == 0,
                        }
                    if optimization_status != 0:
                        logger.error("RDKit UFF optimization did not converge")
                        return False
                else:
                    optimization_status = AllChem.MMFFOptimizeMolecule(mol)
                    if trace is not None:
                        trace["ligand_geometry"] = {
                            "status": "completed" if optimization_status == 0 else "failed",
                            "embedding": {"attempts": [], "source": "input_conformer"},
                            "force_field": "MMFF",
                            "optimization_status": int(optimization_status),
                            "converged": optimization_status == 0,
                        }
                    if optimization_status != 0:
                        logger.error("RDKit MMFF optimization did not converge")
                        return False
            if mol is None:
                return False

            normalized_sdf_path = os.path.join(job_dir, "ligand_explicit_h.sdf")
            writer = Chem.SDWriter(normalized_sdf_path)
            writer.write(mol)
            writer.close()

            success, _ = self._run_prepare_ligand(
                normalized_sdf_path,
                output_path,
                job_dir,
                "配体文件准备(RDKit显式氢处理后)",
                cancel_event=cancel_event,
            )
            return success

        except (CommandCancelledError, CommandOwnershipUncertainError):
            raise
        except Exception as e:
            logger.error(f"RDKit 显式氢处理异常: {e}")
            return False

    def verify_environment(self) -> bool:
        """Verify that the molecular docking runtime is available."""
        try:
            # Check AutoDock Vina executable.
            if not os.path.exists(self.vina_exe):
                logger.error(f"Vina executable not found: {self.vina_exe}")
                return False

            # Check ADFRsuite prepare_receptor.
            adfr_candidates = self._prepare_receptor_candidates()
            if not any(os.path.exists(p) for p in adfr_candidates):
                logger.error(f"ADFRsuite prepare_receptor not found. Tried: {adfr_candidates}")
                return False

            # Check ligand preparation command, usually provided by Meeko.
            if not self.prepare_ligand_cmd or not os.path.exists(self.prepare_ligand_cmd):
                logger.error(f"Ligand preparation tool not found: {self.prepare_ligand_cmd}")
                return False

            # Check RDKit for SMILES -> SDF conversion and ligand normalization.
            import importlib.util
            if importlib.util.find_spec('rdkit') is None:
                logger.error("当前 Python 环境缺少 rdkit")
                return False

            logger.info("分子对接环境自检通过")
            return True

        except Exception as e:
            logger.error(f"环境自检异常: {e}")
            return False

    def env_diagnostics(self) -> Dict[str, Any]:
        """Return molecular docking environment diagnostics for the frontend."""
        info: Dict[str, Any] = {
            "vina": {"path": self.vina_exe, "exists": False},
            "adfr_prepare_receptor": {"path": None, "exists": False},
            "mk_prepare_ligand": {"path": self.prepare_ligand_cmd, "exists": False},
            "python_env": {"executable": self.python_exe, "has_rdkit": False},
            "work_dir": self.work_dir
        }

        try:
            info["vina"]["exists"] = os.path.exists(self.vina_exe)
        except Exception:
            pass
        try:
            adfr_candidates = self._prepare_receptor_candidates()
            existing = next((p for p in adfr_candidates if os.path.exists(p)), None)
            info["adfr_prepare_receptor"]["path"] = existing or (adfr_candidates[0] if adfr_candidates else None)
            info["adfr_prepare_receptor"]["exists"] = existing is not None
        except Exception:
            pass
        try:
            info["mk_prepare_ligand"]["exists"] = bool(self.prepare_ligand_cmd) and os.path.exists(self.prepare_ligand_cmd)
        except Exception:
            pass

        # Check RDKit in current Python environment.
        try:
            import importlib.util
            info["python_env"]["has_rdkit"] = importlib.util.find_spec('rdkit') is not None
        except Exception as e:
            logger.warning(f"检查 RDKit 环境失败: {e}")

        # Human-readable setup suggestions.
        suggestions: List[str] = []
        if not info["vina"]["exists"]:
            suggestions.append(f"未找到 AutoDock Vina，请配置 MOLECULAR_DOCKING_VINA 或将 vina 加入 PATH。当前检测路径：{self.vina_exe or '未设置'}")
        if not info["adfr_prepare_receptor"]["exists"]:
            suggestions.append(f"未找到 ADFRsuite prepare_receptor，请配置 MOLECULAR_DOCKING_ADFR_BIN 或 MOLECULAR_DOCKING_PREPARE_RECEPTOR。当前检测目录：{self.adfrsuite_path or '未设置'}")
        if not info["mk_prepare_ligand"]["exists"]:
            suggestions.append(f"未找到配体准备工具 mk_prepare_ligand，请安装 Meeko 或配置 MOLECULAR_DOCKING_PREPARE_LIGAND。当前检测路径：{self.prepare_ligand_cmd or '未设置'}")
        if not info["python_env"]["has_rdkit"]:
            suggestions.append("当前 Python 环境缺少 RDKit，请在运行项目的环境中安装 rdkit。")

        info["suggestions"] = suggestions
        info["ok"] = (info["vina"]["exists"] and info["adfr_prepare_receptor"]["exists"]
                      and info["mk_prepare_ligand"]["exists"] and info["python_env"]["has_rdkit"])
        return info

    def prepare_protein(
        self,
        protein_path: str,
        output_path: str,
        *,
        cancel_event=None,
        trace: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        准备蛋白质文件 (PDB -> PDBQT)
        """
        try:
            self._raise_if_cancelled(cancel_event)
            self._require_finished_commands()
            toolchain = self._require_chat_toolchain()
            approved = approved_heavy_atoms(protein_path) if toolchain is not None else None
            if approved is not None and os.path.lexists(output_path):
                # A successful no-op must not reuse stale preparation evidence.
                raise ReceptorPreservationError()
            # 如果传入的本身是PDBQT，直接复制
            if protein_path.lower().endswith('.pdbqt'):
                self._copy_file_cooperatively(
                    protein_path,
                    output_path,
                    cancel_event,
                )
                logger.info("Using receptor input already in PDBQT format")
                if trace is not None:
                    trace["receptor_method"] = "pdbqt_passthrough"
                return True

            adfr_input = protein_path
            if toolchain is None:
                adfr_input, removed_unknown = self._sanitize_adfr_receptor_input(
                    protein_path, output_path)
                if trace is not None and removed_unknown:
                    trace["receptor_input_sanitization"] = "removed_unknown_atoms"
                    trace["receptor_input_removed_atoms"] = removed_unknown

            # 使用 ADFRsuite 的 prepare_receptor（支持 .bat / 无扩展 / .py）
            adfr_candidates = self._prepare_receptor_candidates()
            prepare_receptor_cmd = next((p for p in adfr_candidates if os.path.exists(p)), None)

            if prepare_receptor_cmd:
                self.receptor_adapter = ADFRAdapter(prepare_receptor_cmd, **self._adapter_ownership())
                self._require_command_scope()
                logger.info("Executing receptor preparation command")
                adapter_control = {}
                if cancel_event is not None:
                    adapter_control["cancel_event"] = cancel_event
                if toolchain is not None:
                    # Never apply ADFR's default water/nonstandard-chain removal
                    # to the explicitly approved C receptor. No heavy-atom repair.
                    result = self.receptor_adapter.run(
                        self.receptor_adapter.wrap_command(
                            "-r", adfr_input, "-o", output_path,
                            "-A", "None", "-U", "nphs",
                        ), cwd=self.work_dir, **adapter_control,
                    )
                else:
                    result = self.receptor_adapter.prepare_receptor(
                        adfr_input, output_path, self.work_dir, **adapter_control,
                    )

                if result.returncode == 0:
                    if approved is not None:
                        self._require_finished_commands()
                        if self._require_chat_toolchain() is not toolchain:
                            raise ReceptorPreservationError()
                        validate_prepared_receptor(output_path, approved)
                    logger.info("Receptor preparation succeeded")
                    if trace is not None:
                        trace["receptor_method"] = "adfrsuite_prepare_receptor"
                    return True
                else:
                    logger.error(
                        "ADFRsuite receptor preparation failed (returncode=%s)",
                        result.returncode,
                    )

            logger.error(
                "Receptor preparation unavailable; unsafe simplified fallback disabled"
            )
            return False

        except (CommandCancelledError, CommandOwnershipUncertainError):
            raise
        except Exception as e:
            logger.error(f"蛋白质准备异常: {e}")
            return False

    @staticmethod
    def _sanitize_adfr_receptor_input(protein_path: str, output_path: str) -> Tuple[str, int]:
        """Remove only PDB atoms that ADFR cannot parameterize.

        Some experimental PDB exports contain ``UNX`` records whose element is
        ``X``. Passing those records to ADFR makes charge assignment fail before
        receptor preparation. They are not a validated protein atom or a
        parameterized cofactor, so they must be excluded rather than guessed.
        The original receptor remains unchanged; chat-docking's strict source
        path does not use this compatibility cleanup.
        """
        source = Path(protein_path)
        if source.suffix.lower() != ".pdb":
            return protein_path, 0
        raw_lines = source.read_bytes().splitlines(keepends=True)
        kept, removed = [], 0
        for line in raw_lines:
            if line.startswith((b"ATOM  ", b"HETATM")) and len(line) >= 78:
                residue = line[17:20].strip().upper()
                element = line[76:78].strip().upper()
                if residue == b"UNX" or element == b"X":
                    removed += 1
                    continue
            kept.append(line)
        if removed == 0:
            return protein_path, 0
        destination = Path(output_path).with_name("receptor_adfr_input.pdb")
        destination.write_bytes(b"".join(kept))
        return str(destination), removed

    def prepare_ligand_from_smiles(
        self,
        smiles: str,
        output_path: str,
        *,
        cancel_event=None,
        trace: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        从SMILES生成配体PDBQT文件
        流程：SMILES -> SDF (RDKit，在当前进程内) -> PDBQT (mk_prepare_ligand.exe)
        """
        try:
            self._require_finished_commands()
            from rdkit import Chem
            from rdkit.Chem import AllChem

            job_dir = os.path.dirname(output_path)
            self._raise_if_cancelled(cancel_event)

            # Step 1: SMILES -> 3D SDF（在当前进程内，无需子进程）
            mol = Chem.MolFromSmiles(smiles)
            if mol is None:
                logger.error("Ligand SMILES input is invalid")
                return False

            self._raise_if_cancelled(cancel_event)
            mol = Chem.AddHs(mol)
            geometry_trace = None if trace is None else trace.setdefault("ligand_geometry", {})
            mol = self._prepare_3d_molecule(mol, trace=geometry_trace)
            if mol is None:
                return False
            self._raise_if_cancelled(cancel_event)

            sdf_path = os.path.join(job_dir, "ligand_input.sdf")
            writer = Chem.SDWriter(sdf_path)
            writer.write(mol)
            writer.close()
            self._raise_if_cancelled(cancel_event)

            # Step 2: SDF -> PDBQT (mk_prepare_ligand.exe)
            success, _ = self._run_prepare_ligand(
                sdf_path,
                output_path,
                job_dir,
                "配体准备",
                cancel_event=cancel_event,
            )
            if success and trace is not None:
                trace["ligand_method"] = "rdkit_3d_then_meeko"
            return success

        except (CommandCancelledError, CommandOwnershipUncertainError):
            raise
        except Exception as e:
            logger.error(f"配体准备异常: {e}")
            return False

    def prepare_ligand_from_file(
        self,
        ligand_path: str,
        output_path: str,
        *,
        cancel_event=None,
        trace: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        从文件准备配体 (SDF/MOL/PDB -> PDBQT)
        使用 mk_prepare_ligand.exe 直接转换，无需写临时 Python 脚本
        """
        try:
            self._raise_if_cancelled(cancel_event)
            self._require_finished_commands()
            src_ext = os.path.splitext(ligand_path)[1].lower()
            job_dir = os.path.dirname(output_path)

            # 若输入已经是 PDBQT，直接复制并返回
            if src_ext == '.pdbqt':
                self._copy_file_cooperatively(
                    ligand_path,
                    output_path,
                    cancel_event,
                )
                logger.info("Using ligand input already in PDBQT format")
                if trace is not None:
                    trace["ligand_method"] = "pdbqt_passthrough"
                return True

            success, error_output = self._run_prepare_ligand(
                ligand_path,
                output_path,
                job_dir,
                "配体文件准备",
                log_on_failure=False,
                cancel_event=cancel_event,
            )
            if success:
                if trace is not None:
                    trace["ligand_method"] = "meeko_direct"
                return True

            # Meeko 对部分文件要求显式氢，这里在失败后做一次 RDKit 归一化再重试
            if "implicit Hs" in error_output:
                self._require_finished_commands()
                logger.warning("检测到配体含隐式氢，将先用 RDKit 补显式氢后再次尝试")
                retried = self._prepare_ligand_file_with_explicit_hs(
                    ligand_path,
                    output_path,
                    job_dir,
                    cancel_event=cancel_event,
                    trace=trace,
                )
                if retried and trace is not None:
                    trace["ligand_method"] = "rdkit_explicit_h_retry_then_meeko"
                return retried

            logger.error("Ligand file preparation failed")
            return False

        except (CommandCancelledError, CommandOwnershipUncertainError):
            raise
        except Exception as e:
            logger.error(f"配体文件准备异常: {e}")
            return False

    def run_vina_docking(
        self,
        receptor_path: str,
        ligand_path: str,
        config: DockingConfig,
        output_path: str,
        job_dir: Optional[str] = None,
        *,
        cancel_event=None,
    ) -> bool:
        """
        运行AutoDock Vina对接计算
        """
        try:
            # Config objects are mutable for backwards compatibility. Validate
            # again at the trust boundary so a caller cannot inject a Vina
            # config line after construction.
            config.validate_random_seed()
            self._require_finished_commands()
            try:
                center_values = (config.center_x, config.center_y, config.center_z)
                size_values = (config.size_x, config.size_y, config.size_z)
                if (
                    not all(math.isfinite(float(value)) for value in (*center_values, *size_values))
                    or not all(float(value) > 0 for value in size_values)
                ):
                    return False
            except (TypeError, ValueError):
                return False
            # 每个作业拥有独立配置文件和进程工作目录。
            resolved_job_dir = os.path.abspath(
                job_dir or os.path.dirname(os.path.abspath(output_path))
            )
            os.makedirs(resolved_job_dir, exist_ok=True)
            manifest_path = os.path.join(resolved_job_dir, "run_manifest.json")
            if not os.path.isfile(manifest_path):
                self._write_run_manifest(
                    resolved_job_dir,
                    receptor_path,
                    ligand_path,
                    "file",
                    config,
                    {
                        "source": "direct_caller",
                        "mode": "targeted",
                        "center": [config.center_x, config.center_y, config.center_z],
                        "size": [config.size_x, config.size_y, config.size_z],
                        "exhaustiveness": config.exhaustiveness,
                        "num_modes": config.num_modes,
                        "energy_range": config.energy_range,
                        "warning": "Direct Vina execution received an explicit box; pocket evidence was not independently verified.",
                    },
                )
            if not self._update_run_manifest(
                resolved_job_dir,
                search={"random_seed": config.random_seed},
            ):
                logger.error("Unable to persist docking random seed in manifest")
                return False
            config_path = os.path.join(resolved_job_dir, "config.txt")
            with open(config_path, 'w', encoding='utf-8') as f:
                f.write(f"receptor = {receptor_path}\n")
                f.write(f"ligand = {ligand_path}\n")
                f.write(f"out = {output_path}\n")
                f.write(f"center_x = {config.center_x}\n")
                f.write(f"center_y = {config.center_y}\n")
                f.write(f"center_z = {config.center_z}\n")
                f.write(f"size_x = {config.size_x}\n")
                f.write(f"size_y = {config.size_y}\n")
                f.write(f"size_z = {config.size_z}\n")
                f.write(f"exhaustiveness = {config.exhaustiveness}\n")
                f.write(f"num_modes = {config.num_modes}\n")
                f.write(f"energy_range = {config.energy_range}\n")
                if config.random_seed is not None:
                    f.write(f"seed = {config.random_seed}\n")

            if not self._update_run_manifest(
                resolved_job_dir,
                execution={
                    "status": "running",
                    "config": "config.txt",
                    "command": [Path(self.vina_exe).name or "vina", "--config", "config.txt"],
                    "config_sha256": self._sha256_file(config_path)[0],
                },
            ):
                logger.error("Unable to persist docking execution manifest before Vina launch")
                return False

            # The output path belongs to this job. Remove a pre-existing file
            # before launching Vina so a zero-exit process cannot make a stale
            # pose look like a fresh scientific result.
            output_was_present = os.path.isfile(output_path)
            if output_was_present:
                try:
                    os.remove(output_path)
                except OSError:
                    self._update_run_manifest(
                        resolved_job_dir,
                        execution={
                            "status": "failed",
                            "failure_reason": "output_artifact_stale",
                        },
                    )
                    logger.error("Unable to clear pre-existing Vina output artifact")
                    return False

            # 运行Vina
            adapter_control = {}
            if cancel_event is not None:
                adapter_control["cancel_event"] = cancel_event
            try:
                result = self.vina_adapter.run_config(
                    config_path,
                    resolved_job_dir,
                    timeout=self.vina_timeout_seconds,
                    **adapter_control,
                )
            except (CommandCancelledError, CommandOwnershipUncertainError) as error:
                self._update_run_manifest(
                    resolved_job_dir,
                    execution={
                        "status": (
                            "cancelled"
                            if isinstance(error, CommandCancelledError)
                            else "ownership_uncertain"
                        )
                    },
                )
                raise
            except subprocess.TimeoutExpired:
                self._update_run_manifest(resolved_job_dir, execution={"status": "timeout"})
                raise
            except Exception:
                self._update_run_manifest(resolved_job_dir, execution={"status": "failed"})
                raise

            if result.returncode == 0:
                if not os.path.isfile(output_path):
                    self._update_run_manifest(
                        resolved_job_dir,
                        execution={
                            "status": "failed",
                            "returncode": result.returncode,
                            "failure_reason": (
                                "output_artifact_stale"
                                if output_was_present
                                else "output_artifact_missing"
                            ),
                        },
                    )
                    logger.error("Vina returned success without an output artifact")
                    return False
                actual_command = getattr(result, "args", None) or [
                    Path(self.vina_exe).name or "vina",
                    "--config",
                    "config.txt",
                ]
                self._update_run_manifest(
                    resolved_job_dir,
                    execution={
                        "status": "completed",
                        "returncode": result.returncode,
                        "command": self._manifest_command(actual_command, resolved_job_dir),
                        "output_sha256": (
                            self._sha256_file(output_path)[0]
                            if os.path.isfile(output_path)
                            else None
                        ),
                    },
                )
                logger.info("Vina docking command succeeded")
                return True
            else:
                actual_command = getattr(result, "args", None) or [
                    Path(self.vina_exe).name or "vina",
                    "--config",
                    "config.txt",
                ]
                self._update_run_manifest(
                    resolved_job_dir,
                    execution={
                        "status": "failed",
                        "returncode": result.returncode,
                        "command": self._manifest_command(actual_command, resolved_job_dir),
                    },
                )
                logger.error(
                    "Vina docking command failed (returncode=%s)",
                    result.returncode,
                )
                return False

        except (CommandCancelledError, CommandOwnershipUncertainError):
            raise
        except subprocess.TimeoutExpired:
            logger.error(
                "Vina docking timed out after %s seconds",
                self.vina_timeout_seconds,
            )
            raise
        except Exception as e:
            logger.error(f"Vina对接计算异常: {e}")
            return False

    @staticmethod
    def parse_vina_results_text(output_text: str) -> List[Dict[str, Any]]:
        """Project one verified Vina text artifact into safe pose rows.

        The file-based parser remains the single scientific parser. This text
        adapter is used by owned HTTP projections after the artifact has been
        read through its integrity boundary.
        """
        if not isinstance(output_text, str):
            return []
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                suffix=".pdbqt",
                delete=False,
                newline="\n",
            ) as stream:
                stream.write(output_text)
                temporary_path = stream.name
            parsed = MolecularDockingService.parse_vina_results(temporary_path)
            return [
                {
                    "pose": item.pose_index,
                    "binding_energy": item.binding_energy,
                    "rmsd_lb": item.rmsd_lb,
                    "rmsd_ub": item.rmsd_ub,
                }
                for item in parsed
            ]
        except (OSError, UnicodeError):
            return []
        finally:
            if temporary_path:
                try:
                    os.unlink(temporary_path)
                except OSError:
                    pass

    @staticmethod
    def parse_vina_results(
        output_path: str,
        *,
        diagnostics: Optional[List[str]] = None,
    ) -> List[DockingResult]:
        """
        Parse and validate the complete Vina pose file.

        A score remark by itself is not a scientific result. Every accepted
        pose must contain a finite Vina score, at least one valid atom record,
        and a matching MODEL/ENDMDL boundary. Results are sorted by score so
        callers cannot accidentally treat the first file model as the best.
        """
        results: List[DockingResult] = []
        try:
            if not os.path.exists(output_path):
                logger.error("Vina result file is missing")
                return results

            with open(output_path, "r", encoding="utf-8", errors="strict") as stream:
                lines = stream.read().splitlines()

            model = None
            completed_models = 0
            score_pattern = re.compile(
                r"REMARK\s+VINA\s+RESULT:\s*"
                r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s+"
                r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s+"
                r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
            )

            def invalid(reason: str):
                logger.error("Invalid Vina output: %s", reason)
                if diagnostics is not None:
                    diagnostics.append(reason)
                return []

            for line in lines:
                if line.startswith("MODEL"):
                    if model is not None:
                        return invalid("nested MODEL")
                    fields = line.split()
                    if len(fields) != 2 or not fields[1].isdigit():
                        return invalid("invalid MODEL record")
                    pose_index = int(fields[1])
                    if pose_index != completed_models + 1:
                        return invalid("non-sequential MODEL record")
                    model = {
                        "pose_index": pose_index,
                        "energy": None,
                        "rmsd_lb": None,
                        "rmsd_ub": None,
                        "atoms": [],
                        "atom_serials": set(),
                    }
                    continue

                if line == "ENDMDL":
                    if model is None:
                        return invalid("ENDMDL without MODEL")
                    if model["energy"] is None or not model["atoms"]:
                        return invalid("pose lacks finite score or atom records")
                    results.append(
                        DockingResult(
                            ligand_id=f"pose_{model['pose_index']}",
                            binding_energy=model["energy"],
                            rmsd_lb=model["rmsd_lb"],
                            rmsd_ub=model["rmsd_ub"],
                            pose_data="\n".join(model["atoms"]),
                            pose_index=model["pose_index"],
                        )
                    )
                    completed_models += 1
                    model = None
                    continue

                if model is None:
                    # Vina may emit header remarks outside MODEL blocks. They
                    # are not accepted as scientific scores or poses.
                    if line.startswith(("ATOM  ", "HETATM")):
                        return invalid("atom record outside MODEL")
                    continue

                score = score_pattern.fullmatch(line.strip())
                if score is not None:
                    if model["energy"] is not None:
                        return invalid("multiple score records in pose")
                    try:
                        values = tuple(float(value) for value in score.groups())
                    except (TypeError, ValueError, OverflowError):
                        return invalid("non-numeric score record")
                    if not all(math.isfinite(value) for value in values):
                        return invalid("non-finite score record")
                    if values[1] < 0 or values[2] < values[1]:
                        return invalid("invalid RMSD values")
                    model["energy"], model["rmsd_lb"], model["rmsd_ub"] = values
                    continue

                if line.startswith("REMARK VINA RESULT:"):
                    return invalid("malformed Vina score record")

                if line.startswith(("ATOM  ", "HETATM")):
                    try:
                        serial = int(line[6:11].strip())
                        coordinates = tuple(float(line[start:start + 8]) for start in (30, 38, 46))
                    except (TypeError, ValueError, IndexError):
                        return invalid("malformed atom record")
                    if (
                        serial <= 0
                        or serial in model["atom_serials"]
                        or not all(math.isfinite(value) for value in coordinates)
                    ):
                        return invalid("invalid atom serial or coordinates")
                    model["atom_serials"].add(serial)
                    model["atoms"].append(line)
                elif (
                    line.startswith("REMARK")
                    or line.strip() in {"ROOT", "ENDROOT"}
                    or line.startswith(("BRANCH", "ENDBRANCH", "TORSDOF"))
                ):
                    continue
                elif line.strip():
                    return invalid("unexpected content inside pose")

            if model is not None or not results:
                return invalid("incomplete or empty pose set")

            results.sort(key=lambda item: item.binding_energy)
            logger.info("解析并验证到 %s 个对接构象", len(results))
            return results

        except Exception as e:
            logger.error(f"结果解析异常: {e}")
            if diagnostics is not None:
                diagnostics.append("parser_exception")
            return []

    @staticmethod
    def recover_vina_candidate_pose(
        output_path: str,
        *,
        pose_index: int,
    ) -> tuple[Any, Dict[str, Any]]:
        """Recover one topology-bearing Vina pose through Meeko.

        The output file is parsed in full because ``DockingResult.pose_data``
        intentionally contains only coordinate records.  Meeko's converted
        result list represents ligands, while each ligand carries one
        conformer per Vina model; consequently ``pose_index`` must select a
        conformer and must never be replaced with an implicit first-pose
        assumption.
        """
        if type(pose_index) is not int or pose_index <= 0:
            raise ValueError("pose_index must be a positive integer")
        try:
            from importlib.metadata import version

            meeko_version = version("meeko")
        except Exception:
            meeko_version = "unknown"
        try:
            from rdkit import Chem, __version__ as rdkit_version
            from meeko import PDBQTMolecule, RDKitMolCreate
        except ImportError as error:
            raise ValueError("Meeko and RDKit are required for candidate recovery") from error

        try:
            with open(output_path, "rb") as stream:
                output_bytes = stream.read(64 * 1024 * 1024 + 1)
            if len(output_bytes) > 64 * 1024 * 1024:
                raise ValueError("candidate output exceeds recovery size limit")
            output_text = output_bytes.decode("utf-8", errors="strict")
            pdbqt_molecule = PDBQTMolecule(output_text, skip_typing=True)
            molecule_indices = sorted(set(pdbqt_molecule._atom_annotations["mol_index"]))
            if len(molecule_indices) != 1:
                raise ValueError("candidate output must contain exactly one ligand")
            ligand_index = molecule_indices[0]
            try:
                smiles = pdbqt_molecule._pose_data["smiles"][ligand_index]
                index_map = pdbqt_molecule._pose_data["smiles_index_map"][ligand_index]
            except (KeyError, IndexError, TypeError) as error:
                raise ValueError("candidate output lacks SMILES/index mapping") from error
            if not isinstance(smiles, str) or not smiles or not index_map:
                raise ValueError("candidate output lacks SMILES/index mapping")
            topology = Chem.MolFromSmiles(smiles)
            if topology is None or len(index_map) % 2:
                raise ValueError("candidate mapping is invalid")
            atom_indices = pdbqt_molecule._atom_annotations["mol_index"][ligand_index]
            atoms = pdbqt_molecule.atoms(atom_indices)
            from meeko.utils.autodock4_atom_types_elements import autodock4_atom_types_elements
            mapped_mol, mapped_pdbqt = set(), set()
            for mol_id, pdbqt_id in zip(index_map[::2], index_map[1::2]):
                if (
                    type(mol_id) is not int or type(pdbqt_id) is not int
                    or not 1 <= mol_id <= topology.GetNumAtoms()
                    or not 1 <= pdbqt_id <= len(atoms)
                    or mol_id in mapped_mol or pdbqt_id in mapped_pdbqt
                ):
                    raise ValueError("candidate mapping is invalid")
                element = autodock4_atom_types_elements.get(str(atoms[pdbqt_id - 1]["atom_type"]))
                if element != topology.GetAtomWithIdx(mol_id - 1).GetSymbol():
                    raise ValueError("candidate mapping element mismatch")
                mapped_mol.add(mol_id)
                mapped_pdbqt.add(pdbqt_id)
            heavy_ids = {atom.GetIdx() + 1 for atom in topology.GetAtoms() if atom.GetAtomicNum() > 1}
            if not heavy_ids.issubset(mapped_mol):
                raise ValueError("candidate mapping omits heavy atoms")
            converted = RDKitMolCreate.from_pdbqt_mol(pdbqt_molecule)
            if len(converted) != 1 or converted[0] is None:
                raise ValueError("candidate output did not produce exactly one ligand")
            molecule = converted[0]
            conformer_count = molecule.GetNumConformers()
            model_count = sum(line.startswith("MODEL ") for line in output_text.splitlines())
            if conformer_count != model_count or pose_index > conformer_count:
                raise ValueError("candidate output lacks the requested model conformer")
            selected = Chem.Mol(molecule)
            selected_conformer = pose_index - 1
            for conformer_id in range(selected.GetNumConformers() - 1, -1, -1):
                if conformer_id != selected_conformer:
                    selected.RemoveConformer(conformer_id)
            if selected.GetNumConformers() != 1:
                raise ValueError("candidate model selection did not produce one conformer")
            return selected, {
                "selected_model_index": pose_index,
                "ligand_count": len(converted),
                "conformer_count": conformer_count,
                "meeko_version": meeko_version,
                "rdkit_version": rdkit_version,
                "output_sha256": hashlib.sha256(output_bytes).hexdigest(),
                "output_size_bytes": len(output_bytes),
            }
        except Exception as error:
            # External parsers may raise IndexError/AttributeError as well as
            # ValueError. Normalize failures without exposing input paths.
            if isinstance(error, ValueError) and str(error).startswith("candidate "):
                raise
            raise ValueError("candidate output could not be converted to an independent topology") from error

    @classmethod
    def _auto_box_from_co_crystal(cls, pdb_path: str, cancel_event=None):
        minimum = [float("inf"), float("inf"), float("inf")]
        maximum = [float("-inf"), float("-inf"), float("-inf")]
        excluded_residues = {
            "HOH", "WAT", "H2O", "NA", "CL", "K", "CA", "MG", "ZN",
            "MN", "FE", "CU", "CO", "NI", "BR", "I",
        }
        ligand_residues = set()
        found = 0
        try:
            with open(pdb_path, "r", encoding="utf-8", errors="ignore") as stream:
                for line_number, line in enumerate(stream):
                    if line_number % 256 == 0:
                        cls._raise_if_cancelled(cancel_event)
                    if not line.startswith("HETATM"):
                        continue
                    residue = (line[17:20].strip() or "").upper()
                    if not residue or residue in excluded_residues:
                        continue
                    # Auto-boxing is only safe for one explicitly identifiable
                    # co-crystal ligand residue. Multiple hetero residues may
                    # be cofactors, metals, additives, or alternate ligands;
                    # fail closed and require an explicit user box instead.
                    ligand_residues.add((
                        line[21:22].strip(),
                        residue,
                        line[22:26].strip(),
                        line[26:27].strip(),
                    ))
                    if len(ligand_residues) > 1:
                        return None
                    try:
                        coordinates = [
                            float(line[30:38]),
                            float(line[38:46]),
                            float(line[46:54]),
                        ]
                    except (TypeError, ValueError):
                        parts = line.split()
                        if len(parts) < 9:
                            continue
                        coordinates = [float(parts[6]), float(parts[7]), float(parts[8])]
                    found += 1
                    for index, coordinate in enumerate(coordinates):
                        minimum[index] = min(minimum[index], coordinate)
                        maximum[index] = max(maximum[index], coordinate)
        except CommandCancelledError:
            raise
        except Exception:
            return None
        if found == 0:
            return None
        center = [(low + high) / 2.0 for low, high in zip(minimum, maximum)]
        size = [max(10.0, high - low + 8.0) for low, high in zip(minimum, maximum)]
        return (*center, *size)

    @staticmethod
    def _validate_box_values(center_values, size_values) -> None:
        try:
            values = (*center_values, *size_values)
            if (
                not all(math.isfinite(float(value)) for value in values)
                or not all(float(value) > 0 for value in size_values)
            ):
                raise ValueError("invalid_docking_box")
        except (TypeError, ValueError):
            raise ValueError("invalid_docking_box") from None

    def _resolve_docking_box(self, receptor_file: str, config: DockingConfig, cancel_event=None) -> Dict[str, Any]:
        """Resolve the box source and reject unsupported silent defaults."""
        center_values = (config.center_x, config.center_y, config.center_z)
        size_values = (config.size_x, config.size_y, config.size_z)
        self._validate_box_values(center_values, size_values)
        default_box = (
            abs(config.center_x) < 1e-6
            and abs(config.center_y) < 1e-6
            and abs(config.center_z) < 1e-6
            and abs(config.size_x - 20.0) < 1e-6
            and abs(config.size_y - 20.0) < 1e-6
            and abs(config.size_z - 20.0) < 1e-6
        )
        if config.blind_docking:
            return {
                "source": "blind_explicit",
                "mode": "blind",
                "center": [config.center_x, config.center_y, config.center_z],
                "size": [config.size_x, config.size_y, config.size_z],
                "exhaustiveness": config.exhaustiveness,
                "num_modes": config.num_modes,
                "energy_range": config.energy_range,
                "warning": "The user explicitly enabled blind docking; box size, search cost and pocket coverage must be interpreted accordingly.",
            }
        if config.manual_center or not default_box:
            return {
                "source": "user_explicit",
                "mode": "targeted",
                "center": [config.center_x, config.center_y, config.center_z],
                "size": [config.size_x, config.size_y, config.size_z],
                "exhaustiveness": config.exhaustiveness,
                "num_modes": config.num_modes,
                "energy_range": config.energy_range,
                "warning": "The docking box was supplied explicitly; pocket evidence was not independently verified.",
            }

        auto_box = self._auto_box_from_co_crystal(receptor_file, cancel_event)
        if auto_box is not None:
            self._validate_box_values(auto_box[:3], auto_box[3:])
            (
                config.center_x,
                config.center_y,
                config.center_z,
                config.size_x,
                config.size_y,
                config.size_z,
            ) = auto_box
            return {
                "source": "co_crystal_ligand",
                "mode": "targeted",
                "center": [config.center_x, config.center_y, config.center_z],
                "size": [config.size_x, config.size_y, config.size_z],
                "exhaustiveness": config.exhaustiveness,
                "num_modes": config.num_modes,
                "energy_range": config.energy_range,
                "warning": "The box was estimated from one identifiable non-solvent co-crystal ligand residue; ambiguous hetero residues require explicit confirmation.",
            }

        raise ValueError("docking_box_confirmation_required")

    @staticmethod
    def _sha256_file(path: str) -> tuple[str, int]:
        digest = hashlib.sha256()
        size = 0
        with open(path, "rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(chunk)
                digest.update(chunk)
        return digest.hexdigest(), size

    def _write_run_manifest(
        self,
        job_dir: str,
        receptor_file: str,
        ligand_input: str,
        input_type: str,
        config: DockingConfig,
        box_provenance: Dict[str, Any],
    ) -> tuple[str, Dict[str, Any]]:
        """Persist reproducibility metadata without exposing absolute inputs."""
        if os.path.isfile(receptor_file):
            receptor_digest, receptor_size = self._sha256_file(receptor_file)
            receptor_available = True
        else:
            receptor_digest, receptor_size = None, None
            receptor_available = False
        if input_type == "smiles":
            ligand_bytes = ligand_input.encode("utf-8")
            ligand_digest = hashlib.sha256(ligand_bytes).hexdigest()
            ligand_size = len(ligand_bytes)
            ligand_source = "smiles"
            ligand_available = True
        else:
            if os.path.isfile(ligand_input):
                ligand_digest, ligand_size = self._sha256_file(ligand_input)
                ligand_available = True
            else:
                ligand_digest, ligand_size = None, None
                ligand_available = False
            ligand_source = Path(ligand_input).suffix.lower().lstrip(".") or "file"
        preprocessing = {
            "receptor": "ADFRsuite preparation or validated PDBQT passthrough",
            "ligand": (
                "RDKit ETKDGv3/ETKDGv2 + converged MMFF/UFF + Meeko"
                if input_type == "smiles"
                else "Meeko direct preparation or RDKit explicit-hydrogen retry; PDBQT is passed through"
            ),
            "implicit_hydrogen_policy": "recorded by preparation path; no silent fallback to unoptimized geometry",
        }
        preprocessing_policy = {
            "protonation": "not_changed_by_service",
            "tautomerization": "not_performed_by_service",
            "stereochemistry": "preserved_from_input",
            "missing_atoms": "not_reconstructed_by_service",
            "alternate_conformations": "delegated_to_input_or_preparer",
            "waters": "delegated_to_receptor_preparer",
            "metals_and_cofactors": "delegated_to_receptor_preparer",
        }
        preprocessing_state = {
            "status": "planned",
            "receptor": {"status": "pending", "output": "receptor.pdbqt"},
            "ligand": {"status": "pending", "output": "ligand.pdbqt"},
        }
        manifest = {
            "schema_version": 1,
            "inputs": {
                "receptor": {"available": receptor_available, "sha256": receptor_digest, "size_bytes": receptor_size},
                "ligand": {"available": ligand_available, "source": ligand_source, "sha256": ligand_digest, "size_bytes": ligand_size},
            },
            "docking_box": box_provenance,
            "search": {
                "exhaustiveness": config.exhaustiveness,
                "num_modes": config.num_modes,
                "energy_range": config.energy_range,
                "random_seed": config.random_seed,
            },
            "preprocessing": preprocessing,
            "preprocessing_policy": preprocessing_policy,
            "preprocessing_state": preprocessing_state,
            "execution": {"status": "planned"},
        }
        manifest_path = os.path.join(job_dir, "run_manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as stream:
            json.dump(manifest, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
        return manifest_path, {"preprocessing": preprocessing, "manifest": "run_manifest.json"}

    def _update_run_manifest(self, job_dir: str, **updates: Dict[str, Any]) -> None:
        """Atomically add actual execution state to the per-job manifest."""
        manifest_path = Path(job_dir) / "run_manifest.json"
        if not manifest_path.is_file():
            return False
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for key, value in updates.items():
                current = manifest.get(key)
                if isinstance(current, dict) and isinstance(value, dict):
                    current.update(value)
                else:
                    manifest[key] = value
            temporary = manifest_path.with_name(f".{manifest_path.name}.tmp")
            temporary.write_text(
                json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, manifest_path)
            return True
        except Exception:
            logger.warning("Unable to update docking run manifest", exc_info=True)
            try:
                if 'temporary' in locals() and temporary.exists():
                    temporary.unlink()
            except OSError:
                logger.warning("Unable to remove temporary docking manifest", exc_info=True)
            return False

    def _manifest_command(self, command: Any, job_dir: str) -> List[str]:
        """Return the adapter's command without absolute machine paths."""
        command = list(command or [])
        root = Path(job_dir).resolve()
        safe = []
        for value in command:
            raw_value = str(value)
            # Windows command switches such as ``/c`` are absolute-looking
            # on POSIX, but are not filesystem paths and must remain intact.
            if raw_value.startswith("/") and len(raw_value) <= 3 and "/" not in raw_value[1:]:
                safe.append(raw_value)
                continue
            path = Path(raw_value)
            try:
                relative = path.resolve().relative_to(root)
                safe.append(str(relative).replace("\\", "/"))
            except (OSError, ValueError):
                safe.append(path.name if path.is_absolute() else str(value))
        return safe

    @staticmethod
    def _cancel_requested(cancel_event) -> bool:
        return bool(cancel_event is not None and cancel_event.is_set())

    @classmethod
    def _copy_file_cooperatively(
        cls,
        source: str,
        destination: str,
        cancel_event,
        *,
        max_bytes: int | None = None,
    ) -> None:
        with open(source, "rb") as reader, open(destination, "wb") as writer:
            total = 0
            while True:
                cls._raise_if_cancelled(cancel_event)
                chunk = reader.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if max_bytes is not None and total > max_bytes:
                    raise ValueError("reference pose exceeds size limit")
                writer.write(chunk)
            writer.flush()
            os.fsync(writer.fileno())
        cls._raise_if_cancelled(cancel_event)

    @classmethod
    def _raise_if_cancelled(cls, cancel_event) -> None:
        if cls._cancel_requested(cancel_event):
            raise CommandCancelledError()

    @classmethod
    def _emit_phase(
        cls,
        phase: str,
        progress: int,
        progress_callback,
        cancel_event,
    ) -> None:
        cls._raise_if_cancelled(cancel_event)
        if progress_callback is not None:
            try:
                callback_result = progress_callback(phase, progress)
                if inspect.isawaitable(callback_result):
                    close = getattr(callback_result, "close", None)
                    if callable(close):
                        close()
                    raise TypeError("asynchronous progress callbacks are unsupported")
            except Exception as error:
                cls._raise_if_cancelled(cancel_event)
                raise _ProgressCallbackError() from error
        cls._raise_if_cancelled(cancel_event)

    @staticmethod
    def _discard_partial_job(job_dir: str) -> List[str]:
        path = Path(job_dir)
        if not path.exists():
            return []
        try:
            shutil.rmtree(path)
            return ["Partial docking artifacts were removed."]
        except Exception:
            try:
                import uuid

                quarantine = path.with_name(f".cancelled-{uuid.uuid4().hex}")
                os.replace(path, quarantine)
                return ["Partial docking artifacts were quarantined."]
            except Exception:
                return ["Partial docking artifacts could not be fully removed."]

    @classmethod
    def _failed_job_response(
        cls,
        job_dir: str,
        error_code: str,
        error: str,
        *,
        cleanup=None,
        scientific_quality: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        response: Dict[str, Any] = {
            "success": False,
            "error_code": error_code,
            "error": error,
        }
        warnings = cls._discard_partial_job(job_dir) if cleanup is None else cleanup()
        if warnings:
            response["warnings"] = warnings
        if scientific_quality is not None:
            response["scientific_quality"] = scientific_quality
        return response

    async def perform_docking(
        self,
        receptor_file: str,
        ligand_input: str,
        config: DockingConfig,
        input_type: str = "smiles",
        *,
        job_id: str | None = None,
        progress_callback=None,
        cancel_event=None,
        owner_session_id: str | None = None,
        reference_pose: str | None = None,
        redocking_rmsd_threshold: float | None = None,
    ) -> Dict[str, Any]:
        """Run a traceable docking workflow with cooperative cancellation."""
        import uuid

        scope = self._command_scope
        resolved_job_id = (scope._task_id if scope is not None else str(uuid.uuid4())) if job_id is None else job_id
        if scope is not None and cancel_event is None:
            cancel_event = scope._cancel_event
        try:
            CommandAdapter._validate_cancel_event(cancel_event)
            if scope is not None and scope._cancel_event is not None and cancel_event is not scope._cancel_event:
                raise TypeError("Command cancellation owner mismatch")
        except TypeError:
            return {
                "success": False,
                "error_code": "invalid_control",
                "error": "Docking cancellation control is invalid",
            }
        if progress_callback is not None and not callable(progress_callback):
            return {
                "success": False,
                "error_code": "invalid_control",
                "error": "Docking progress callback is invalid",
            }
        if (
            not isinstance(resolved_job_id, str)
            or not _SAFE_JOB_ID.fullmatch(resolved_job_id)
            or resolved_job_id in {".", ".."}
            or resolved_job_id.endswith(".")
        ):
            return {
                "success": False,
                "error_code": "invalid_job_id",
                "error": "Docking job id is invalid",
            }

        if redocking_rmsd_threshold is not None and (
            isinstance(redocking_rmsd_threshold, bool)
            or type(redocking_rmsd_threshold) not in (int, float)
            or not math.isfinite(float(redocking_rmsd_threshold))
            or float(redocking_rmsd_threshold) <= 0
        ):
            return {
                "success": False,
                "error_code": "invalid_redocking_rmsd_threshold",
                "error": "Redocking RMSD threshold must be a finite positive number",
            }

        if scope is not None:
            try:
                self._require_command_scope()
                if resolved_job_id != scope._task_id:
                    raise CommandOwnershipUncertainError()
                with scope._condition:
                    if scope._own_job_cleanup is not None:
                        return {"success": False, "error_code": "job_conflict",
                                "error": "Docking job already exists"}
                    if scope._sealed:
                        raise CommandOwnershipUncertainError()
                self._require_finished_commands()
            except CommandOwnershipUncertainError:
                return {"success": False, "error_code": "process_ownership_uncertain",
                        "error": "Docking process ownership could not be verified"}

        job_dir = os.path.join(self.work_dir, f"docking_{resolved_job_id}")
        try:
            os.mkdir(job_dir)
        except FileExistsError:
            return {
                "success": False,
                "error_code": "job_conflict",
                "error": "Docking job already exists",
            }
        except OSError:
            return {
                "success": False,
                "error_code": "job_directory_failed",
                "error": "Docking job directory could not be created",
            }

        # Preserve a topology-bearing receptor for the optional backend
        # interaction analyzer.  The prepared PDBQT is intentionally not used
        # as an analysis fallback because it may lack bond orders and explicit
        # hydrogens.
        analysis_receptor_trace: Dict[str, Any] = {}

        # Capture only the directory created above. No conflicting invocation,
        # mutable service root or neighbor can acquire this cleanup obligation.
        owned_root = self.work_dir
        history_attempted = False
        owned_cleanup_warnings: List[str] = []
        pending_warning = "Docking physical cleanup is pending or unconfirmed."

        def cleanup_owned_job():
            if pending_warning in owned_cleanup_warnings:
                owned_cleanup_warnings.remove(pending_warning)
            cleaned = True
            if history_attempted:
                try:
                    from src.docking.history_index import remove_history_record

                    remove_history_record(owned_root, resolved_job_id)
                except Exception:
                    owned_cleanup_warnings.append("Docking history cleanup could not be confirmed.")
                    cleaned = False
            try:
                warnings = self._discard_partial_job(job_dir)
            except Exception:
                warnings = ["Partial docking artifacts could not be fully removed."]
            owned_cleanup_warnings.extend(warnings)
            # Quarantine is truthful preservation, not confirmed deletion. Keep
            # it unresolved for the existing caller's cleanup accounting.
            return cleaned and warnings in ([], ["Partial docking artifacts were removed."])

        def defer_owned_cleanup():
            scope._defer_own_job_cleanup(resolved_job_id, cleanup_owned_job)
            if not scope._run_own_job_cleanup() and not owned_cleanup_warnings:
                owned_cleanup_warnings.append(pending_warning)
            return owned_cleanup_warnings

        cleanup_control = {} if scope is None else {"cleanup": defer_owned_cleanup}

        try:
            history_written = False
            history_cleanup_warnings: List[str] = []
            toolchain = self._require_chat_toolchain()
            approved_receptor = None
            if toolchain is not None:
                try:
                    approved_receptor = approved_heavy_atoms(receptor_file)
                except ReceptorPreservationError:
                    return self._failed_job_response(
                        job_dir, "receptor_preparation_failed",
                        "Approved receptor cannot be safely prepared", **cleanup_control,
                    )
            receptor_pdbqt = os.path.join(job_dir, "receptor.pdbqt")
            ligand_pdbqt = os.path.join(job_dir, "ligand.pdbqt")
            output_pdbqt = os.path.join(job_dir, "result.pdbqt")
            command_control = {}
            if cancel_event is not None:
                command_control["cancel_event"] = cancel_event

            try:
                receptor_suffix = Path(receptor_file).suffix.lower()
                if receptor_suffix in {".pdb", ".cif", ".mmcif"} and os.path.isfile(receptor_file):
                    self.prepare_analysis_receptor(
                        receptor_file,
                        os.path.join(job_dir, f"analysis_receptor{receptor_suffix}"),
                        cancel_event=cancel_event,
                        trace=analysis_receptor_trace,
                    )
            except (CommandCancelledError, CommandOwnershipUncertainError):
                raise
            except OSError:
                logger.warning("Unable to preserve receptor analysis artifact", exc_info=True)

            self._emit_phase(*_DOCKING_PHASES[0], progress_callback, cancel_event)
            try:
                box_provenance = self._resolve_docking_box(
                    receptor_file,
                    config,
                    cancel_event,
                )
            except ValueError as error:
                if str(error) == "docking_box_confirmation_required":
                    return self._failed_job_response(
                        job_dir,
                        "docking_box_confirmation_required",
                        "No supported pocket evidence or explicit docking box was provided; confirm a targeted or blind docking box.",
                        **cleanup_control,
                    )
                if str(error) == "invalid_docking_box":
                    return self._failed_job_response(
                        job_dir,
                        "invalid_docking_box",
                        "Docking box coordinates must be finite and box dimensions must be positive.",
                        **cleanup_control,
                    )
                raise
            _, preparation_provenance = self._write_run_manifest(
                job_dir,
                receptor_file,
                ligand_input,
                input_type,
                config,
                box_provenance,
            )
            reference_provenance: Dict[str, Any] = {
                "supplied": reference_pose is not None,
            }
            assessment_reference_pose = reference_pose
            if reference_pose is not None:
                try:
                    reference_path = Path(reference_pose).expanduser()
                    if reference_path.suffix.lower() not in {".sdf", ".sd", ".mol", ".mol2", ".pdb"}:
                        raise ValueError("unsupported reference format")
                    if not reference_path.is_file():
                        raise ValueError("reference file missing")
                    reference_provenance["format"] = reference_path.suffix.lower().lstrip(".")
                    snapshot_name = f"reference_pose{reference_path.suffix.lower()}"
                    snapshot_path = Path(job_dir) / snapshot_name
                    self._copy_file_cooperatively(
                        str(reference_path), str(snapshot_path), cancel_event,
                        max_bytes=10 * 1024 * 1024,
                    )
                    reference_digest, reference_size = self._sha256_file(str(snapshot_path))
                    if not reference_size:
                        raise ValueError("empty reference file")
                    assessment_reference_pose = str(snapshot_path)
                    reference_provenance.update({
                        "available": True,
                        "sha256": reference_digest,
                        "size_bytes": reference_size,
                        "snapshot": snapshot_name,
                        "snapshot_sha256": reference_digest,
                    })
                except (OSError, TypeError, ValueError):
                    quality = {
                        "status": "failed",
                        "redocking_rmsd": {"status": "failed", "reason_code": "reference_pose_invalid"},
                    }
                    return self._failed_job_response(
                        job_dir, "reference_pose_invalid", "Reference pose snapshot could not be preserved.",
                        scientific_quality=quality, **cleanup_control,
                    )
            self._update_run_manifest(
                job_dir,
                reference_pose=reference_provenance,
            )
            preparation_trace: Dict[str, Any] = {}
            if analysis_receptor_trace:
                preparation_trace["analysis_receptor"] = analysis_receptor_trace

            def run_preparation(method, *args):
                kwargs = dict(command_control)
                try:
                    parameters = inspect.signature(method).parameters.values()
                    if any(
                        parameter.name == "trace"
                        or parameter.kind is inspect.Parameter.VAR_KEYWORD
                        for parameter in parameters
                    ):
                        kwargs["trace"] = preparation_trace
                except (TypeError, ValueError):
                    pass
                return method(*args, **kwargs)

            self._update_run_manifest(
                job_dir,
                preprocessing_state={"status": "in_progress"},
            )
            if not run_preparation(self.prepare_protein, receptor_file, receptor_pdbqt):
                self._update_run_manifest(
                    job_dir,
                    preprocessing_state={
                        "status": "failed",
                        "receptor": {"status": "failed", "method": "adfrsuite_or_pdbqt_passthrough"},
                    },
                )
                return self._failed_job_response(
                    job_dir,
                    "receptor_preparation_failed",
                    "Receptor preparation failed",
                    **cleanup_control,
                )
            self._update_run_manifest(
                job_dir,
                preprocessing_state={
                    "status": "in_progress",
                    "receptor": {
                        "status": "completed",
                        "method": preparation_trace.get(
                            "receptor_method",
                            "pdbqt_passthrough"
                            if receptor_file.lower().endswith(".pdbqt")
                            else "adfrsuite_prepare_receptor",
                        ),
                    },
                },
            )
            self._raise_if_cancelled(cancel_event)
            self._require_finished_commands()

            self._emit_phase(*_DOCKING_PHASES[1], progress_callback, cancel_event)
            if input_type == "smiles":
                ligand_ready = run_preparation(
                    self.prepare_ligand_from_smiles,
                    ligand_input,
                    ligand_pdbqt,
                )
            else:
                ligand_ready = run_preparation(
                    self.prepare_ligand_from_file,
                    ligand_input,
                    ligand_pdbqt,
                )
            if not ligand_ready:
                self._update_run_manifest(
                    job_dir,
                    preprocessing_state={
                        "status": "failed",
                        "ligand": {"status": "failed"},
                    },
                )
                return self._failed_job_response(
                    job_dir,
                    "ligand_preparation_failed",
                    "Ligand preparation failed",
                    **cleanup_control,
                )
            self._update_run_manifest(
                job_dir,
                preprocessing_state={
                    "status": "completed",
                    "ligand": {
                        "status": "completed",
                        "method": preparation_trace.get(
                            "ligand_method",
                            "rdkit_3d_then_meeko"
                            if input_type == "smiles"
                            else (
                                "pdbqt_passthrough"
                                if ligand_input.lower().endswith(".pdbqt")
                                else "meeko_file_preparation_with_explicit_hydrogen_retry"
                            ),
                        ),
                    },
                    "trace": preparation_trace,
                },
            )
            self._raise_if_cancelled(cancel_event)
            self._require_finished_commands()

            self._emit_phase(*_DOCKING_PHASES[2], progress_callback, cancel_event)
            if self._require_chat_toolchain() is not toolchain:
                raise ReceptorPreservationError()
            if approved_receptor is not None:
                try:
                    # Recheck AFTER ligand preparation/callbacks, against the
                    # pre-command snapshot (never a reread of a mutated source).
                    validate_prepared_receptor(receptor_pdbqt, approved_receptor)
                except ReceptorPreservationError:
                    return self._failed_job_response(
                        job_dir, "receptor_preparation_failed",
                        "Prepared receptor does not preserve approved heavy atoms",
                        **cleanup_control,
                    )
            docking_succeeded = self.run_vina_docking(
                receptor_pdbqt,
                ligand_pdbqt,
                config,
                output_pdbqt,
                job_dir=job_dir,
                **command_control,
            )
            self._raise_if_cancelled(cancel_event)
            if not docking_succeeded:
                return self._failed_job_response(
                    job_dir,
                    "docking_failed",
                    "AutoDock Vina execution failed",
                    **cleanup_control,
                )

            self._require_finished_commands()
            self._emit_phase(*_DOCKING_PHASES[3], progress_callback, cancel_event)
            results = self.parse_vina_results(output_pdbqt)
            self._raise_if_cancelled(cancel_event)
            if not results:
                return self._failed_job_response(
                    job_dir,
                    "scientific_validation_failed",
                    "Docking result validation failed",
                    **cleanup_control,
                )
            self._require_finished_commands()

            heavy_atom_count = 0
            try:
                if input_type == "smiles":
                    from rdkit import Chem as _Chem

                    molecule = _Chem.MolFromSmiles(ligand_input)
                    if molecule:
                        heavy_atom_count = molecule.GetNumHeavyAtoms()
                else:
                    with open(ligand_pdbqt, "r", errors="ignore") as stream:
                        heavy_atom_count = sum(
                            1
                            for line in stream
                            if (line.startswith("ATOM") or line.startswith("HETATM"))
                            and not line[12:16].strip().startswith("H")
                        )
            except Exception:
                heavy_atom_count = 0
            from .reproducibility import assess_pose_geometry

            candidate_pose = None
            candidate_provenance: Dict[str, Any] = {}
            if reference_pose is not None and redocking_rmsd_threshold is not None:
                try:
                    candidate_pose, candidate_provenance = self.recover_vina_candidate_pose(
                        output_pdbqt,
                        pose_index=results[0].pose_index,
                    )
                except (AttributeError, IndexError, OSError, TypeError, ValueError, RuntimeError):
                    candidate_provenance = {
                        "status": "unavailable",
                        "reason_code": "candidate_pose_unavailable",
                    }
            output_digest = output_size = None
            if reference_pose is not None:
                try:
                    output_digest, output_size = self._sha256_file(output_pdbqt)
                except (OSError, TypeError, ValueError):
                    output_digest, output_size = None, None

            pose_geometry = assess_pose_geometry(
                results,
                center=(config.center_x, config.center_y, config.center_z),
                size=(config.size_x, config.size_y, config.size_z),
                expected_heavy_atom_count=heavy_atom_count or None,
                box_source=box_provenance.get("source"),
                reference_pose=assessment_reference_pose,
                candidate_pose=candidate_pose,
                redocking_rmsd_threshold=redocking_rmsd_threshold,
            )
            if reference_pose is not None:
                pose_geometry["redocking_rmsd_provenance"] = {
                    "reference": reference_provenance,
                    "candidate": candidate_provenance or None,
                    "output_sha256": output_digest,
                    "output_size_bytes": output_size,
                }
            manifest_quality_updates: Dict[str, Any] = {
                "scientific_quality": pose_geometry,
            }
            if reference_pose is not None:
                manifest_quality_updates["output"] = {
                    "path": "result.pdbqt",
                    "sha256": output_digest,
                    "size_bytes": output_size,
                    "selected_model_index": results[0].pose_index,
                    "candidate_recovery": candidate_provenance or None,
                }
            quality_persisted = self._update_run_manifest(job_dir, **manifest_quality_updates)
            if not quality_persisted:
                pose_geometry["status"] = "failed"
                pose_geometry["failures"].append("scientific_quality_persistence_failed")
            if pose_geometry["status"] != "passed":
                self._update_run_manifest(
                    job_dir,
                    scientific_quality=pose_geometry,
                    execution={
                        "status": "failed",
                        "failure_reason": "scientific_pose_geometry_failed",
                    },
                )
                return self._failed_job_response(
                    job_dir,
                    "scientific_pose_geometry_failed",
                    "Docking pose geometry did not satisfy the recorded input and box evidence.",
                    **cleanup_control,
                    scientific_quality=pose_geometry,
                )
            formatted_results = [
                {
                    "pose": item.pose_index or index,
                    "binding_energy": item.binding_energy,
                    "rmsd_lb": item.rmsd_lb,
                    "rmsd_ub": item.rmsd_ub,
                    "ligand_efficiency": _ligand_efficiency(
                        item.binding_energy,
                        heavy_atom_count,
                    ),
                }
                for index, item in enumerate(results, 1)
            ]
            self._raise_if_cancelled(cancel_event)

            resolved_pose_file = str(Path(output_pdbqt).resolve())
            best_pose = dict(formatted_results[0])
            best_pose["pose_file"] = resolved_pose_file
            history_warnings = []
            self._require_finished_commands()
            try:
                from src.docking.history_index import (
                    build_history_record,
                    remove_history_record,
                    upsert_history_record,
                )

                self._raise_if_cancelled(cancel_event)
                if scope is not None:
                    history_attempted = True  # commit-then-raise still owns this one entry
                upsert_history_record(
                    self.work_dir,
                    build_history_record(
                        job_dir,
                        job_id=resolved_job_id,
                        status="completed",
                        owner_session_id=owner_session_id,
                    ),
                )
                history_written = True
                if self._cancel_requested(cancel_event):
                    if scope is None:
                        remove_history_record(self.work_dir, resolved_job_id)
                        history_written = False
                    raise CommandCancelledError()
            except CommandCancelledError:
                if history_written and scope is None:
                    try:
                        remove_history_record(self.work_dir, resolved_job_id)
                    except Exception:
                        history_cleanup_warnings.append(
                            "Docking history cleanup could not be confirmed."
                        )
                raise
            except Exception:
                history_warnings.append(
                    "History persistence failed; docking output remains "
                    "available through the returned job and pose references."
                )

            self._raise_if_cancelled(cancel_event)
            response = {
                "success": True,
                "job_id": resolved_job_id,
                "results": formatted_results,
                "best_pose": best_pose,
                "pose_file": resolved_pose_file,
                "total_poses": len(formatted_results),
                "provenance": {
                    "docking_box": box_provenance,
                    "preprocessing": preparation_provenance["preprocessing"],
                    "reproducibility": {
                        "random_seed": config.random_seed,
                        "seed_recorded": config.random_seed is not None,
                    },
                    "manifest": preparation_provenance["manifest"],
                },
                "scientific_quality": pose_geometry,
            }
            warnings = list(history_warnings)
            if box_provenance.get("warning"):
                warnings.append(box_provenance["warning"])
            if warnings:
                response["warnings"] = warnings
            return response

        except CommandCancelledError:
            if scope is not None:
                return {"success": False, "error_code": "cancelled",
                        "error": "Docking was cancelled", "warnings": defer_owned_cleanup()}
            if history_written:
                try:
                    from src.docking.history_index import remove_history_record

                    remove_history_record(self.work_dir, resolved_job_id)
                except Exception:
                    warning = "Docking history cleanup could not be confirmed."
                    if warning not in history_cleanup_warnings:
                        history_cleanup_warnings.append(warning)
            cancellation_warnings = list(history_cleanup_warnings)
            cancellation_warnings.extend(self._discard_partial_job(job_dir))
            return {
                "success": False,
                "error_code": "cancelled",
                "error": "Docking was cancelled",
                "warnings": cancellation_warnings,
            }
        except CommandOwnershipUncertainError:
            if scope is not None:
                return {"success": False, "error_code": "process_ownership_uncertain",
                        "error": "Docking process ownership could not be verified",
                        "warnings": defer_owned_cleanup()}
            self._discard_partial_job(job_dir)
            return {
                "success": False,
                "error_code": "process_ownership_uncertain",
                "error": "Docking process ownership could not be verified",
            }
        except subprocess.TimeoutExpired:
            if scope is not None:
                return {"success": False, "error_code": "timeout",
                        "error": "AutoDock Vina timed out", "warnings": defer_owned_cleanup()}
            self._discard_partial_job(job_dir)
            return {
                "success": False,
                "error_code": "timeout",
                "error": "AutoDock Vina timed out",
            }
        except _ProgressCallbackError:
            if scope is not None:
                return {"success": False, "error_code": "progress_callback_failed",
                        "error": "Docking progress reporting failed", "warnings": defer_owned_cleanup()}
            self._discard_partial_job(job_dir)
            return {
                "success": False,
                "error_code": "progress_callback_failed",
                "error": "Docking progress reporting failed",
            }
        except Exception as error:
            cleanup_warnings = (self._discard_partial_job(job_dir) if scope is None
                                else defer_owned_cleanup())
            logger.error(
                "Docking workflow failed (%s)",
                type(error).__name__,
            )
            response = {
                "success": False,
                "error_code": "docking_failed",
                "error": "Docking workflow failed",
            }
            if cleanup_warnings:
                response["warnings"] = cleanup_warnings
            return response

    def cleanup(self):
        """清理临时文件"""
        if self._command_scope is not None:
            raise CommandOwnershipUncertainError()  # C never owns the whole output root
        try:
            if os.path.exists(self.work_dir):
                shutil.rmtree(self.work_dir)
                logger.info("临时文件清理完成")
        except Exception as e:
            logger.error(f"清理临时文件失败: {e}")

# 全局服务实例
docking_service = MolecularDockingService()
