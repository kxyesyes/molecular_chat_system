"""
API 路由模块 - 分子对接和反向寻靶
"""
import os
import logging
import tempfile
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any, List, Optional
from fastapi import UploadFile, File, Form, HTTPException, Response, Body

logger = logging.getLogger(__name__)

_PHARM3D_EXECUTOR = ThreadPoolExecutor(
    max_workers=max(1, int(os.getenv("REVERSE_TARGET_PHARM3D_WORKERS", "2")))
)


def _get_pharm3d_timeout(default: float = 25.0) -> float:
    try:
        return max(0.1, float(os.getenv("REVERSE_TARGET_PHARM3D_TIMEOUT_SECONDS", str(default))))
    except ValueError:
        return default


def _get_int_env(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def _get_pharm3d_candidate_pool_limit(top_k: int, max_refine: int) -> int:
    multiplier = _get_int_env("REVERSE_TARGET_PHARM3D_POOL_MULTIPLIER", 20, minimum=1)
    min_pool = _get_int_env("REVERSE_TARGET_PHARM3D_MIN_CANDIDATE_POOL", 250, minimum=1)
    max_pool = _get_int_env("REVERSE_TARGET_PHARM3D_MAX_CANDIDATE_POOL", 5000, minimum=1)
    desired = max(top_k * multiplier, max_refine, min_pool)
    return min(desired, max_pool)


async def _run_pharm3d_job(func, timeout_seconds: Optional[float] = None):
    loop = asyncio.get_running_loop()
    timeout = _get_pharm3d_timeout() if timeout_seconds is None else timeout_seconds
    return await asyncio.wait_for(
        loop.run_in_executor(_PHARM3D_EXECUTOR, func),
        timeout=timeout,
    )


def _build_pharm3d_fallback(candidates: List[Dict[str, Any]], error: str = "") -> List[Dict[str, Any]]:
    fallback = []
    for cand in candidates:
        row = dict(cand)
        score_2d = row.get("final_similarity", 0.0)
        row["final_3d_score"] = score_2d
        row["pharm_combined_3d"] = None
        row["pharm_similarity"] = None
        row["alignment_score"] = None
        row["spatial_score"] = None
        row["pharm_features"] = []
        if error:
            row["pharm_error"] = error
        fallback.append(row)
    return fallback


def setup_api_routes(app, docking_service=None):
    """设置 API 路由"""

    # ==================== 分子对接 API ====================
    
    @app.post("/api/docking/submit")
    async def submit_docking_job(
        protein_file: UploadFile = File(...),
        ligand_file: UploadFile = File(None),
        smiles: str = Form(None),
        center_x: float = Form(0.0),
        center_y: float = Form(0.0),
        center_z: float = Form(0.0),
        size_x: float = Form(20.0),
        size_y: float = Form(20.0),
        size_z: float = Form(20.0),
        exhaustiveness: int = Form(8),
        num_modes: int = Form(10),
        energy_range: float = Form(3.0),
        manual_center: bool = Form(False)
    ):
        """提交分子对接任务"""
        if not docking_service:
            raise HTTPException(status_code=503, detail="分子对接服务不可用")

        try:
            if not ligand_file and not smiles:
                raise HTTPException(status_code=400, detail="请提供配体文件或SMILES字符串")

            # 保存蛋白质文件（保留上传后缀，避免 .pdbqt 被误当成 .pdb）
            protein_name = (protein_file.filename or "protein").lower()
            protein_name = (protein_file.filename or "protein").lower()
            _, protein_ext = os.path.splitext(protein_name)
            if protein_ext not in {".pdb", ".pdbqt"}:
                protein_ext = ".pdb"
            protein_temp = tempfile.NamedTemporaryFile(delete=False, suffix=protein_ext)
            protein_content = await protein_file.read()
            protein_temp.write(protein_content)
            protein_temp.close()

            # 准备对接配置
            from src.docking import DockingConfig
            config = DockingConfig(
                center_x=center_x, center_y=center_y, center_z=center_z,
                size_x=size_x, size_y=size_y, size_z=size_z,
                exhaustiveness=exhaustiveness,
                num_modes=num_modes,
                energy_range=energy_range,
                manual_center=manual_center
            )

            # 执行对接
            ligand_temp_path = None
            if smiles:
                result = await docking_service.perform_docking(
                    receptor_file=protein_temp.name,
                    ligand_input=smiles,
                    config=config,
                    input_type="smiles"
                )
            else:
                original_name = ligand_file.filename or "ligand"
                _, ext = os.path.splitext(original_name)
                ext = ext if ext else ".sdf"

                ligand_temp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
                ligand_content = await ligand_file.read()
                ligand_temp.write(ligand_content)
                ligand_temp.close()
                ligand_temp_path = ligand_temp.name
                
                result = await docking_service.perform_docking(
                    receptor_file=protein_temp.name,
                    ligand_input=ligand_temp_path,
                    config=config,
                    input_type="file"
                )

            # 清理临时文件
            os.unlink(protein_temp.name)
            if ligand_temp_path and os.path.exists(ligand_temp_path):
                os.unlink(ligand_temp_path)

            return result

        except Exception as e:
            logger.error(f"分子对接任务提交失败: {e}")
            raise HTTPException(status_code=500, detail=f"对接计算失败: {str(e)}")

    @app.get("/api/docking/env_check")
    async def docking_env_check():
        """返回分子对接环境详细诊断"""
        try:
            if not docking_service:
                raise HTTPException(status_code=503, detail="分子对接服务不可用")
            info = docking_service.env_diagnostics()
            return {"success": info.get("ok", False), "diagnostics": info}
        except Exception as e:
            logger.error(f"环境自检失败: {e}")
            raise HTTPException(status_code=500, detail=f"环境自检失败: {str(e)}")

    @app.post("/api/docking/batch_submit")
    async def submit_batch_docking_job(
        protein_file: UploadFile = File(...),
        ligand_files: Optional[List[UploadFile]] = File(None),
        batch_smiles: str = Form(""),
        center_x: float = Form(0.0),
        center_y: float = Form(0.0),
        center_z: float = Form(0.0),
        size_x: float = Form(20.0),
        size_y: float = Form(20.0),
        size_z: float = Form(20.0),
        exhaustiveness: int = Form(8),
        num_modes: int = Form(10),
        energy_range: float = Form(3.0),
        manual_center: bool = Form(False)
    ):
        """Submit one receptor against multiple ligand files and/or SMILES rows."""
        if not docking_service:
            raise HTTPException(status_code=503, detail="分子对接服务不可用")

        import copy
        import uuid

        ligand_files = ligand_files or []
        smiles_rows = [
            line.strip()
            for line in (batch_smiles or "").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

        if not ligand_files and not smiles_rows:
            raise HTTPException(status_code=400, detail="请提供至少一个配体文件或一行 SMILES")

        batch_id = str(uuid.uuid4())[:8]
        temp_paths: List[str] = []

        try:
            protein_name = (protein_file.filename or "protein").lower()
            _, protein_ext = os.path.splitext(protein_name)
            if protein_ext not in {".pdb", ".pdbqt"}:
                protein_ext = ".pdb"
            protein_temp = tempfile.NamedTemporaryFile(delete=False, suffix=protein_ext)
            temp_paths.append(protein_temp.name)
            protein_temp.write(await protein_file.read())
            protein_temp.close()

            from src.docking import DockingConfig
            base_config = DockingConfig(
                center_x=center_x, center_y=center_y, center_z=center_z,
                size_x=size_x, size_y=size_y, size_z=size_z,
                exhaustiveness=exhaustiveness,
                num_modes=num_modes,
                energy_range=energy_range,
                manual_center=manual_center
            )

            jobs = []
            for index, ligand_file in enumerate(ligand_files, 1):
                original_name = ligand_file.filename or f"ligand_{index}"
                _, ext = os.path.splitext(original_name)
                ligand_temp = tempfile.NamedTemporaryFile(delete=False, suffix=ext or ".sdf")
                temp_paths.append(ligand_temp.name)
                ligand_temp.write(await ligand_file.read())
                ligand_temp.close()
                jobs.append({
                    "ligand_name": original_name,
                    "input_type": "file",
                    "ligand_input": ligand_temp.name,
                })

            for index, row in enumerate(smiles_rows, 1):
                if "," in row:
                    parts = [part.strip() for part in row.split(",") if part.strip()]
                else:
                    parts = row.split()
                if not parts or parts[0].lower() in {"smiles", "smile"}:
                    continue
                smiles_value = parts[0]
                ligand_name = parts[1] if len(parts) > 1 else f"SMILES_{index}"
                jobs.append({
                    "ligand_name": ligand_name,
                    "input_type": "smiles",
                    "ligand_input": smiles_value,
                })

            if not jobs:
                raise HTTPException(status_code=400, detail="没有解析到有效的批量配体输入")

            batch_results = []
            for index, job in enumerate(jobs, 1):
                config = copy.deepcopy(base_config)
                try:
                    result = await docking_service.perform_docking(
                        receptor_file=protein_temp.name,
                        ligand_input=job["ligand_input"],
                        config=config,
                        input_type=job["input_type"],
                    )
                    best_pose = result.get("best_pose") if result.get("success") else None
                    batch_results.append({
                        "index": index,
                        "ligand_name": job["ligand_name"],
                        "input_type": job["input_type"],
                        "success": bool(result.get("success")),
                        "job_id": result.get("job_id"),
                        "best_pose": best_pose,
                        "best_energy": best_pose.get("binding_energy") if best_pose else None,
                        "total_poses": result.get("total_poses", 0),
                        "error": result.get("error"),
                    })
                except Exception as item_error:
                    logger.error(f"批量对接子任务失败 ({job['ligand_name']}): {item_error}")
                    batch_results.append({
                        "index": index,
                        "ligand_name": job["ligand_name"],
                        "input_type": job["input_type"],
                        "success": False,
                        "job_id": None,
                        "best_pose": None,
                        "best_energy": None,
                        "total_poses": 0,
                        "error": str(item_error),
                    })

            completed = sum(1 for item in batch_results if item["success"])
            return {
                "success": completed > 0,
                "batch_job_id": batch_id,
                "total": len(batch_results),
                "completed": completed,
                "failed": len(batch_results) - completed,
                "results": batch_results,
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"批量分子对接任务提交失败: {e}")
            raise HTTPException(status_code=500, detail=f"批量对接失败: {str(e)}")
        finally:
            for path in temp_paths:
                try:
                    if os.path.exists(path):
                        os.unlink(path)
                except Exception:
                    pass

    @app.get("/api/docking/status")
    async def get_docking_status():
        """获取分子对接服务状态"""
        if not docking_service:
            return {"status": "unavailable", "message": "分子对接服务未初始化"}

        try:
            env_ok = docking_service.verify_environment()
            return {
                "status": "available" if env_ok else "error",
                "environment_check": env_ok,
                "message": "服务正常" if env_ok else "环境配置有问题"
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @app.get("/api/docking/result/{job_id}")
    async def get_docking_result(job_id: str):
        """获取分子对接结果文件"""
        if not docking_service:
            raise HTTPException(status_code=503, detail="分子对接服务不可用")

        try:
            job_dir = os.path.join(docking_service.work_dir, f"docking_{job_id}")
            result_file = os.path.join(job_dir, "result.pdbqt")
            
            if not os.path.exists(result_file):
                raise HTTPException(status_code=404, detail="对接结果文件不存在")

            with open(result_file, 'r', encoding='utf-8') as f:
                content = f.read()

            return Response(
                content=content,
                media_type="text/plain",
                headers={"Content-Disposition": f"attachment; filename=docking_result_{job_id}.pdbqt"}
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"获取对接结果失败: {e}")
            raise HTTPException(status_code=500, detail=f"获取结果失败: {str(e)}")

    def _extract_pose_atom_lines(pdbqt_text: str, pose_index: int = 1):
        lines = pdbqt_text.splitlines()
        current = 0
        collecting = False
        atom_lines = []

        for line in lines:
            if line.startswith("MODEL"):
                current += 1
                collecting = current == pose_index
                continue
            if line.startswith("ENDMDL"):
                if collecting:
                    break
                collecting = False
                continue
            if collecting and line.startswith(("ATOM", "HETATM")):
                atom_lines.append(line)

        if not atom_lines:
            atom_lines = [line for line in lines if line.startswith(("ATOM", "HETATM"))]

        return atom_lines

    def _extract_remark_smiles(pdbqt_text: str) -> str:
        for line in pdbqt_text.splitlines():
            if line.startswith("REMARK SMILES ") and not line.startswith("REMARK SMILES IDX"):
                return line.split("REMARK SMILES ", 1)[1].strip()
        return ""

    def _extract_remark_pairs(pdbqt_text: str, prefix: str):
        import re

        nums = []
        for line in pdbqt_text.splitlines():
            if line.startswith(prefix):
                tail = line[len(prefix):].strip()
                nums.extend(int(x) for x in re.findall(r"\d+", tail))

        pairs = []
        for i in range(0, len(nums) - 1, 2):
            pairs.append((nums[i], nums[i + 1]))
        return pairs

    @app.get("/api/docking/pose_sdf/{job_id}")
    async def get_docking_pose_sdf(job_id: str, pose: int = 1):
        """将指定 pose 重建为标准 SDF，保留原始化学拓扑并应用对接坐标"""
        if not docking_service:
            raise HTTPException(status_code=503, detail="分子对接服务不可用")

        try:
            job_dir = os.path.join(docking_service.work_dir, f"docking_{job_id}")
            result_file = os.path.join(job_dir, "result.pdbqt")

            if not os.path.exists(result_file):
                raise HTTPException(status_code=404, detail="对接结果文件不存在")

            with open(result_file, "r", encoding="utf-8", errors="ignore") as f:
                pdbqt_text = f.read()

            smiles = _extract_remark_smiles(pdbqt_text)
            if not smiles:
                raise HTTPException(status_code=400, detail="结果文件缺少 SMILES 注释，无法重建标准配体结构")

            smiles_idx_pairs = _extract_remark_pairs(pdbqt_text, "REMARK SMILES IDX")
            if not smiles_idx_pairs:
                raise HTTPException(status_code=400, detail="结果文件缺少 SMILES IDX 映射，无法重建标准配体结构")

            h_parent_pairs = _extract_remark_pairs(pdbqt_text, "REMARK H PARENT")
            atom_lines = _extract_pose_atom_lines(pdbqt_text, pose)
            if not atom_lines:
                raise HTTPException(status_code=404, detail=f"未找到 Pose {pose} 的坐标")

            from rdkit import Chem
            from rdkit.Chem import AllChem
            from rdkit.Geometry import Point3D

            mol = Chem.MolFromSmiles(smiles)
            if mol is None:
                raise HTTPException(status_code=400, detail="无法从结果文件中的 SMILES 重建分子")

            mol = Chem.AddHs(mol)

            params = AllChem.ETKDGv3()
            params.randomSeed = 42
            if AllChem.EmbedMolecule(mol, params) != 0:
                raise HTTPException(status_code=500, detail="无法初始化分子构象")

            conf = mol.GetConformer()
            pose_coords = {}
            for line in atom_lines:
                try:
                    serial = int(line[6:11])
                    x = float(line[30:38])
                    y = float(line[38:46])
                    z = float(line[46:54])
                    pose_coords[serial] = (x, y, z)
                except Exception:
                    continue

            assigned_atoms = set()
            for smiles_atom_idx, pdbqt_serial in smiles_idx_pairs:
                atom_idx = smiles_atom_idx - 1
                coords = pose_coords.get(pdbqt_serial)
                if coords is None or atom_idx < 0 or atom_idx >= mol.GetNumAtoms():
                    continue
                conf.SetAtomPosition(atom_idx, Point3D(*coords))
                assigned_atoms.add(atom_idx)

            hydrogen_map = {}
            for parent_smiles_idx, pdbqt_serial in h_parent_pairs:
                hydrogen_map.setdefault(parent_smiles_idx - 1, []).append(pdbqt_serial)

            for parent_idx, hydrogen_serials in hydrogen_map.items():
                if parent_idx < 0 or parent_idx >= mol.GetNumAtoms():
                    continue
                hydrogen_neighbors = [
                    nbr.GetIdx()
                    for nbr in mol.GetAtomWithIdx(parent_idx).GetNeighbors()
                    if nbr.GetAtomicNum() == 1
                ]
                for h_idx, pdbqt_serial in zip(hydrogen_neighbors, hydrogen_serials):
                    coords = pose_coords.get(pdbqt_serial)
                    if coords is None:
                        continue
                    conf.SetAtomPosition(h_idx, Point3D(*coords))
                    assigned_atoms.add(h_idx)

            if not assigned_atoms:
                raise HTTPException(status_code=500, detail="未能将 pose 坐标映射回分子拓扑")

            mol_no_h = Chem.RemoveHs(mol)
            sdf_block = Chem.MolToMolBlock(mol_no_h)
            if not sdf_block:
                raise HTTPException(status_code=500, detail="无法导出标准 SDF")

            return Response(
                content=sdf_block,
                media_type="chemical/x-mdl-sdfile",
                headers={"Content-Disposition": f"attachment; filename=docking_pose_{job_id}_{pose}.sdf"},
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"重建 pose SDF 失败: {e}")
            raise HTTPException(status_code=500, detail=f"重建 pose SDF 失败: {str(e)}")

    @app.get("/api/docking/history")
    async def get_docking_history():
        """获取对接历史记录列表"""
        import re
        from datetime import datetime

        try:
            work_dir = docking_service.work_dir if docking_service else os.path.join(os.getcwd(), "temp_docking")
            if not os.path.isdir(work_dir):
                return {"success": True, "history": [], "total": 0}

            history = []
            for entry in os.scandir(work_dir):
                if not entry.is_dir() or not entry.name.startswith("docking_"):
                    continue
                job_id = entry.name.replace("docking_", "")
                result_file = os.path.join(entry.path, "result.pdbqt")
                receptor_file = os.path.join(entry.path, "receptor.pdbqt")
                ligand_file = os.path.join(entry.path, "ligand.pdbqt")
                ligand_sdf = os.path.join(entry.path, "ligand_input.sdf")

                has_result = os.path.exists(result_file)
                has_receptor = os.path.exists(receptor_file)
                has_ligand = os.path.exists(ligand_file)

                # 时间取目录修改时间
                mtime = entry.stat().st_mtime
                time_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")

                # 解析最佳结合能
                best_energy = None
                pose_count = 0
                if has_result:
                    try:
                        with open(result_file, 'r', errors='ignore') as rf:
                            for line in rf:
                                m = re.match(r"REMARK\s+VINA\s+RESULT:\s*([\-+]?\d*\.?\d+)", line)
                                if m:
                                    e = float(m.group(1))
                                    pose_count += 1
                                    if best_energy is None or e < best_energy:
                                        best_energy = e
                    except Exception:
                        pass

                # 计算目录大小
                dir_size = 0
                try:
                    for f in os.scandir(entry.path):
                        if f.is_file():
                            dir_size += f.stat().st_size
                except Exception:
                    pass

                status = "completed" if has_result else ("processing" if has_receptor else "failed")

                history.append({
                    "job_id": job_id,
                    "time": time_str,
                    "timestamp": mtime,
                    "status": status,
                    "best_energy": best_energy,
                    "pose_count": pose_count,
                    "has_receptor": has_receptor,
                    "has_ligand": has_ligand,
                    "has_result": has_result,
                    "size_bytes": dir_size,
                })

            # 按时间倒序
            history.sort(key=lambda x: x["timestamp"], reverse=True)
            return {"success": True, "history": history, "total": len(history)}

        except Exception as e:
            logger.error(f"获取对接历史失败: {e}")
            raise HTTPException(status_code=500, detail=f"获取历史失败: {str(e)}")

    @app.delete("/api/docking/history")
    async def clear_docking_history():
        """清除所有对接历史记录"""
        try:
            work_dir = docking_service.work_dir if docking_service else os.path.join(os.getcwd(), "temp_docking")
            if not os.path.isdir(work_dir):
                return {"success": True, "message": "无历史记录", "deleted": 0}

            import shutil as _shutil
            deleted = 0
            for entry in os.scandir(work_dir):
                if entry.is_dir() and entry.name.startswith("docking_"):
                    try:
                        _shutil.rmtree(entry.path)
                        deleted += 1
                    except Exception as ex:
                        logger.warning(f"删除 {entry.path} 失败: {ex}")

            return {"success": True, "message": f"已清除 {deleted} 条历史记录", "deleted": deleted}
        except Exception as e:
            logger.error(f"清除对接历史失败: {e}")
            raise HTTPException(status_code=500, detail=f"清除历史失败: {str(e)}")

    @app.delete("/api/docking/history/{job_id}")
    async def delete_docking_job(job_id: str):
        """删除指定的对接历史记录"""
        try:
            import shutil as _shutil
            work_dir = docking_service.work_dir if docking_service else os.path.join(os.getcwd(), "temp_docking")
            job_dir = os.path.join(work_dir, f"docking_{job_id}")
            if not os.path.isdir(job_dir):
                raise HTTPException(status_code=404, detail="记录不存在")
            _shutil.rmtree(job_dir)
            return {"success": True, "message": f"已删除任务 {job_id}"}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"删除对接记录失败: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/docking/smiles_to_3d")
    async def smiles_to_3d(payload: Dict[str, Any] = Body(...)):
        """将SMILES转换为3D结构用于预览"""
        try:
            smiles = payload.get("smiles", "").strip()
            if not smiles:
                raise HTTPException(status_code=400, detail="SMILES字符串不能为空")
            
            from rdkit import Chem
            from rdkit.Chem import AllChem
            
            mol = Chem.MolFromSmiles(smiles)
            if mol is None:
                raise HTTPException(status_code=400, detail="无效的SMILES字符串")
            
            mol = Chem.AddHs(mol)
            embed_result = AllChem.EmbedMolecule(mol, randomSeed=42)
            
            if embed_result != 0:
                params = AllChem.ETKDGv3()
                params.randomSeed = 42
                embed_result = AllChem.EmbedMolecule(mol, params)
                    
            if embed_result != 0:
                raise HTTPException(status_code=400, detail="无法生成3D构象")
            
            AllChem.MMFFOptimizeMolecule(mol)

            # 去掉氢原子后转 PDB 用于可视化：
            # 氢原子颜色为白色，白色背景下不可见，且球模式下会产生视觉噪点
            mol_noH = Chem.RemoveHs(mol)
            pdb_block = Chem.MolToPDBBlock(mol_noH)
            
            if not pdb_block:
                raise HTTPException(status_code=500, detail="无法转换为PDB格式")
            
            return {
                "success": True,
                "pdb_data": pdb_block,
                "message": "3D结构生成成功"
            }
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"SMILES转3D失败: {e}")
            raise HTTPException(status_code=500, detail=f"转换失败: {str(e)}")

    @app.get("/api/utils/smiles_to_image")
    async def smiles_to_image(smiles: str, width: int = 300, height: int = 200):
        """生成分子2D图片"""
        try:
            from rdkit import Chem
            from rdkit.Chem import Draw
            import io
            
            if not smiles:
                 raise HTTPException(status_code=400, detail="SMILES cannot be empty")

            mol = Chem.MolFromSmiles(smiles)
            if mol is None:
                raise HTTPException(status_code=400, detail="Invalid SMILES")
                
            # Generate Image
            img = Draw.MolToImage(mol, size=(width, height))
            
            # Convert to bytes
            img_byte_arr = io.BytesIO()
            img.save(img_byte_arr, format='PNG')
            img_byte_arr.seek(0)
            
            return Response(content=img_byte_arr.getvalue(), media_type="image/png")
            
        except Exception as e:
            logger.error(f"生成分子图片失败: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/utils/mcs")
    async def get_mcs(
        smiles1: str,
        smiles2: str,
        width: int = 360,
        height: int = 260,
        timeout: int = 3,
    ):
        """计算两分子的最大公共子结构(MCS)，返回SMARTS与高亮SVG"""
        try:
            from rdkit import Chem
            from rdkit.Chem import rdFMCS
            from rdkit.Chem.Draw import rdMolDraw2D

            if not smiles1 or not smiles2:
                raise HTTPException(status_code=400, detail="SMILES cannot be empty")

            mol1 = Chem.MolFromSmiles(smiles1)
            mol2 = Chem.MolFromSmiles(smiles2)
            if mol1 is None or mol2 is None:
                raise HTTPException(status_code=400, detail="Invalid SMILES")

            mcs_res = rdFMCS.FindMCS(
                [mol1, mol2],
                timeout=timeout,
                ringMatchesRingOnly=True,
                completeRingsOnly=True,
                matchValences=True,
            )

            mcs_smarts = (mcs_res.smartsString or "").strip()
            if not mcs_smarts:
                return {"success": False, "error": "MCS not found"}

            mcs_mol = Chem.MolFromSmarts(mcs_smarts)
            if mcs_mol is None:
                return {"success": False, "error": "MCS parse failed", "mcs_smarts": mcs_smarts}

            match1 = mol1.GetSubstructMatch(mcs_mol)
            match2 = mol2.GetSubstructMatch(mcs_mol)
            if not match1 or not match2:
                return {"success": False, "error": "MCS match failed", "mcs_smarts": mcs_smarts}

            highlight_color = (0.2, 0.7, 0.9)
            atom_colors1 = {int(a): highlight_color for a in match1}
            atom_colors2 = {int(a): highlight_color for a in match2}

            def _bond_indices_in_match(mol, atom_indices):
                atom_set = set(map(int, atom_indices))
                bond_ids = []
                for b in mol.GetBonds():
                    a1 = int(b.GetBeginAtomIdx())
                    a2 = int(b.GetEndAtomIdx())
                    if a1 in atom_set and a2 in atom_set:
                        bond_ids.append(int(b.GetIdx()))
                return bond_ids

            bond_ids1 = _bond_indices_in_match(mol1, match1)
            bond_ids2 = _bond_indices_in_match(mol2, match2)
            bond_colors1 = {int(b): highlight_color for b in bond_ids1}
            bond_colors2 = {int(b): highlight_color for b in bond_ids2}

            def _mol_svg(mol, highlight_atoms, atom_colors):
                drawer = rdMolDraw2D.MolDraw2DSVG(int(width), int(height))
                opts = drawer.drawOptions()
                opts.addAtomIndices = False
                # 让共同片段更“块状”而不是只显示圆点
                if hasattr(opts, "fillHighlights"):
                    opts.fillHighlights = True
                if hasattr(opts, "highlightBondWidthMultiplier"):
                    opts.highlightBondWidthMultiplier = 18
                rdMolDraw2D.PrepareAndDrawMolecule(
                    drawer,
                    mol,
                    highlightAtoms=list(map(int, highlight_atoms)),
                    highlightBonds=_bond_indices_in_match(mol, highlight_atoms),
                    highlightAtomColors=atom_colors,
                    highlightBondColors={int(b): highlight_color for b in _bond_indices_in_match(mol, highlight_atoms)},
                )
                drawer.FinishDrawing()
                return drawer.GetDrawingText()

            return {
                "success": True,
                "mcs_smarts": mcs_smarts,
                "query_highlight_atoms": [int(a) for a in match1],
                "hit_highlight_atoms": [int(a) for a in match2],
                "query_highlight_bonds": [int(b) for b in bond_ids1],
                "hit_highlight_bonds": [int(b) for b in bond_ids2],
                "query_svg": _mol_svg(mol1, match1, atom_colors1),
                "hit_svg": _mol_svg(mol2, match2, atom_colors2),
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"MCS计算失败: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/docking/report/{job_id}")
    async def get_docking_report(job_id: str, payload: Dict[str, Any] = Body(None)):
        """生成并返回对接报告"""
        if not docking_service:
            raise HTTPException(status_code=503, detail="分子对接服务不可用")

        try:
            job_dir = os.path.join(docking_service.work_dir, f"docking_{job_id}")
            result_file = os.path.join(job_dir, "result.pdbqt")
            
            if not os.path.exists(result_file):
                raise HTTPException(status_code=404, detail="对接结果文件不存在")

            results = docking_service.parse_vina_results(result_file)
            
            # 读取配置
            config_file = os.path.join(job_dir, "config.txt")
            config_lines = []
            if os.path.exists(config_file):
                with open(config_file, 'r', encoding='utf-8', errors='ignore') as cf:
                    config_lines = [line.strip() for line in cf.readlines() if line.strip()]

            # 解析参数
            fmt = "md"
            viewer_png_b64 = None
            smiles_images_b64 = []
            if payload and isinstance(payload, dict):
                fmt = str(payload.get("format", "md")).lower()
                viewer_png_b64 = payload.get("viewer_png_base64")
                smiles_images_b64 = payload.get("smiles_images", []) or []

            # 生成报告内容
            from .report_generator import generate_report
            return generate_report(job_id, results, config_lines, fmt, viewer_png_b64, smiles_images_b64, job_dir)

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"生成报告失败: {e}")
            raise HTTPException(status_code=500, detail=f"生成报告失败: {str(e)}")
    
    # ==================== 反向寻靶 API ====================
    
    @app.post("/api/reverse_target/predict")
    async def reverse_target_predict(
        smiles: str = Form(...),
        threshold: float = Form(0.6),
        top_k: int = Form(10),
        organism_filter: str = Form("")
    ):
        """反向寻靶预测API"""
        try:
            if not smiles or not smiles.strip():
                raise HTTPException(status_code=400, detail="SMILES字符串不能为空")
            
            if threshold < 0 or threshold > 1:
                raise HTTPException(status_code=400, detail="阈值必须在0-1之间")
            
            if top_k < 1 or top_k > 100:
                raise HTTPException(status_code=400, detail="返回数量必须在1-100之间")
            
            from src.reverse_target.predictor import get_predictor
            predictor = get_predictor()
            results = predictor.predict(
                smiles=smiles.strip(),
                threshold=threshold,
                top_k=top_k,
                combine_by_target=True,
                organism_filter=organism_filter,
            )
            
            logger.info(f"反向寻靶预测成功: 找到 {len(results)} 个靶点")
            
            return {
                "success": True,
                "count": len(results),
                "results": results
            }
            
        except HTTPException:
            raise
        except ValueError as e:
            logger.warning(f"反向寻靶输入无效: {e}")
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(f"反向寻靶预测失败: {e}")
            raise HTTPException(status_code=500, detail=f"预测失败: {str(e)}")
    
    @app.post("/api/reverse_target/batch_predict")
    async def reverse_target_batch_predict(
        file: UploadFile = File(...),
        threshold: float = Form(0.6),
        top_k: int = Form(10),
        organism_filter: str = Form("")
    ):
        """反向寻靶批量预测API"""
        try:
            if top_k < 1 or top_k > 100:
                raise HTTPException(status_code=400, detail="返回数量必须在1-100之间")

            content = await file.read()
            text = content.decode("utf-8")
            
            smiles_list = []
            # 简单的文件解析逻辑
            import io
            import csv
            
            if file.filename.endswith('.csv'):
                reader = csv.DictReader(io.StringIO(text))
                # 尝试寻找 smiles 列
                smiles_col = None
                if reader.fieldnames:
                    for col in reader.fieldnames:
                        if col.lower() in ['smiles', 'smile', 'canonical_smiles']:
                            smiles_col = col
                            break
                
                if smiles_col:
                    for row in reader:
                        if row[smiles_col].strip():
                            smiles_list.append(row[smiles_col].strip())
                else:
                    # 如果找不到列名，尝试读取第一列
                    reader = csv.reader(io.StringIO(text))
                    for row in reader:
                        if row and row[0].strip():
                            smiles_list.append(row[0].strip())
            else:
                # 假设是每行一个SMILES的文本文件
                lines = text.splitlines()
                for line in lines:
                    line = line.strip()
                    if line:
                        # 简单的处理：如果包含逗号或空格，取第一部分
                        parts = line.replace(',', ' ').split()
                        if parts:
                            smiles_list.append(parts[0])
            
            # 去重并限制数量
            smiles_list = list(set(smiles_list))
            if len(smiles_list) > 100:
                smiles_list = smiles_list[:100]
            
            if not smiles_list:
                raise HTTPException(status_code=400, detail="未能从文件中解析出有效的SMILES")

            from src.reverse_target.predictor import get_predictor
            predictor = get_predictor()
            results = predictor.predict_batch(
                smiles_list=smiles_list,
                threshold=threshold,
                top_k=top_k,
                combine_by_target=True,
                organism_filter=organism_filter,
            )
            
            logger.info(f"批量反向寻靶预测完成: {len(results)} 个分子")
            
            return {
                "success": True,
                "count": len(results),
                "results": results
            }
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"批量预测失败: {e}")
            raise HTTPException(status_code=500, detail=f"批量预测失败: {str(e)}")

    @app.get("/api/reverse_target/stats")
    async def get_reverse_target_stats():
        """获取反向寻靶数据库统计信息"""
        try:
            from src.reverse_target.predictor import get_predictor
            predictor = get_predictor()
            stats = predictor.get_stats()
            return {"success": True, "stats": stats}
        except Exception as e:
            logger.error(f"获取统计信息失败: {e}")
            raise HTTPException(status_code=500, detail=f"获取统计信息失败: {str(e)}")

    @app.get("/api/reverse_target/health")
    async def get_reverse_target_health():
        """轻量检查反向寻靶本地数据库状态，不加载大型指纹矩阵。"""
        try:
            from src.reverse_target.config import get_reverse_target_data_dir
            from src.reverse_target.health import inspect_reverse_target_database

            data_dir = get_reverse_target_data_dir()
            return inspect_reverse_target_database(data_dir)
        except Exception as e:
            logger.error(f"反向寻靶健康检查失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"反向寻靶健康检查失败: {str(e)}")
    
    @app.get("/api/reverse_target/similar_molecules")
    async def get_similar_molecules(
        smiles: str,
        target_name: str,
        threshold: float = 0.6,
        limit: int = 50,
        organism_filter: str = ""
    ):
        """获取特定靶点的相似分子列表"""
        try:
            if not smiles or not smiles.strip():
                raise HTTPException(status_code=400, detail="SMILES字符串不能为空")
            
            if not target_name or not target_name.strip():
                raise HTTPException(status_code=400, detail="靶点名称不能为空")
            
            from src.reverse_target.predictor import get_predictor
            predictor = get_predictor()
            
            results = predictor.get_similar_molecules(
                smiles=smiles.strip(),
                target_name=target_name.strip(),
                threshold=threshold,
                limit=limit,
                organism_filter=organism_filter,
            )
            
            return {
                "success": True,
                "count": len(results),
                "target_name": target_name,
                "query_smiles": smiles,
                "results": results
            }
            
        except HTTPException:
            raise
        except ValueError as e:
            logger.warning(f"获取相似分子输入无效: {e}")
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(f"获取相似分子失败: {e}")
            raise HTTPException(status_code=500, detail=f"获取失败: {str(e)}")

    # ==================== 3D 药效团 API ====================

    @app.post("/api/reverse_target/predict_3d")
    async def reverse_target_predict_3d(
        smiles: str = Form(...),
        threshold: float = Form(0.5),
        top_k: int = Form(10),
        max_refine: int = Form(50),
        alpha_2d: float = Form(0.4),
        alpha_3d: float = Form(0.6),
        organism_filter: str = Form(""),
    ):
        """
        2D+3D 级联药效团反向寻靶
        
        流程:
        1. 用 2D Morgan 指纹粗筛 top_k * 10 个候选分子
        2. 对候选分子进行 3D 构象生成 + 药效团打分
        3. 融合 2D+3D 分数进行重新排序，返回最终 top_k 靶点
        """
        try:
            if not smiles or not smiles.strip():
                raise HTTPException(status_code=400, detail="SMILES字符串不能为空")

            if top_k < 1 or top_k > 100:
                raise HTTPException(status_code=400, detail="返回数量必须在1-100之间")

            if max_refine < 1 or max_refine > 100:
                raise HTTPException(status_code=400, detail="3D精修候选数必须在1-100之间")

            smiles = smiles.strip()
            candidate_limit = _get_pharm3d_candidate_pool_limit(top_k, max_refine)

            # Step 1: 2D 预筛选。返回数量(top_k)和 3D 精修数量(max_refine)解耦。
            from src.reverse_target.predictor import get_predictor
            predictor = get_predictor()
            
            # 降低阈值保证候选数量，提升召回
            adjusted_threshold = max(0.0, threshold - 0.2)
            raw_candidates = predictor.get_raw_similar_molecules(
                smiles=smiles,
                threshold=adjusted_threshold,
                limit=candidate_limit,
                organism_filter=organism_filter,
            )
            raw_candidate_count = len(raw_candidates)
            unique_target_count_before_top_k = len(
                {
                    item.get("target_name") or item.get("target_id") or item.get("chembl_id")
                    for item in raw_candidates
                    if item.get("target_name") or item.get("target_id") or item.get("chembl_id")
                }
            )

            if not raw_candidates:
                return {
                    "success": True,
                    "mode": "2d+3d",
                    "count": 0,
                    "results": [],
                    "query_pharmacophore": None,
                    "warning": "未找到候选分子，请降低相似度阈值",
                    "requested_top_k": top_k,
                    "candidate_pool_size": candidate_limit,
                    "raw_candidate_count": 0,
                    "unique_target_count_before_top_k": 0,
                    "adjusted_threshold": adjusted_threshold,
                }

            logger.info(
                f"3D精修: 2D粗筛得到 {len(raw_candidates)} 个候选分子，"
                f"候选池上限 {candidate_limit}，计划精修前 {min(max_refine, len(raw_candidates))} 个，"
                f"最终返回前 {top_k} 个"
            )

            # Step 2: 3D 药效团精修（在线程池中执行以避免阻塞事件循环）
            from src.reverse_target.pharmacophore_refiner import (
                refine_with_pharmacophore,
                get_molecule_pharmacophore,
            )

            refinement_status = "success"
            refinement_message = "3D 药效团精修完成"
            timeout_seconds = _get_pharm3d_timeout(25.0)

            try:
                outer_timeout = timeout_seconds + min(1.0, max(0.1, timeout_seconds * 0.2))
                refined = await _run_pharm3d_job(
                    lambda: refine_with_pharmacophore(
                        query_smiles=smiles,
                        candidates=raw_candidates,
                        max_to_refine=max_refine,
                        alpha_2d=alpha_2d,
                        alpha_3d=alpha_3d,
                        timeout_seconds=timeout_seconds,
                    ),
                    timeout_seconds=outer_timeout,
                )
            except asyncio.TimeoutError:
                logger.warning("3D药效团精修超时，返回2D基础结果")
                refinement_status = "timeout"
                refinement_message = "3D 药效团精修超时，已返回基础反向寻靶结果"
                refined = _build_pharm3d_fallback(raw_candidates, refinement_message)
                query_pharm = None
            except Exception as e:
                logger.warning(f"3D药效团精修失败，返回2D基础结果: {e}")
                refinement_status = "fallback"
                refinement_message = f"3D 药效团精修失败，已返回基础反向寻靶结果: {str(e)}"
                refined = _build_pharm3d_fallback(raw_candidates, refinement_message)
                query_pharm = None
            else:
                timeout_fallback_count = sum(
                    1 for item in refined if item.get("pharm_refinement_status") == "timeout_fallback"
                )
                refined_count = sum(
                    1 for item in refined if item.get("pharm_refinement_status") == "refined"
                )
                not_refined_count = max(0, len(refined) - refined_count)
                if timeout_fallback_count:
                    refinement_status = "timeout_partial"
                    refinement_message = (
                        f"3D 药效团精修达到时间上限，已完成 {refined_count} 个候选；"
                        f"其余候选使用 2D 基础分数返回"
                    )
                elif not_refined_count:
                    refinement_status = "partial"
                    refinement_message = (
                        f"已对前 {refined_count} 个候选完成 3D 药效团精修；"
                        f"其余候选使用 2D 基础分数返回"
                    )

                try:
                    query_pharm = await _run_pharm3d_job(
                        lambda: get_molecule_pharmacophore(smiles),
                        timeout_seconds=min(5.0, timeout_seconds),
                    )
                except asyncio.TimeoutError:
                    logger.warning("查询分子药效团提取超时，继续返回精修结果")
                    query_pharm = None
                except Exception as e:
                    logger.warning(f"查询分子药效团提取失败，继续返回精修结果: {e}")
                    query_pharm = None

            # Step 3: 按靶点聚合，选 top_k 靶点
            from src.reverse_target.predictor import _aggregate_by_target
            final_results = _aggregate_by_target(refined, top_k=top_k, score_field="final_3d_score")

            logger.info(f"3D精修完成, 返回 {len(final_results)} 个靶点")

            return {
                "success": True,
                "mode": "2d+3d",
                "count": len(final_results),
                "results": final_results,
                "query_pharmacophore": query_pharm if query_pharm and query_pharm.get("success") else None,
                "pharmacophore_refinement_status": refinement_status,
                "message": refinement_message,
                "requested_top_k": top_k,
                "candidate_pool_size": candidate_limit,
                "raw_candidate_count": raw_candidate_count,
                "unique_target_count_before_top_k": unique_target_count_before_top_k,
                "adjusted_threshold": adjusted_threshold,
            }

        except HTTPException:
            raise
        except ValueError as e:
            logger.warning(f"3D药效团预测输入无效: {e}")
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(f"3D药效团预测失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"3D预测失败: {str(e)}")

    @app.post("/api/reverse_target/pharmacophore")
    async def get_pharmacophore(
        smiles: str = Form(...),
    ):
        """
        单分子 3D 药效团特征提取 API
        
        返回:
            features      : 药效团特征列表（含坐标、类型、颜色）
            feature_counts: 各类特征数量统计
            mol_block     : MolBlock (供 3Dmol.js 可视化)
            properties    : 理化性质 (MW, LogP, TPSA, HBD, HBA, RotBonds)
        """
        try:
            if not smiles or not smiles.strip():
                raise HTTPException(status_code=400, detail="SMILES字符串不能为空")

            from src.reverse_target.pharmacophore_refiner import get_molecule_pharmacophore

            try:
                result = await _run_pharm3d_job(
                    lambda: get_molecule_pharmacophore(smiles.strip()),
                    timeout_seconds=_get_pharm3d_timeout(25.0),
                )
            except asyncio.TimeoutError:
                raise HTTPException(status_code=504, detail="药效团提取超时，请稍后重试或使用更简单的分子")

            if not result.get("success"):
                raise HTTPException(status_code=422, detail=result.get("error", "药效团提取失败"))

            return result

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"药效团提取失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"药效团提取失败: {str(e)}")

    # ==================== 活性预测 API ====================

    
    @app.post("/api/activity/predict")
    async def activity_predict(
        smiles: str = Form(...)
    ):
        """活性预测 API"""
        try:
            from src.activity.predictor import get_predictor
            predictor = get_predictor()
            results = predictor.predict(smiles)
            return {"success": True, "results": results}
        except Exception as e:
            logger.error(f"活性预测失败: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/activity/batch_predict")
    async def activity_batch_predict(
        file: UploadFile = File(...)
    ):
        """活性批量预测 API"""
        try:
            content = await file.read()
            text = content.decode("utf-8")
            # Simple parsing
            smiles_list = []
            for line in text.splitlines():
                line = line.strip()
                if line:
                    # Handle CSV or simple list
                    parts = line.replace(',', ' ').split()
                    if parts:
                        smiles_list.append(parts[0])
            
            from src.activity.predictor import get_predictor
            predictor = get_predictor()
            results = predictor.predict(smiles_list)
            return {"success": True, "results": results}
        except Exception as e:
            logger.error(f"批量活性预测失败: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # ==================== 活性模型训练与管理 API ====================
    
    @app.post("/api/activity/train")
    async def start_activity_training(
        file: UploadFile           = File(...),
        target_column: str         = Form(...),
        task_type: str             = Form("regression"),
        epochs: int                = Form(50),
        learning_rate: float       = Form(0.001),
        batch_size: int            = Form(32),
        dropout: float             = Form(0.2),
        num_layers: int            = Form(5),
        hidden_size: int           = Form(256),
        weight_decay: float        = Form(0.0001),
        patience: int              = Form(12),
        loss_metric: str           = Form("MSE"),
        lr_scheduler: str          = Form("Cosine")
    ):
        """提交活性预测模型训练任务"""
        try:
            # 1. 保存上传的数据集
            import tempfile
            from src.activity.trainer import submit_training_job
            
            ext = os.path.splitext(file.filename)[1] or ".csv"
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
            content = await file.read()
            temp_file.write(content)
            temp_file.close()

            # 2. 启动后台训练
            job_id = submit_training_job(
                file_path=temp_file.name,
                target_column=target_column,
                task_type=task_type,
                epochs=epochs,
                lr=learning_rate,
                batch_size=batch_size,
                dropout=dropout,
                # 透传新参数
                num_layers=num_layers,
                hidden_size=hidden_size,
                weight_decay=weight_decay,
                patience=patience,
                loss_metric=loss_metric,
                lr_scheduler=lr_scheduler
            )
            
            return {"success": True, "job_id": job_id, "message": "训练任务已启动"}
        except Exception as e:
            logger.error(f"启动训练失败: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/activity/train/status/{job_id}")
    async def get_training_status(job_id: str):
        """查询训练状态和指标"""
        from src.activity.trainer import get_job_status
        status = get_job_status(job_id)
        if not status:
            raise HTTPException(status_code=404, detail="任务不存在")
        return {"success": True, "status": status}
        
    @app.get("/api/activity/models")
    async def list_activity_models():
        """列出所有已训练的活性模型"""
        from src.activity.trainer import list_available_models, get_best_model_path
        models = list_available_models()
        current_model_path = get_best_model_path()
        current_model_file = None
        if current_model_path:
            import os
            current_model_file = os.path.basename(current_model_path)
            
        return {"success": True, "models": models, "current_model": current_model_file}

    @app.post("/api/activity/models/switch")
    async def switch_activity_model(data: Dict[str, str] = Body(...)):
        """切换当前使用的活性预测模型权重"""
        try:
            model_file = data.get("model_file")
            if not model_file:
                raise HTTPException(status_code=400, detail="缺少 model_file 参数")
                
            from src.activity.trainer import set_active_model
            set_active_model(model_file)
            
            # Force reload in predictor
            from src.activity.predictor import get_predictor
            predictor = get_predictor()
            predictor._loaded = False  # trigger reload on next predict
            
            return {"success": True, "message": f"已切换至模型 {model_file}"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.delete("/api/activity/models/{model_id}")
    async def remove_activity_model(model_id: str):
        """物理删除一个活性预测模型"""
        try:
            from src.activity.trainer import delete_model
            delete_model(model_id)
            return {"success": True, "message": f"模型 {model_id} 已删除"}
        except ValueError as ve:
            raise HTTPException(status_code=404, detail=str(ve))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ==================== 分子属性计算 API ====================
    
    @app.post("/api/molecule/properties")
    async def calculate_molecule_properties(data: Dict[str, Any] = Body(...)):
        """计算分子的基础属性和ADMET属性"""
        try:
            smiles = data.get('smiles')
            if not smiles:
                raise HTTPException(status_code=400, detail="缺少SMILES参数")
            
            logger.info(f"计算分子属性: {smiles}")
            
            # 直接使用RDKit计算属性，避免工具的SMILES提取逻辑
            try:
                from rdkit import Chem
                from rdkit.Chem import Descriptors, Crippen, Lipinski, QED
                
                # 验证SMILES - 尝试多种解析方式
                mol = Chem.MolFromSmiles(smiles)
                
                # 如果标准解析失败，尝试不做清洗的解析（可能包含部分错误但能读取） -> 再手动清洗
                if mol is None:
                    mol = Chem.MolFromSmiles(smiles, sanitize=False)
                    if mol:
                        try:
                            Chem.SanitizeMol(mol)
                        except Exception:
                            mol = None

                if mol is None:
                    logger.warning(f"RDKit无法解析SMILES: {smiles}")
                    return {
                        "success": False,
                        "error": f"无法识别的分子结构: {smiles}",
                        "properties": None
                    }
                
                # 计算基础属性
                properties = {
                    'basic': {
                        'molecular_weight': round(Descriptors.MolWt(mol), 2),
                        'logp': round(Crippen.MolLogP(mol), 2),
                        'hbd': Lipinski.NumHDonors(mol),
                        'hba': Lipinski.NumHAcceptors(mol),
                        'tpsa': round(Descriptors.TPSA(mol), 2),
                        'rotatable_bonds': Lipinski.NumRotatableBonds(mol),
                        'qed': round(QED.qed(mol), 3)
                    },
                    'admet': {}
                }
                
                # 尝试使用ADMET预测工具
                try:
                    from src.agent.tools import ADMETPredictor
                    admet_predictor = ADMETPredictor()
                    
                    # 直接调用内部方法，传入已验证的mol对象
                    if hasattr(admet_predictor, '_predict_admet_properties'):
                        admet_result = admet_predictor._predict_admet_properties(mol)
                        if admet_result:
                            properties['admet'] = {
                                'bbb_penetration': admet_result.get('bbb_penetration', 'Unknown'),
                                'cyp_inhibition': admet_result.get('cyp_inhibition', 'Unknown'),
                                'hepatotoxicity': admet_result.get('hepatotoxicity', 'Unknown'),
                                'solubility': admet_result.get('solubility', 'Unknown'),
                                'bioavailability': admet_result.get('bioavailability', 'Unknown')
                            }
                    else:
                        # 如果没有内部方法，使用简单的规则预测
                        properties['admet'] = {
                            'bbb_penetration': 'High' if properties['basic']['tpsa'] < 90 else 'Low',
                            'cyp_inhibition': 'Low' if properties['basic']['molecular_weight'] < 400 else 'Moderate',
                            'hepatotoxicity': 'Low',
                            'solubility': 'Good' if properties['basic']['logp'] < 3 else 'Moderate',
                            'bioavailability': 'High' if properties['basic']['qed'] > 0.5 else 'Moderate'
                        }
                except Exception as admet_error:
                    logger.warning(f"ADMET预测失败，使用简单规则: {admet_error}")
                    # 使用简单的规则预测
                    properties['admet'] = {
                        'bbb_penetration': 'High' if properties['basic']['tpsa'] < 90 else 'Low',
                        'cyp_inhibition': 'Low' if properties['basic']['molecular_weight'] < 400 else 'Moderate',
                        'hepatotoxicity': 'Low',
                        'solubility': 'Good' if properties['basic']['logp'] < 3 else 'Moderate',
                        'bioavailability': 'High' if properties['basic']['qed'] > 0.5 else 'Moderate'
                    }
                
                logger.info(f"属性计算完成: {len(properties['basic'])} 个基础属性, {len(properties['admet'])} 个ADMET属性")
                
                return {
                    "success": True,
                    "properties": properties,
                    "smiles": smiles
                }
                
            except Exception as rdkit_error:
                logger.error(f"RDKit计算失败: {rdkit_error}")
                raise
            
        except Exception as e:
            logger.error(f"分子属性计算失败: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "properties": None
            }

    # ==================== Agent 监控 API ====================

    @app.get("/api/agent/metrics")
    async def get_agent_metrics():
        """获取 Agent 技能性能统计报表"""
        try:
            from src.agent.metrics import metrics_system
            return {
                "success": True,
                "report": metrics_system.get_report()
            }
        except Exception as e:
            logger.error(f"获取指标报表失败: {e}")
            return {"success": False, "error": str(e)}
