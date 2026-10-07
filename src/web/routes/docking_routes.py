"""Docking route registration."""
import asyncio
import copy
import math
import os
import re
import threading
from typing import List, Optional
from fastapi import UploadFile, File, Form, Header, HTTPException, Request, Response
from src.web.api_response import api_error
from src.agent.persistence.redaction import redact_sensitive


_SAFE_JOB_ID = re.compile(r"[A-Za-z0-9_-]+")


def _normalize_warning_strings(values) -> list[str]:
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


def _positive_float_env(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) and value > 0 else default


def _batch_result(index: int, job: dict, result: object, *, error: str | None = None,
                 status: str | None = None) -> dict:
    payload = result if isinstance(result, dict) else {}
    payload_status = payload.get("status")
    non_success_statuses = {"partial", "failed", "cancelled", "timed_out"}
    success_statuses = {"completed", "succeeded"}
    if error is not None:
        success = False
        resolved_status = status or "failed"
    elif payload_status in non_success_statuses:
        success = False
        resolved_status = status or payload_status
    elif payload_status in success_statuses or payload_status is None:
        success = bool(payload.get("success"))
        resolved_status = status or ("completed" if success else "failed")
    else:
        success = False
        resolved_status = status or "failed"
        error = "Docking result contained an unsupported status."
    best_pose = payload.get("best_pose") if success else None
    return {
        "index": index,
        "ligand_name": job["ligand_name"],
        "input_type": job["input_type"],
        "status": resolved_status,
        "success": success,
        "job_id": payload.get("job_id"),
        "best_pose": best_pose,
        "best_energy": best_pose.get("binding_energy") if isinstance(best_pose, dict) else None,
        "total_poses": payload.get("total_poses", 0),
        "error": error or payload.get("error"),
        "warnings": _normalize_warning_strings(payload.get("warnings", [])),
    }


async def _run_batch_docking_jobs(
    *, _support, docking_service, jobs: list[dict], receptor_file: str,
    base_config, owner_session_id: str,
) -> dict:
    """Run batch docking through a bounded, physically cancellable queue."""

    concurrency = max(1, int(os.environ.get("MEDCHAT_DOCKING_BATCH_CONCURRENCY", "1")))
    overall_timeout = _positive_float_env("MEDCHAT_DOCKING_BATCH_TIMEOUT_SECONDS", 1800.0)
    item_timeout = _positive_float_env("MEDCHAT_DOCKING_ITEM_TIMEOUT_SECONDS", 300.0)
    queue = asyncio.Semaphore(concurrency)
    cancel_events = [threading.Event() for _ in jobs]

    async def run_one(index: int, job: dict) -> dict:
        async with queue:
            cancel_event = cancel_events[index - 1]
            config = copy.deepcopy(base_config)
            call = asyncio.create_task(_support._invoke_in_threadpool(
                docking_service.perform_docking,
                receptor_file=receptor_file,
                ligand_input=job["ligand_input"],
                config=config,
                input_type=job["input_type"],
                owner_session_id=owner_session_id,
                cancel_event=cancel_event,
            ))
            try:
                result = await asyncio.wait_for(asyncio.shield(call), timeout=item_timeout)
                return _batch_result(index, job, result)
            except asyncio.TimeoutError:
                cancel_event.set()
                # The route does not release the batch task until the worker
                # has observed cancellation and completed its cleanup.
                try:
                    result = await call
                except Exception as exc:
                    result = {"success": False, "error": str(exc)}
                return _batch_result(
                    index, job, result,
                    status="timed_out",
                    error="Docking item exceeded its time limit.",
                )
            except asyncio.CancelledError:
                cancel_event.set()
                try:
                    await call
                except BaseException:
                    pass
                raise
            except Exception as exc:
                return _batch_result(index, job, {}, error=str(exc))

    tasks = [asyncio.create_task(run_one(index, job)) for index, job in enumerate(jobs, 1)]
    gathered = asyncio.gather(*tasks, return_exceptions=True)
    timed_out = False
    try:
        rows = await asyncio.wait_for(asyncio.shield(gathered), timeout=overall_timeout)
    except asyncio.TimeoutError:
        timed_out = True
        for event in cancel_events:
            event.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        rows = await gathered
    except asyncio.CancelledError:
        # A disconnected request cancels this coroutine, but the shielded
        # gather would otherwise leave worker tasks and threadpool docking
        # calls running without their cancellation events being set.
        for event in cancel_events:
            event.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await gathered
        raise

    results = []
    for index, (job, row) in enumerate(zip(jobs, rows), 1):
        if isinstance(row, dict):
            if timed_out and row.get("status") not in {"completed", "timed_out"}:
                row = _batch_result(index, job, row, status="timed_out",
                                    error="Batch docking exceeded its overall time limit.")
            results.append(row)
        else:
            results.append(_batch_result(
                index, job, {}, status="timed_out" if timed_out else "failed",
                error="Batch docking was cancelled before this item completed."
                if timed_out else "Batch docking item failed.",
            ))

    completed = sum(row["status"] == "completed" for row in results)
    failed = len(results) - completed
    status = "completed" if completed == len(results) else "failed" if completed == 0 else "partial"
    return {
        "status": status,
        "timed_out": timed_out,
        "total": len(results),
        "completed": completed,
        "failed": failed,
        "results": results,
    }


def _validate_job_id(job_id: str) -> None:
    if not _SAFE_JOB_ID.fullmatch(job_id or ""):
        raise HTTPException(status_code=400, detail="Invalid job_id")


def _session_id(request: Request) -> str:
    session_id = request.scope.get("agent_session_id")
    if type(session_id) is not str or not session_id.strip():
        raise HTTPException(status_code=401, detail="Browser session required")
    return session_id


def _owned_history(request: Request, work_dir: str, job_id: str) -> dict:
    from src.docking.history_index import get_history_record

    record = get_history_record(work_dir, job_id, owner_session_id=_session_id(request))
    if record is None:
        raise HTTPException(status_code=404, detail="对接任务不存在")
    return record


def _clear_owned_history_records(work_dir: str, owner_session_id: str) -> int:
    """Remove every history page owned by one browser session."""
    import shutil

    from src.docking.history_index import read_history_page, remove_history_record

    deleted = 0
    while True:
        history, _, _, _ = read_history_page(
            work_dir, page=1, limit=200, owner_session_id=owner_session_id,
        )
        if not history:
            return deleted
        for record in history:
            job_id = str(record.get("job_id") or "")
            _validate_job_id(job_id)
            job_dir = os.path.join(work_dir, f"docking_{job_id}")
            if os.path.isdir(job_dir):
                shutil.rmtree(job_dir)
            remove_history_record(work_dir, job_id)
            deleted += 1


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
        request: Request,
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
        # Authenticate before resolving the runtime or touching any backend.
        # This keeps unavailable-runtime responses from becoming an
        # unauthenticated probe and gives durable docking the same ownership
        # boundary as the legacy endpoints.
        owner_session_id = _session_id(request)
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
            center_x=center_x,
            center_y=center_y,
            center_z=center_z,
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
                owner_session_id=owner_session_id,
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
        request: Request,
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
        owner_session_id = _session_id(request)
        if not docking_service:
            raise HTTPException(status_code=503, detail="分子对接服务不可用")

        temp_paths: List[str] = []
        try:
            clean_smiles = (smiles or "").strip() or None
            if ligand_file is None and clean_smiles is None:
                raise HTTPException(status_code=400, detail="请提供配体文件或SMILES字符串")
            if ligand_file is not None and clean_smiles is not None:
                raise HTTPException(
                    status_code=400,
                    detail="Provide either a ligand file or SMILES, not both",
                )
            _support._validate_docking_limits(
                center_x=center_x,
                center_y=center_y,
                center_z=center_z,
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
            if clean_smiles is not None:
                result = await _support._invoke_in_threadpool(
                    docking_service.perform_docking,
                    receptor_file=protein_temp.name,
                    ligand_input=clean_smiles,
                    config=config,
                    input_type="smiles",
                    owner_session_id=owner_session_id,
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
                    input_type="file",
                    owner_session_id=owner_session_id,
                )

            result = dict(result)
            result["warnings"] = _support._normalize_warning_strings(result.get("warnings"))
            return result

        except HTTPException:
            raise
        except Exception:
            _support.logger.exception("分子对接任务提交失败")
            return api_error("DOCKING_EXECUTION_FAILED", "对接计算失败", status_code=500)
        finally:
            for path in temp_paths:
                try:
                    if _support.os.path.exists(path):
                        _support.os.unlink(path)
                except Exception:
                    pass

    @app.get("/api/docking/env_check")
    async def docking_env_check(request: Request):
        """返回分子对接环境详细诊断"""
        _session_id(request)
        try:
            if not docking_service:
                raise HTTPException(status_code=503, detail="分子对接服务不可用")
            info = docking_service.env_diagnostics()
            return {"success": info.get("ok", False), "diagnostics": info}
        except Exception:
            _support.logger.exception("环境自检失败")
            return api_error("DOCKING_ENVIRONMENT_CHECK_FAILED", "环境自检失败", status_code=500)

    @app.post("/api/docking/batch_submit")
    async def submit_batch_docking_job(
        request: Request,
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
        owner_session_id = _session_id(request)
        if not docking_service:
            raise HTTPException(status_code=503, detail="分子对接服务不可用")

        ligand_files = ligand_files or []
        smiles_rows = [
            line.strip()
            for line in (batch_smiles or "").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

        if not ligand_files and not smiles_rows:
            raise HTTPException(status_code=400, detail="请提供至少一个配体文件或一行 SMILES")
        if ligand_files and smiles_rows:
            raise HTTPException(
                status_code=400,
                detail="Provide either ligand files or batch SMILES, not both",
            )
        max_batch_ligands = _support._get_int_env("MEDCHAT_DOCKING_MAX_BATCH_LIGANDS", 100)
        if len(ligand_files) + len(smiles_rows) > max_batch_ligands:
            raise HTTPException(
                status_code=413,
                detail=f"Batch docking accepts at most {max_batch_ligands} ligands",
            )
        _support._validate_docking_limits(
            center_x=center_x,
            center_y=center_y,
            center_z=center_z,
            size_x=size_x,
            size_y=size_y,
            size_z=size_z,
            exhaustiveness=exhaustiveness,
            num_modes=num_modes,
            energy_range=energy_range,
        )

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

            batch = await _run_batch_docking_jobs(
                _support=_support,
                docking_service=docking_service,
                jobs=jobs,
                receptor_file=protein_temp.name,
                base_config=base_config,
                owner_session_id=owner_session_id,
            )
            batch_results = batch["results"]
            completed = batch["completed"]
            task_result = {
                # A partial batch is not a successful task even when some
                # ligands completed. Keep the per-item results for diagnosis.
                "success": batch["status"] == "completed",
                "status": batch["status"],
                "timed_out": batch["timed_out"],
                "total": len(batch_results),
                "completed": completed,
                "failed": len(batch_results) - completed,
                "results": batch_results,
            }
            from src.task_runtime.legacy_bridge import persist_legacy_terminal_task
            from src.task_runtime.manager import get_task_manager

            receipt = persist_legacy_terminal_task(
                get_task_manager(),
                task_type="docking_batch",
                owner_session_id=owner_session_id,
                result=task_result,
            )
            task_result["task_id"] = receipt.task_id
            task_result["batch_job_id"] = receipt.task_id
            return task_result

        except HTTPException:
            raise
        except Exception:
            _support.logger.exception("批量分子对接任务提交失败")
            return api_error("DOCKING_BATCH_FAILED", "批量对接失败", status_code=500)
        finally:
            for path in temp_paths:
                try:
                    if _support.os.path.exists(path):
                        _support.os.unlink(path)
                except Exception:
                    pass

    @app.get("/api/docking/status")
    async def get_docking_status(request: Request):
        """获取分子对接服务状态"""
        _session_id(request)
        if not docking_service:
            return {"status": "unavailable", "message": "分子对接服务未初始化"}

        try:
            env_ok = docking_service.verify_environment()
            return {
                "status": "available" if env_ok else "error",
                "environment_check": env_ok,
                "message": "服务正常" if env_ok else "环境配置有问题"
            }
        except Exception:
            _support.logger.exception("对接服务状态检查失败")
            return {
                "status": "error",
                "message": "对接服务状态检查失败",
                "error_code": "DOCKING_STATUS_FAILED",
            }

    @app.get("/api/docking/result/{job_id}")
    async def get_docking_result(job_id: str, request: Request):
        """获取分子对接结果文件"""
        if not docking_service:
            raise HTTPException(status_code=503, detail="分子对接服务不可用")
        _validate_job_id(job_id)
        _owned_history(request, docking_service.work_dir, job_id)

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
        except Exception:
            _support.logger.exception("获取对接结果失败")
            return api_error("DOCKING_RESULT_READ_FAILED", "获取结果失败", status_code=500)

    @app.get("/api/docking/interactions/{job_id}")
    async def get_docking_interactions(job_id: str, request: Request, pose: int = 1):
        """Return backend-derived interactions for an explicitly prepared pose.

        The endpoint intentionally does not analyze ``result.pdbqt`` directly:
        PDBQT does not reliably carry the bond orders and explicit hydrogens
        required for defensible interaction assignment.
        """
        _validate_job_id(job_id)
        work_dir = docking_service.work_dir if docking_service else _support.os.path.join(
            _support.os.getcwd(), "temp_docking"
        )
        _owned_history(request, work_dir, job_id)
        if type(pose) is not int or pose <= 0:
            raise HTTPException(status_code=422, detail="pose 必须是正整数")

        job_dir = _support.os.path.join(work_dir, f"docking_{job_id}")
        if not _support.os.path.isdir(job_dir):
            raise HTTPException(status_code=404, detail="对接任务不存在")

        from src.docking.interaction_analysis import (
            analyze_docking_interactions,
            resolve_analysis_inputs,
        )

        receptor_path, ligand_path = resolve_analysis_inputs(job_dir, pose)
        pose_export_error = None
        if receptor_path is not None and ligand_path is None:
            result_file = _support.os.path.join(job_dir, "result.pdbqt")
            if _support.os.path.isfile(result_file):
                temporary = None
                try:
                    from src.docking.pose_export import pose_sdf_from_pdbqt

                    with open(result_file, "r", encoding="utf-8", errors="ignore") as stream:
                        pose_text = stream.read()
                    pose_sdf = pose_sdf_from_pdbqt(
                        pose_text,
                        pose,
                        keep_hydrogens=True,
                    )
                    ligand_candidate = _support.os.path.join(
                        job_dir,
                        f"analysis_pose_{pose}.sdf",
                    )
                    temporary = f"{ligand_candidate}.tmp"
                    with open(temporary, "w", encoding="utf-8", newline="\n") as stream:
                        stream.write(pose_sdf)
                        stream.flush()
                        _support.os.fsync(stream.fileno())
                    _support.os.replace(temporary, ligand_candidate)
                    _, ligand_path = resolve_analysis_inputs(job_dir, pose)
                except Exception as error:
                    pose_export_error = type(error).__name__
                    if temporary and _support.os.path.exists(temporary):
                        try:
                            _support.os.unlink(temporary)
                        except OSError:
                            pass
                    _support.logger.warning(
                        "Unable to create topology-bearing pose artifact for interaction analysis: %s",
                        type(error).__name__,
                    )
        if receptor_path is None or ligand_path is None:
            if pose_export_error:
                return {
                    "status": "failed",
                    "reason_code": "pose_export_failed",
                    "pose": pose,
                    "interactions": [],
                    "warnings": [f"pose_export_failed:{pose_export_error}"],
                    "provenance": {"analyzer": "prolif", "tool_derived": False},
                }
            return {
                "status": "unavailable",
                "reason_code": "analysis_input_missing",
                "pose": pose,
                "interactions": [],
                "warnings": [
                    "explicit_analysis_receptor_and_pose_sdf_are_required",
                    "pdbqt_was_not_used_as_a_topology_fallback",
                ],
                "provenance": {"analyzer": "prolif", "tool_derived": False},
            }
        result = analyze_docking_interactions(
            receptor_path,
            ligand_path,
            pose_index=1,
        )
        result["pose"] = pose
        return result

    @app.get("/api/docking/pose_sdf/{job_id}")
    async def get_docking_pose_sdf(job_id: str, request: Request, pose: int = 1):
        """将指定 pose 重建为标准 SDF，保留原始化学拓扑并应用对接坐标"""
        if not docking_service:
            raise HTTPException(status_code=503, detail="分子对接服务不可用")
        _validate_job_id(job_id)
        _owned_history(request, docking_service.work_dir, job_id)
        if type(pose) is not int or pose <= 0:
            raise HTTPException(status_code=422, detail="pose 必须是正整数")

        try:
            job_dir = _support.os.path.join(docking_service.work_dir, f"docking_{job_id}")
            result_file = _support.os.path.join(job_dir, "result.pdbqt")

            if not _support.os.path.exists(result_file):
                raise HTTPException(status_code=404, detail="对接结果文件不存在")

            with open(result_file, "r", encoding="utf-8", errors="ignore") as f:
                pdbqt_text = f.read()
            from src.docking.pose_export import PoseExportError, pose_sdf_from_pdbqt

            try:
                sdf_block = pose_sdf_from_pdbqt(pdbqt_text, pose)
            except PoseExportError as exc:
                raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

            return Response(
                content=sdf_block,
                media_type="chemical/x-mdl-sdfile",
                headers={"Content-Disposition": f"attachment; filename=docking_pose_{job_id}_{pose}.sdf"},
            )

        except HTTPException:
            raise
        except Exception:
            _support.logger.exception("重建 pose SDF 失败")
            return api_error("DOCKING_POSE_EXPORT_FAILED", "重建 pose SDF 失败", status_code=500)

    @app.get("/api/docking/history")
    async def get_docking_history(request: Request, page: int = 1, limit: int = 50):
        """获取对接历史记录列表"""
        owner = _session_id(request)
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
                owner_session_id=owner,
            )
            return {
                "success": True,
                "history": history,
                "total": total,
                "page": resolved_page,
                "limit": resolved_limit,
            }

        except Exception:
            _support.logger.exception("获取对接历史失败")
            return api_error("DOCKING_HISTORY_READ_FAILED", "获取历史失败", status_code=500)

    @app.delete("/api/docking/history")
    async def clear_docking_history(request: Request):
        """清除所有对接历史记录"""
        owner = _session_id(request)
        try:
            work_dir = docking_service.work_dir if docking_service else _support.os.path.join(_support.os.getcwd(), "temp_docking")
            if not _support.os.path.isdir(work_dir):
                return {"success": True, "message": "无历史记录", "deleted": 0}

            deleted = _clear_owned_history_records(work_dir, owner)

            return {"success": True, "message": f"已清除 {deleted} 条历史记录", "deleted": deleted}
        except Exception:
            _support.logger.exception("清除对接历史失败")
            return api_error("DOCKING_HISTORY_CLEAR_FAILED", "清除历史失败", status_code=500)

    @app.delete("/api/docking/history/{job_id}")
    async def delete_docking_job(job_id: str, request: Request):
        """删除指定的对接历史记录"""
        try:
            import shutil as _shutil
            from src.docking.history_index import remove_history_record

            _validate_job_id(job_id)
            work_dir = docking_service.work_dir if docking_service else _support.os.path.join(_support.os.getcwd(), "temp_docking")
            _owned_history(request, work_dir, job_id)
            job_dir = _support.os.path.join(work_dir, f"docking_{job_id}")
            if not _support.os.path.isdir(job_dir):
                raise HTTPException(status_code=404, detail="记录不存在")
            _shutil.rmtree(job_dir)
            remove_history_record(work_dir, job_id)
            return {"success": True, "message": f"已删除任务 {job_id}"}
        except HTTPException:
            raise
        except Exception:
            _support.logger.exception("删除对接记录失败")
            return api_error("DOCKING_HISTORY_DELETE_FAILED", "删除对接记录失败", status_code=500)
