"""Docking route registration."""
from typing import List, Optional
from fastapi import UploadFile, File, Form, Header, HTTPException, Response


def setup_docking_routes(app, docking_service=None, task_runtime=None, *, _support):
    """Register the original endpoints with dynamically resolved compatibility support."""
    def current_task_runtime():
        if task_runtime is None:
            return None
        return task_runtime() if callable(task_runtime) else task_runtime

    # ==================== 分子对接 API ====================
    
    @app.post(
        "/api/docking/tasks",
        status_code=202,
    )
    async def submit_durable_docking_task(
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
        manual_center: bool = Form(False),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ):
        try:
            runtime = current_task_runtime()
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Task runtime unavailable") from exc
        if runtime is None:
            raise HTTPException(status_code=503, detail="Task runtime unavailable")
        clean_smiles = (smiles or "").strip() or None
        if ligand_file is None and clean_smiles is None:
            raise HTTPException(status_code=400, detail="Provide a ligand file or SMILES")
        if ligand_file is not None and clean_smiles is not None:
            raise HTTPException(
                status_code=400,
                detail="Provide either a ligand file or SMILES, not both",
            )
        clean_idempotency_key = (idempotency_key or "").strip() or None
        if clean_idempotency_key is not None and len(clean_idempotency_key) > 256:
            raise HTTPException(status_code=422, detail="Idempotency-Key is too long")
        _support._validate_docking_limits(
            size_x=size_x,
            size_y=size_y,
            size_z=size_z,
            exhaustiveness=exhaustiveness,
            num_modes=num_modes,
            energy_range=energy_range,
        )
        if not manual_center:
            raise HTTPException(
                status_code=422,
                detail="Durable docking requires an explicit manual center",
            )
        if energy_range != 3.0:
            raise HTTPException(
                status_code=422,
                detail="Durable docking currently supports energy_range=3 only",
            )
        receptor_bytes = await _support._read_upload_limited(protein_file, "protein file")
        ligand_bytes = (
            await _support._read_upload_limited(ligand_file, "ligand file")
            if ligand_file is not None
            else None
        )
        config = {
            "center": [center_x, center_y, center_z],
            "size": [size_x, size_y, size_z],
            "exhaustiveness": exhaustiveness,
            "num_modes": num_modes,
        }
        try:
            receipt = await runtime.submit_docking(
                receptor_name=protein_file.filename or "protein.pdb",
                receptor_bytes=receptor_bytes,
                ligand_name=(ligand_file.filename if ligand_file is not None else None),
                ligand_bytes=ligand_bytes,
                smiles=clean_smiles,
                config=config,
                idempotency_key=clean_idempotency_key,
            )
            record = await runtime.get(receipt.task_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid docking task input") from exc
        except HTTPException:
            raise
        except Exception as exc:
            _support.logger.error("Durable docking task submission failed")
            raise HTTPException(status_code=503, detail="Task submission unavailable") from exc
        data = record.to_public_dict()
        data["start_outcome"] = receipt.outcome.value
        return _support.api_success(data, message="Docking task submitted")

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

        temp_paths: List[str] = []
        try:
            if not ligand_file and not smiles:
                raise HTTPException(status_code=400, detail="请提供配体文件或SMILES字符串")
            _support._validate_docking_limits(
                size_x=size_x,
                size_y=size_y,
                size_z=size_z,
                exhaustiveness=exhaustiveness,
                num_modes=num_modes,
                energy_range=energy_range,
            )

            # 保存蛋白质文件（保留上传后缀，避免 .pdbqt 被误当成 .pdb）
            protein_name = (protein_file.filename or "protein").lower()
            _, protein_ext = _support.os.path.splitext(protein_name)
            if protein_ext not in {".pdb", ".pdbqt"}:
                protein_ext = ".pdb"
            protein_temp = _support.tempfile.NamedTemporaryFile(delete=False, suffix=protein_ext)
            temp_paths.append(protein_temp.name)
            protein_content = await _support._read_upload_limited(protein_file, "protein file")
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
                result = await _support._invoke_in_threadpool(
                    docking_service.perform_docking,
                    receptor_file=protein_temp.name,
                    ligand_input=smiles,
                    config=config,
                    input_type="smiles"
                )
            else:
                original_name = ligand_file.filename or "ligand"
                _, ext = _support.os.path.splitext(original_name)
                ext = ext if ext else ".sdf"

                ligand_temp = _support.tempfile.NamedTemporaryFile(delete=False, suffix=ext)
                temp_paths.append(ligand_temp.name)
                ligand_content = await _support._read_upload_limited(ligand_file, "ligand file")
                ligand_temp.write(ligand_content)
                ligand_temp.close()
                ligand_temp_path = ligand_temp.name
                
                result = await _support._invoke_in_threadpool(
                    docking_service.perform_docking,
                    receptor_file=protein_temp.name,
                    ligand_input=ligand_temp_path,
                    config=config,
                    input_type="file"
                )

            result = dict(result)
            result["warnings"] = _support._normalize_warning_strings(result.get("warnings"))
            return result

        except HTTPException:
            raise
        except Exception as e:
            _support.logger.error(f"分子对接任务提交失败: {e}")
            raise HTTPException(status_code=500, detail=f"对接计算失败: {str(e)}")
        finally:
            for path in temp_paths:
                try:
                    if _support.os.path.exists(path):
                        _support.os.unlink(path)
                except Exception:
                    pass

    @app.get("/api/docking/env_check")
    async def docking_env_check():
        """返回分子对接环境详细诊断"""
        try:
            if not docking_service:
                raise HTTPException(status_code=503, detail="分子对接服务不可用")
            info = docking_service.env_diagnostics()
            return {"success": info.get("ok", False), "diagnostics": info}
        except Exception as e:
            _support.logger.error(f"环境自检失败: {e}")
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
        max_batch_ligands = _support._get_int_env("MEDCHAT_DOCKING_MAX_BATCH_LIGANDS", 100)
        if len(ligand_files) + len(smiles_rows) > max_batch_ligands:
            raise HTTPException(
                status_code=413,
                detail=f"Batch docking accepts at most {max_batch_ligands} ligands",
            )
        _support._validate_docking_limits(
            size_x=size_x,
            size_y=size_y,
            size_z=size_z,
            exhaustiveness=exhaustiveness,
            num_modes=num_modes,
            energy_range=energy_range,
        )

        batch_id = str(uuid.uuid4())[:8]
        temp_paths: List[str] = []

        try:
            protein_name = (protein_file.filename or "protein").lower()
            _, protein_ext = _support.os.path.splitext(protein_name)
            if protein_ext not in {".pdb", ".pdbqt"}:
                protein_ext = ".pdb"
            protein_temp = _support.tempfile.NamedTemporaryFile(delete=False, suffix=protein_ext)
            temp_paths.append(protein_temp.name)
            protein_temp.write(await _support._read_upload_limited(protein_file, "protein file"))
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
                _, ext = _support.os.path.splitext(original_name)
                ligand_temp = _support.tempfile.NamedTemporaryFile(delete=False, suffix=ext or ".sdf")
                temp_paths.append(ligand_temp.name)
                ligand_temp.write(await _support._read_upload_limited(ligand_file, "ligand file"))
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
                    result = await _support._invoke_in_threadpool(
                        docking_service.perform_docking,
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
                        "warnings": _support._normalize_warning_strings(result.get("warnings")),
                    })
                except Exception as item_error:
                    _support.logger.error(f"批量对接子任务失败 ({job['ligand_name']}): {item_error}")
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
                        "warnings": [],
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
            _support.logger.error(f"批量分子对接任务提交失败: {e}")
            raise HTTPException(status_code=500, detail=f"批量对接失败: {str(e)}")
        finally:
            for path in temp_paths:
                try:
                    if _support.os.path.exists(path):
                        _support.os.unlink(path)
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
            job_dir = _support.os.path.join(docking_service.work_dir, f"docking_{job_id}")
            result_file = _support.os.path.join(job_dir, "result.pdbqt")
            
            if not _support.os.path.exists(result_file):
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
            _support.logger.error(f"获取对接结果失败: {e}")
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
            job_dir = _support.os.path.join(docking_service.work_dir, f"docking_{job_id}")
            result_file = _support.os.path.join(job_dir, "result.pdbqt")

            if not _support.os.path.exists(result_file):
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
            _support.logger.error(f"重建 pose SDF 失败: {e}")
            raise HTTPException(status_code=500, detail=f"重建 pose SDF 失败: {str(e)}")

    @app.get("/api/docking/history")
    async def get_docking_history(page: int = 1, limit: int = 50):
        """获取对接历史记录列表"""
        try:
            work_dir = docking_service.work_dir if docking_service else _support.os.path.join(_support.os.getcwd(), "temp_docking")
            if not _support.os.path.isdir(work_dir):
                return {
                    "success": True,
                    "history": [],
                    "total": 0,
                    "page": max(1, page),
                    "limit": max(1, limit),
                }

            from src.docking.history_index import read_history_page

            history, total, resolved_page, resolved_limit = read_history_page(
                work_dir,
                page=page,
                limit=limit,
            )
            return {
                "success": True,
                "history": history,
                "total": total,
                "page": resolved_page,
                "limit": resolved_limit,
            }

        except Exception as e:
            _support.logger.error(f"获取对接历史失败: {e}")
            raise HTTPException(status_code=500, detail=f"获取历史失败: {str(e)}")

    @app.delete("/api/docking/history")
    async def clear_docking_history():
        """清除所有对接历史记录"""
        try:
            work_dir = docking_service.work_dir if docking_service else _support.os.path.join(_support.os.getcwd(), "temp_docking")
            if not _support.os.path.isdir(work_dir):
                return {"success": True, "message": "无历史记录", "deleted": 0}

            import shutil as _shutil
            from src.docking.history_index import clear_history_records

            deleted = 0
            for entry in _support.os.scandir(work_dir):
                if entry.is_dir() and entry.name.startswith("docking_"):
                    try:
                        _shutil.rmtree(entry.path)
                        deleted += 1
                    except Exception as ex:
                        _support.logger.warning(f"删除 {entry.path} 失败: {ex}")
            clear_history_records(work_dir)

            return {"success": True, "message": f"已清除 {deleted} 条历史记录", "deleted": deleted}
        except Exception as e:
            _support.logger.error(f"清除对接历史失败: {e}")
            raise HTTPException(status_code=500, detail=f"清除历史失败: {str(e)}")

    @app.delete("/api/docking/history/{job_id}")
    async def delete_docking_job(job_id: str):
        """删除指定的对接历史记录"""
        try:
            import re as _re
            import shutil as _shutil
            from src.docking.history_index import remove_history_record

            if not _re.fullmatch(r"[A-Za-z0-9_-]+", job_id or ""):
                raise HTTPException(status_code=400, detail="Invalid job_id")
            work_dir = docking_service.work_dir if docking_service else _support.os.path.join(_support.os.getcwd(), "temp_docking")
            job_dir = _support.os.path.join(work_dir, f"docking_{job_id}")
            if not _support.os.path.isdir(job_dir):
                raise HTTPException(status_code=404, detail="记录不存在")
            _shutil.rmtree(job_dir)
            remove_history_record(work_dir, job_id)
            return {"success": True, "message": f"已删除任务 {job_id}"}
        except HTTPException:
            raise
        except Exception as e:
            _support.logger.error(f"删除对接记录失败: {e}")
            raise HTTPException(status_code=500, detail=str(e))
