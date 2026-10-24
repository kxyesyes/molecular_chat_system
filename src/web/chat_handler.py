"""
聊天处理模块 - WebSocket 消息处理（性能优化版）
"""
import json
import asyncio
import logging
import math
import re
import time
from collections.abc import Mapping
from typing import List, Dict, Any
from fastapi import WebSocket

from src.agent.persistence.redaction import (
    contains_secret_material,
    redact_sensitive,
    sanitize_bounded,
)
from src.agent.contracts import (
    AgentResult, CandidateSet, ObservationStatus, RunOutcome,
    AgentErrorCode, AgentExecutionError,
)
from src.agent.contracts.generation_request import has_generation_intent
from src.agent.routing.hybrid import SMILES_PATTERN
from src.agent.runtime.allocation import (
    AllocationScope, AllocationStopped,
    DEFAULT_ALLOCATION_TIMEOUT_SECONDS, DEFAULT_ALLOCATION_PROGRESS_SECONDS,
    current_allocation, finish_allocation,
)
from src.web.prompt_budget import character_limit
from src.web import agent_result_presentation as result_presentation
from src.web import chat_prompt_builder as prompt_builder
from src.web.agent_result_presentation import (
    _AGENT_FAILURE_FALLBACK,
    _AGENT_FAILURE_CONTENT_MAX_CHARS,
    _AGENT_FAILURE_WARNING_MAX_CHARS,
    _AGENT_FAILURE_WARNING_LIMIT,
)
from src.web.model_lifecycle import finish_on_cancel
from src.web.rag_presentation import format_rag_context, rag_info_molecule

logger = logging.getLogger(__name__)

_AGENT_EVENT_TEXT_MAX_CHARS = 512
_AGENT_EVENT_KEY_MAX_CHARS = 128
_AGENT_EVENT_MAX_DEPTH = 6
_AGENT_EVENT_MAX_ITEMS = 64
MAX_RETAINED_ALLOCATIONS = 8
_AGENT_FAILURE_EVENT_TYPES = frozenset({
    "tool_failed",
    "task_failed",
    "task_rejected",
    "task_cancelled",
})


class _AllocationSocket:
    """Only this request may publish; late work cannot overwrite its terminal."""
    def __init__(self, websocket, scope):
        self.websocket, self.allocation = websocket, scope
        self._pending_terminal_event = None

    def __getattr__(self, name):
        return getattr(self.websocket, name)

    async def send_text(self, payload):
        self.allocation.check()
        frame = json.loads(payload)
        terminal_event = (frame.get("type") == "agent_event"
            and isinstance(frame.get("event"), dict)
            and frame["event"].get("event") in {
                "task_completed", "task_partial", "task_failed", "task_rejected", "task_cancelled"})
        if frame.get("type") in {"agent_result", "complete"}:
            # The first result/completion atomically wins completion or expiry.
            # Parsing/checking earlier is not a grant to publish past deadline;
            # subsequent frames cannot turn an accepted result into a timeout.
            self.allocation.finish()
        self.allocation.check()
        if terminal_event and self.allocation.phase == "allocating":
            # Session emits its terminal event before the final durable write.
            # Retain only the latest correction; it cannot stop the timer yet.
            self._pending_terminal_event = payload
            return
        if (frame.get("type") in {"agent_result", "complete"}
                and self._pending_terminal_event is not None):
            pending, self._pending_terminal_event = self._pending_terminal_event, None
            await self.websocket.send_text(pending)
        await self.websocket.send_text(payload)


class ChatHandler:
    """聊天消息处理器"""
    
    def __init__(self, model, rag_service, agent_system, config, refresh_model_config=None,
                 scientific_references=None):
        self.model = model
        self.rag_service = rag_service
        self.agent_system = agent_system
        self.config = config
        self.conversation_history = []
        self.refresh_model_config = refresh_model_config
        self.scientific_references = scientific_references
        self.decision_runtime = None
        self.docking_controller = None
        self._allocation_tasks = set()

    def _ranked_reverse_evidence(self):
        """Use the current registered tool, never instantiate another producer."""
        from .reverse_target_followup import RankedReverseEvidence
        agent = self.agent_system
        store = getattr(agent, 'state_store', None)
        registry = getattr(agent, 'tool_registry', None)
        tools = registry.as_mapping() if registry is not None else getattr(agent, 'tools', {})
        if self.decision_runtime is not None:
            store = self.decision_runtime.store
            tools = self.decision_runtime.registry.as_mapping()
        tool = tools.get('reverse_target_predictor') if isinstance(tools, Mapping) else None
        tool = getattr(tool, 'tool', tool)
        if store is not None and callable(getattr(tool, 'validate_current_observation', None)):
            return RankedReverseEvidence(store, tool)
        return None

    @staticmethod
    def _reverse_display_count(message):
        counts = list(re.finditer(r'前\s*([0-9一二三四五六七八九十百]+)|\btop\s*(five|[0-9]+)\b', message, re.I))
        if len(counts) != 1 or ''.join(value or '' for value in counts[0].groups()).casefold() not in {'5', '五', 'five'}:
            return 10
        # Only a negation governing the count phrase can veto its display cap.
        # "not docking" in another clause is not a negated ranking request.
        prefix = re.split(r'[，,;；。.!?！？\n]|\b(?:and|but|then)\b|并且|然后|但是?',
                          message[:counts[0].start()], flags=re.I)[-1]
        negated = re.search(r"不要|不用|不是|而是|不只|不仅|别|勿|无需|\b(?:not|never|instead|don['’]t)\b", prefix, re.I)
        postfix_negated = re.match(
            r'\s*(?:个)?\s*(?:候选)?(?:靶点)?\s*(?:不要|不用|无需|别|勿)\s*(?:列出|展示|显示)|'
            r'\s*(?:targets?\s*)?(?:(?:are|is|should|must)\s*)?not\s+(?:be\s+)?(?:requested|wanted|shown|displayed)\b',
            message[counts[0].end():], re.I)
        return 10 if negated or postfix_negated else 5

    async def _with_allocation(self, websocket, operation, *, cancel_event=None,
                               timeout_result=None, handoff_timeout=False,
                               cancellation_delegated=None):
        settings = self.config.get("agent", {})

        def positive_setting(name, default):
            value = settings.get(name, default)
            return value if type(value) in (int, float) and math.isfinite(value) and value > 0 else default

        timeout = positive_setting("allocation_timeout_seconds", DEFAULT_ALLOCATION_TIMEOUT_SECONDS)
        interval = positive_setting("allocation_progress_seconds", DEFAULT_ALLOCATION_PROGRESS_SECONDS)
        scope = AllocationScope(timeout)
        sender = _AllocationSocket(websocket, scope)
        if len(self._allocation_tasks) >= MAX_RETAINED_ALLOCATIONS:
            message = "任务分配资源正在清理，请稍后重试。"
            result = None
            if timeout_result is not None:
                result = AgentResult(timeout_result.trace_id, False, message, final_answer=message,
                    outcome=RunOutcome.REJECTED, metadata={"stop_reason": "allocation_busy"})
                await websocket.send_text(json.dumps({**result.to_legacy_dict(),
                    "type": "agent_result", "trace_id": result.trace_id,
                    "final_answer": message}, ensure_ascii=False))
            await websocket.send_text(json.dumps({
                "type": "complete", "status": "rejected",
                "content": message,
                "error": {"code": "allocation_busy", "message": "任务分配资源已达到有界容量。"},
            }, ensure_ascii=False))
            return result
        with scope.bind():
            pending = operation(sender)
            try:
                task = asyncio.create_task(pending)
            except BaseException:
                pending.close()
                raise
        # Reserve before yielding: both legacy drains and native supervised
        # turns count against the same bounded allocation capacity.
        self._allocation_tasks.add(task)
        task.add_done_callback(self._allocation_tasks.discard)
        if handoff_timeout:
            # A caller-owned model gate retains the physical allocation task.
            self.model_request_gate.retain_task(task)
        next_progress = time.monotonic() + interval
        try:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    if cancellation_delegated is not None and cancellation_delegated():
                        # The native bridge owns post-dispatch cancellation,
                        # cleanup and its terminal. Do not race that delivery
                        # with a second cancellation from this admission timer.
                        scope.finish()
                    else:
                        raise asyncio.CancelledError()
                if scope.is_set():
                    if scope.reason != "timeout":
                        raise asyncio.CancelledError()
                    if timeout_result is not None:
                        await websocket.send_text(json.dumps({
                            **timeout_result.to_legacy_dict(), "type": "agent_result",
                            "trace_id": timeout_result.trace_id,
                            "final_answer": timeout_result.final_answer,
                        }, ensure_ascii=False))
                    await websocket.send_text(json.dumps({
                        "type": "complete", "status": "timeout",
                        "content": "任务分配超时，尚未启动科学工具；请稍后重试或拆分请求。",
                        "error": {"code": "allocation_timeout", "message": "工具执行前的任务分配超过时限。"},
                        **({"trace_id": timeout_result.trace_id} if timeout_result is not None else {}),
                    }, ensure_ascii=False))
                    return timeout_result
                if task.done():
                    return task.result()
                now = time.monotonic()
                if scope.phase == "allocating" and now >= next_progress:
                    try:
                        await sender.send_text(json.dumps({
                            "type": "agent_status", "stage": "allocation",
                            "message": "正在分配任务，尚未启动科学工具…",
                            "elapsed_seconds": round(now - scope.started_at, 1),
                        }, ensure_ascii=False))
                    except AllocationStopped:
                        continue  # Expiry won between the loop check and send.
                    next_progress = now + interval
                delay = min(0.05, max(0, next_progress - now), max(0, scope.deadline - now)) if scope.phase == "allocating" else 0.05
                await asyncio.wait({task}, timeout=delay)
        except BaseException:
            scope.cancel()
            raise
        finally:
            if not task.done():
                task.cancel()
            # Only legacy consumers own their lease inside this retained task.
            # Native callers still own an outer lease and must drain inline.
            if not (handoff_timeout and scope.reason == "timeout"):
                await finish_on_cancel(asyncio.gather(task, return_exceptions=True))

    @staticmethod
    def _validate_request_temperature(value: Any) -> float:
        """Validate the temperature once and reuse it for the whole turn."""
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 2:
            raise ValueError("temperature must be a finite number between 0 and 2")
        return float(value)

    @staticmethod
    async def _send_model_failure(websocket, error):
        messages = {
            "http_error": "Ollama 返回 HTTP 错误",
            "timeout": "Ollama 请求超时",
            "transport_error": "无法连接或读取 Ollama 服务",
            "invalid_response": "Ollama 返回无效响应",
            "client_error": "Ollama 客户端请求失败",
        }
        reason = error.reason if error.reason in messages else "client_error"
        message = messages[reason]
        if reason == "http_error" and error.http_status is not None:
            message = f"Ollama 返回 HTTP {error.http_status}"
        await websocket.send_text(json.dumps({
            "type": "complete",
            "status": "failed",
            "content": "模型服务暂时不可用，请稍后重试。",
            "error": {"code": f"ollama_{reason}", "message": message},
        }, ensure_ascii=False))

    async def process_decision_message(self, websocket, *, context, decision_loop,
                                       request_kind, allowed_tools, required_tools,
                                       requirements=None, continuation_id=None, clarified_query=None,
                                       worker_owner=None, cancel_event=None,
                                       admission_carry=None, admission_exchange=None,
                                       web_projection=None, reference_claim=None):
        """Server-only opt-in bridge; never dispatch browser kwargs into this API."""
        from .decision_chat import process_decision_message
        if (self.decision_runtime is not None and self.decision_runtime.semantic
                and (admission_carry is None or admission_exchange is None)):
            from fastapi import HTTPException
            raise HTTPException(status_code=503, detail='ordinary_semantic_not_assembled')
        async def process(sender):
            return await process_decision_message(
                self, sender, context=context, decision_loop=decision_loop,
                request_kind=request_kind, allowed_tools=allowed_tools, required_tools=required_tools,
                requirements=requirements, continuation_id=continuation_id, clarified_query=clarified_query,
                worker_owner=worker_owner,
                cancel_event=cancel_event,
                **({'web_projection': web_projection} if web_projection is not None else {}),
                **({'reference_claim': reference_claim} if reference_claim is not None else {}),
                **({'admission_carry': admission_carry, 'admission_exchange': admission_exchange}
                   if admission_carry is not None else {}),
            )
        if request_kind == "chat":
            # Native admission already owns a scope while waiting for refresh
            # and the model gate. Ordinary generation has its own deadlines.
            finish_allocation()
            return await process(websocket)
        if current_allocation() is not None:
            # Reuse the native turn's admission timer and capacity reservation.
            return await process(websocket)
        return await self._with_allocation(
            websocket, process, cancel_event=cancel_event,
            timeout_result=self._allocation_timeout_result(context.trace_id))

    @staticmethod
    def _allocation_timeout_result(trace_id):
        message = "任务分配超时，尚未启动科学工具；请稍后重试或拆分请求。"
        return AgentResult(
            trace_id, False, message, final_answer=message,
            outcome=RunOutcome.TIMEOUT,
            error=AgentExecutionError(AgentErrorCode.TOOL_TIMEOUT, message,
                                      details={"stage": "allocation", "reason": "allocation_timeout"}),
            metadata={"stop_reason": "allocation_timeout"},
        )
    
    async def handle_websocket(self, websocket: WebSocket):
        """Serve only the composed Pi Web runtime; never dispatch legacy chat."""
        if self.decision_runtime is not None:
            await self.decision_runtime.handle_websocket(handler=self, websocket=websocket)
            return
        await websocket.accept()
        try:
            await websocket.send_json({
                "type": "error",
                "code": "pi_runtime_unavailable",
                "message": "Pi chat runtime is unavailable. Please check server startup logs.",
            })
        finally:
            await websocket.close(code=1011)
    

    @staticmethod
    def _append_history(history, entry):
        """原位保留最近 20 条记录，不改变记录对象及其字段。"""
        history.append(entry)
        if len(history) > 20:
            del history[:-20]

    @staticmethod
    def _rag_info_payload(molecules, message):
        """复用共享 RAG 投影；展示条数和文案由调用方决定。"""
        return {
            "type": "rag_info",
            "molecules": [rag_info_molecule(mol) for mol in molecules],
            "message": message,
        }

    async def _send_agent_rag_results(self, websocket, agent_result, rag_count):
        """Project existing successful retrieval evidence without retrieving again."""
        warning = "部分检索卡片格式无效，无法展示；已保留可用记录，不代表重新执行了检索。"
        try:
            tool_results = agent_result.get("tool_results", {})
            if not isinstance(tool_results, Mapping):
                raise ValueError("invalid retrieval observations")
            rag_result = (
                tool_results.get("rag_search")
                or tool_results.get("rag_database_search")
            )
            if rag_result is None and not any(
                alias in tool_results for alias in ("rag_search", "rag_database_search")
            ):
                return [], []
            if not isinstance(rag_result, Mapping):
                raise ValueError("invalid retrieval observation")
            if rag_result.get("success") is not True:
                return [], []
            records = rag_result.get("data")
            if not isinstance(records, list):
                raise ValueError("invalid retrieval records")
        except Exception:
            return [], [warning]

        molecules, cards = [], []
        warnings = []
        for record in records:
            try:
                if not isinstance(record, Mapping):
                    raise ValueError("invalid retrieval record")
                record = dict(record)
                if not isinstance(record.get("SMILES"), str) or not record["SMILES"].strip():
                    raise ValueError("missing retrieval molecule")
                card = rag_info_molecule(record)
                # Validate the complete card, including source metadata, before
                # transport. One malformed record must not discard its peers.
                json.dumps(card, allow_nan=False)
            except Exception:
                warnings = [warning]
                continue
            molecules.append(record)
            cards.append(card)
        if molecules:
            try:
                payload = json.dumps({
                    "type": "rag_info", "molecules": cards[:rag_count],
                    "message": f"✅ 技能自动触发：从库中找到 {len(molecules)} 个相关分子",
                })
            except Exception:
                return molecules, [warning]
            # Transport failures and cancellation belong to the request owner,
            # never to the optional-data recovery path above.
            await websocket.send_text(payload)
        return molecules, warnings


    @staticmethod
    def _agent_failure_envelope() -> Dict[str, Any]:
        return result_presentation.failure_envelope()

    @staticmethod
    def _sanitize_agent_failure_text(
        value: Any,
        *,
        max_chars: int,
    ) -> tuple[str, bool]:
        return result_presentation.sanitize_failure_text(value, max_chars=max_chars)

    @classmethod
    def _sanitize_agent_warnings(
        cls, raw_warnings: Any, *, reject_claims: bool = False,
    ) -> list[str]:
        return result_presentation.sanitize_warnings(
            raw_warnings, sanitize_text=cls._sanitize_agent_failure_text,
            reject_claims=reject_claims,
        )

    @classmethod
    def _agent_presentation_status(cls, agent_result: Mapping[str, Any]) -> str:
        return result_presentation.presentation_status(agent_result)

    @classmethod
    def _partial_agent_projection(cls, agent_result: Mapping[str, Any]) -> Dict[str, Any]:
        return result_presentation.partial_projection(
            agent_result, sanitize_text=cls._sanitize_agent_failure_text,
            sanitize_warnings=cls._sanitize_agent_warnings,
            on_malformed_step=lambda: logger.warning("Skipped malformed partial step metadata"),
        )

    async def _send_partial_agent_result(
        self, websocket: WebSocket, agent_result: Mapping[str, Any],
    ) -> str:
        payload = self._partial_agent_projection(agent_result)
        await websocket.send_text(json.dumps({
            **payload, "type": "agent_result", "message": payload["content"],
        }, ensure_ascii=False))
        await websocket.send_text(json.dumps({
            **payload, "type": "complete",
        }, ensure_ascii=False))
        return payload["content"]

    @classmethod
    def _agent_failure_content(cls, agent_result: Dict[str, Any]) -> str:
        return result_presentation.failure_content(
            agent_result, sanitize_text=cls._sanitize_agent_failure_text,
        )

    async def _finish_terminal_agent_failure(
        self,
        websocket: WebSocket,
        agent_result: Dict[str, Any],
        *,
        message: str,
        enable_rag: bool,
        history: List[Dict[str, Any]],
    ) -> None:
        agent_response = await self._send_terminal_agent_failure(
            websocket,
            agent_result,
        )
        self._append_history(history, {
            "user": message,
            "agent_used": True,
            "agent_response": agent_response,
            "rag_enabled": enable_rag,
            "molecules_retrieved": 0,
            "assistant": agent_response,
        })

    async def _send_terminal_agent_failure(
        self,
        websocket: WebSocket,
        agent_result: Dict[str, Any],
    ) -> str:
        content = self._agent_failure_content(agent_result)
        raw_status = agent_result.get("status")
        status = (
            raw_status
            if raw_status in {"failed", "rejected", "cancelled", "timeout", "unavailable", "not_calculated"}
            else "failed"
        )
        trace_id, trace_sensitive = self._sanitize_agent_failure_text(
            agent_result.get("trace_id"),
            max_chars=128,
        )
        if trace_sensitive:
            trace_id = ""
        active_skill, skill_sensitive = self._sanitize_agent_failure_text(
            agent_result.get("active_skill"),
            max_chars=128,
        )
        if skill_sensitive:
            active_skill = ""
        warnings = self._sanitize_agent_warnings(
            agent_result.get("warnings"), reject_claims=True,
        )

        public_error = {}
        raw_error = agent_result.get("error")
        if isinstance(raw_error, Mapping):
            code, code_sensitive = self._sanitize_agent_failure_text(
                raw_error.get("code"), max_chars=128,
            )
            error_message, message_sensitive = self._sanitize_agent_failure_text(
                raw_error.get("message"), max_chars=_AGENT_FAILURE_CONTENT_MAX_CHARS,
            )
            if not code_sensitive:
                public_error["code"] = code
            if not message_sensitive and not result_presentation.failure_claim_risk(
                raw_error.get("message")
            ):
                public_error["message"] = error_message

        successful_observations = []
        raw_sequence = agent_result.get("tool_result_sequence")
        if isinstance(raw_sequence, list):
            for observation in raw_sequence:
                if not isinstance(observation, Mapping) or observation.get("success") is not True:
                    continue
                safe_observation = self._sanitize_agent_event_keys(dict(observation))
                safe_observation, _ = sanitize_bounded(
                    redact_sensitive(safe_observation),
                    max_depth=_AGENT_EVENT_MAX_DEPTH,
                    max_items=_AGENT_EVENT_MAX_ITEMS,
                    max_text_chars=_AGENT_EVENT_TEXT_MAX_CHARS,
                )
                if isinstance(safe_observation, Mapping):
                    successful_observations.append(dict(safe_observation))

        await self._send_status(websocket, f"⚠️ 代理执行失败: {content}")
        result_payload = {
            "type": "agent_result",
            "message": content,
            "active_skill": active_skill,
            "trace_id": trace_id,
            "status": status,
            "warnings": warnings,
        }
        if public_error:
            result_payload["error"] = public_error
        if successful_observations:
            result_payload["tool_result_sequence"] = successful_observations
        await websocket.send_text(json.dumps(result_payload, ensure_ascii=False))
        await websocket.send_text(json.dumps({
            "type": "complete",
            "content": content,
            "trace_id": trace_id,
            "status": status,
        }, ensure_ascii=False))
        return content

    @classmethod
    def _candidate_event_from_observation(
        cls,
        observation: Mapping[str, Any],
        *,
        trace_id: Any,
    ) -> Dict[str, Any] | None:
        status = observation.get("status")
        if status not in {"succeeded", "partial"}:
            return None
        # A partial candidate set intentionally has success=False at the
        # workflow level.  It still contains validated molecules that must be
        # shown, with the partial status preserved, rather than disappearing
        # from the live UI and reference flow.
        if observation.get("success") is not True and not (
            status == "partial" and observation.get("success") is False
        ):
            return None
        quality = observation.get("quality")
        if (
            not isinstance(quality, Mapping)
            or quality.get("output_contract") != "CandidateSet@1"
        ):
            return None

        candidate_set = CandidateSet.from_dict(observation.get("data"))
        if (
            candidate_set.status
            not in {
                ObservationStatus.SUCCEEDED,
                ObservationStatus.PARTIAL,
            }
            or not candidate_set.candidates
        ):
            return None
        sanitized_data = cls._sanitize_agent_event(candidate_set.to_dict())
        candidate_set = CandidateSet.from_dict(sanitized_data)

        safe_trace_id, trace_sensitive = cls._sanitize_agent_failure_text(
            trace_id,
            max_chars=128,
        )
        if trace_sensitive:
            safe_trace_id = ""
        tool_name, tool_sensitive = cls._sanitize_agent_failure_text(
            observation.get("tool_name"),
            max_chars=128,
        )
        if tool_sensitive:
            tool_name = ""
        provenance = observation.get("provenance")
        raw_model_name = (
            provenance.get("model_name")
            if isinstance(provenance, Mapping)
            else None
        )
        model_name, model_sensitive = cls._sanitize_agent_failure_text(
            raw_model_name,
            max_chars=128,
        )
        if model_sensitive:
            model_name = ""

        raw_warnings = observation.get("warnings")
        warning_mappings = []
        if isinstance(raw_warnings, Mapping):
            warning_mappings.append(raw_warnings)
        elif isinstance(raw_warnings, list):
            warning_mappings.extend(
                warning
                for warning in raw_warnings
                if isinstance(warning, Mapping)
            )
        for warning_mapping in warning_mappings:
            sanitize_bounded(
                warning_mapping,
                max_depth=_AGENT_EVENT_MAX_DEPTH,
                max_items=_AGENT_EVENT_MAX_ITEMS,
                max_text_chars=_AGENT_FAILURE_WARNING_MAX_CHARS,
            )
            raise ValueError("candidate warnings must contain only text")

        return {
            "type": "molecule_candidates",
            "trace_id": safe_trace_id,
            "source": {
                "tool_name": tool_name,
                "model_name": model_name,
                "status": status,
            },
            "candidate_set": candidate_set.to_dict(),
            "warnings": cls._sanitize_agent_warnings(raw_warnings),
        }

    async def _send_reference_candidate_events(self, websocket, agent_result, *, strict_transport=False):
        if self.scientific_references is None:
            return await self._send_molecule_candidate_events(websocket, agent_result)
        events = await asyncio.to_thread(self.scientific_references.project, agent_result,
            session_id=getattr(websocket, "scope", {}).get("agent_session_id"))
        for event in events:
            await websocket.send_text(json.dumps(event, ensure_ascii=False))
        # Additive live-only presentation; never republish candidates or change ACK.
        try:
            from .scientific_report import prepare_report_event
            report = await prepare_report_event(self.scientific_references.store, agent_result,
                events, session_id=getattr(websocket, "scope", {}).get("agent_session_id"))
        except Exception:
            report = None  # Optional computation failure does not change the run.
        if report is not None:
            if strict_transport:
                await websocket.send_text(json.dumps(report, ensure_ascii=False))
            else:
                try:
                    await websocket.send_text(json.dumps(report, ensure_ascii=False))
                except Exception:
                    pass  # Preserve the established legacy sidecar send behavior.
        return events

    @classmethod
    async def _send_molecule_candidate_events(
        cls,
        websocket: WebSocket,
        agent_result: Dict[str, Any],
    ) -> None:
        if agent_result.get("status") in {
            "failed", "rejected", "cancelled", "timeout", "unavailable", "not_calculated",
        }:
            return
        sequence = agent_result.get("tool_result_sequence")
        if not isinstance(sequence, list):
            return
        for observation in sequence:
            if not isinstance(observation, Mapping):
                continue
            try:
                event = cls._candidate_event_from_observation(
                    observation,
                    trace_id=agent_result.get("trace_id"),
                )
                payload = (
                    json.dumps(event, ensure_ascii=False)
                    if event is not None
                    else None
                )
            except Exception:
                logger.warning(
                    "Skipped malformed molecule candidate observation"
                )
                continue
            if payload is not None:
                await websocket.send_text(payload)

    async def _send_agent_events(self, websocket: WebSocket, agent_result: Dict[str, Any]):
        """Forward structured Agent workflow events to the browser."""
        events = agent_result.get("agent_events") or []
        if not isinstance(events, list):
            return

        for event in events:
            if not isinstance(event, dict):
                continue
            await self._send_agent_event(websocket, event)

    @classmethod
    def _safe_agent_event_key(cls, key: Any) -> str:
        if not isinstance(key, str):
            return ""
        sanitized_key, sensitive = cls._sanitize_agent_failure_text(
            key,
            max_chars=_AGENT_EVENT_KEY_MAX_CHARS,
        )
        if sensitive or not sanitized_key or sanitized_key != key:
            return ""

        probe_value = "agent-event-safe-key-probe"
        redacted_probe = redact_sensitive({key: probe_value})
        if redacted_probe.get(key) != probe_value:
            return ""
        return sanitized_key

    @classmethod
    def _sanitize_agent_event_keys(
        cls,
        value: Any,
        *,
        depth: int = 0,
        max_depth: int = _AGENT_EVENT_MAX_DEPTH,
        max_items: int = _AGENT_EVENT_MAX_ITEMS,
    ) -> Any:
        if depth >= max_depth:
            return value
        if isinstance(value, Mapping):
            sanitized_mapping = {}
            for index, (key, child) in enumerate(value.items()):
                if index >= max_items:
                    break
                sanitized_key = cls._safe_agent_event_key(key)
                if not sanitized_key or sanitized_key in sanitized_mapping:
                    continue
                sanitized_mapping[sanitized_key] = cls._sanitize_agent_event_keys(
                    child,
                    depth=depth + 1,
                    max_depth=max_depth, max_items=max_items,
                )
            return sanitized_mapping
        if isinstance(value, list):
            return [
                cls._sanitize_agent_event_keys(child, depth=depth + 1, max_depth=max_depth, max_items=max_items)
                for child in value[:max_items]
            ]
        if isinstance(value, tuple):
            return tuple(
                cls._sanitize_agent_event_keys(child, depth=depth + 1, max_depth=max_depth, max_items=max_items)
                for child in value[:max_items]
            )
        return value

    @classmethod
    def _sanitize_agent_event(cls, event: Dict[str, Any]) -> Dict[str, Any]:
        event_name = event.get("event")
        if not isinstance(event_name, str):
            event_name = event.get("type")
        is_failure_event = event_name in _AGENT_FAILURE_EVENT_TYPES
        _, message_sensitive = cls._sanitize_agent_failure_text(
            event.get("message"),
            max_chars=_AGENT_EVENT_TEXT_MAX_CHARS,
        )

        key_safe_event = cls._sanitize_agent_event_keys(event)
        redacted_event = redact_sensitive(key_safe_event)
        if is_failure_event:
            redacted_event = cls._scrub_failure_event_claims(redacted_event)
        sanitized_event, _ = sanitize_bounded(
            redacted_event,
            max_depth=_AGENT_EVENT_MAX_DEPTH,
            max_items=_AGENT_EVENT_MAX_ITEMS,
            max_text_chars=_AGENT_EVENT_TEXT_MAX_CHARS,
        )
        if not isinstance(sanitized_event, dict):
            return {}
        if is_failure_event and message_sensitive:
            sanitized_event["message"] = _AGENT_FAILURE_FALLBACK
        return sanitized_event

    @classmethod
    def _scrub_failure_event_claims(
        cls, value: Any, *, depth: int = 0,
    ) -> Any:
        if isinstance(value, str):
            return (
                _AGENT_FAILURE_FALLBACK
                if result_presentation.failure_claim_risk(value)
                else value
            )
        if depth >= _AGENT_EVENT_MAX_DEPTH:
            return "[omitted]" if isinstance(value, (Mapping, list, tuple)) else value
        if isinstance(value, Mapping):
            return {
                key: cls._scrub_failure_event_claims(child, depth=depth + 1)
                for index, (key, child) in enumerate(value.items())
                if index < _AGENT_EVENT_MAX_ITEMS
            }
        if isinstance(value, list):
            return [
                cls._scrub_failure_event_claims(child, depth=depth + 1)
                for child in value[:_AGENT_EVENT_MAX_ITEMS]
            ]
        if isinstance(value, tuple):
            return tuple(
                cls._scrub_failure_event_claims(child, depth=depth + 1)
                for child in value[:_AGENT_EVENT_MAX_ITEMS]
            )
        return value

    @classmethod
    async def _send_agent_event(
        cls,
        websocket: WebSocket,
        event: Dict[str, Any],
    ):
        sanitized_event = cls._sanitize_agent_event(event)
        await websocket.send_text(json.dumps({
            "type": "agent_event",
            "event": sanitized_event,
        }, ensure_ascii=False))
    
    async def _send_status(self, websocket: WebSocket, message: str):
        """发送状态消息"""
        await websocket.send_text(json.dumps({
            "type": "status",
            "message": message
        }))

    @staticmethod
    def _route_clarification(route_decision) -> str:
        if not route_decision or not route_decision.requires_confirmation:
            return ""
        reasons = " ".join(route_decision.reasons)
        if "target_clarification_required" in route_decision.reasons:
            from src.agent.contracts.target_request import TARGET_CLARIFICATION

            return TARGET_CLARIFICATION
        if "target_reference_required" in route_decision.reasons:
            return "请提供已确认的靶点名称或 UniProt 标识；当前没有可绑定的排名靶点，不能猜测或运行对接。"
        if "target_subtype_clarification_required" in route_decision.reasons:
            return "请明确要查询的具体靶点亚型或 UniProt 标识；不同亚型的结构不能混用。本次未生成分子或运行对接。"
        if "invalid SMILES" in reasons:
            return (
                "你提供的 SMILES 无效，请检查并更正结构后重试。"
                "本次未进行科学计算，没有计算 LogP、QED、pIC50 或 binding energy（结合能）。"
            )
        if "SMILES validation is unavailable" in reasons:
            return (
                "当前无法使用科学验证组件校验 SMILES，请稍后重试。"
                "本次未进行科学计算，没有计算 LogP、QED、pIC50 或 binding energy（结合能）。"
            )
        if "SMILES" in reasons:
            return (
                "要进行真实、可追溯的分子性质计算，请提供该分子的有效 "
                "SMILES。收到结构后，系统会调用 RDKit 计算，而不会由语言模型"
                "猜测数值。"
            )
        if route_decision.selected_skill == "docking_simulation":
            return "请提供明确的配体 SMILES/文件、受体结构及对接口袋参数（center/size）；输入不足，本次未运行对接。"
        return ""

    @staticmethod
    def _molecular_input_clarification(molecular_input) -> str:
        if not molecular_input.blocks_execution:
            return ""
        if molecular_input.validation_available:
            return (
                "你提供的 SMILES 无效，请检查并更正结构后重试。"
                "本次未进行科学计算，没有计算 LogP、QED、pIC50 或 binding energy（结合能）。"
            )
        return (
            "当前无法使用科学验证组件校验 SMILES，请稍后重试。"
            "本次未进行科学计算，没有计算 LogP、QED、pIC50 或 binding energy（结合能）。"
        )

    @staticmethod
    def _should_use_legacy_rag(message: str) -> bool:
        lower = message.lower()
        return bool(SMILES_PATTERN.search(message)) or any(
            marker in lower
            for marker in (
                "smiles",
                "相似分子",
                "类似分子",
                "知识库",
                "检索",
                "搜索",
                "rag",
                "similar molecule",
                "knowledge base",
            )
        )
    
    def _format_rag_context(self, molecules: List[Dict[str, Any]]) -> str:
        return prompt_builder.format_budgeted_rag_context(
            molecules, config=self.config, formatter=format_rag_context,
        )

    def _input_limit(self) -> int:
        return character_limit(self.config, bool(getattr(self.model, "provider_name", None)))
    
    def _build_prompt(self, user_message: str, rag_context: str,
                     retrieved_molecules: List[Dict[str, Any]],
                     conversation_history: List[Dict[str, Any]] | None = None) -> str:
        history = conversation_history if conversation_history is not None else self.conversation_history
        return prompt_builder.build_chat_prompt(
            user_message, rag_context, history=history, config=self.config,
            input_limit=self._input_limit,
        )

    def _model_max_tokens(self, model=None) -> int:
        """Select an output budget without leaking local GPU limits to remote APIs."""
        inference = self.config.get("inference", {})
        local_limit = int(inference.get("max_tokens", 1500))
        if getattr(self.model if model is None else model, "provider_name", None):
            return int(inference.get("external_max_tokens", 4096))
        return local_limit

    def _finalize_model_response(self, content: str, model=None):
        """Expose safe completion state and make provider truncation visible."""
        metadata = getattr(self.model if model is None else model, "last_response_metadata", {}) or {}
        finish_reason = metadata.get("finish_reason")
        completion_meta = {}
        warning = ""

        if finish_reason:
            completion_meta["finish_reason"] = str(finish_reason)
        if str(finish_reason).lower() == "length":
            warning = (
                "\n\n> ⚠️ 回答达到输出长度上限，内容可能不完整。"
                "请回复“继续”以从中断处续写。"
            )
            if warning not in content:
                content += warning
            completion_meta["truncated"] = True
            logger.warning("Model response reached its output length limit")

        return content, warning, completion_meta
    
    def _analyze_user_intent(self, user_message_lower: str) -> Dict[str, Any]:
        """分析用户意图"""
        intent = {
            'intent_type': '通用咨询',
            'task_description': '回答用户问题',
            'rag_usage': '作为参考信息',
            'suggested_tools': []
        }
        
        # 生成类任务
        if has_generation_intent(user_message_lower):
            intent['intent_type'] = '分子生成'
            intent['task_description'] = '使用AI生成符合要求的新分子结构'
            intent['rag_usage'] = '作为设计灵感和参考'
            intent['suggested_tools'] = ['llm_molecular_generator - AI分子生成工具']
        
        # 分析类任务
        elif any(kw in user_message_lower for kw in ['分析', '计算', '评估', 'analyze', 'calculate', 'evaluate']):
            intent['intent_type'] = '分子分析'
            intent['task_description'] = '分析分子的药物化学性质'
            intent['rag_usage'] = '提供相似分子的对比数据'
            intent['suggested_tools'] = [
                'property_calculator - 基础属性计算',
                'drug_likeness_assessment - 类药性评估',
                'admet_predictor - ADMET预测'
            ]
        
        # 对接类任务
        elif any(kw in user_message_lower for kw in ['对接', '结合', '靶点', 'docking', 'binding', 'target']):
            intent['intent_type'] = '分子对接'
            intent['task_description'] = '预测分子与靶点的结合'
            intent['rag_usage'] = '提供相似活性分子参考'
            intent['suggested_tools'] = ['molecular_docking - 分子对接分析']
        
        # ADMET类任务
        elif any(kw in user_message_lower for kw in ['admet', 'adme', '吸收', '代谢', '毒性']):
            intent['intent_type'] = 'ADMET预测'
            intent['task_description'] = '预测分子的药代动力学性质'
            intent['rag_usage'] = '提供ADMET性质对比'
            intent['suggested_tools'] = ['admet_predictor - ADMET性质预测']
        
        return intent
    
    def _build_prompt_with_agent(self, user_message: str, agent_response: str,
                                rag_context: str, retrieved_molecules: List[Dict[str, Any]],
                                agent_result: Mapping | None = None) -> str:
        return prompt_builder.build_agent_prompt(
            user_message, agent_response, rag_context, config=self.config,
            input_limit=self._input_limit, agent_result=agent_result,
        )
