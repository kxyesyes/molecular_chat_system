"""Activity prediction route registration."""
from typing import Optional
from fastapi import UploadFile, File, Form, HTTPException


def setup_activity_prediction_routes(app, *, _support):
    """Register the original endpoints with dynamically resolved compatibility support."""
    @app.post("/api/activity/predict")
    async def activity_predict(
        smiles: str = Form(...),
        target: Optional[str] = Form(None),
    ):
        """活性预测 API"""
        try:
            def run_activity_prediction():
                from src.activity.prediction_service import predict_activity
                return predict_activity(smiles, target=target)

            return await _support._invoke_in_threadpool(run_activity_prediction)
        except HTTPException:
            raise
        except Exception:
            _support.logger.error("活性预测请求失败")
            raise HTTPException(status_code=500, detail="活性预测服务不可用")

    @app.post("/api/activity/batch_predict")
    async def activity_batch_predict(
        file: UploadFile = File(...),
        target: Optional[str] = Form(None),
    ):
        """活性批量预测 API"""
        try:
            content = await _support._read_upload_limited(file, "activity batch file")
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
            
            def run_activity_batch_prediction():
                from src.activity.prediction_service import predict_activity
                return predict_activity(smiles_list, target=target)

            return await _support._invoke_in_threadpool(run_activity_batch_prediction)
        except HTTPException:
            raise
        except Exception:
            _support.logger.error("批量活性预测请求失败")
            raise HTTPException(status_code=500, detail="批量活性预测服务不可用")
