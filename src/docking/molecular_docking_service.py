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
from typing import Dict, List, Any, Tuple, Optional
import shutil
from dataclasses import dataclass
from pathlib import Path
import sys

from .adapters import ADFRAdapter, MeekoAdapter, OpenBabelAdapter, VinaAdapter
from .adapters.base import (
    CommandAdapter,
    CommandCancelledError,
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

class MolecularDockingService:
    """分子对接服务类"""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        # ===== 软件路径配置（根据实际安装位置修改）=====
        # AutoDock Vina 可执行文件
        self.vina_exe = ""
        # ADFRsuite bin 目录（用于蛋白准备）
        self.adfrsuite_path = ""
        # 可直接指定 prepare_receptor 命令；优先于 ADFRsuite bin 自动推断
        self.prepare_receptor_cmd = ""
        # Meeko 命令行工具（用于配体准备，来自 Anaconda 环境）
        self.prepare_ligand_cmd = ""
        # 保留兼容字段
        self.python_exe = sys.executable

        # 创建临时工作目录
        self.work_dir = os.path.abspath(os.path.join(os.getcwd(), "temp_docking"))
        os.makedirs(self.work_dir, exist_ok=True)
        self.vina_adapter = VinaAdapter()
        self.receptor_adapter = ADFRAdapter()
        self.ligand_adapter = MeekoAdapter()
        self.openbabel_adapter = OpenBabelAdapter()
        self.configure(config)

        logger.info(f"分子对接服务初始化完成，工作目录: {self.work_dir}")

    def _refresh_adapters(self) -> None:
        self.vina_adapter = VinaAdapter(self.vina_exe)
        self.receptor_adapter = ADFRAdapter(self.prepare_receptor_cmd)
        self.ligand_adapter = MeekoAdapter(self.prepare_ligand_cmd)
        self.openbabel_adapter = OpenBabelAdapter(shutil.which("obabel") or "")

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
        self.prepare_ligand_cmd = self._first_existing_path(
            docking_config.get("prepare_ligand_cmd"),
            os.environ.get("MOLECULAR_DOCKING_PREPARE_LIGAND"),
            os.path.join(env_scripts_dir, "mk_prepare_ligand.exe"),
            os.path.join(env_scripts_dir, "mk_prepare_ligand.py"),
            os.path.join(self.adfrsuite_path, "prepare_ligand.bat") if self.adfrsuite_path else None,
            prepare_ligand_in_path,
        )
        self._refresh_adapters()

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
        input_path = os.path.abspath(input_path)
        output_path = os.path.abspath(output_path)
        job_dir = os.path.abspath(job_dir)
        logger.info("Executing %s command", log_label)
        adapter_control = {}
        if cancel_event is not None:
            adapter_control["cancel_event"] = cancel_event
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

    def _prepare_ligand_file_with_explicit_hs(self, ligand_path: str, output_path: str,
                                              job_dir: str, *, cancel_event=None) -> bool:
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
                params = AllChem.ETKDGv3()
                if AllChem.EmbedMolecule(mol, params) < 0:
                    if AllChem.EmbedMolecule(mol, AllChem.ETKDGv2()) < 0:
                        logger.error("RDKit无法为配体生成 3D 构象")
                        return False

            try:
                AllChem.MMFFOptimizeMolecule(mol)
            except Exception as optimize_error:
                logger.warning(
                    "Ligand MMFF optimization failed (%s); using current conformer",
                    type(optimize_error).__name__,
                )

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
    ) -> bool:
        """
        准备蛋白质文件 (PDB -> PDBQT)
        """
        try:
            self._raise_if_cancelled(cancel_event)
            # 如果传入的本身是PDBQT，直接复制
            if protein_path.lower().endswith('.pdbqt'):
                self._copy_file_cooperatively(
                    protein_path,
                    output_path,
                    cancel_event,
                )
                logger.info("Using receptor input already in PDBQT format")
                return True

            # 使用 ADFRsuite 的 prepare_receptor（支持 .bat / 无扩展 / .py）
            adfr_candidates = self._prepare_receptor_candidates()
            prepare_receptor_cmd = next((p for p in adfr_candidates if os.path.exists(p)), None)

            if prepare_receptor_cmd:
                self.receptor_adapter = ADFRAdapter(prepare_receptor_cmd)
                logger.info("Executing receptor preparation command")
                adapter_control = {}
                if cancel_event is not None:
                    adapter_control["cancel_event"] = cancel_event
                result = self.receptor_adapter.prepare_receptor(
                    protein_path,
                    output_path,
                    self.work_dir,
                    **adapter_control,
                )

                if result.returncode == 0:
                    logger.info("Receptor preparation succeeded")
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

    def prepare_ligand_from_smiles(
        self,
        smiles: str,
        output_path: str,
        *,
        cancel_event=None,
    ) -> bool:
        """
        从SMILES生成配体PDBQT文件
        流程：SMILES -> SDF (RDKit，在当前进程内) -> PDBQT (mk_prepare_ligand.exe)
        """
        try:
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
            params = AllChem.ETKDGv3()
            if AllChem.EmbedMolecule(mol, params) < 0:
                # ETKDGv3 失败时回退 ETKDG
                self._raise_if_cancelled(cancel_event)
                AllChem.EmbedMolecule(mol, AllChem.ETKDGv2())
            self._raise_if_cancelled(cancel_event)
            AllChem.MMFFOptimizeMolecule(mol)
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
    ) -> bool:
        """
        从文件准备配体 (SDF/MOL/PDB -> PDBQT)
        使用 mk_prepare_ligand.exe 直接转换，无需写临时 Python 脚本
        """
        try:
            self._raise_if_cancelled(cancel_event)
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
                return True

            # Meeko 对部分文件要求显式氢，这里在失败后做一次 RDKit 归一化再重试
            if "implicit Hs" in error_output:
                logger.warning("检测到配体含隐式氢，将先用 RDKit 补显式氢后再次尝试")
                return self._prepare_ligand_file_with_explicit_hs(
                    ligand_path,
                    output_path,
                    job_dir,
                    cancel_event=cancel_event,
                )

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
            # 如果未提供中心坐标（默认为0,0,0），则自动从受体估算网格框
            def _auto_box_from_receptor(pdbqt_path: str) -> Tuple[float, float, float, float, float, float]:
                min_x = min_y = min_z = float('inf')
                max_x = max_y = max_z = float('-inf')
                try:
                    with open(pdbqt_path, 'r', encoding='utf-8', errors='ignore') as rf:
                        for line in rf:
                            if line.startswith(('ATOM', 'HETATM')):
                                try:
                                    x = float(line[30:38])
                                    y = float(line[38:46])
                                    z = float(line[46:54])
                                except Exception:
                                    # 宽松解析：按空白拆分尝试
                                    parts = line.split()
                                    if len(parts) >= 9:
                                        x = float(parts[6]); y = float(parts[7]); z = float(parts[8])
                                    else:
                                        continue
                                min_x = min(min_x, x); max_x = max(max_x, x)
                                min_y = min(min_y, y); max_y = max(max_y, y)
                                min_z = min(min_z, z); max_z = max(max_z, z)
                    if any(v in (float('inf'), float('-inf')) for v in (min_x, max_x, min_y, max_y, min_z, max_z)):
                        # 解析失败时回退原默认
                        return (0.0, 0.0, 0.0, config.size_x, config.size_y, config.size_z)
                    cx = (min_x + max_x) / 2.0
                    cy = (min_y + max_y) / 2.0
                    cz = (min_z + max_z) / 2.0
                    # 尺寸：边界加适度边距
                    padding = 8.0
                    sx = max(10.0, (max_x - min_x) + padding)
                    sy = max(10.0, (max_y - min_y) + padding)
                    sz = max(10.0, (max_z - min_z) + padding)
                    return (cx, cy, cz, sx, sy, sz)
                except Exception:
                    return (0.0, 0.0, 0.0, config.size_x, config.size_y, config.size_z)

            if (
                not config.manual_center
                and abs(config.center_x) < 1e-6
                and abs(config.center_y) < 1e-6
                and abs(config.center_z) < 1e-6
            ):
                cx, cy, cz, sx, sy, sz = _auto_box_from_receptor(receptor_path)
                config.center_x = cx
                config.center_y = cy
                config.center_z = cz
                # 仅当用户未自定义尺寸（保持默认20）时，才替换为自动尺寸
                if (abs(config.size_x - 20.0) < 1e-6 and abs(config.size_y - 20.0) < 1e-6 and abs(config.size_z - 20.0) < 1e-6):
                    config.size_x = sx
                    config.size_y = sy
                    config.size_z = sz

            # 每个作业拥有独立配置文件和进程工作目录。
            resolved_job_dir = os.path.abspath(
                job_dir or os.path.dirname(os.path.abspath(output_path))
            )
            os.makedirs(resolved_job_dir, exist_ok=True)
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

            # 运行Vina
            adapter_control = {}
            if cancel_event is not None:
                adapter_control["cancel_event"] = cancel_event
            result = self.vina_adapter.run_config(
                config_path,
                resolved_job_dir,
                timeout=self.vina_timeout_seconds,
                **adapter_control,
            )

            if result.returncode == 0:
                logger.info("Vina docking command succeeded")
                return True
            else:
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

    def parse_vina_results(self, output_path: str) -> List[DockingResult]:
        """
        解析Vina输出结果
        """
        results = []

        try:
            if not os.path.exists(output_path):
                logger.error("Vina result file is missing")
                return results

            with open(output_path, 'r') as f:
                content = f.read()

            # 解析结果（更稳健正则解析，兼容不同空白/制表）
            import re
            lines = content.split('\n')
            current_pose = 1

            for line in lines:
                if line.startswith('REMARK VINA RESULT:'):
                    # 典型: REMARK VINA RESULT:    -8.2      0.000      0.000
                    m = re.search(r"REMARK\s+VINA\s+RESULT:\s*([\-+]?\d*\.?\d+(?:[eE][\-+]?\d+)?)\s+([\-+]?\d*\.?\d+(?:[eE][\-+]?\d+)?)\s+([\-+]?\d*\.?\d+(?:[eE][\-+]?\d+)?)", line)
                    if m:
                        energy = float(m.group(1))
                        rmsd_lb = float(m.group(2))
                        rmsd_ub = float(m.group(3))

                        result = DockingResult(
                            ligand_id=f"pose_{current_pose}",
                            binding_energy=energy,
                            rmsd_lb=rmsd_lb,
                            rmsd_ub=rmsd_ub,
                            pose_data=""  # 可以扩展为包含具体坐标
                        )
                        results.append(result)
                        current_pose += 1

            logger.info(f"解析到 {len(results)} 个对接结果")
            return results

        except Exception as e:
            logger.error(f"结果解析异常: {e}")
            return results

    @classmethod
    def _auto_box_from_co_crystal(cls, pdb_path: str, cancel_event=None):
        minimum = [float("inf"), float("inf"), float("inf")]
        maximum = [float("-inf"), float("-inf"), float("-inf")]
        excluded_residues = {
            "HOH", "WAT", "H2O", "NA", "CL", "K", "CA", "MG", "ZN",
            "MN", "FE", "CU", "CO", "NI", "BR", "I",
        }
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
    def _cancel_requested(cancel_event) -> bool:
        return bool(cancel_event is not None and cancel_event.is_set())

    @classmethod
    def _copy_file_cooperatively(
        cls,
        source: str,
        destination: str,
        cancel_event,
    ) -> None:
        with open(source, "rb") as reader, open(destination, "wb") as writer:
            while True:
                cls._raise_if_cancelled(cancel_event)
                chunk = reader.read(1024 * 1024)
                if not chunk:
                    break
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
    ) -> Dict[str, Any]:
        response: Dict[str, Any] = {
            "success": False,
            "error_code": error_code,
            "error": error,
        }
        warnings = cls._discard_partial_job(job_dir)
        if warnings:
            response["warnings"] = warnings
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
    ) -> Dict[str, Any]:
        """Run a traceable docking workflow with cooperative cancellation."""
        import uuid

        resolved_job_id = str(uuid.uuid4()) if job_id is None else job_id
        try:
            CommandAdapter._validate_cancel_event(cancel_event)
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

        try:
            history_written = False
            history_cleanup_warnings: List[str] = []
            receptor_pdbqt = os.path.join(job_dir, "receptor.pdbqt")
            ligand_pdbqt = os.path.join(job_dir, "ligand.pdbqt")
            output_pdbqt = os.path.join(job_dir, "result.pdbqt")
            command_control = {}
            if cancel_event is not None:
                command_control["cancel_event"] = cancel_event

            self._emit_phase(*_DOCKING_PHASES[0], progress_callback, cancel_event)
            if (
                not config.manual_center
                and abs(config.center_x) < 1e-6
                and abs(config.center_y) < 1e-6
                and abs(config.center_z) < 1e-6
                and abs(config.size_x - 20.0) < 1e-6
                and abs(config.size_y - 20.0) < 1e-6
                and abs(config.size_z - 20.0) < 1e-6
            ):
                auto_box = self._auto_box_from_co_crystal(
                    receptor_file,
                    cancel_event,
                )
                if auto_box is not None:
                    (
                        config.center_x,
                        config.center_y,
                        config.center_z,
                        config.size_x,
                        config.size_y,
                        config.size_z,
                    ) = auto_box
            if not self.prepare_protein(
                receptor_file,
                receptor_pdbqt,
                **command_control,
            ):
                return self._failed_job_response(
                    job_dir,
                    "receptor_preparation_failed",
                    "Receptor preparation failed",
                )
            self._raise_if_cancelled(cancel_event)

            self._emit_phase(*_DOCKING_PHASES[1], progress_callback, cancel_event)
            if input_type == "smiles":
                ligand_ready = self.prepare_ligand_from_smiles(
                    ligand_input,
                    ligand_pdbqt,
                    **command_control,
                )
            else:
                ligand_ready = self.prepare_ligand_from_file(
                    ligand_input,
                    ligand_pdbqt,
                    **command_control,
                )
            if not ligand_ready:
                return self._failed_job_response(
                    job_dir,
                    "ligand_preparation_failed",
                    "Ligand preparation failed",
                )
            self._raise_if_cancelled(cancel_event)

            self._emit_phase(*_DOCKING_PHASES[2], progress_callback, cancel_event)
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
                )

            self._emit_phase(*_DOCKING_PHASES[3], progress_callback, cancel_event)
            results = self.parse_vina_results(output_pdbqt)
            self._raise_if_cancelled(cancel_event)
            if not results:
                return self._failed_job_response(
                    job_dir,
                    "scientific_validation_failed",
                    "Docking result validation failed",
                )

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
            heavy_atom_count = max(1, heavy_atom_count)

            formatted_results = [
                {
                    "pose": index,
                    "binding_energy": item.binding_energy,
                    "rmsd_lb": item.rmsd_lb,
                    "rmsd_ub": item.rmsd_ub,
                    "ligand_efficiency": round(
                        item.binding_energy / heavy_atom_count,
                        3,
                    ),
                }
                for index, item in enumerate(results, 1)
            ]
            self._raise_if_cancelled(cancel_event)

            resolved_pose_file = str(Path(output_pdbqt).resolve())
            best_pose = dict(formatted_results[0])
            best_pose["pose_file"] = resolved_pose_file
            history_warnings = []
            try:
                from src.docking.history_index import (
                    build_history_record,
                    remove_history_record,
                    upsert_history_record,
                )

                self._raise_if_cancelled(cancel_event)
                upsert_history_record(
                    self.work_dir,
                    build_history_record(
                        job_dir,
                        job_id=resolved_job_id,
                        status="completed",
                    ),
                )
                history_written = True
                if self._cancel_requested(cancel_event):
                    remove_history_record(self.work_dir, resolved_job_id)
                    history_written = False
                    raise CommandCancelledError()
            except CommandCancelledError:
                if history_written:
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
            }
            if history_warnings:
                response["warnings"] = history_warnings
            return response

        except CommandCancelledError:
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
            self._discard_partial_job(job_dir)
            return {
                "success": False,
                "error_code": "process_ownership_uncertain",
                "error": "Docking process ownership could not be verified",
            }
        except subprocess.TimeoutExpired:
            self._discard_partial_job(job_dir)
            return {
                "success": False,
                "error_code": "timeout",
                "error": "AutoDock Vina timed out",
            }
        except _ProgressCallbackError:
            self._discard_partial_job(job_dir)
            return {
                "success": False,
                "error_code": "progress_callback_failed",
                "error": "Docking progress reporting failed",
            }
        except Exception as error:
            cleanup_warnings = self._discard_partial_job(job_dir)
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
        try:
            if os.path.exists(self.work_dir):
                shutil.rmtree(self.work_dir)
                logger.info("临时文件清理完成")
        except Exception as e:
            logger.error(f"清理临时文件失败: {e}")

# 全局服务实例
docking_service = MolecularDockingService()
