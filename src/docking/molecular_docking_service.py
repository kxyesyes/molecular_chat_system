"""
分子对接服务模块
集成AutoDock Vina进行蛋白质-配体对接计算
"""

import os
import subprocess
import logging
from typing import Dict, List, Any, Tuple, Optional
import shutil
from dataclasses import dataclass
import sys

from .adapters import ADFRAdapter, MeekoAdapter, OpenBabelAdapter, VinaAdapter

logger = logging.getLogger(__name__)

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
                               log_on_failure: bool = True) -> Tuple[bool, str]:
        """Run ligand preparation and return success flag plus CLI output."""
        input_path = os.path.abspath(input_path)
        output_path = os.path.abspath(output_path)
        job_dir = os.path.abspath(job_dir)
        cmd = self.ligand_adapter.build_prepare_command(input_path, output_path)
        logger.info(f"执行{log_label}命令: {' '.join(cmd)}")
        result = self.ligand_adapter.prepare_ligand(input_path, output_path, job_dir, timeout=60)
        combined_output = (result.stderr or "") + ("\n" if result.stderr and result.stdout else "") + (result.stdout or "")

        if result.returncode == 0 and os.path.exists(output_path):
            logger.info(f"{log_label}成功: {output_path}")
            return True, combined_output

        if log_on_failure:
            logger.error(f"{log_label}失败 (returncode={result.returncode}): {combined_output}")
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
                                              job_dir: str) -> bool:
        """Normalize ligand files through RDKit so Meeko receives explicit hydrogens."""
        try:
            from rdkit import Chem
            from rdkit.Chem import AllChem

            mol = self._load_rdkit_mol_from_file(ligand_path)
            if mol is None:
                logger.error(f"RDKit无法读取配体文件: {ligand_path}")
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
                logger.warning(f"配体 MMFF 优化失败，将继续使用当前构象: {optimize_error}")

            normalized_sdf_path = os.path.join(job_dir, "ligand_explicit_h.sdf")
            writer = Chem.SDWriter(normalized_sdf_path)
            writer.write(mol)
            writer.close()

            success, _ = self._run_prepare_ligand(
                normalized_sdf_path,
                output_path,
                job_dir,
                "配体文件准备(RDKit显式氢处理后)"
            )
            return success

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

    def prepare_protein(self, protein_path: str, output_path: str) -> bool:
        """
        准备蛋白质文件 (PDB -> PDBQT)
        """
        try:
            # 如果传入的本身是PDBQT，直接复制
            if protein_path.lower().endswith('.pdbqt'):
                shutil.copyfile(protein_path, output_path)
                logger.info(f"检测到PDBQT受体，已直接使用: {output_path}")
                return True

            # 使用 ADFRsuite 的 prepare_receptor（支持 .bat / 无扩展 / .py）
            adfr_candidates = self._prepare_receptor_candidates()
            prepare_receptor_cmd = next((p for p in adfr_candidates if os.path.exists(p)), None)

            if prepare_receptor_cmd:
                self.receptor_adapter = ADFRAdapter(prepare_receptor_cmd)
                cmd = self.receptor_adapter.build_prepare_command(protein_path, output_path)

                logger.info(f"执行蛋白质准备命令: {' '.join(cmd)}")
                result = self.receptor_adapter.prepare_receptor(protein_path, output_path, self.work_dir)

                if result.returncode == 0:
                    logger.info(f"蛋白质准备成功: {output_path}")
                    return True
                else:
                    logger.error(f"ADFRsuite 蛋白质准备失败: {result.stderr}")

            # 回退：使用简化的 PDB→PDBQT 转换（避免 OpenBabel 格式问题）
            logger.warning("尝试使用简化 PDB→PDBQT 转换 (回退方案)")
            try:
                # 读取 PDB 文件并生成简化的 PDBQT
                with open(protein_path, 'r', encoding='utf-8') as f:
                    pdb_lines = f.readlines()
                
                pdbqt_lines = []
                pdbqt_lines.append("REMARK  Generated by simplified PDB to PDBQT converter\n")
                
                for line in pdb_lines:
                    if line.startswith(('ATOM', 'HETATM')):
                        # 提取原子信息
                        atom_name = line[12:16].strip()
                        res_name = line[17:20].strip()
                        chain_id = line[21:22].strip()
                        res_num = line[22:26].strip()
                        x = float(line[30:38].strip())
                        y = float(line[38:46].strip())
                        z = float(line[46:54].strip())
                        
                        # 简化的原子类型映射
                        atom_type = "C"  # 默认碳原子
                        if atom_name.startswith('N'):
                            atom_type = "N"
                        elif atom_name.startswith('O'):
                            atom_type = "O"
                        elif atom_name.startswith('S'):
                            atom_type = "S"
                        elif atom_name.startswith('H'):
                            atom_type = "H"
                        
                        # 生成 PDBQT 格式行
                        pdbqt_line = f"ATOM  {line[6:11]}  {atom_name:<4} {res_name} {chain_id}{res_num:>4}    {x:8.3f}{y:8.3f}{z:8.3f}  1.00  0.00      {atom_type:>2}\n"
                        pdbqt_lines.append(pdbqt_line)
                
                # 写入 PDBQT 文件
                with open(output_path, 'w', encoding='utf-8') as f:
                    f.writelines(pdbqt_lines)
                    f.write("ENDMDL\n")
                
                logger.info(f"简化 PDBQT 转换成功: {output_path}")
                return True
                
            except Exception as e:
                logger.error(f"简化 PDBQT 转换失败: {e}")
                return False

        except Exception as e:
            logger.error(f"蛋白质准备异常: {e}")
            return False

    def prepare_ligand_from_smiles(self, smiles: str, output_path: str) -> bool:
        """
        从SMILES生成配体PDBQT文件
        流程：SMILES -> SDF (RDKit，在当前进程内) -> PDBQT (mk_prepare_ligand.exe)
        """
        try:
            from rdkit import Chem
            from rdkit.Chem import AllChem

            job_dir = os.path.dirname(output_path)

            # Step 1: SMILES -> 3D SDF（在当前进程内，无需子进程）
            mol = Chem.MolFromSmiles(smiles)
            if mol is None:
                logger.error(f"无效的 SMILES: {smiles}")
                return False

            mol = Chem.AddHs(mol)
            params = AllChem.ETKDGv3()
            if AllChem.EmbedMolecule(mol, params) < 0:
                # ETKDGv3 失败时回退 ETKDG
                AllChem.EmbedMolecule(mol, AllChem.ETKDGv2())
            AllChem.MMFFOptimizeMolecule(mol)

            sdf_path = os.path.join(job_dir, "ligand_input.sdf")
            writer = Chem.SDWriter(sdf_path)
            writer.write(mol)
            writer.close()

            # Step 2: SDF -> PDBQT (mk_prepare_ligand.exe)
            success, _ = self._run_prepare_ligand(sdf_path, output_path, job_dir, "配体准备")
            return success

        except Exception as e:
            logger.error(f"配体准备异常: {e}")
            return False

    def prepare_ligand_from_file(self, ligand_path: str, output_path: str) -> bool:
        """
        从文件准备配体 (SDF/MOL/PDB -> PDBQT)
        使用 mk_prepare_ligand.exe 直接转换，无需写临时 Python 脚本
        """
        try:
            src_ext = os.path.splitext(ligand_path)[1].lower()
            job_dir = os.path.dirname(output_path)

            # 若输入已经是 PDBQT，直接复制并返回
            if src_ext == '.pdbqt':
                shutil.copyfile(ligand_path, output_path)
                logger.info(f"检测到PDBQT配体，已直接使用: {output_path}")
                return True

            success, error_output = self._run_prepare_ligand(
                ligand_path,
                output_path,
                job_dir,
                "配体文件准备",
                log_on_failure=False
            )
            if success:
                return True

            # Meeko 对部分文件要求显式氢，这里在失败后做一次 RDKit 归一化再重试
            if "implicit Hs" in error_output:
                logger.warning("检测到配体含隐式氢，将先用 RDKit 补显式氢后再次尝试")
                return self._prepare_ligand_file_with_explicit_hs(ligand_path, output_path, job_dir)

            logger.error(f"配体文件准备失败: {error_output}")
            return False

        except Exception as e:
            logger.error(f"配体文件准备异常: {e}")
            return False

    def run_vina_docking(self, receptor_path: str, ligand_path: str,
                        config: DockingConfig, output_path: str) -> bool:
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

            # 创建配置文件
            config_path = os.path.join(self.work_dir, "config.txt")
            with open(config_path, 'w') as f:
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
            cmd = self.vina_adapter.build_run_command(config_path)
            result = self.vina_adapter.run_config(config_path, self.work_dir)

            if result.returncode == 0:
                logger.info(f"Vina对接计算成功: {output_path}")
                return True
            else:
                logger.error(f"Vina对接计算失败: {result.stderr}")
                return False

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
                logger.error(f"结果文件不存在: {output_path}")
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

    async def perform_docking(self, receptor_file: str, ligand_input: str,
                            config: DockingConfig, input_type: str = "smiles") -> Dict[str, Any]:
        """
        执行完整的分子对接流程

        Args:
            receptor_file: 蛋白质文件路径
            ligand_input: 配体输入（SMILES字符串或文件路径）
            config: 对接配置参数
            input_type: 输入类型 ("smiles" 或 "file")

        Returns:
            对接结果字典
        """

        # 生成唯一的工作子目录
        import uuid
        job_id = str(uuid.uuid4())[:8]
        job_dir = os.path.join(self.work_dir, f"docking_{job_id}")
        os.makedirs(job_dir, exist_ok=True)

        try:
            def _auto_box_from_protein_ligand(pdb_path: str) -> "Optional[Tuple[float, float, float, float, float, float]]":
                min_x = min_y = min_z = float('inf')
                max_x = max_y = max_z = float('-inf')
                water_resn = {"HOH", "WAT", "H2O"}
                ion_resn = {"NA", "CL", "K", "CA", "MG", "ZN", "MN", "FE", "CU", "CO", "NI", "BR", "I"}
                found = 0
                try:
                    with open(pdb_path, 'r', encoding='utf-8', errors='ignore') as f:
                        for line in f:
                            if not line.startswith('HETATM'):
                                continue
                            resn = (line[17:20].strip() or "").upper()
                            if not resn or resn in water_resn or resn in ion_resn:
                                continue
                            try:
                                x = float(line[30:38])
                                y = float(line[38:46])
                                z = float(line[46:54])
                            except Exception:
                                parts = line.split()
                                if len(parts) >= 9:
                                    x = float(parts[6]); y = float(parts[7]); z = float(parts[8])
                                else:
                                    continue
                            found += 1
                            min_x = min(min_x, x); max_x = max(max_x, x)
                            min_y = min(min_y, y); max_y = max(max_y, y)
                            min_z = min(min_z, z); max_z = max(max_z, z)
                    if found == 0 or any(v in (float('inf'), float('-inf')) for v in (min_x, max_x, min_y, max_y, min_z, max_z)):
                        return None
                    cx = (min_x + max_x) / 2.0
                    cy = (min_y + max_y) / 2.0
                    cz = (min_z + max_z) / 2.0
                    padding = 8.0
                    sx = max(10.0, (max_x - min_x) + padding)
                    sy = max(10.0, (max_y - min_y) + padding)
                    sz = max(10.0, (max_z - min_z) + padding)
                    return (cx, cy, cz, sx, sy, sz)
                except Exception:
                    return None

            if (
                not config.manual_center
                and abs(config.center_x) < 1e-6 and abs(config.center_y) < 1e-6 and abs(config.center_z) < 1e-6
                and abs(config.size_x - 20.0) < 1e-6 and abs(config.size_y - 20.0) < 1e-6 and abs(config.size_z - 20.0) < 1e-6
            ):
                auto_box = _auto_box_from_protein_ligand(receptor_file)
                if auto_box is not None:
                    config.center_x, config.center_y, config.center_z, config.size_x, config.size_y, config.size_z = auto_box

            # 1. 准备蛋白质
            receptor_pdbqt = os.path.join(job_dir, "receptor.pdbqt")
            if not self.prepare_protein(receptor_file, receptor_pdbqt):
                return {"success": False, "error": "蛋白质准备失败"}

            # 2. 准备配体
            ligand_pdbqt = os.path.join(job_dir, "ligand.pdbqt")
            if input_type == "smiles":
                if not self.prepare_ligand_from_smiles(ligand_input, ligand_pdbqt):
                    return {"success": False, "error": "配体准备失败"}
            else:
                if not self.prepare_ligand_from_file(ligand_input, ligand_pdbqt):
                    return {"success": False, "error": "配体文件准备失败"}

            # 3. 运行对接计算
            output_pdbqt = os.path.join(job_dir, "result.pdbqt")
            if not self.run_vina_docking(receptor_pdbqt, ligand_pdbqt, config, output_pdbqt):
                return {"success": False, "error": "对接计算失败"}

            # 4. 解析结果
            results = self.parse_vina_results(output_pdbqt)

            if not results:
                return {"success": False, "error": "结果解析失败"}

            # 5. 格式化返回结果
            # 计算配体重原子数（用于配体效率 LE = ΔG / N_heavy）
            heavy_atom_count = 0
            try:
                if input_type == "smiles":
                    from rdkit import Chem as _Chem
                    _mol = _Chem.MolFromSmiles(ligand_input)
                    if _mol:
                        heavy_atom_count = _mol.GetNumHeavyAtoms()
                else:
                    # 从 PDBQT 文件统计非氢 ATOM/HETATM 行
                    with open(ligand_pdbqt, 'r', errors='ignore') as _f:
                        heavy_atom_count = sum(
                            1 for ln in _f
                            if (ln.startswith('ATOM') or ln.startswith('HETATM'))
                            and not ln[12:16].strip().startswith('H')
                        )
            except Exception:
                pass
            if heavy_atom_count < 1:
                heavy_atom_count = 1  # 防止除零

            formatted_results = []
            for i, result in enumerate(results, 1):
                formatted_results.append({
                    "pose": i,
                    "binding_energy": result.binding_energy,
                    "rmsd_lb": result.rmsd_lb,
                    "rmsd_ub": result.rmsd_ub,
                    "ligand_efficiency": round(result.binding_energy / heavy_atom_count, 3)
                })

            return {
                "success": True,
                "job_id": job_id,
                "results": formatted_results,
                "best_pose": formatted_results[0] if formatted_results else None,
                "total_poses": len(formatted_results)
            }

        except Exception as e:
            logger.error(f"分子对接流程异常: {e}")
            return {"success": False, "error": str(e)}

        finally:
            # 可选：清理临时文件
            # shutil.rmtree(job_dir, ignore_errors=True)
            pass

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
