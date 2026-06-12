#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Agent基础类 - 参考ChemCrow设计
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from langchain.tools import BaseTool
from pydantic import BaseModel, Field
import logging

logger = logging.getLogger(__name__)


class ToolInput(BaseModel):
    """工具输入基类"""
    query: str = Field(description="User query")


class ChemicalTool(BaseTool, ABC):
    """化学工具基类"""
    
    name: str = "chemical_tool"
    description: str = "Base chemical tool"
    
    def _run(self, query: str) -> str:
        """同步运行"""
        try:
            return self.execute(query)
        except Exception as e:
            logger.error(f"Tool {self.name} failed: {e}")
            return f"Error: {str(e)}"
    
    async def _arun(self, query: str) -> str:
        """异步运行"""
        return self._run(query)
    
    @abstractmethod
    def execute(self, query: str) -> str:
        """执行工具逻辑"""
        pass
    
    def parse_input(self, query: str) -> Dict[str, Any]:
        """解析输入"""
        return {"query": query}