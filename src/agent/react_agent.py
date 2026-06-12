#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ReAct框架实现 - 让LLM进行推理(Reasoning)和行动(Acting)
"""

import json
import logging
import re
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
from .prompts import PREFIX, SUFFIX, REACT_FORMAT, SIMPLE_TOOL_TEMPLATE
from .router import SkillRouter

logger = logging.getLogger(__name__)


@dataclass
class ReActStep:
    """ReAct步骤数据类"""
    thought: str  # 推理/思考
    action: Optional[str] = None  # 行动/工具调用
    action_input: Optional[str] = None  # 行动输入
    observation: Optional[str] = None  # 观察/工具结果
    final_answer: Optional[str] = None  # 最终答案


class ReActMolecularAgent:
    """基于ReAct框架的分子agent"""

    def __init__(self, llm=None):
        self.llm = llm
        self.tools = {}
        self.max_iterations = 5  # 最大推理循环次数
        self._active_skill = None  # 当前激活的技能
        self._initialize_tools()
        # 初始化 Skill 路由器
        try:
            self.skill_router = SkillRouter()
            logger.info("✅ SkillRouter 初始化成功")
        except Exception as e:
            logger.warning(f"⚠️ SkillRouter 初始化失败，将使用传统模式: {e}")
            self.skill_router = None

    def _initialize_tools(self):
        """初始化工具集合 - 使用统一的工具注册表"""
        try:
            from .tools import get_all_tools
            all_tools = get_all_tools()

            for tool in all_tools:
                self.tools[tool.name] = tool
                logger.info(f"Registered tool: {tool.name}")

            logger.info(f"✅ 共注册 {len(self.tools)} 个工具: {list(self.tools.keys())}")

        except Exception as e:
            logger.error(f"Tool initialization failed: {e}")
            raise

    def should_use_tools(self, query: str) -> bool:
        """判断是否需要使用工具处理查询"""
        # 检查是否包含SMILES分子结构
        if self._contains_smiles(query):
            return True
        
        # 检查是否包含工具相关的关键词
        tool_keywords = [
            # 属性计算
            '分子量', '计算', '属性', 'logp', 'tpsa', 'qed', 'lipinski',
            # ADMET预测
            'adme', 'admet', '吸收', '分布', '代谢', '排泄', '毒性',
            # 类药性评估
            '类药', '类药性', 'druglikeness', 'drug-likeness',
            # 分子对接
            '对接', 'docking', '结合', '受体', 'receptor',
            # 反应预测
            '合成', '逆合成', '反应', 'reaction', 'retrosynthesis',
            # 分子生成
            '生成', '设计', 'generate', 'design', '优化'
        ]
        
        query_lower = query.lower()
        for keyword in tool_keywords:
            if keyword in query_lower:
                logger.info(f"检测到工具关键词: {keyword}")
                return True
        
        # 检查每个工具的should_use方法
        for tool_name, tool in self.tools.items():
            if hasattr(tool, 'should_use') and tool.should_use(query):
                logger.info(f"工具 {tool_name} 应该用于此查询")
                return True
        
        logger.info("未检测到需要使用工具的查询")
        return False

    def execute(self, query: str, temperature: float = 0.7, mol_count: int = 5, active_skill=None) -> Dict[str, Any]:
        """执行ReAct推理循环 - 集成 Skill 路由"""
        import time
        from .metrics import metrics_system
        start_time = time.time()
        
        self.current_temperature = temperature
        self.current_mol_count = mol_count
        result = {
            'query': query,
            'success': True,
            'steps': [],
            'final_answer': '',
            'reasoning_trace': [],
            'tools_used': [],
            'active_skill': None  # 记录激活的技能
        }

        logger.info(f"=== ReAct Agent 开始执行 ===")
        logger.info(f"查询: {query}")
        logger.info(f"LLM可用: {self.llm is not None}")

        # ── Skill 路由：按需加载上下文 ──────────────────────────
        self._active_skill = active_skill
        if not self._active_skill and self.skill_router:
            self._active_skill = self.skill_router.route(query, llm=self.llm)
            
        if self._active_skill:
            result['active_skill'] = self._active_skill.name
            logger.info(f"🎯 激活技能: {self._active_skill.name}")
        else:
            logger.info("未匹配到特定技能，使用通用模式")

        try:
            if self._active_skill and hasattr(self._active_skill, "workflow_steps"):
                logger.info(f"使用 WorkflowOrchestrator 执行技能: {self._active_skill.name}")
                return self._execute_workflow_skill(query, result)

            if not self.llm:
                logger.warning("LLM不可用，直接使用回退模式")
                return self._fallback_simple_execution(query)

            # 开始ReAct推理循环
            steps = []
            tools_executed = set()  # 跟踪已执行的工具，避免重复

            # 编排技能需要更多推理步数来串联多个工具
            effective_max_iterations = self.max_iterations
            if self._active_skill and hasattr(self._active_skill, 'max_iterations_override'):
                effective_max_iterations = self._active_skill.max_iterations_override
                logger.info(f"📈 编排技能 {self._active_skill.name} 提升最大迭代次数: {effective_max_iterations}")

            for iteration in range(effective_max_iterations):
                logger.info(f"ReAct iteration {iteration + 1}/{effective_max_iterations}")

                # 生成思考和行动
                step = self._generate_react_step(query, steps)
                steps.append(step)

                logger.info(f"生成的步骤:")
                logger.info(f"  - 思考: {step.thought}")
                logger.info(f"  - 行动: {step.action}")
                logger.info(f"  - 行动输入: {step.action_input}")
                logger.info(f"  - 最终答案: {step.final_answer}")

                # 如果是最终答案，结束循环
                if step.final_answer:
                    result['final_answer'] = step.final_answer
                    break

                # 如果有行动，执行工具
                if step.action and step.action_input:
                    # 检查是否已经执行过相同的工具和输入
                    tool_key = f"{step.action}:{step.action_input}"
                    if tool_key in tools_executed:
                        logger.warning(f"检测到重复工具调用: {tool_key}, 强制结束")
                        # 使用之前的结果作为最终答案
                        if steps:
                            last_observation = None
                            for prev_step in reversed(steps[:-1]):  # 排除当前步骤
                                if prev_step.observation:
                                    last_observation = prev_step.observation
                                    break
                            if last_observation:
                                step.final_answer = last_observation
                                result['final_answer'] = last_observation
                            else:
                                step.final_answer = "分析完成，但无详细结果"
                                result['final_answer'] = "分析完成，但无详细结果"
                        break

                    tools_executed.add(tool_key)

                    logger.info(f"执行工具: {step.action}")
                    observation, raw_result = self._execute_tool(step.action, step.action_input)
                    step.observation = observation
                    logger.info(f"工具执行结果: {observation[:200]}...")

                    if step.action not in result['tools_used']:
                        result['tools_used'].append(step.action)
                    
                    # Store raw results for UI rendering
                    if 'tool_results' not in result:
                        result['tool_results'] = {}
                    result['tool_results'][step.action] = raw_result

                    # 如果工具执行成功，下一轮应该生成最终答案
                    if "错误" not in observation and "失败" not in observation:
                        logger.info("工具执行成功，下一轮将生成最终答案")
                else:
                    # 没有行动，可能是思考步骤，继续下一轮
                    logger.info("当前步骤没有行动，继续下一轮")

            result['steps'] = steps
            result['reasoning_trace'] = [step.thought for step in steps]

            # 如果循环结束后仍没有最终答案，使用最后的工具结果
            if not result['final_answer'] and steps:
                for step in reversed(steps):
                    if step.observation and "错误" not in step.observation:
                        result['final_answer'] = step.observation
                        logger.info("使用最后的工具观察结果作为最终答案")
                        break

                if not result['final_answer']:
                    if result['tools_used']:
                        result['final_answer'] = f"已完成{', '.join(result['tools_used'])}工具的分析，但未获得有效结果。"
                    else:
                        result['final_answer'] = "无法处理您的请求，未找到合适的工具或获得有效结果。"
                    logger.info("生成通用最终答案")

            # ── 事后校验：防止幻觉答案流出 ──────────────────────────
            # 如果没有工具被调用，但查询里含 SMILES，说明 LLM 编造了答案
            if result['final_answer'] and not result['tools_used'] and self._contains_smiles(query):
                logger.warning("⚠️ 工具未被调用但已有最终答案，且查询含SMILES，强制用工具结果替换")
                result['final_answer'] = self._force_tool_result(query)
                result['tools_used'].append('property_calculator')

            # 即使工具被调用，也检查答案里是否含有异常数值（如 QED > 1）
            elif result['final_answer']:
                result['final_answer'] = self._validate_final_answer(query, result['final_answer'])

            logger.info(f"ReAct执行完成:")
            logger.info(f"  - 成功: {result['success']}")
            logger.info(f"  - 使用的工具: {result['tools_used']}")
            logger.info(f"  - 步骤数量: {len(result['steps'])}")
            logger.info(f"  - 最终答案长度: {len(result.get('final_answer', ''))}")

        except Exception as e:
            logger.error(f"ReAct execution failed: {e}")
            result['success'] = False
            result['final_answer'] = f"执行过程中发生错误: {str(e)}"

        # 记录性能指标
        if self._active_skill:
            duration = time.time() - start_time
            metrics_system.record_execution(
                self._active_skill.name, 
                duration, 
                result['success']
            )
            # 自动保存
            metrics_system.save_metrics()

        return result

    def _generate_react_step(self, query: str, previous_steps: List[ReActStep]) -> ReActStep:
        """生成ReAct步骤（思考+行动）"""
        # 如果上一步已有工具观察结果，直接以工具结果作为最终答案
        if previous_steps:
            last_step = previous_steps[-1]
            if last_step.observation and last_step.observation != "工具执行出错":
                return ReActStep(
                    thought="已获得工具执行结果，现在提供最终答案",
                    final_answer=last_step.observation
                )

        # 构建ReAct提示
        prompt = self._build_react_prompt(query, previous_steps)

        try:
            response = self._call_llm_safely(prompt, query)
            step = self._parse_react_response(response)

            # ── 反幻觉拦截器 ────────────────────────────────────────
            # 若 LLM 在第一轮就给出了「最终答案」（跳过工具调用），
            # 但查询里含有 SMILES，则强制先执行属性计算工具。
            if step.final_answer and not previous_steps and self._contains_smiles(query):
                logger.warning(
                    "⚠️ LLM跳过工具调用直接给出最终答案（可能含幻觉数值），"
                    "拦截并强制调用 property_calculator"
                )
                smiles = self._extract_first_smiles(query)
                return ReActStep(
                    thought="检测到SMILES结构，必须先通过工具计算真实数值，禁止直接输出估计值",
                    action="property_calculator",
                    action_input=smiles or query
                )

            return step

        except Exception as e:
            logger.error(f"Failed to generate ReAct step: {e}")
            return ReActStep(
                thought="LLM调用失败，直接使用工具处理请求",
                action="property_calculator",
                action_input=query
            )

    def _extract_first_smiles(self, query: str) -> str:
        """从查询中提取第一个有效SMILES字符串"""
        # 使用 property_calculator 的提取逻辑
        try:
            from .tools.property_calculator import PropertyCalculator
            pc = PropertyCalculator()
            smiles_list = pc.extract_smiles(query)
            if smiles_list:
                return smiles_list[0]
        except Exception:
            pass
        # fallback: 用正则匹配较长的化学式串
        import re
        m = re.search(r'[A-Za-z][A-Za-z0-9@+\-\[\]()=#.\\/:]{8,}', query)
        return m.group(0) if m else query

    def _validate_final_answer(self, query: str, answer: str) -> str:
        """
        对最终答案做数值合法性校验：
        - 若 QED > 1 或 QED 字段与 LogP 字段完全相同，判定为幻觉
        - 尝试用真实工具结果替换
        """
        import re
        # 检测 QED 值
        qed_match = re.search(r'QED[:\s]+([0-9.]+)', answer, re.IGNORECASE)
        if qed_match:
            try:
                qed_val = float(qed_match.group(1))
                if qed_val > 1.0:
                    logger.warning(f"最终答案中 QED={qed_val} 超出合法范围 [0,1]，判定为幻觉，调用真实工具")
                    return self._force_tool_result(query)
            except ValueError:
                pass
        return answer

    def _force_tool_result(self, query: str) -> str:
        """强制调用 property_calculator 获取真实结果"""
        try:
            smiles = self._extract_first_smiles(query)
            observation = self._execute_tool("property_calculator", smiles or query)
            if observation and "错误" not in observation:
                return observation
        except Exception as e:
            logger.error(f"_force_tool_result failed: {e}")
        return "无法获取准确的分子属性，请确认 SMILES 格式正确。"

    def _call_llm_safely(self, prompt: str, query: str) -> str:
        """安全调用LLM，处理同步/异步问题"""
        try:
            if hasattr(self.llm, 'generate') and callable(self.llm.generate):
                # 检查是否是异步方法
                import asyncio
                import inspect

                if inspect.iscoroutinefunction(self.llm.generate):
                    # 异步调用 - 使用更安全的方法
                    try:
                        # 尝试在新的事件循环中运行
                        import threading
                        import queue

                        result_queue = queue.Queue()
                        exception_queue = queue.Queue()

                        def run_async():
                            try:
                                # 在新线程中创建新的事件循环
                                loop = asyncio.new_event_loop()
                                asyncio.set_event_loop(loop)
                                try:
                                    result = loop.run_until_complete(
                                        asyncio.wait_for(self.llm.generate(prompt), timeout=60.0)
                                    )
                                    result_queue.put(result)
                                finally:
                                    loop.close()
                            except Exception as e:
                                exception_queue.put(e)

                        # 在新线程中运行异步调用
                        thread = threading.Thread(target=run_async)
                        thread.start()
                        thread.join(timeout=65.0)  # 稍长于asyncio超时

                        # 检查结果
                        if not exception_queue.empty():
                            raise exception_queue.get()

                        if not result_queue.empty():
                            return result_queue.get()
                        else:
                            raise TimeoutError("LLM调用超时")

                    except Exception as async_e:
                        logger.error(f"异步LLM调用失败: {async_e}")
                        # 智能回退：根据查询内容选择合适的工具
                        return self._intelligent_fallback_response(query)
                else:
                    # 同步调用
                    return self.llm.generate(prompt)
            else:
                logger.warning("LLM没有generate方法，使用回退逻辑")
                # 智能回退：根据查询内容选择合适的工具
                return self._intelligent_fallback_response(query)
        except Exception as e:
            logger.error(f"LLM调用出错: {e}")
            # 智能回退：根据查询内容选择合适的工具
            return self._intelligent_fallback_response(query)

    def _intelligent_fallback_response(self, query: str) -> str:
        """智能回退响应：根据查询内容选择合适的工具 - 优化版"""
        query_lower = query.lower()

        # 优先级0: 分子生成关键词（最高优先级）
        generation_keywords = [
            '随机生成', '生成一个', '生成几个', '生成新', '创建分子', '设计分子',
            'generate', 'create molecule', 'design molecule', 'random molecule'
        ]
        
        # 逆合成分析关键词
        retro_keywords = [
            'retrosynthesis', 'retro', 'synthetic route', 'synthesis pathway',
            '逆合成', '合成路线', '合成路径', '逆向合成', '逆合成路线'
        ]

        # 反应预测关键词
        reaction_keywords = [
            'reaction', 'predict', 'product', 'synthesis', 'react',
            '反应', '预测', '产物', '合成', '反应预测'
        ]

        # 分子对接关键词
        docking_keywords = [
            'docking', 'dock', 'binding', 'affinity', 'receptor', 'ligand',
            '对接', '结合', '亲和力', '受体', '配体', '分子对接'
        ]

        # ADMET关键词
        admet_keywords = [
            'admet', 'absorption', 'distribution', 'metabolism', 'excretion',
            'pharmacokinetic', 'bioavailability', 'clearance', 'half-life',
            '吸收', '分布', '代谢', '排泄', '药代动力学', '生物利用度'
        ]

        # 检查是否包含SMILES
        has_smiles = self._contains_smiles(query)

        # 优先级0: 检查是否是分子生成请求
        if any(word in query_lower for word in generation_keywords):
            logger.info("🎯 智能回退：检测到分子生成需求，选择LLM Molecular Generator")
            return f"思考: 检测到分子生成需求，使用AI模型生成新分子。\n行动: llm_molecular_generator\n行动输入: {query}"

        # 如果没有SMILES且不是生成请求，提示用户
        if not has_smiles and not any(word in query_lower for word in generation_keywords):
            # 检查是否可能是生成意图但表达不明确
            if any(word in query_lower for word in ['生成', 'generate', '创建', 'create', '设计', 'design']):
                logger.info("🎯 智能回退：检测到可能的生成意图，选择LLM Molecular Generator")
                return f"思考: 检测到可能的分子生成需求。\n行动: llm_molecular_generator\n行动输入: {query}"
            else:
                return "思考: 没有检测到有效的分子结构或生成请求。\n最终答案: 请提供具体的分子结构（如SMILES格式）进行分析，或明确说明要生成新分子。"

        # 按优先级检查关键词匹配
        if any(word in query_lower for word in retro_keywords):
            logger.info("智能回退：检测到逆合成分析需求，选择RXN Chemistry Agent")
            return f"思考: 检测到逆合成分析需求，需要使用专门的化学反应工具。\n行动: rxn_chemistry_agent\n行动输入: {query}"
        elif any(word in query_lower for word in reaction_keywords):
            logger.info("智能回退：检测到反应预测需求，选择RXN Chemistry Agent")
            return f"思考: 检测到反应预测需求，需要使用专门的化学反应工具。\n行动: rxn_chemistry_agent\n行动输入: {query}"
        elif any(word in query_lower for word in docking_keywords):
            logger.info("智能回退：检测到分子对接需求，选择Molecular Docking工具")
            return f"思考: 检测到分子对接需求，需要使用分子对接工具。\n行动: molecular_docking\n行动输入: {query}"
        elif any(word in query_lower for word in admet_keywords):
            logger.info("智能回退：检测到ADMET分析需求，选择ADMET预测工具")
            return f"思考: 检测到ADMET分析需求，需要使用ADMET预测工具。\n行动: admet_predictor\n行动输入: {query}"
        else:
            # 默认使用属性计算工具
            logger.info("智能回退：未检测到特殊需求，默认使用属性计算工具")
            return f"思考: 检测到分子结构信息，使用基础属性计算工具进行分析。\n行动: property_calculator\n行动输入: {query}"

    def _build_react_prompt(self, query: str, previous_steps: List[ReActStep]) -> str:
        """构建ReAct提示 - 支持 Skill 动态上下文注入"""

        # ── 根据是否有激活技能，决定工具范围和系统提示 ──────────
        if self._active_skill:
            # 技能模式：只暴露该技能声明的工具
            active_tools = self.skill_router.get_tools_for_skill(
                self._active_skill, self.tools
            )
            skill_system_prompt = self._active_skill.system_prompt
            logger.info(
                f"[Skill模式] 技能={self._active_skill.name}, "
                f"暴露工具={list(active_tools.keys())}"
            )
        else:
            # 通用模式：暴露全部工具
            active_tools = self.tools
            skill_system_prompt = ""

        # 工具描述
        tool_descriptions = []
        tool_names = []
        tool_name_cn = {
            'property_calculator': '基础属性计算器',
            'drug_likeness_assessment': '类药性评估器',
            'admet_predictor': 'ADMET预测器',
            'molecular_docking': '分子对接工具',
            'rxn_chemistry_agent': '反应预测与逆合成',
            'llm_molecular_generator': 'AI分子生成器',
            'reverse_target_predictor': '反向寻靶预测器',
            'target_database_search': '靶点数据库搜索',
            'activity_predictor': '活性预测器 (RG-MPNN)',
        }

        for name, tool in active_tools.items():
            cn_name = tool_name_cn.get(name, name)
            tool_descriptions.append(f"- **{name}** ({cn_name}): {tool.description}")
            tool_names.append(name)

        tools_text = "\n".join(tool_descriptions)
        tool_names_text = ", ".join(tool_names)

        # 构建历史步骤
        agent_scratchpad = ""
        if previous_steps:
            agent_scratchpad = "\n## 📝 推理历史\n"
            for i, step in enumerate(previous_steps, 1):
                agent_scratchpad += f"\n**步骤 {i}:**\n"
                agent_scratchpad += f"思考: {step.thought}\n"
                if step.action:
                    agent_scratchpad += f"行动: {step.action}\n"
                    agent_scratchpad += f"行动输入: {step.action_input}\n"
                    if step.observation:
                        obs = step.observation[:300] + "..." if len(step.observation) > 300 else step.observation
                        agent_scratchpad += f"观察: {obs}\n"

        # ── 组装最终 Prompt ───────────────────────────────────
        if skill_system_prompt:
            # 技能模式：注入技能专属的 System Prompt
            complete_prompt = f"""{skill_system_prompt}

---

## 可用工具
{tools_text}

## ReAct 推理格式
使用以下格式进行推理和行动：

思考: 分析当前情况，决定下一步行动
行动: 选择一个工具，必须是 [{tool_names_text}] 之一
行动输入: 工具的输入参数
观察: 工具返回的结果
... (可以重复多次)
思考: 我现在有足够信息给出最终答案了
最终答案: 对用户问题的完整回答

## 当前任务
**问题**: {query}
{agent_scratchpad}

请开始推理（从"思考:"开始）：
"""
        else:
            # 通用模式：使用原有的通用提示词
            complete_prompt = f"""# 🧬 药物设计智能Agent - ReAct推理模式

## 你的角色
你是药物设计系统的智能Agent，负责通过推理和行动解决用户问题。

## 可用工具
{tools_text}

## 工作流程
使用以下格式进行推理和行动：

思考: 分析当前情况，决定下一步行动
行动: 选择一个工具，必须是 [{tool_names_text}] 之一
行动输入: 工具的输入参数（对于分子工具，通常是SMILES字符串）
观察: 工具返回的结果
... (可以重复多次思考-行动-观察循环)
思考: 我现在有足够信息给出最终答案了
最终答案: 对用户问题的完整回答

## 重要规则
1. 🎯 **精准选择工具** - 根据问题类型选择最合适的工具
2. 📝 **正确输入格式** - 工具输入必须是单个SMILES字符串
3. 🔄 **迭代优化** - 如果一个工具不够，可以调用多个
4. ✅ **基于事实** - 最终答案必须基于工具观察结果
5. 🚫 **避免重复** - 不要重复调用相同的工具和输入
6. 🔴 **严禁编造数值** - 分子量、LogP、QED、TPSA等数值**绝对禁止**凭经验猜测！必须先调用工具才能回答。
7. 🔬 **SMILES必须调工具** - 只要问题中包含SMILES分子结构，第一步必须调用 `property_calculator` 获取真实计算结果，禁止直接给出数值答案。

## 当前任务
**问题**: {query}
{agent_scratchpad}

请开始推理（从"思考:"开始）：
"""

        return complete_prompt

    def _parse_react_response(self, response: str) -> ReActStep:
        """解析ReAct响应"""
        step = ReActStep(thought="")

        try:
            # 解析思考
            thought_match = re.search(r'思考:?\s*([^\n]+)', response, re.IGNORECASE)
            if thought_match:
                step.thought = thought_match.group(1).strip()

            # 解析行动
            action_match = re.search(r'行动:?\s*([^\n]+)', response, re.IGNORECASE)
            if action_match:
                step.action = action_match.group(1).strip()

            # 解析行动输入
            input_match = re.search(r'行动输入:?\s*([^\n]+)', response, re.IGNORECASE)
            if input_match:
                step.action_input = input_match.group(1).strip()

            # 解析最终答案
            answer_match = re.search(r'最终答案:?\s*([^\n]+(?:\n[^\n]+)*)', response, re.IGNORECASE)
            if answer_match:
                step.final_answer = answer_match.group(1).strip()

        except Exception as e:
            logger.error(f"Failed to parse ReAct response: {e}")
            step.thought = "解析响应时出错"
            step.final_answer = "无法解析LLM响应"

        return step

    def _execute_tool(self, tool_name: str, tool_input: str) -> tuple[str, Dict[str, Any]]:
        """执行工具调用"""
        try:
            # 标准化工具名称匹配
            tool_name = tool_name.strip().lower()

            # 工具名称映射
            tool_name_mapping = {
                'property_calculator': 'property_calculator',
                'drug_likeness_assessment': 'drug_likeness_assessment',
                'admet_predictor': 'admet_predictor',
                'molecular_docking': 'molecular_docking',
                'rxn_chemistry_agent': 'rxn_chemistry_agent',
                'molecular_generator': 'molecular_generator',
                'llm_molecular_generator': 'llm_molecular_generator',
                # 允许一些别名
                'property': 'property_calculator',
                'admet': 'admet_predictor',
                'docking': 'molecular_docking',
                'reaction': 'rxn_chemistry_agent',
                'drug_likeness': 'drug_likeness_assessment',
                'generator': 'molecular_generator',
                'llm_generator': 'llm_molecular_generator',
                'ai_generator': 'llm_molecular_generator',
                'rag_database_search': 'rag_database_search',
                'rag_search': 'rag_database_search',
                'database_search': 'rag_database_search'
            }

            # 尝试匹配工具名称
            matched_tool_name = tool_name_mapping.get(tool_name)
            if not matched_tool_name:
                # 尝试部分匹配
                for alias, real_name in tool_name_mapping.items():
                    if alias in tool_name or tool_name in alias:
                        matched_tool_name = real_name
                        break

            if not matched_tool_name or matched_tool_name not in self.tools:
                available_tools = list(self.tools.keys())
                logger.error(f"工具 '{tool_name}' 未找到。可用工具: {available_tools}")
                return f"错误: 未找到工具 '{tool_name}'。可用工具: {available_tools}", {}

            tool = self.tools[matched_tool_name]
            logger.info(f"执行工具: {matched_tool_name} with input: {tool_input[:100]}...")

            # 准备工具执行参数
            exec_params = {"query": tool_input}
            
            # 特殊处理：如果是分子生成工具，传入用户在UI设置的数量
            if matched_tool_name == "llm_molecular_generator":
                exec_params["mol_count"] = getattr(self, "current_mol_count", 5)
                exec_params["temperature"] = getattr(self, "current_temperature", 0.7)
            elif matched_tool_name in ["rxn_chemistry_agent", "property_calculator"]:
                # 其他可能支持temperature的工具
                exec_params["temperature"] = getattr(self, "current_temperature", 0.7)

            # 调用工具的execute方法（通过具名参数调用以确保兼容性）
            import inspect
            sig = inspect.signature(tool.execute)
            
            # 过滤掉不支持的参数
            filtered_params = {k: v for k, v in exec_params.items() if k in sig.parameters}
            result = tool.execute(**filtered_params)

            if result.get('success'):
                # 返回格式化的结果
                formatted = result.get('summary', '') or result.get('formatted', '')
                if not formatted:
                    formatted = result.get('message', '工具执行成功但无详细结果')
                logger.info(f"工具 {matched_tool_name} 执行成功")
                return formatted, result
            else:
                logger.warning(f"工具 {matched_tool_name} 执行失败: {result.get('message', '未知错误')}")
                return f"工具执行失败: {result.get('message', '未知错误')}", result

        except Exception as e:
            logger.error(f"Tool execution failed: {e}")
            return f"工具执行出错: {str(e)}", {}

    def _execute_workflow_skill(self, query: str, base_result: Dict[str, Any]) -> Dict[str, Any]:
        """Execute declarative workflow skills without relying on LLM reasoning."""
        from uuid import uuid4

        from .contracts import AgentContext
        from .orchestrators import WorkflowOrchestrator
        from .planning import TaskPlanner
        from .runtime.event_bus import AgentEventBus

        context = AgentContext(
            query=query,
            trace_id=f"agent-{uuid4().hex[:12]}",
            active_skill=self._active_skill.name if self._active_skill else None,
            temperature=getattr(self, "current_temperature", 0.7),
            mol_count=getattr(self, "current_mol_count", 5),
        )
        plan = TaskPlanner().plan(context)
        steps = list(plan.steps)
        if not steps and self._active_skill and hasattr(self._active_skill, "workflow_steps"):
            steps = list(self._active_skill.workflow_steps)

        event_bus = AgentEventBus()
        orchestrator = WorkflowOrchestrator(event_bus=event_bus)
        agent_result = orchestrator.run(
            context=context,
            steps=steps,
            tools=self.tools,
            continue_on_error=True,
        )

        tool_results = {item.tool_name: item.to_legacy_dict() for item in agent_result.tool_results}
        base_result.update(
            {
                "success": agent_result.success or agent_result.partial,
                "final_answer": agent_result.final_answer or agent_result.message,
                "reasoning_trace": [f"workflow:{self._active_skill.name}"],
                "tools_used": [item.tool_name for item in agent_result.tool_results],
                "tool_results": tool_results,
                "active_skill": self._active_skill.name if self._active_skill else None,
                "partial": agent_result.partial,
                "trace_id": agent_result.trace_id,
                "agent_result": agent_result,
                "agent_events": [event.to_dict() for event in event_bus.events],
                "workflow_plan": {
                    "workflow_name": plan.workflow_name,
                    "steps": [step.tool_name for step in steps],
                    "metadata": plan.metadata,
                },
            }
        )
        return base_result

    def _generate_final_answer(self, query: str, steps: List[ReActStep]) -> str:
        """生成最终答案"""
        if not steps:
            return "无法处理您的请求，请提供更多信息。"

        # 收集所有观察结果
        observations = []
        for step in steps:
            if step.observation and "错误" not in step.observation:
                observations.append(step.observation)

        if observations:
            # 如果有有效的工具结果，返回最后一个
            return observations[-1]
        else:
            # 否则返回最后的思考
            return steps[-1].thought

    def _fallback_simple_execution(self, query: str) -> Dict[str, Any]:
        """回退到简单工具匹配模式（当没有LLM时）- 优化版"""
        import time
        start_time = time.time()
        
        result = {
            'query': query,
            'success': True,
            'steps': [],
            'final_answer': '',
            'reasoning_trace': ['使用简化模式处理请求'],
            'tools_used': []
        }

        try:
            # 找到匹配的工具
            matching_tools = []
            for tool_name, tool in self.tools.items():
                if hasattr(tool, 'should_use') and tool.should_use(query):
                    matching_tools.append((tool_name, tool))
                    logger.info(f"✅ 工具 {tool_name} 匹配成功")

            if not matching_tools:
                result['final_answer'] = "未找到合适的工具来处理您的请求。请提供更具体的信息或SMILES结构。"
                return result

            # 工具优先级调整 - 优化版
            def get_tool_priority(tool_name: str, query_str: str) -> int:
                """动态计算工具优先级，考虑查询内容"""
                query_lower = query_str.lower()

                # 分子生成请求（最高优先级）
                generation_keywords = [
                    '随机生成', '生成一个', '生成几个', '生成新', '创建分子', '设计分子',
                    'generate', 'create molecule', 'design molecule', 'random'
                ]
                is_generation = any(kw in query_lower for kw in generation_keywords)

                # 逆合成分析请求
                retro_keywords = [
                    'retrosynthesis', 'retro', 'synthetic route', 'synthesis pathway', 'synthesis route',
                    '逆合成', '合成路线', '合成路径', '逆向合成', '逆合成路线', '逆合成分析',
                    '合成方法', '合成途径', '制备方法', '制备路线'
                ]
                is_retro_request = any(kw in query_lower for kw in retro_keywords)

                # 根据请求类型设置优先级
                if is_generation:
                    priority_map = {
                        'llm_molecular_generator': 1,  # 分子生成最高优先级
                        'property_calculator': 2,
                        'drug_likeness_assessment': 3,
                        'admet_predictor': 4,
                        'rxn_chemistry_agent': 5,
                        'molecular_docking': 6
                    }
                elif is_retro_request:
                    priority_map = {
                        'rxn_chemistry_agent': 1,  # 逆合成分析最高优先级
                        'property_calculator': 2,
                        'drug_likeness_assessment': 3,
                        'admet_predictor': 4,
                        'llm_molecular_generator': 5,
                        'molecular_docking': 6
                    }
                else:
                    # 默认优先级
                    priority_map = {
                        'property_calculator': 1,  # 基础属性计算优先级最高
                        'drug_likeness_assessment': 2,  # 类药评估
                        'admet_predictor': 3,  # ADME预测
                        'rxn_chemistry_agent': 4,  # RXN Chemistry Agent
                        'llm_molecular_generator': 5,  # 分子生成
                        'molecular_docking': 6  # 分子对接
                    }

                return priority_map.get(tool_name, 999)

            # 按优先级排序
            matching_tools.sort(key=lambda x: get_tool_priority(x[0], query))
            tool_name, tool = matching_tools[0]
            
            logger.info(f"🎯 选择工具: {tool_name} (共{len(matching_tools)}个匹配)")

            # 执行工具
            tool_start = time.time()
            tool_result = tool.execute(query)
            tool_elapsed = time.time() - tool_start
            
            result['tools_used'].append(tool_name)
            logger.info(f"⏱️ 工具 {tool_name} 执行耗时: {tool_elapsed:.2f}秒")

            if tool_result.get('success'):
                result['final_answer'] = tool_result.get('formatted', tool_result.get('message', ''))
            else:
                result['final_answer'] = tool_result.get('message', '工具执行失败')
                result['success'] = False

            total_elapsed = time.time() - start_time
            logger.info(f"✅ 回退执行完成，总耗时: {total_elapsed:.2f}秒")

        except Exception as e:
            logger.error(f"Fallback execution failed: {e}", exc_info=True)
            result['success'] = False
            result['final_answer'] = f"处理请求时发生错误: {str(e)}"

        return result

    def _contains_smiles(self, query: str) -> bool:
        """检测查询中是否包含SMILES结构"""
        import re

        # 常见的SMILES模式
        smiles_patterns = [
            r'\b[A-Z][a-z]?(?:\([+-]?\d*\))?(?:\[[A-Z][a-z]?[+-]?\d*\])?[A-Za-z0-9()\[\]=#@\-+:]*\b',
            r'\bC[CO]+\b',  # 简单有机分子如CCO, CO等
            r'\b[CNO]\w*\b',  # 以C、N、O开头的化学式
        ]

        query_upper = query.upper()

        # 检查明确的分子名称模式
        molecular_keywords = ['CCO', 'CO', 'SMILES', 'C1', 'C2', 'C=', 'C#', '分子', '结构']
        for keyword in molecular_keywords:
            if keyword in query_upper:
                logger.info(f"检测到分子关键词: {keyword}")
                return True

        # 使用正则表达式检查
        for pattern in smiles_patterns:
            if re.search(pattern, query):
                logger.info(f"检测到SMILES模式: {pattern}")
                return True

        return False

    def format_result(self, result: Dict[str, Any]) -> str:
        """格式化ReAct结果用于显示"""
        if not result['success']:
            return result['final_answer']

        formatted_output = []

        # 如果有推理步骤，显示推理过程
        if result['steps'] and len(result['steps']) > 1:
            formatted_output.append("🤔 **推理过程:**")
            for i, step in enumerate(result['steps'], 1):
                if step.thought:
                    formatted_output.append(f"   {i}. {step.thought}")
                if step.action and step.observation:
                    formatted_output.append(f"      → 使用了 {step.action} 工具")
            formatted_output.append("")

        # 显示使用的工具
        if result['tools_used']:
            tools_text = "、".join(result['tools_used'])
            formatted_output.append(f"🔧 **使用工具:** {tools_text}")
            formatted_output.append("")

        # 显示最终答案
        formatted_output.append("📋 **分析结果:**")
        formatted_output.append(result['final_answer'])

        return "\n".join(formatted_output)
