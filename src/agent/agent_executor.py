#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
优化版Agent执行器 - 快速高效的分子属性预测
"""

from typing import List, Dict, Any, Optional
import logging
from .contracts import AgentErrorCode, ToolResult
from .tools import get_core_tools, get_optional_tool, OPTIONAL_TOOLS
from .tools.base_tool import execute_tool_compat
from .contracts.generation_request import (
    GenerationRequestError,
    build_generation_request,
    generation_request_error_details,
    has_generation_intent,
    preflight_generation_request,
)

logger = logging.getLogger(__name__)


class MolecularAgent:
    """优化的分子Agent系统 - 专注核心功能，快速执行"""

    def __init__(self, llm=None):
        self.llm = llm
        self.core_tools = []
        self.optional_tools = {}  # 延迟加载
        self._initialize_core_tools()
        logger.info(f"MolecularAgent initialized with {len(self.core_tools)} core tools")

    def _initialize_core_tools(self):
        """初始化核心工具"""
        try:
            self.core_tools = get_core_tools()
            for tool in self.core_tools:
                logger.info(f"Initialized core tool: {tool.name}")
        except Exception as e:
            logger.error(f"Core tools initialization failed: {e}")
            raise

    def _get_optional_tool(self, tool_name: str):
        """按需加载可选工具"""
        if tool_name not in self.optional_tools:
            try:
                self.optional_tools[tool_name] = get_optional_tool(tool_name)
                logger.info(f"Loaded optional tool: {tool_name}")
            except Exception as e:
                logger.error(f"Failed to load optional tool {tool_name}: {e}")
                return None
        return self.optional_tools[tool_name]

    def get_all_tools(self) -> List:
        """获取所有可用工具（核心 + 已加载的可选工具）"""
        all_tools = self.core_tools.copy()
        all_tools.extend(self.optional_tools.values())
        return all_tools

    def should_use_tools(self, query: str, *, preflight: bool = True) -> bool:
        """快速判断是否需要使用工具"""
        if preflight:
            preflight_generation_request(query)
        # 首先检查核心工具
        for tool in self.core_tools:
            if hasattr(tool, 'should_use') and tool.should_use(query):
                return True

        # 检查是否需要可选工具
        query_lower = query.lower()
        for tool_name in OPTIONAL_TOOLS:
            if self._should_load_optional_tool(query_lower, tool_name):
                tool = self._get_optional_tool(tool_name)
                if tool and hasattr(tool, 'should_use') and tool.should_use(query):
                    return True

        return False

    def _should_load_optional_tool(self, query_lower: str, tool_name: str) -> bool:
        """判断是否需要加载特定的可选工具"""
        if tool_name == 'RXNChemistryAgent':
            return any(keyword in query_lower for keyword in [
                'reaction', 'synthesis', 'retrosynthesis', 'rxn', '反应', '合成', '逆合成'
            ])
        elif tool_name == 'MolecularDocking':
            return any(keyword in query_lower for keyword in [
                'docking', 'binding', 'dock', '对接', '结合', '分子对接'
            ])
        return False

    def execute_tools(
        self,
        query: str,
        temperature: float = 0.7,
        mol_count: Optional[int] = None,
    ) -> Dict[str, Any]:
        """执行工具预测"""
        try:
            preflight_generation_request(
                query,
                mol_count,
                count_supplied=mol_count is not None,
                field="mol_count",
                active_molecular_skill=True,
            )
        except GenerationRequestError as exc:
            tool_result = self._invalid_generation_tool_result(exc)
            return {
                'success': False,
                'results': [tool_result],
                'used_tools': [],
                'error': tool_result['error'],
                'message': tool_result['error']['message'],
            }

        results = []
        used_tools = []
        first_error = None

        # 执行核心工具
        for tool in self.core_tools:
            try:
                if hasattr(tool, 'should_use') and tool.should_use(query):
                    logger.info(f"Executing core tool: {tool.name}")
                    # 如果是分子生成工具，传递temperature和mol_count参数
                    if tool.name == 'llm_molecular_generator':
                        generation_kwargs = {"temperature": temperature}
                        if mol_count is not None:
                            generation_kwargs["mol_count"] = mol_count
                        tool_result = execute_tool_compat(
                            tool, query, **generation_kwargs
                        )
                        result = tool_result.to_legacy_dict()
                    else:
                        result = tool.execute(query)
                    if result.get('success'):
                        results.append(result)
                        used_tools.append(tool.name)
                    elif tool.name == 'llm_molecular_generator':
                        results.append(result)
                        first_error = first_error or result.get("error")
            except Exception as e:
                logger.error(f"Core tool {tool.name} execution failed: {e}")

        # 按需执行可选工具
        query_lower = query.lower()
        for tool_name in OPTIONAL_TOOLS:
            if self._should_load_optional_tool(query_lower, tool_name):
                tool = self._get_optional_tool(tool_name)
                if tool:
                    try:
                        if hasattr(tool, 'should_use') and tool.should_use(query):
                            logger.info(f"Executing optional tool: {tool_name}")
                            result = tool.execute(query)
                            if result.get('success'):
                                results.append(result)
                                used_tools.append(tool_name)
                    except Exception as e:
                        logger.error(f"Optional tool {tool_name} execution failed: {e}")

        return {
            'success': len(used_tools) > 0,
            'results': results,
            'used_tools': used_tools,
            'error': first_error,
            'message': f"Successfully executed {len(used_tools)} tools" if used_tools else "No tools were triggered"
        }

    def get_tool_descriptions(self) -> Dict[str, str]:
        """获取工具描述信息"""
        descriptions = {}

        # 核心工具描述
        for tool in self.core_tools:
            descriptions[tool.name] = getattr(tool, 'description', 'No description available')

        # 可选工具描述（不加载实例）
        descriptions['RXNChemistryAgent'] = "Chemical reaction prediction and synthesis planning"
        descriptions['MolecularDocking'] = "Molecular docking and binding analysis"

        return descriptions

    def execute(
        self,
        query: str,
        temperature: float = 0.7,
        mol_count: Optional[int] = None,
    ) -> Dict[str, Any]:
        """执行agent任务 - 简化版本，直接使用工具"""
        try:
            requested_count = preflight_generation_request(
                query,
                mol_count,
                count_supplied=mol_count is not None,
                field="mol_count",
                active_molecular_skill=True,
            )
        except GenerationRequestError as exc:
            return self._invalid_generation_response(exc)

        logger.info(f"Executing query: {query[:100]}... (temperature={temperature})")

        if has_generation_intent(query):
            try:
                build_generation_request(query, requested_count)
            except GenerationRequestError as exc:
                return self._invalid_generation_response(exc)

        # 快速判断是否需要工具
        if not self.should_use_tools(query, preflight=False):
            return {
                'success': False,
                'message': 'No relevant tools found for this query',
                'response': '抱歉，我无法处理这个查询。请提供分子结构(SMILES)或相关的药物化学问题。'
            }

        # 执行工具，传递temperature和mol_count参数
        tool_results = self.execute_tools(query, temperature, mol_count)

        if tool_results['success']:
            # 格式化工具结果
            formatted_responses = []
            for result in tool_results['results']:
                if result.get('formatted'):
                    formatted_responses.append(result['formatted'])

            return {
                'success': True,
                'message': tool_results['message'],
                'response': '\n\n'.join(formatted_responses),
                'used_tools': tool_results['used_tools'],
                'tool_results': tool_results['results']
            }
        else:
            failure = {
                'success': False,
                'message': (
                    tool_results['error']['message']
                    if tool_results.get('error')
                    else 'Tool execution failed'
                ),
                'response': '工具执行失败，请检查输入格式或稍后重试。',
                'used_tools': tool_results['used_tools'],
                'tool_results': tool_results['results'],
            }
            if tool_results.get('error'):
                failure['error'] = tool_results['error']
            return failure

    @staticmethod
    def _invalid_generation_tool_result(exc: GenerationRequestError) -> Dict[str, Any]:
        message = f"Invalid molecular generation request: {exc}"
        return ToolResult.error_result(
            tool_name="llm_molecular_generator",
            code=AgentErrorCode.INVALID_INPUT,
            message=message,
            details=generation_request_error_details(exc),
        ).to_legacy_dict()

    @classmethod
    def _invalid_generation_response(
        cls, exc: GenerationRequestError
    ) -> Dict[str, Any]:
        tool_result = cls._invalid_generation_tool_result(exc)
        return {
            "success": False,
            "message": tool_result["error"]["message"],
            "response": "工具执行失败，请检查输入格式或稍后重试。",
            "used_tools": [],
            "tool_results": [tool_result],
            "error": tool_result["error"],
        }
