"""Activity model route registration."""
from typing import Dict, Any
from fastapi import UploadFile, File, Form, Body, HTTPException


def setup_activity_model_routes(app, *, _support):
    """Register the original endpoints with dynamically resolved compatibility support."""
    @app.post("/api/activity/train")
    async def start_activity_training(
        file: UploadFile           = File(...),
        target_column: str         = Form(...),
        task_type: str             = Form("regression"),
        epochs: int                = Form(50, ge=1, le=1000),
        learning_rate: float       = Form(0.001, gt=0, le=1),
        batch_size: int            = Form(32, ge=1, le=4096),
        dropout: float             = Form(0.2, ge=0, le=0.9),
        num_layers: int            = Form(5, ge=1, le=32),
        hidden_size: int           = Form(256, ge=8, le=4096),
        weight_decay: float        = Form(0.0001, ge=0, le=1),
        patience: int              = Form(12, ge=1, le=1000),
        loss_metric: str           = Form("MSE"),
        lr_scheduler: str          = Form("Cosine"),
        split_strategy: str        = Form("scaffold"),
        random_seed: int           = Form(42, ge=0, le=2147483647)
    ):
        """提交活性预测模型训练任务"""
        temp_file = None
        temp_path = None
        retain_temp_file = False
        try:
            split_strategy = split_strategy.strip().lower()
            if split_strategy not in {"scaffold", "random"}:
                raise HTTPException(
                    status_code=400,
                    detail="split_strategy must be 'scaffold' or 'random'",
                )
            content = await _support._read_upload_limited(file, "activity training dataset")

            # 1. 保存上传的数据集
            from src.activity.trainer import submit_training_job

            ext = _support.os.path.splitext(file.filename)[1] or ".csv"
            temp_file = _support.tempfile.NamedTemporaryFile(delete=False, suffix=ext)
            temp_path = temp_file.name
            try:
                temp_file.write(content)
            finally:
                temp_file.close()

            # 2. 启动后台训练
            job_id = submit_training_job(
                file_path=temp_path,
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
                lr_scheduler=lr_scheduler,
                split_strategy=split_strategy,
                random_seed=random_seed,
            )
            retain_temp_file = True

            return {"success": True, "job_id": job_id, "message": "训练任务已启动"}
        except HTTPException:
            raise
        except Exception as e:
            _support.logger.error(f"启动训练失败: {e}")
            raise HTTPException(status_code=500, detail=str(e))
        finally:
            if temp_file is not None:
                try:
                    temp_file.close()
                except Exception:
                    pass
            if temp_path and not retain_temp_file:
                try:
                    if _support.os.path.exists(temp_path):
                        _support.os.unlink(temp_path)
                except Exception:
                    pass

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
        from src.activity.trainer import list_available_models, get_current_model_id

        models = list_available_models()
        return {
            "success": True,
            "models": models,
            "current_model": get_current_model_id(),
        }

    @app.post("/api/activity/models/switch")
    async def switch_activity_model(data: Dict[str, Any] = Body(...)):
        """切换当前使用的活性预测模型权重"""
        if set(data) != {"model_id"} or not isinstance(data.get("model_id"), str):
            raise HTTPException(status_code=400, detail="仅接受 model_id 参数")

        model_id = data["model_id"]
        try:
            from src.activity.trainer import set_active_model

            set_active_model(model_id)

            # Force reload in predictor
            from src.activity.predictor import get_predictor

            predictor = get_predictor()
            if hasattr(predictor, "invalidate"):
                predictor.invalidate()
            else:
                predictor._loaded = False  # compatibility for injected predictors

            return {"success": True, "message": f"已切换至模型 {model_id}"}
        except ValueError:
            raise HTTPException(status_code=400, detail="model_id 无效或模型未注册")
        except HTTPException:
            raise
        except Exception:
            _support.logger.exception("切换活性模型失败")
            raise HTTPException(status_code=500, detail="切换活性模型失败")

    @app.delete("/api/activity/models/{model_id}")
    async def remove_activity_model(model_id: str):
        """物理删除一个活性预测模型"""
        try:
            from src.activity.trainer import delete_model
            delete_model(model_id)

            from src.activity.predictor import get_predictor

            predictor = get_predictor()
            if hasattr(predictor, "invalidate"):
                predictor.invalidate()
            else:
                predictor._loaded = False  # compatibility for injected predictors
            return {"success": True, "message": f"模型 {model_id} 已删除"}
        except ValueError:
            raise HTTPException(status_code=404, detail="模型未注册或记录无效")
        except Exception:
            _support.logger.exception("删除活性模型失败")
            raise HTTPException(status_code=500, detail="删除活性模型失败")
