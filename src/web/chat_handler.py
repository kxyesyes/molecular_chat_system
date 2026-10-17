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
from fastapi import WebSocket, WebSocketDisconnect

from src.agent.persistence.redaction import (
    contains_secret_material,
    redact_sensitive,
    sanitize_bounded,
)
from src.agent.contracts import (
    AgentResult, CandidateSet, ObservationStatus, RunOutcome,
    AgentErrorCode, AgentExecutionError,
)
from src.agent.contracts.generation_request import (
    GenerationRequestError,
    generation_request_error_details,
    has_generation_intent,
    preflight_generation_request,
)
from src.agent.routing.hybrid import SMILES_PATTERN
from src.agent.runtime.allocation import (
    AllocationScope, AllocationStopped,
    DEFAULT_ALLOCATION_TIMEOUT_SECONDS, DEFAULT_ALLOCATION_PROGRESS_SECONDS,
    current_allocation, finish_allocation, owned_allocation_call,
)
from src.agent.utils.validators import InputValidator
from src.web.prompt_budget import (
    InputBudgetExceeded, character_limit, validate_question,
)
from src.web import agent_result_presentation as result_presentation
from src.web import chat_prompt_builder as prompt_builder
from src.web import property_comparison
from src.web.ranked_target_structures import (
    rank_followup_kind, ranked_structure_followup, reverse_rank_routing_query,
)
from src.web.property_constraints import generation_constraint_report
from src.agent import evidence_followup
from src.web.agent_result_presentation import (
    _AGENT_FAILURE_FALLBACK,
    _AGENT_FAILURE_CONTENT_MAX_CHARS,
    _AGENT_FAILURE_WARNING_MAX_CHARS,
    _AGENT_FAILURE_WARNING_LIMIT,
)
from src.web.models import generate_for_chat, stream_for_chat
from src.system.ollama_model import OllamaGenerationError
from src.web.model_lifecycle import ModelRequestGate, model_request, finish_on_cancel
from src.web.rag_presentation import format_rag_context, rag_info_molecule
from src.web.legacy_websocket_protocol import LegacyFrameError, decode_legacy_frame

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
            # _process_message_body's model_request decorator owns this lease.
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
                                       admission_carry=None, admission_exchange=None):
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
        """处理 WebSocket 连接"""
        if self.decision_runtime is not None:
            await self.decision_runtime.handle_websocket(handler=self, websocket=websocket)
            return
        controller = None
        if self.docking_controller is not None:
            from .decision_runtime import _Sender, _enabled_docking_controller, _docking_history
            controller = _enabled_docking_controller(self)
        if controller is not None and controller.runtime.closing:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        conversation_history: List[Dict[str, Any]] = []
        output = websocket
        if controller is not None:
            output = _Sender(websocket)
            output._runtime = controller.runtime
            output._chat_controller = controller
            controller.runtime.sockets.add(output)
        
        try:
            # Handshake delivery belongs to the same registered socket lifetime.
            try:
                await output.send_text(json.dumps({
                    "type": "connection_ready",
                    "message": "连接成功",
                    "model_status": "ready",
                    "timestamp": time.time(),
                    **({"capabilities": {"chat_docking": {"preparation_available": True,
                        "execution_authorized": False}}} if controller is not None else {}),
                }))
                logger.info("WebSocket连接已建立，发送就绪消息")
            except Exception as e:
                logger.error(f"发送连接就绪消息失败: {e}")
                if controller is not None:
                    return

            while True:
                data = await websocket.receive_text()
                if controller is not None and (controller.runtime.closing or not output.writable):
                    break
                try:
                    message_data = decode_legacy_frame(data, allow_docking_controls=controller is not None)
                except LegacyFrameError as exc:
                    await output.send_text(json.dumps({
                        "type": "error",
                        "message": "请求格式无效。",
                        "error": {"code": exc.code},
                    }, ensure_ascii=False))
                    continue

                # 处理 ping 消息
                if message_data.get("type") == "ping":
                    await output.send_text(json.dumps({
                        "type": "pong",
                        "timestamp": message_data.get("timestamp", 0)
                    }))
                    continue

                if controller is not None:
                    if await controller.handle(output, message_data,
                            history=_docking_history(conversation_history)):
                        continue
                    if controller.busy(output):
                        await output.send({'type': 'error', 'code': 'docking_in_progress',
                                           'docking_request_rejected': True})
                        continue

                message = message_data.get("message", "")
                enable_rag = message_data.get("enable_rag", True)
                enable_tools = message_data.get("enable_tools", True)
                rag_count = message_data.get("rag_count", 5)  # 获取RAG检索数量
                temperature = message_data.get("temperature", 0.7)  # 获取生成温度
                mol_count = message_data.get("mol_count")  # 未提供时由生成请求解析器决定

                if not message.strip():
                    continue

                # Long-lived sockets must not reuse revoked or corrupt configuration.
                if self.refresh_model_config is not None:
                    try:
                        await self.refresh_model_config()
                    except Exception:
                        await output.send_text(json.dumps({
                            "type": "error",
                            "message": "本机模型配置不可用；请检查配置文件与目录权限。",
                        }))
                        await websocket.close(code=1011)
                        return

                # 发送确认
                await self._send_status(output, "Processing your message...")

                # 处理消息，传递高级配置
                await self._process_message(
                    output,
                    message,
                    enable_rag,
                    enable_tools,
                    rag_count,
                    temperature,
                    mol_count,
                    conversation_history=conversation_history,
                    session_id=websocket.scope.get("agent_session_id"),
                    reference=message_data.get("reference"),
                    selection=message_data.get("selection"),
                )
                    
        except WebSocketDisconnect:
            logger.info("WebSocket disconnected")
        except Exception as e:
            error_str = str(e)
            if "received 1000" in error_str and "sent 1000" in error_str:
                logger.info("WebSocket closed normally")
            else:
                logger.error(f"WebSocket error: {e}")
                try:
                    await output.send_text(json.dumps({
                        "type": "error",
                        "message": f"处理错误: {str(e)[:100]}..."
                    }))
                except:
                    pass
        finally:
            if controller is not None:
                output.writable = False
                output.receiver_attached = False
                output.memory = []
                output.waiting = None
                await controller.disconnect(output)
                runtime = controller.runtime
                if output._docking_used:
                    runtime._stop_docking(output)
                if not any(op.sender is output for op in runtime.docking_operations):
                    runtime.sockets.discard(output)
    
    async def _process_message(self, websocket: WebSocket, message: str,
                              enable_rag: bool, enable_tools: bool,
                              rag_count: int = 5, temperature: float = 0.7,
                              mol_count: int | None = None,
                              conversation_history: List[Dict[str, Any]] | None = None,
                              session_id: str | None = None,
                              reference=None, selection=None):
        # Consume the prior turn's continuation pointer at admission, before
        # allocation/model-gate/input-validation early returns. Only a successful
        # current turn may publish a new one; historical receipts stay unchanged.
        reverse_context = None
        if type(conversation_history) is list and conversation_history and type(conversation_history[-1]) is dict:
            reverse_context = conversation_history[-1].pop('reverse_target_evidence', None)
        async def process(sender):
            return await self._process_message_body(
                sender, message, enable_rag, enable_tools, rag_count, temperature,
                mol_count, conversation_history, session_id, reference, selection,
                reverse_context=reverse_context)
        if enable_tools and self.agent_system is not None:
            result = await self._with_allocation(websocket, process,
                handoff_timeout=isinstance(getattr(self, 'model_request_gate', None), ModelRequestGate))
        else:
            result = await process(websocket)
        if self.docking_controller is None:
            return result
        from .decision_runtime import _Sender, _enabled_docking_controller, _capture_docking_result
        if type(websocket) is _Sender and type(result) is dict:
            controller = _enabled_docking_controller(self, websocket._runtime)
            if controller is not None:
                await _capture_docking_result(controller, websocket, result)

    @model_request
    async def _process_message_body(self, websocket: WebSocket, message: str,
                              enable_rag: bool, enable_tools: bool, 
                              rag_count: int = 5, temperature: float = 0.7,
                              mol_count: int | None = None,
                              conversation_history: List[Dict[str, Any]] | None = None,
                              session_id: str | None = None,
                              reference=None, selection=None, *, reverse_context=None):
        """处理用户消息 - 性能优化版"""
        # Hold one client for generation, retries and completion metadata.
        request_model = self.model
        try:
            temperature = self._validate_request_temperature(temperature)
        except ValueError as exc:
            await websocket.send_text(json.dumps({
                "type": "complete",
                "content": "生成参数无效，temperature 必须是 0 到 2 之间的有限数值。",
                "error": {"code": "invalid_temperature", "message": str(exc)},
            }, ensure_ascii=False))
            return
        history = (
            conversation_history
            if conversation_history is not None
            else self.conversation_history
        )
        # Legacy conversational history is not a socket-bound scientific source.
        # Both fields are supplied by the standard WebSocket entry point.
        scientific_context_enabled = (
            isinstance(conversation_history, list)
            and isinstance(session_id, str) and bool(session_id.strip())
        )
        scientific_history = history if scientific_context_enabled else []
        try:
            validate_question(message, self._input_limit())
            for section in ("history", "rag", "tool"):
                character_limit(self.config, section=section)
        except InputBudgetExceeded as exc:
            await websocket.send_text(json.dumps({
                "type": "complete", "content": str(exc),
                "error": {"code": "input_budget_exceeded"},
            }, ensure_ascii=False))
            return
        start_time = time.time()
        full_response = ""
        agent_used = False
        agent_response = ""
        agent_result = None
        docking_result = None
        
        # 初始化 Agent 执行状态
        agent_task = None
        agent_event_queue = None
        live_agent_events = 0
        property_evidence = None
        property_answer = None
        activity_evidence = None
        reverse_target_evidence = None
        reverse_answer = None
        comparison_answer = None
        execution_message = property_comparison.corrected_property_query(message, scientific_history)

        if rank_followup_kind(message) is not None:
            ranked_followup = await owned_allocation_call(ranked_structure_followup,
                message, ([{'reverse_target_evidence': reverse_context}]
                          if scientific_context_enabled and reverse_context is not None else []),
                session_id=session_id, enable_tools=enable_tools,
                boundary=self._ranked_reverse_evidence())
            await websocket.send_text(json.dumps({'type': 'complete',
                'content': ranked_followup['content'], 'status': ranked_followup['status']}, ensure_ascii=False))
            self._append_history(history, {'user': message, 'assistant': ranked_followup['content'],
                'agent_used': False, 'rag_enabled': False, 'molecules_retrieved': 0,
                **({'reverse_target_evidence': ranked_followup['context']} if ranked_followup['context'] is not None else {})})
            return

        # Evidence reuse is socket-local, before molecular-input admission. No
        # shared handler history, persistence lookup or tool/model execution.
        followup = evidence_followup.followup_answer(message,
            scientific_history, session_id)
        if followup is not None:
            await websocket.send_text(json.dumps({"type": "complete",
                "content": followup["final_answer"], "status": followup["status"]}, ensure_ascii=False))
            self._append_history(history, {"user": message, "assistant": followup["final_answer"],
                "agent_used": False, "rag_enabled": False, "molecules_retrieved": 0})
            return

        try:
            validated_mol_count = preflight_generation_request(
                message,
                mol_count,
                count_supplied=mol_count is not None,
                field="mol_count",
            )
        except GenerationRequestError as exc:
            content = "Invalid molecular generation request"
            await websocket.send_text(
                json.dumps({"type": "complete", "content": content})
            )
            self._append_history(history,
                {
                    "user": message,
                    "agent_used": False,
                    "agent_response": None,
                    "rag_enabled": False,
                    "molecules_retrieved": 0,
                    "assistant": content,
                    "error": {"code": "invalid_input", "message": str(exc)},
                }
            )
            return
        if validated_mol_count is not None:
            mol_count = validated_mol_count
        
        # Agent 处理（使用temperature参数）
        matched_skill = None
        route_decision = None
        resolved_molecule = None
        routing_message = execution_message
        if self.scientific_references is not None:
            try:
                resolved_molecule = await owned_allocation_call(
                    self.scientific_references.resolve, message, reference, selection,
                    session_id=session_id, enable_tools=enable_tools)
                if resolved_molecule is not None:
                    execution_message = await owned_allocation_call(
                        self.scientific_references.continuation_query, execution_message,
                        resolved_molecule, session_id=session_id)
                    routing_message = resolved_molecule.routing_query(execution_message)
            except ValueError:
                from .scientific_references import REFERENCE_CLARIFICATION
                await websocket.send_text(json.dumps({"type": "complete", "content": REFERENCE_CLARIFICATION}, ensure_ascii=False))
                return
        routing_message = reverse_rank_routing_query(routing_message)
        molecular_input = InputValidator().analyze_molecular_input(routing_message)
        clarification = self._molecular_input_clarification(molecular_input)
        skill_router = getattr(self.agent_system, "skill_router", None)
        if (
            not clarification
            and enable_tools
            and skill_router
            and hasattr(skill_router, "decide")
        ):
            route_decision = await owned_allocation_call(
                skill_router.decide,
                routing_message,
                llm=getattr(self.agent_system, "llm", None),
            )
            clarification = self._route_clarification(route_decision)
        if clarification:
            await websocket.send_text(json.dumps({
                "type": "complete",
                "content": clarification,
            }, ensure_ascii=False))
            self._append_history(history, {
                "user": message,
                "agent_used": False,
                "agent_response": None,
                "rag_enabled": False,
                "molecules_retrieved": 0,
                "assistant": clarification,
            })
            return

        should_use_agent = bool(
            route_decision and route_decision.selected_skill
        ) if route_decision is not None else bool(
            enable_tools
            and self.agent_system
            and await owned_allocation_call(self.agent_system.should_use_tools, routing_message)
        )
        if enable_tools and self.agent_system and should_use_agent:
            logger.info(f"检测到需要使用 Agent 工具 (temperature={temperature})")
            
            # 预先进行技能路由，以便向前端发送状态指示器
            if skill_router:
                if route_decision is not None:
                    matched_skill = skill_router.catalog.get(
                        route_decision.selected_skill
                    )
                else:
                    matched_skill = await owned_allocation_call(skill_router.route,
                        routing_message,
                        llm=self.agent_system.llm,
                    )
                
                if matched_skill:
                    skill_status_map = {
                        "molecular_design": "✨ 正在设计生成新分子...",
                        "activity_prediction": "🔬 正在预测分子活性...",
                        "reverse_target_prediction": "🎯 正在反向预测结合靶点...",
                        "target_database_search": "🗄️ 正在检索靶点结构数据库...",
                        "admet_assessment": "💊 正在评估分子基础属性...",
                        "docking_simulation": "🔗 正在进行分子对接模拟...",
                        "comprehensive_evaluation": "📋 正在执行分子一键体检...",
                        "hit_to_lead_optimization": "🔧 正在进行先导物诊断与优化...",
                        "rag_search": "🔍 正在检索本地知识库..."
                    }
                    status_msg = skill_status_map.get(matched_skill.name, f"🎯 激活技能: {matched_skill.name}...")
                    await websocket.send_text(json.dumps({
                        "type": "agent_status",
                        "message": status_msg
                    }))
                else:
                    await websocket.send_text(json.dumps({
                        "type": "agent_status",
                        "message": "🔧 启动智能代理 (通用模式)..."
                    }))
            else:
                await websocket.send_text(json.dumps({
                    "type": "agent_status",
                    "message": "🔧 启动智能代理..."
                }))
                
            loop = asyncio.get_running_loop()
            agent_event_queue = asyncio.Queue()

            def on_agent_event(event):
                payload = event.to_dict() if hasattr(event, "to_dict") else event
                if isinstance(payload, dict):
                    loop.call_soon_threadsafe(
                        agent_event_queue.put_nowait,
                        payload,
                    )

            agent_task = asyncio.create_task(
                self._execute_agent(
                    execution_message,
                    temperature,
                    mol_count,
                    matched_skill,
                    event_callback=on_agent_event,
                    enable_rag=enable_rag,
                    enable_tools=enable_tools,
                    session_id=session_id,
                    **({"resolved_molecule": resolved_molecule} if resolved_molecule is not None else {}),
                )
            )
        
        # 接收 Agent 事件并等待执行结果
        retrieved_molecules = []
        rag_context = ""
        agent_result = None
        
        if agent_task:
            try:
                while not agent_task.done():
                    try:
                        event = await asyncio.wait_for(
                            agent_event_queue.get(),
                            timeout=0.05,
                        )
                    except asyncio.TimeoutError:
                        continue
                    await self._send_agent_event(websocket, event)
                    live_agent_events += 1
                agent_result = await agent_task
                if not isinstance(agent_result, Mapping):
                    agent_result = self._agent_failure_envelope()
                else:
                    agent_result = dict(agent_result)
                    if type(agent_result.get("success")) is not bool:
                        agent_result = self._agent_failure_envelope()
                while agent_event_queue and not agent_event_queue.empty():
                    event = agent_event_queue.get_nowait()
                    await self._send_agent_event(websocket, event)
                    live_agent_events += 1
                if not live_agent_events:
                    await self._send_agent_events(websocket, agent_result)
                presentation_status = self._agent_presentation_status(agent_result)
                if presentation_status == 'completed' and self.docking_controller is not None:
                    from .decision_runtime import _enabled_docking_controller
                    if _enabled_docking_controller(self) is not None:
                        # Hold the actual execution result before presentation
                        # rewrites; return it only after successful completion.
                        docking_result = dict(agent_result)
                sequence = agent_result.get('tool_result_sequence')
                reverse_observations = [item for item in sequence if type(item) is dict
                    and item.get('tool_name') == 'reverse_target_predictor'] if type(sequence) is list else []
                # Only a single-tool reverse workflow has a pure ranked presentation.
                # Mixed workflows retain their other tool results and final status.
                if (scientific_context_enabled and reverse_observations
                        and presentation_status in {'partial', 'completed'}):
                    boundary = self._ranked_reverse_evidence()
                    if boundary is not None:
                        reverse_target_evidence = await owned_allocation_call(
                            boundary.capture, agent_result, session_id=session_id)
                    if len(sequence) == 1:
                        count = self._reverse_display_count(message)
                        if reverse_target_evidence is not None:
                            reverse_answer = await owned_allocation_call(boundary.render_prefix,
                                reverse_target_evidence, session_id=session_id, count=count)
                        if reverse_answer is None:
                            reverse_target_evidence = None
                            reverse_answer = '排名证据不可用：无法确认同会话、已接受且仍有效的来源；请重新运行反向寻靶。'
                            agent_result = {**agent_result, 'success': False, 'status': 'partial', 'partial': True}
                            presentation_status = 'partial'
                        agent_result = {**agent_result, 'final_answer': reverse_answer}
                if presentation_status in {"partial", "completed"}:
                    if scientific_context_enabled:
                        activity_evidence = evidence_followup.accepted_activity_evidence(
                            agent_result, message, session_id)
                    comparison_answer = evidence_followup.comparison_answer(message, agent_result, session_id)
                    if comparison_answer is not None:
                        # A presentation can demote incomplete coverage, never
                        # promote an existing partial run to completed.
                        if presentation_status == "partial":
                            comparison_answer.update(success=False, status="partial", partial=True)
                        agent_result = {**agent_result, **comparison_answer}
                        presentation_status = self._agent_presentation_status(agent_result)
                if presentation_status in {"partial", "completed"}:
                    constraint_report = generation_constraint_report(agent_result, execution_message)
                    if constraint_report:
                        agent_result = {**agent_result, "final_answer": constraint_report + "\n\n"
                                        + agent_result.get("final_answer", "")}
                    # A confirmed reference supplies identity out of band. Use
                    # its validated routing view, not SMILES-free user prose;
                    # leave original request/history and evidence gates intact.
                    property_query = routing_message if resolved_molecule is not None else message
                    property_evidence = property_comparison.accepted_property_evidence(
                        agent_result, property_query, session_id)
                if presentation_status == "completed":
                    property_result = property_comparison.descriptor_result(
                        message, property_evidence, scientific_history, session_id)
                    if property_result is not None:
                        property_answer = property_result["final_answer"]
                        existing_warnings = agent_result.get("warnings")
                        agent_result = {**agent_result, **property_result,
                            "warnings": (existing_warnings if isinstance(existing_warnings, list) else [])
                                        + property_result["warnings"]}
                        presentation_status = self._agent_presentation_status(agent_result)
                if presentation_status in {"partial", "completed"}:
                    retrieved_molecules, rag_warnings = await self._send_agent_rag_results(
                        websocket, agent_result, rag_count,
                    )
                    if rag_warnings:
                        if presentation_status == "partial":
                            existing_warnings = agent_result.get("warnings")
                            agent_result = {
                                **agent_result,
                                "warnings": rag_warnings + (
                                    existing_warnings if isinstance(existing_warnings, list) else []
                                ),
                            }
                        else:
                            await self._send_status(websocket, "⚠️ " + " ".join(rag_warnings))
                if presentation_status == "partial":
                    await self._send_reference_candidate_events(websocket, agent_result)
                    agent_response = await self._send_partial_agent_result(
                        websocket, agent_result,
                    )
                    self._append_history(history, {
                        "user": message,
                        "agent_used": True,
                        "agent_response": agent_response,
                        "rag_enabled": enable_rag,
                        "molecules_retrieved": len(retrieved_molecules),
                        "assistant": agent_response,
                        **({"property_evidence": property_evidence}
                           if scientific_context_enabled and property_evidence is not None else {}),
                        **({"activity_evidence": activity_evidence} if activity_evidence is not None else {}),
                        **({'reverse_target_evidence': reverse_target_evidence} if reverse_target_evidence is not None else {}),
                    })
                    return
                if presentation_status == "completed":
                    agent_response = agent_result.get('final_answer', '')
                    agent_used = True
                    tools_used = agent_result.get('tools_used', [])
                    active_skill = agent_result.get('active_skill', '')
                    
                    if retrieved_molecules:
                        rag_context = self._format_rag_context(retrieved_molecules)

                    skill_info = f" (当前技能: {active_skill})" if active_skill else ""

                    await self._send_reference_candidate_events(
                        websocket,
                        agent_result,
                    )
                    await websocket.send_text(json.dumps({
                        "type": "agent_result",
                        "message": f"✅ 智能代理完成，使用工具: {', '.join(tools_used)}{skill_info}",
                        "active_skill": active_skill
                    }))
                else:
                    agent_result = {**agent_result, "status": presentation_status}
                    await self._finish_terminal_agent_failure(
                        websocket,
                        agent_result,
                        message=message,
                        enable_rag=enable_rag,
                        history=history,
                    )
                    return
            except AllocationStopped:
                # Allocation owns the terminal. A late event/result is control
                # flow, not a presentation failure to retry on the sealed socket.
                return
            except Exception:
                logger.error(
                    "Agent result processing failed; exception details omitted"
                )
                if isinstance(agent_result, Mapping):
                    # A projection/reference failure must not erase the
                    # already-settled trace, error, or successful observations.
                    # Stop presenting it as completed, but retain the record
                    # for the terminal envelope.
                    try:
                        preserved_result = dict(agent_result)
                    except Exception:
                        preserved_result = None
                    if preserved_result is None:
                        agent_result = self._agent_failure_envelope()
                    else:
                        preserved_result["success"] = False
                        preserved_result["status"] = "failed"
                        preserved_result["final_answer"] = ""
                        if not isinstance(preserved_result.get("error"), Mapping):
                            preserved_result["error"] = {
                                "code": "presentation_error",
                                "message": "结果展示失败，但执行记录已保留。",
                            }
                        agent_result = preserved_result
                else:
                    agent_result = self._agent_failure_envelope()
                await self._finish_terminal_agent_failure(
                    websocket,
                    agent_result,
                    message=message,
                    enable_rag=enable_rag,
                    history=history,
                )
                return
            finally:
                # Event delivery may fail while the executor thread still owns
                # the model. Drain it before releasing the request lease.
                if agent_task.done():
                    # An event send may lose the deadline race before the
                    # settled worker was awaited. Retrieve its exception too.
                    if not agent_task.cancelled():
                        agent_task.exception()
                else:
                    try:
                        await finish_on_cancel(agent_task)
                    except AllocationStopped:
                        pass  # Expected settlement after allocation expiry.

        # Ordinary chat/retrieval and post-tool summaries are not allocation.
        finish_allocation()

        # 如果没有触发 Agent 技能但启用了 RAG 开关，则执行传统 RAG（保留向后兼容性）
        if (
            not agent_used
            and enable_rag
            and self._should_use_legacy_rag(message)
        ):
            await self._send_status(websocket, f"🔍 搜索相关分子数据 (top {rag_count})...")
            outcome = {"status": "unavailable", "records": []}
            try:
                search = getattr(
                    self.rag_service,
                    "search_similar_molecules_with_status",
                    None,
                )
                if callable(search):
                    outcome = await search(message, rag_count)
            except Exception:
                logger.warning("Legacy RAG unavailable; details omitted")

            records = outcome.get("records") if isinstance(outcome, dict) else None
            valid = (
                isinstance(outcome, dict)
                and isinstance(records, list)
                and outcome.get("status") in {"succeeded", "no_hits"}
                and (outcome.get("status") == "succeeded") == bool(records)
            )
            if not valid:
                await websocket.send_text(json.dumps({
                    "type": "complete",
                    "status": "failed",
                    "retrieval_status": "unavailable",
                    "content": "知识库检索暂时不可用，未生成基于检索的回答。",
                    "error": {
                        "code": "rag_unavailable",
                        "message": "知识库检索未完成",
                    },
                }, ensure_ascii=False))
                return

            retrieved_molecules = records
            if retrieved_molecules:
                rag_context = self._format_rag_context(retrieved_molecules)
                await websocket.send_text(json.dumps(self._rag_info_payload(
                    retrieved_molecules,
                    f"✅ 找到 {len(retrieved_molecules)} 个相关分子",
                )))
                await self._send_status(websocket, f"📊 基于 {len(retrieved_molecules)} 个分子数据生成回答...")
            else:
                await self._send_status(websocket, "知识库检索完成，未找到匹配分子。")
        
        # 构建提示词
        summarize_workflow = self.config.get("agent", {}).get(
            "summarize_workflow_results",
            False,
        )
        is_workflow_result = bool(
            agent_result and agent_result.get("workflow_plan")
        )
        if (
            agent_used
            and agent_response
            and is_workflow_result
            and (not summarize_workflow or property_answer is not None or comparison_answer is not None or reverse_answer is not None)
        ):
            full_response = agent_response
            await websocket.send_text(json.dumps({
                "type": "complete",
                "content": full_response,
                "status": "succeeded",
            }, ensure_ascii=False))
            self._append_history(history, {
                "user": message,
                "agent_used": True,
                "agent_response": agent_response,
                "rag_enabled": enable_rag,
                "molecules_retrieved": len(retrieved_molecules),
                "assistant": full_response,
                **({"property_evidence": property_evidence}
                   if scientific_context_enabled and property_evidence is not None else {}),
                **({"activity_evidence": activity_evidence} if activity_evidence is not None else {}),
                **({'reverse_target_evidence': reverse_target_evidence} if reverse_target_evidence is not None else {}),
            })
            return docking_result

        if agent_used and agent_response:
            try:
                prompt = self._build_prompt_with_agent(message, agent_response, rag_context, retrieved_molecules, agent_result=agent_result)
            except InputBudgetExceeded:
                # Do not let the model reinterpret evidence without its limitations.
                content = agent_response + (
                    "\n\n工具来源或错误信息超过解读预算，未进行模型总结。"
                    "以上为原始工具答复，不表示全部步骤成功；请查看结构化工具结果。"
                )
                await websocket.send_text(json.dumps({
                    "type": "complete", "content": content,
                    "error": {"code": "interpretation_budget_exceeded"},
                }, ensure_ascii=False))
                self._append_history(history, {"user": message, "assistant": content, "agent_used": True})
                # Same post-delivery evidence hook as normal presentation; this
                # captured execution result is not a preparation or approval.
                return docking_result
        else:
            prompt = self._build_prompt(
                message,
                rag_context,
                retrieved_molecules,
                conversation_history=history,
            )
        
        # 生成响应
        await self._send_status(websocket, "🤖 AI正在生成回答...")
        
        model_max_tokens = self._model_max_tokens(request_model)

        use_nonstream = not self.config.get("inference", {}).get("stream", True)
        failure = None
        if not use_nonstream:
            try:
                stream = stream_for_chat(
                    request_model, prompt, temperature, model_max_tokens)
            except OllamaGenerationError as exc:
                failure = exc
                stream = None
            except Exception:
                # An unknown legacy adapter may fail while constructing its iterator.
                # Only that adapter path may fall back to non-streaming generation.
                logger.error("Legacy stream setup failed; details omitted")
                use_nonstream = True
                stream = None
            if stream is not None:
                chunk_count = 0
                try:
                    while True:
                        try:
                            chunk = await stream.__anext__()
                        except StopAsyncIteration:
                            break
                        except OllamaGenerationError as exc:
                            failure = exc
                            break
                        except Exception:
                            # Preserve the existing fallback for non-Ollama legacy adapters.
                            # Known Ollama strict failures are always handled above.
                            logger.error("Legacy stream generation failed; details omitted")
                            use_nonstream = True
                            break
                        if chunk:
                            full_response += chunk
                            chunk_count += 1
                            await websocket.send_text(json.dumps({
                                "type": "stream", "content": chunk
                            }))
                            if chunk_count % 5 == 0:
                                await asyncio.sleep(0.001)
                finally:
                    close = getattr(stream, "aclose", None)
                    if callable(close):
                        await finish_on_cancel(close())
        if failure is not None:
            await self._send_model_failure(websocket, failure)
            return

        if use_nonstream:
            try:
                full_response = await generate_for_chat(
                    request_model, prompt, temperature=temperature,
                    max_tokens=model_max_tokens, strict_errors=True)
            except OllamaGenerationError as exc:
                failure = exc
            if failure is not None:
                await self._send_model_failure(websocket, failure)
                return

        full_response, warning_chunk, completion_meta = self._finalize_model_response(
            full_response, request_model)
        if warning_chunk and not use_nonstream:
            await websocket.send_text(json.dumps({
                "type": "stream", "content": warning_chunk,
            }, ensure_ascii=False))
        await websocket.send_text(json.dumps({
            "type": "complete", "content": full_response,
            **completion_meta, "status": "succeeded",
        }, ensure_ascii=False))
        
        # 保存本轮结果并限制历史长度
        self._append_history(history, {
            "user": message,
            "agent_used": agent_used,
            "agent_response": agent_response if agent_used else None,
            "rag_enabled": enable_rag,
            "molecules_retrieved": len(retrieved_molecules),
            "assistant": full_response,
            **({"property_evidence": property_evidence}
               if scientific_context_enabled and property_evidence is not None else {}),
            **({"activity_evidence": activity_evidence} if activity_evidence is not None else {}),
            **({'reverse_target_evidence': reverse_target_evidence} if reverse_target_evidence is not None else {}),
        })
        return docking_result

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

    async def _execute_agent(
        self,
        message: str,
        temperature: float = 0.7,
        mol_count: int | None = None,
        active_skill=None,
        event_callback=None,
        enable_rag: bool = True,
        enable_tools: bool = True,
        session_id: str | None = None,
        resolved_molecule=None,
    ):
        """在线程池中执行 Agent，并转发执行事件。"""
        try:
            import inspect

            validated_mol_count = preflight_generation_request(
                message,
                mol_count,
                count_supplied=mol_count is not None,
                field="mol_count",
            )
            if validated_mol_count is not None:
                mol_count = validated_mol_count

            execute_parameters = inspect.signature(
                self.agent_system.execute
            ).parameters
            execute_kwargs = {
                "temperature": temperature,
                "active_skill": active_skill,
            }
            if session_id is not None:
                execute_kwargs["session_id"] = session_id
            if resolved_molecule is not None:
                execute_kwargs["resolved_molecule"] = resolved_molecule
            if mol_count is not None:
                execute_kwargs["mol_count"] = mol_count
            if "event_callback" in execute_parameters:
                execute_kwargs["event_callback"] = event_callback
            if "capabilities" in execute_parameters:
                execute_kwargs["capabilities"] = {
                    "rag": bool(enable_rag),
                    "scientific_tools": bool(enable_tools),
                }
            # 在线程池中执行同步的agent.execute，传递temperature和mol_count参数
            result = await owned_allocation_call(
                self.agent_system.execute, message, **execute_kwargs)
            return result
        except AllocationStopped:
            raise
        except GenerationRequestError as exc:
            return {
                "success": False,
                "final_answer": "Invalid molecular generation request",
                "tools_used": [],
                "tool_results": {},
                "error": {
                    "code": "invalid_input",
                    "message": str(exc),
                    "details": generation_request_error_details(exc),
                },
            }
        except Exception:
            logger.error("Agent execution error; exception details omitted")
            return self._agent_failure_envelope()

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
