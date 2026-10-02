"""Activity prediction route registration."""
import csv
import io
from pathlib import Path
from typing import Optional
from fastapi import UploadFile, File, Form, HTTPException


_SMILES_COLUMN_ALIASES = {"smiles", "smile", "canonical_smiles", "structure"}


def _parse_batch_smiles(content: str, *, filename: str, smiles_column: str | None = None) -> list[str]:
    """Parse one SMILES per text row or a headered CSV without shifting columns."""
    suffix = Path(filename or "").suffix.casefold()
    if suffix in {".csv", ".tsv"}:
        sample = content[:8192]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
        except csv.Error:
            dialect = csv.excel_tab if suffix == ".tsv" else csv.excel
        reader = csv.DictReader(io.StringIO(content), dialect=dialect)
        headers = [str(header).strip() for header in (reader.fieldnames or []) if header is not None]
        if not headers:
            raise ValueError("CSV 缺少表头，无法定位 SMILES 列")
        requested = str(smiles_column or "").strip()
        if requested:
            matches = [header for header in headers if header.casefold() == requested.casefold()]
        else:
            matches = [header for header in headers if header.casefold() in _SMILES_COLUMN_ALIASES]
        if len(matches) != 1:
            raise ValueError("CSV 必须明确且唯一地提供 SMILES 列")
        selected = matches[0]
        values: list[str] = []
        for row in reader:
            value = row.get(selected)
            values.append("" if value is None else str(value).strip())
        return values

    if suffix not in {"", ".txt", ".smi", ".smiles"}:
        raise ValueError("仅支持 CSV、TSV 或每行一个 SMILES 的文本文件")
    # Text input has historically allowed an optional name/comment after the
    # first token; keep that compatibility while CSV/TSV uses the named field
    # above and never treats its header as a molecule.
    values: list[str] = []
    for line in content.splitlines():
        parts = line.strip().replace(",", " ").split()
        if parts:
            values.append(parts[0])
    return values


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
        smiles_column: Optional[str] = Form(None),
    ):
        """活性批量预测 API"""
        try:
            content = await _support._read_upload_limited(file, "activity batch file")
            text = content.decode("utf-8-sig")
            smiles_list = _parse_batch_smiles(
                text,
                filename=file.filename or "",
                smiles_column=smiles_column,
            )
            
            def run_activity_batch_prediction():
                from src.activity.prediction_service import predict_activity
                return predict_activity(smiles_list, target=target)

            return await _support._invoke_in_threadpool(run_activity_batch_prediction)
        except HTTPException:
            raise
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="批量文件必须是 UTF-8 文本") from None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except Exception:
            _support.logger.error("批量活性预测请求失败")
            raise HTTPException(status_code=500, detail="批量活性预测服务不可用")
