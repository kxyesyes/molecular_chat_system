"""
聊天处理模块 - WebSocket 消息处理（性能优化版）
"""
import json
import asyncio
import logging
import pandas as pd
import time
from collections.abc import Mapping
from typing import List, Dict, Any
from fastapi import WebSocket, WebSocketDisconnect

from src.agent.persistence.redaction import (
    contains_sensitive_text,
    redact_sensitive,
    sanitize_bounded,
    sanitize_sensitive_text,
)
from src.agent.contracts import CandidateSet, ObservationStatus
from src.agent.contracts.generation_request import (
    GenerationRequestError,
    generation_request_error_details,
    has_generation_intent,
    preflight_generation_request,
)
from src.agent.routing.hybrid import SMILES_PATTERN
from src.agent.utils.validators import InputValidator
from src.web.models import generate_for_chat

logger = logging.getLogger(__name__)

_AGENT_FAILURE_FALLBACK = "科学计算未成功完成，请检查输入或工具状态后重试。"
_AGENT_FAILURE_CONTENT_MAX_CHARS = 1024
_AGENT_FAILURE_WARNING_MAX_CHARS = 256
_AGENT_FAILURE_WARNING_LIMIT = 20
_AGENT_EVENT_TEXT_MAX_CHARS = 512
_AGENT_EVENT_KEY_MAX_CHARS = 128
_AGENT_EVENT_MAX_DEPTH = 6
_AGENT_EVENT_MAX_ITEMS = 64
_AGENT_FAILURE_EVENT_TYPES = frozenset({
    "tool_failed",
    "task_failed",
    "task_rejected",
    "task_cancelled",
})


class ChatHandler:
    """聊天消息处理器"""
    
    def __init__(self, model, rag_service, agent_system, config):
        self.model = model
        self.rag_service = rag_service
        self.agent_system = agent_system
        self.config = config
        self.conversation_history = []
    
    async def handle_websocket(self, websocket: WebSocket):
        """处理 WebSocket 连接"""
        await websocket.accept()
        conversation_history: List[Dict[str, Any]] = []
        
        # 连接建立后立即发送连接成功消息
        try:
            await websocket.send_text(json.dumps({
                "type": "connection_ready",
                "message": "连接成功",
                "model_status": "ready",
                "timestamp": time.time()
            }))
            logger.info("WebSocket连接已建立，发送就绪消息")
        except Exception as e:
            logger.error(f"发送连接就绪消息失败: {e}")
        
        try:
            while True:
                data = await websocket.receive_text()
                message_data = json.loads(data)

                # 处理 ping 消息
                if message_data.get("type") == "ping":
                    await websocket.send_text(json.dumps({
                        "type": "pong",
                        "timestamp": message_data.get("timestamp", 0)
                    }))
                    continue

                message = message_data.get("message", "")
                enable_rag = message_data.get("enable_rag", True)
                enable_tools = message_data.get("enable_tools", True)
                rag_count = message_data.get("rag_count", 5)  # 获取RAG检索数量
                temperature = message_data.get("temperature", 0.7)  # 获取生成温度
                mol_count = message_data.get("mol_count")  # 未提供时由生成请求解析器决定

                if not message.strip():
                    continue

                # 发送确认
                await self._send_status(websocket, "Processing your message...")

                # 处理消息，传递高级配置
                await self._process_message(
                    websocket,
                    message,
                    enable_rag,
                    enable_tools,
                    rag_count,
                    temperature,
                    mol_count,
                    conversation_history=conversation_history,
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
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "message": f"处理错误: {str(e)[:100]}..."
                    }))
                except:
                    pass
    
    async def _process_message(self, websocket: WebSocket, message: str, 
                              enable_rag: bool, enable_tools: bool, 
                              rag_count: int = 5, temperature: float = 0.7,
                              mol_count: int | None = None,
                              conversation_history: List[Dict[str, Any]] | None = None):
        """处理用户消息 - 性能优化版"""
        history = (
            conversation_history
            if conversation_history is not None
            else self.conversation_history
        )
        start_time = time.time()
        full_response = ""
        agent_used = False
        agent_response = ""
        agent_result = None
        
        # 优化1: 并行执行Agent和RAG检索（如果都启用）
        agent_task = None
        agent_event_queue = None
        live_agent_events = 0
        rag_task = None

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
            history.append(
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
            if len(history) > 20:
                del history[:-20]
            return
        if validated_mol_count is not None:
            mol_count = validated_mol_count
        
        # Agent 处理（使用temperature参数）
        matched_skill = None
        route_decision = None
        molecular_input = InputValidator().analyze_molecular_input(message)
        clarification = self._molecular_input_clarification(molecular_input)
        skill_router = getattr(self.agent_system, "skill_router", None)
        if (
            not clarification
            and enable_tools
            and skill_router
            and hasattr(skill_router, "decide")
        ):
            route_decision = skill_router.decide(
                message,
                llm=getattr(self.agent_system, "llm", None),
            )
            clarification = self._route_clarification(route_decision)
        if clarification:
            await websocket.send_text(json.dumps({
                "type": "complete",
                "content": clarification,
            }, ensure_ascii=False))
            history.append({
                "user": message,
                "agent_used": False,
                "agent_response": None,
                "rag_enabled": False,
                "molecules_retrieved": 0,
                "assistant": clarification,
            })
            if len(history) > 20:
                del history[:-20]
            return

        should_use_agent = bool(
            route_decision and route_decision.selected_skill
        ) if route_decision is not None else bool(
            enable_tools
            and self.agent_system
            and self.agent_system.should_use_tools(message)
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
                    matched_skill = skill_router.route(
                        message,
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
                    message,
                    temperature,
                    mol_count,
                    matched_skill,
                    event_callback=on_agent_event,
                    enable_rag=enable_rag,
                    enable_tools=enable_tools,
                )
            )
        
        # 等待并行任务完成
        retrieved_molecules = []
        rag_context = ""
        rag_task = None # 保持变量兼容性，虽然不再硬编码启动任务
        
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
                if agent_result.get('success'):
                    agent_response = agent_result.get('final_answer', '')
                    agent_used = True
                    tools_used = agent_result.get('tools_used', [])
                    active_skill = agent_result.get('active_skill', '')
                    
                    # 检查是否有 RAG 工具结果，并将其注入 UI
                    tool_results = agent_result.get('tool_results', {})
                    rag_res = (
                        tool_results.get('rag_search')
                        or tool_results.get('rag_database_search')
                    )
                    if rag_res:
                        if rag_res.get('success') and rag_res.get('data'):
                            retrieved_molecules = rag_res['data']
                            rag_context = self._format_rag_context(retrieved_molecules)
                            
                            # 发送 RAG 卡片到前端
                            await websocket.send_text(json.dumps({
                                "type": "rag_info",
                                "molecules": [
                                    {
                                        "smiles": mol.get("SMILES", ""),
                                        "similarity": round(float(mol.get("similarity_score", 0)), 3),
                                        "properties": {
                                            k: (round(float(v), 2) if isinstance(v, (int, float)) else str(v))
                                            for k, v in mol.items()
                                            if k not in ["SMILES", "similarity_score"] and v is not None
                                        }
                                    }
                                    for mol in retrieved_molecules[:rag_count]
                                ],
                                "message": f"✅ 技能自动触发：从库中找到 {len(retrieved_molecules)} 个相关分子"
                            }))

                    skill_info = f" (当前技能: {active_skill})" if active_skill else ""

                    await self._send_molecule_candidate_events(
                        websocket,
                        agent_result,
                    )
                    await websocket.send_text(json.dumps({
                        "type": "agent_result",
                        "message": f"✅ 智能代理完成，使用工具: {', '.join(tools_used)}{skill_info}",
                        "active_skill": active_skill
                    }))
                else:
                    await self._finish_terminal_agent_failure(
                        websocket,
                        agent_result,
                        message=message,
                        enable_rag=enable_rag,
                        history=history,
                    )
                    return
            except Exception:
                logger.error(
                    "Agent result processing failed; exception details omitted"
                )
                agent_result = self._agent_failure_envelope()
                await self._finish_terminal_agent_failure(
                    websocket,
                    agent_result,
                    message=message,
                    enable_rag=enable_rag,
                    history=history,
                )
                return
        
        # 如果没有触发 Agent 技能但启用了 RAG 开关，则执行传统 RAG（保留向后兼容性）
        if (
            not agent_used
            and enable_rag
            and self.rag_service.is_initialized
            and self._should_use_legacy_rag(message)
        ):
            try:
                await self._send_status(websocket, f"🔍 搜索相关分子数据 (top {rag_count})...")
                retrieved_molecules = await self.rag_service.search_similar_molecules(message, rag_count)
                if retrieved_molecules:
                    rag_context = self._format_rag_context(retrieved_molecules)
                    await websocket.send_text(json.dumps({
                        "type": "rag_info",
                        "molecules": [
                            {
                                "smiles": mol.get("SMILES", ""),
                                "similarity": round(float(mol.get("similarity_score", 0)), 3),
                                "properties": {
                                    k: (round(float(v), 2) if isinstance(v, (int, float)) else str(v))
                                    for k, v in mol.items()
                                    if k not in ["SMILES", "similarity_score"] and v is not None
                                }
                            }
                            for mol in retrieved_molecules
                        ],
                        "message": f"✅ 找到 {len(retrieved_molecules)} 个相关分子"
                    }))
                    await self._send_status(websocket, f"📊 基于 {len(retrieved_molecules)} 个分子数据生成回答...")
            except Exception as e:
                logger.error(f"Legacy RAG search failed: {e}")
        
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
            and not summarize_workflow
        ):
            full_response = agent_response
            await websocket.send_text(json.dumps({
                "type": "complete",
                "content": full_response,
            }, ensure_ascii=False))
            history.append({
                "user": message,
                "agent_used": True,
                "agent_response": agent_response,
                "rag_enabled": enable_rag,
                "molecules_retrieved": len(retrieved_molecules),
                "assistant": full_response,
            })
            if len(history) > 20:
                del history[:-20]
            return

        if agent_used and agent_response:
            prompt = self._build_prompt_with_agent(message, agent_response, rag_context, retrieved_molecules)
        else:
            prompt = self._build_prompt(
                message,
                rag_context,
                retrieved_molecules,
                conversation_history=history,
            )
        
        # 优化3: 缩短提示词长度（为4GB显存优化）
        if len(prompt) > 3000:  # 降低阈值从4000到3000
            logger.warning(f"提示词过长({len(prompt)}字符)，进行截断")
            prompt = prompt[:3000] + "\n\n[内容已截断，请基于以上信息回答]"
        
        # 生成响应
        await self._send_status(websocket, "🤖 AI正在生成回答...")
        
        model_max_tokens = self._model_max_tokens()

        if self.config.get("inference", {}).get("stream", True):
            try:
                chunk_count = 0
                async for chunk in self.model.stream_generate(
                    prompt,
                    temperature=self.config.get("inference", {}).get("temperature", 0.7),
                    max_tokens=model_max_tokens,
                ):
                    if chunk:
                        full_response += chunk
                        chunk_count += 1
                        await websocket.send_text(json.dumps({
                            "type": "stream",
                            "content": chunk
                        }))
                        # 优化4: 减少sleep时间，提高响应速度
                        if chunk_count % 5 == 0:  # 每5个chunk才sleep一次
                            await asyncio.sleep(0.001)
                
                full_response, warning_chunk, completion_meta = (
                    self._finalize_model_response(full_response)
                )
                if warning_chunk:
                    await websocket.send_text(json.dumps({
                        "type": "stream",
                        "content": warning_chunk,
                    }, ensure_ascii=False))

                completion_payload = {
                    "type": "complete",
                    "content": full_response,
                    **completion_meta,
                }
                await websocket.send_text(json.dumps(
                    completion_payload,
                    ensure_ascii=False,
                ))
                
                elapsed = time.time() - start_time
                logger.info(f"消息处理完成，总耗时: {elapsed:.2f}秒，生成{len(full_response)}字符")
                
            except Exception as stream_error:
                logger.error(f"流式生成错误: {stream_error}")
                await self._send_status(websocket, "⚠️ 切换到标准生成模式...")
                full_response = await generate_for_chat(
                    self.model,
                    prompt,
                    temperature=self.config.get("inference", {}).get("temperature", 0.7),
                    max_tokens=model_max_tokens,
                )
                full_response, _, completion_meta = self._finalize_model_response(
                    full_response
                )
                await websocket.send_text(json.dumps({
                    "type": "message",
                    "message": full_response,
                    **completion_meta,
                }, ensure_ascii=False))
        else:
            full_response = await generate_for_chat(
                self.model,
                prompt,
                temperature=self.config.get("inference", {}).get("temperature", 0.7),
                max_tokens=model_max_tokens,
            )
            full_response, _, completion_meta = self._finalize_model_response(
                full_response
            )
            await websocket.send_text(json.dumps({
                "type": "message",
                "message": full_response,
                **completion_meta,
            }, ensure_ascii=False))
        
        # 优化5: 限制对话历史长度，避免内存泄漏
        history.append({
            "user": message,
            "agent_used": agent_used,
            "agent_response": agent_response if agent_used else None,
            "rag_enabled": enable_rag,
            "molecules_retrieved": len(retrieved_molecules),
            "assistant": full_response
        })
        
        # 只保留最近20条对话
        if len(history) > 20:
            del history[:-20]
    
    async def _execute_agent(
        self,
        message: str,
        temperature: float = 0.7,
        mol_count: int | None = None,
        active_skill=None,
        event_callback=None,
        enable_rag: bool = True,
        enable_tools: bool = True,
    ):
        """异步执行Agent（用于并行处理）"""
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
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, 
                lambda: self.agent_system.execute(message, **execute_kwargs)
            )
            return result
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
        return {
            "success": False,
            "status": "failed",
            "final_answer": "",
            "active_skill": "",
            "trace_id": "",
            "error": None,
            "warnings": [],
            "tool_result_sequence": [],
        }

    @staticmethod
    def _sanitize_agent_failure_text(
        value: Any,
        *,
        max_chars: int,
    ) -> tuple[str, bool]:
        if not isinstance(value, str):
            return "", False
        value = value.strip()
        if not value:
            return "", False

        redacted = redact_sensitive(value)
        contains_sensitive = (
            redacted != value or contains_sensitive_text(value)
        )
        sanitized, _ = sanitize_sensitive_text(value, max_chars=max_chars)
        return sanitized, contains_sensitive

    @classmethod
    def _sanitize_agent_warnings(cls, raw_warnings: Any) -> list[str]:
        warnings = []
        if not isinstance(raw_warnings, list):
            return warnings
        for warning in raw_warnings[:_AGENT_FAILURE_WARNING_LIMIT]:
            sanitized, sensitive = cls._sanitize_agent_failure_text(
                warning,
                max_chars=_AGENT_FAILURE_WARNING_MAX_CHARS,
            )
            if (
                sensitive
                or not sanitized
                or sanitized.casefold() == "[redacted]"
            ):
                continue
            warnings.append(sanitized)
        return warnings

    @classmethod
    def _agent_failure_content(cls, agent_result: Dict[str, Any]) -> str:
        """Select a safe, authoritative message for a failed Agent run."""

        def safe_content(value: Any) -> tuple[str, bool]:
            return cls._sanitize_agent_failure_text(
                value,
                max_chars=_AGENT_FAILURE_CONTENT_MAX_CHARS,
            )

        final_answer, sensitive = safe_content(agent_result.get("final_answer"))
        if sensitive:
            return _AGENT_FAILURE_FALLBACK
        if final_answer:
            return final_answer

        error = agent_result.get("error")
        if isinstance(error, Mapping):
            error_message, sensitive = safe_content(error.get("message"))
            if sensitive:
                return _AGENT_FAILURE_FALLBACK
            if error_message:
                return error_message

        sequence = agent_result.get("tool_result_sequence")
        if isinstance(sequence, list):
            for result in sequence:
                if not isinstance(result, Mapping):
                    continue
                is_failed = result.get("success") is False or result.get(
                    "status"
                ) in {"failed", "rejected", "cancelled"}
                if not is_failed:
                    continue
                result_message, sensitive = safe_content(result.get("message"))
                if sensitive:
                    return _AGENT_FAILURE_FALLBACK
                if result_message:
                    return result_message

        return _AGENT_FAILURE_FALLBACK

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
        history.append({
            "user": message,
            "agent_used": True,
            "agent_response": agent_response,
            "rag_enabled": enable_rag,
            "molecules_retrieved": 0,
            "assistant": agent_response,
        })
        if len(history) > 20:
            del history[:-20]

    async def _send_terminal_agent_failure(
        self,
        websocket: WebSocket,
        agent_result: Dict[str, Any],
    ) -> str:
        content = self._agent_failure_content(agent_result)
        raw_status = agent_result.get("status")
        status = (
            raw_status
            if raw_status in {"failed", "rejected", "cancelled"}
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
        warnings = self._sanitize_agent_warnings(agent_result.get("warnings"))

        await self._send_status(websocket, f"⚠️ 代理执行失败: {content}")
        await websocket.send_text(json.dumps({
            "type": "agent_result",
            "message": content,
            "active_skill": active_skill,
            "trace_id": trace_id,
            "status": status,
            "warnings": warnings,
        }, ensure_ascii=False))
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
        if observation.get("success") is not True:
            return None
        status = observation.get("status")
        if status not in {"succeeded", "partial"}:
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

    @classmethod
    async def _send_molecule_candidate_events(
        cls,
        websocket: WebSocket,
        agent_result: Dict[str, Any],
    ) -> None:
        if agent_result.get("status") in {"failed", "rejected", "cancelled"}:
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
    ) -> Any:
        if depth >= _AGENT_EVENT_MAX_DEPTH:
            return value
        if isinstance(value, Mapping):
            sanitized_mapping = {}
            for index, (key, child) in enumerate(value.items()):
                if index >= _AGENT_EVENT_MAX_ITEMS:
                    break
                sanitized_key = cls._safe_agent_event_key(key)
                if not sanitized_key or sanitized_key in sanitized_mapping:
                    continue
                sanitized_mapping[sanitized_key] = cls._sanitize_agent_event_keys(
                    child,
                    depth=depth + 1,
                )
            return sanitized_mapping
        if isinstance(value, list):
            return [
                cls._sanitize_agent_event_keys(child, depth=depth + 1)
                for child in value[:_AGENT_EVENT_MAX_ITEMS]
            ]
        if isinstance(value, tuple):
            return tuple(
                cls._sanitize_agent_event_keys(child, depth=depth + 1)
                for child in value[:_AGENT_EVENT_MAX_ITEMS]
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
        """格式化 RAG 上下文"""
        if not molecules:
            return ""
        
        context_parts = ["Relevant molecular data found:"]
        
        for i, mol in enumerate(molecules, 1):
            smiles = mol.get("SMILES", "Unknown")
            score = mol.get("similarity_score", 0)
            
            context_parts.append(f"{i}. SMILES: {smiles} (similarity: {score:.3f})")
            
            for key, value in mol.items():
                if key not in ["SMILES", "similarity_score"] and pd.notna(value):
                    context_parts.append(f"   {key}: {value}")
        
        return "\n".join(context_parts)
    
    def _build_prompt(self, user_message: str, rag_context: str,
                     retrieved_molecules: List[Dict[str, Any]],
                     conversation_history: List[Dict[str, Any]] | None = None) -> str:
        """构建提示词 - 中文优化版，针对API-key模型"""
        history = (
            conversation_history
            if conversation_history is not None
            else self.conversation_history
        )
        
        # 导入系统提示词
        from src.agent.prompts import DRUG_DESIGN_SYSTEM_PROMPT
        
        # 构建对话上下文
        conversation_context = ""
        if history:
            recent_history = history[-2:]  # 仅2轮对话，降低显存占用
            conversation_context = "\n## 📝 最近对话历史\n"
            for i, entry in enumerate(recent_history, 1):
                conversation_context += f"\n**对话 {i}:**\n"
                conversation_context += f"用户: {entry['user']}\n"
                # 如果使用了工具，显示工具信息
                if entry.get('agent_used'):
                    conversation_context += f"（使用了智能工具）\n"
                conversation_context += f"助手: {entry['assistant'][:500]}...\n"  # 缩短到500字符

        # 分析用户意图
        user_message_lower = user_message.lower()
        intent_analysis = self._analyze_user_intent(user_message_lower)
        
        # 构建提示词
        prompt_parts = [DRUG_DESIGN_SYSTEM_PROMPT]
        
        # 添加对话历史
        if conversation_context:
            prompt_parts.append(conversation_context)
        
        # 添加意图分析
        prompt_parts.append(f"\n## 🎯 当前任务分析\n")
        prompt_parts.append(f"**用户意图**: {intent_analysis['intent_type']}")
        prompt_parts.append(f"**任务类型**: {intent_analysis['task_description']}")
        
        # 添加RAG检索结果
        if rag_context:
            prompt_parts.append(f"\n## 📊 相关分子数据（RAG检索）\n")
            prompt_parts.append(f"系统从分子数据库中检索到 {len(retrieved_molecules)} 个相关分子：\n")
            prompt_parts.append(rag_context)
            prompt_parts.append(f"\n**使用建议**: {intent_analysis['rag_usage']}")
            prompt_parts.append("\n⚠️ **重要提示**: 你在回答时无需重复列出这些数值属性。请专注于提供分析、建议和解释。")
        
        # 添加工具使用提示
        if intent_analysis['suggested_tools']:
            prompt_parts.append(f"\n## 🔧 建议使用的工具\n")
            for tool in intent_analysis['suggested_tools']:
                prompt_parts.append(f"- {tool}")
        
        # 添加用户问题
        prompt_parts.append(f"\n## 💬 用户问题\n{user_message}")
        
        # 添加回答指导
        prompt_parts.append(f"\n## 📋 回答要求\n")
        prompt_parts.append("1. 如果需要使用工具，系统会自动调用（智能工具已启用）")
        prompt_parts.append("2. 基于工具结果和检索数据提供专业分析")
        prompt_parts.append("3. 使用清晰的中文，包含具体数值和评估")
        prompt_parts.append("4. 提供可操作的药物化学建议")
        prompt_parts.append("5. 保持专业但易懂的表达风格")
        prompt_parts.append(
            "6. 对问候或概念问答直接回答当前问题，默认保持精炼；除非用户明确询问，"
            "不要展开完整平台功能清单"
        )
        prompt_parts.append(
            "7. 科学数值只能引用真实工具结果；未调用对应工具时必须明确说明不可用"
        )
        
        prompt_parts.append("\n请开始回答：")

        return "\n".join(prompt_parts)

    def _model_max_tokens(self) -> int:
        """Select an output budget without leaking local GPU limits to remote APIs."""
        inference = self.config.get("inference", {})
        local_limit = int(inference.get("max_tokens", 1500))
        if getattr(self.model, "provider_name", None):
            return int(inference.get("external_max_tokens", 4096))
        return local_limit

    def _finalize_model_response(self, content: str):
        """Expose safe completion state and make provider truncation visible."""
        metadata = getattr(self.model, "last_response_metadata", {}) or {}
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
        
        # 合成类任务
        elif any(kw in user_message_lower for kw in ['合成', '逆合成', '合成路线', 'synthesis', 'retrosynthesis']):
            intent['intent_type'] = '合成规划'
            intent['task_description'] = '规划分子的合成路线'
            intent['rag_usage'] = '提供合成策略参考'
            intent['suggested_tools'] = ['rxn_chemistry_agent - 反应预测与逆合成']
        
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
                                rag_context: str, retrieved_molecules: List[Dict[str, Any]]) -> str:
        """构建带 Agent 结果的提示词 - 中文优化版"""
        
        system_prompt = """# 🧬 药物设计AI助手 - 结果解读模式

## 你的任务
智能工具已经完成了专业计算和分析，你需要：
1. 📊 **解读工具结果** - 将技术数据转化为易懂的专业见解
2. 💡 **提供深度分析** - 基于结果给出药物化学建议
3. 🎯 **关联用户需求** - 确保回答直接解决用户问题
4. 📈 **给出优化方向** - 提供可操作的改进建议

## 回答风格
- 使用专业但易懂的中文
- 突出关键发现和重要数值
- 提供具体的药物化学见解
- 给出实用的优化建议
"""

        prompt_parts = [system_prompt]
        
        # 添加工具分析结果
        prompt_parts.append(f"\n## 🔧 智能工具分析结果\n")
        prompt_parts.append("系统已使用专业工具完成分析，结果如下：\n")
        prompt_parts.append(agent_response)
        
        # 添加RAG检索数据
        if rag_context:
            prompt_parts.append(f"\n## 📊 相关分子数据（供对比参考）\n")
            prompt_parts.append(rag_context)
            prompt_parts.append("\n**使用建议**: 将工具分析结果与这些相似分子对比，提供更全面的评估")
            prompt_parts.append("\n⚠️ **重要提示**: 你在回答时无需重复分子属性，只需提供专业的分析和建议。")
        
        # 添加用户原始问题
        prompt_parts.append(f"\n## 💬 用户原始问题\n{user_message}")
        
        # 添加回答指导
        prompt_parts.append(f"\n## 📋 回答要求\n")
        prompt_parts.append("1. **总结关键发现** - 提炼工具结果中的核心信息")
        prompt_parts.append("2. **专业解读** - 解释数值的药物化学意义")
        prompt_parts.append("3. **优缺点分析** - 客观评价分子的优势和不足")
        prompt_parts.append("4. **优化建议** - 给出具体的改进方向")
        prompt_parts.append("5. **结论** - 简明扼要地回答用户问题")
        
        prompt_parts.append("\n请基于工具结果提供专业的解读和建议：")

        return "\n".join(prompt_parts)
