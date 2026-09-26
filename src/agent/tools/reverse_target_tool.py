"""Lazy strict reverse-target consumer; legacy predictor lists are uncertified."""
import threading
import time
from typing import Any, Dict, Mapping

from .base_tool import BaseMolecularTool
from .molecular_input import MolecularInputUnavailable, parse_molecular_smiles
from src.reverse_target.receipt import (
    CONTROLS, REVISION, SOURCE, normalize_target_record, validate_prediction_observation,
)


class _Unavailable(RuntimeError):
    pass


class ReverseTargetTool(BaseMolecularTool):
    """反向寻靶预测工具：输入 SMILES → 输出预测的潜在靶点列表。"""

    def __init__(self, predictor=None):
        super().__init__(
            name="reverse_target_predictor",
            description="基于 ChEMBL 数据库的 Morgan/MACCS 指纹相似度，反向预测输入分子可能结合的靶点。输入：包含 SMILES 的查询文本。输出：预测靶点列表。",
        )
        self._predictor = predictor  # Constructor and historical private injections are borrowed.
        self._state_lock = threading.Lock()
        self._loading = False
        self._closed = False
        self._owned = None
        self._candidate = None

    def _is_closed(self):
        with self._state_lock:
            return self._closed

    @staticmethod
    def _close_owned(predictor):
        if predictor is not None:
            try:
                predictor.close_strict()
            except Exception:
                # No private source diagnostics or paths escape the boundary.
                pass

    def close(self):
        # Producer callbacks may take the tool lock under their own lock.
        # Capture references here, but NEVER call the producer under this lock.
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            owned, candidate = self._owned, self._candidate
            self._owned = self._candidate = None
            if self._predictor is owned:
                self._predictor = None
        self._close_owned(owned)
        if candidate is not owned:
            self._close_owned(candidate)

    def _get_predictor(self, *, timeout_seconds=180, cancelled=None):
        deadline = time.monotonic() + timeout_seconds
        with self._state_lock:
            if self._closed or self._loading:
                raise _Unavailable()
            if self._predictor is not None:
                return self._predictor
            self._loading = True
        candidate = None
        published = False
        try:
            from src.reverse_target.config import get_reverse_target_data_dir
            from src.reverse_target.predictor import ReverseTargetPredictor
            candidate = ReverseTargetPredictor(get_reverse_target_data_dir())
            with self._state_lock:
                if self._closed:
                    raise _Unavailable()
                self._candidate = candidate
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            candidate.initialize_strict(timeout_seconds=min(120, remaining), cancelled=cancelled)
            if time.monotonic() >= deadline:
                raise TimeoutError()
            with self._state_lock:
                if self._closed or self._candidate is not candidate or self._predictor is not None:
                    raise _Unavailable()
                self._predictor = self._owned = candidate
                self._candidate = None
                published = True
            return candidate
        finally:
            if not published:
                self._close_owned(candidate)
            with self._state_lock:
                if self._candidate is candidate:
                    self._candidate = None
                self._loading = False

    def should_use(self, query: str) -> bool:
        keywords = ['靶点', '寻靶', '反向寻靶', '作用靶点', '适应症', '作用机制',
                    '治什么病', 'target prediction', 'reverse target', '潜在靶点', '候选靶点']
        try:
            return any(kw in query.lower() for kw in keywords) and bool(parse_molecular_smiles(query, self))
        except ValueError:
            return False

    @staticmethod
    def _normalize_target_record(record: Mapping[str, Any]) -> Dict[str, Any]:
        return normalize_target_record(record)

    @staticmethod
    def _failure(result, code, message):
        result.update(success=False, data=None, formatted='', message=message,
                      error={'code': code, 'message': message}, quality={'retryable': False})
        return result

    def execute(self, query: str) -> Dict[str, Any]:
        deadline = time.monotonic() + 180
        result = self._create_base_result(query)

        def remaining():
            if self._is_closed():
                raise _Unavailable()
            credit = deadline - time.monotonic()
            if credit <= 0:
                raise TimeoutError()
            return credit

        # Request-local callback: never persisted with a source or observation.
        cancelled = lambda: self._is_closed()
        phase = 'source'
        try:
            remaining()
            if not self._check_rdkit(result):
                return self._failure(result, 'tool_unavailable', 'RDKit SMILES 校验暂不可用，未执行反向寻靶。')
            try:
                smiles = parse_molecular_smiles(query, self)[0]
            except MolecularInputUnavailable:
                return self._failure(result, 'tool_unavailable', 'SMILES 校验暂不可用，未执行反向寻靶。')
            except ValueError:
                return self._failure(result, 'validation_error', 'SMILES 无效或缺失；请提供完整结构，未提取有效片段替代。')

            predictor = self._get_predictor(timeout_seconds=remaining(), cancelled=cancelled)
            from src.reverse_target.owned_source import ReverseSourceUnavailable, validate_envelope
            capture = getattr(predictor, 'capture_prediction_source', None)
            predict = getattr(predictor, 'predict_with_receipt', None)
            postflight = getattr(predictor, 'validate_prediction_source', None)
            if not all(callable(method) for method in (capture, predict, postflight)):
                raise _Unavailable()
            remaining()
            expected = capture()
            envelope = predict(smiles, **CONTROLS, timeout_seconds=min(300, remaining()), cancelled=cancelled)
            remaining()
            phase = 'proof'
            envelope = validate_envelope(envelope, smiles=smiles, controls=CONTROLS, expected=expected)
            entry = dict(source=SOURCE, normalization_revision=REVISION, input_smiles=smiles,
                         record_count=len(envelope['records']), records=envelope['records'],
                         prediction_receipt=envelope['receipt'])
            remaining()
            targets = [self._normalize_target_record(row) for row in entry['records']]
            validate_prediction_observation(targets, [entry], smiles=smiles)
            if targets:
                lines = ['## 🎯 反向寻靶预测结果', '', f'**查询分子**: `{smiles}`',
                         f'**匹配靶点数**: {len(targets)}', '**默认相似度阈值**: 0.6', '',
                         '| 排名 | 靶点名称 | 物种 | 综合相似度 | Morgan | MACCS | 匹配分子数 |',
                         '|------|----------|------|-----------|--------|-------|-----------|']
                for i, row in enumerate(targets, 1):
                    lines.append(f"| {i} | {row['target_name']} | {row['organism']} | "
                                 f"{row['final_similarity']:.3f} | {row['morgan_similarity']:.3f} | "
                                 f"{row['maccs_similarity']:.3f} | {row['similar_count']} |")
                formatted = '\n'.join(lines)
                message = f'成功预测 {len(targets)} 个潜在靶点'
            else:
                formatted = f'## 🎯 反向寻靶结果\n\n查询分子: `{smiles}`\n\n未找到相似度 ≥ 0.6 的已知靶点匹配。可尝试降低相似度阈值。'
                message = '未找到满足阈值的靶点匹配'
            remaining()
            try:
                postflight(envelope, smiles=smiles, expected=expected, **CONTROLS)
            except ReverseSourceUnavailable:
                raise _Unavailable() from None
            remaining()
            # Atomic tool-state publication, with no producer call under lock.
            with self._state_lock:
                if self._closed:
                    raise _Unavailable()
                result.update(success=True, status='succeeded', data=targets, formatted=formatted,
                              message=message, evidence=[entry], quality={
                                  'tool_name': self.name, 'prediction_status': envelope['receipt']['status']})
            return result
        except TimeoutError:
            return self._failure(result, 'tool_timeout', '反向寻靶超时，未返回预测结果。')
        except _Unavailable:
            return self._failure(result, 'tool_unavailable', '反向寻靶暂不可用，未返回预测结果。')
        except ValueError:
            code = 'invalid_output' if phase == 'proof' else 'tool_unavailable'
            return self._failure(result, code, '反向寻靶验证失败，未返回预测结果。')
        except Exception:
            return self._failure(result, 'tool_unavailable', '反向寻靶暂不可用，未返回预测结果。')
