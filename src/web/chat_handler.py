"""
聊天处理模块 - WebSocket 消息处理（性能优化版）
"""
import json
import asyncio
import logging
import time
from collections.abc import Mapping
from typing import List, Dict, Any
from fastapi import WebSocket, WebSocketDisconnect

from src.agent.persistence.redaction import (
    contains_secret_material,
    redact_sensitive,
    sanitize_bounded,
)
from src.agent.contracts import AgentResult, CandidateSet, ObservationStatus
from src.agent.contracts.generation_request import (
    GenerationRequestError,
    generation_request_error_details,
    has_generation_intent,
    preflight_generation_request,
)
from src.agent.routing.hybrid import SMILES_PATTERN
from src.agent.utils.validators import InputValidator
from src.web.prompt_budget import (
    InputBudgetExceeded, character_limit, validate_question,
)
from src.web import agent_result_presentation as result_presentation
from src.web import chat_prompt_builder as prompt_builder
from src.web.agent_result_presentation import (
    _AGENT_FAILURE_FALLBACK,
    _AGENT_FAILURE_CONTENT_MAX_CHARS,
    _AGENT_FAILURE_WARNING_MAX_CHARS,
    _AGENT_FAILURE_WARNING_LIMIT,
)
from src.web.models import generate_for_chat
from src.web.model_lifecycle import model_request, finish_on_cancel
from src.web.rag_presentation import format_rag_context, rag_info_molecule

logger = logging.getLogger(__name__)

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
        return await process_decision_message(
            self, websocket, context=context, decision_loop=decision_loop,
            request_kind=request_kind, allowed_tools=allowed_tools, required_tools=required_tools,
            requirements=requirements, continuation_id=continuation_id, clarified_query=clarified_query,
            worker_owner=worker_owner,
            cancel_event=cancel_event,
            **({'admission_carry': admission_carry, 'admission_exchange': admission_exchange}
               if admission_carry is not None else {}),
        )
    
    async def handle_websocket(self, websocket: WebSocket):
        """处理 WebSocket 连接"""
        if self.decision_runtime is not None:
            await self.decision_runtime.handle_websocket(handler=self, websocket=websocket)
            return
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

                # Long-lived sockets must not reuse revoked or corrupt configuration.
                if self.refresh_model_config is not None:
                    try:
                        await self.refresh_model_config()
                    except Exception:
                        await websocket.send_text(json.dumps({
                            "type": "error",
                            "message": "本机模型配置不可用；请检查配置文件与目录权限。",
                        }))
                        await websocket.close(code=1011)
                        return

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
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "message": f"处理错误: {str(e)[:100]}..."
                    }))
                except:
                    pass
    
    @model_request
    async def _process_message(self, websocket: WebSocket, message: str,
                              enable_rag: bool, enable_tools: bool, 
                              rag_count: int = 5, temperature: float = 0.7,
                              mol_count: int | None = None,
                              conversation_history: List[Dict[str, Any]] | None = None,
                              session_id: str | None = None,
                              reference=None, selection=None):
        """处理用户消息 - 性能优化版"""
        # Hold one client for generation, retries and completion metadata.
        request_model = self.model
        history = (
            conversation_history
            if conversation_history is not None
            else self.conversation_history
        )
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
        
        # 初始化 Agent 执行状态
        agent_task = None
        agent_event_queue = None
        live_agent_events = 0

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
        routing_message = message
        if self.scientific_references is not None:
            try:
                resolved_molecule = await asyncio.to_thread(
                    self.scientific_references.resolve, message, reference, selection,
                    session_id=session_id, enable_tools=enable_tools)
            except ValueError:
                from .scientific_references import REFERENCE_CLARIFICATION
                await websocket.send_text(json.dumps({"type": "complete", "content": REFERENCE_CLARIFICATION}, ensure_ascii=False))
                return
            if resolved_molecule is not None:
                routing_message = resolved_molecule.routing_query(message)
        molecular_input = InputValidator().analyze_molecular_input(routing_message)
        clarification = self._molecular_input_clarification(molecular_input)
        skill_router = getattr(self.agent_system, "skill_router", None)
        if (
            not clarification
            and enable_tools
            and skill_router
            and hasattr(skill_router, "decide")
        ):
            route_decision = await asyncio.to_thread(
                skill_router.decide,
                routing_message,
                getattr(self.agent_system, "llm", None),
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
            and self.agent_system.should_use_tools(routing_message)
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
                    message,
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
            finally:
                # Event delivery may fail while the executor thread still owns
                # the model. Drain it before releasing the request lease.
                if not agent_task.done():
                    await finish_on_cancel(agent_task)

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
                    await websocket.send_text(json.dumps(self._rag_info_payload(
                        retrieved_molecules,
                        f"✅ 找到 {len(retrieved_molecules)} 个相关分子",
                    )))
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
            self._append_history(history, {
                "user": message,
                "agent_used": True,
                "agent_response": agent_response,
                "rag_enabled": enable_rag,
                "molecules_retrieved": len(retrieved_molecules),
                "assistant": full_response,
            })
            return

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
                return
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

        if self.config.get("inference", {}).get("stream", True):
            try:
                chunk_count = 0
                stream = request_model.stream_generate(
                    prompt,
                    temperature=self.config.get("inference", {}).get("temperature", 0.7),
                    max_tokens=model_max_tokens,
                )
                try:
                    async for chunk in stream:
                        if chunk:
                            full_response += chunk
                            chunk_count += 1
                            await websocket.send_text(json.dumps({
                                "type": "stream",
                                "content": chunk
                            }))
                            # 让出事件循环，允许处理其他连接。
                            if chunk_count % 5 == 0:  # 每5个chunk才sleep一次
                                await asyncio.sleep(0.001)
                finally:
                    # Consumer errors do not close async generators implicitly.
                    # Keep the request lease until its HTTP response has drained.
                    close = getattr(stream, "aclose", None)
                    if callable(close):
                        await finish_on_cancel(close())
                
                full_response, warning_chunk, completion_meta = (
                    self._finalize_model_response(full_response, request_model)
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
                    request_model,
                    prompt,
                    temperature=self.config.get("inference", {}).get("temperature", 0.7),
                    max_tokens=model_max_tokens,
                )
                full_response, _, completion_meta = self._finalize_model_response(
                    full_response, request_model
                )
                await websocket.send_text(json.dumps({
                    "type": "message",
                    "message": full_response,
                    **completion_meta,
                }, ensure_ascii=False))
        else:
            full_response = await generate_for_chat(
                request_model,
                prompt,
                temperature=self.config.get("inference", {}).get("temperature", 0.7),
                max_tokens=model_max_tokens,
            )
            full_response, _, completion_meta = self._finalize_model_response(
                full_response, request_model
            )
            await websocket.send_text(json.dumps({
                "type": "message",
                "message": full_response,
                **completion_meta,
            }, ensure_ascii=False))
        
        # 保存本轮结果并限制历史长度
        self._append_history(history, {
            "user": message,
            "agent_used": agent_used,
            "agent_response": agent_response if agent_used else None,
            "rag_enabled": enable_rag,
            "molecules_retrieved": len(retrieved_molecules),
            "assistant": full_response
        })

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
            loop = asyncio.get_event_loop()
            result = await finish_on_cancel(loop.run_in_executor(
                None, 
                lambda: self.agent_system.execute(message, **execute_kwargs)
            ))
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
        return result_presentation.failure_envelope()

    @staticmethod
    def _sanitize_agent_failure_text(
        value: Any,
        *,
        max_chars: int,
    ) -> tuple[str, bool]:
        return result_presentation.sanitize_failure_text(value, max_chars=max_chars)

    @classmethod
    def _sanitize_agent_warnings(cls, raw_warnings: Any) -> list[str]:
        return result_presentation.sanitize_warnings(
            raw_warnings, sanitize_text=cls._sanitize_agent_failure_text,
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
        if "target_clarification_required" in route_decision.reasons:
            from src.agent.contracts.target_request import TARGET_CLARIFICATION

            return TARGET_CLARIFICATION
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
                                rag_context: str, retrieved_molecules: List[Dict[str, Any]],
                                agent_result: Mapping | None = None) -> str:
        return prompt_builder.build_agent_prompt(
            user_message, agent_response, rag_context, config=self.config,
            input_limit=self._input_limit, agent_result=agent_result,
        )
