#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ModelScope API适配器 - 支持魔搭社区的大模型
"""

import httpx
import logging
import json
from typing import Optional, AsyncIterator

logger = logging.getLogger(__name__)


class ModelScopeModel:
    """ModelScope API模型适配器，支持GLM-4和Qwen系列模型"""
    
    def __init__(
        self, 
        api_key: str,
        model_name: str = "ZhipuAI/GLM-4.6",
        base_url: str = "https://api-inference.modelscope.cn/v1/chat/completions"
    ):
        """
        初始化ModelScope模型
        
        Args:
            api_key: ModelScope API密钥
            model_name: 模型名称，如 "ZhipuAI/GLM-4.6" 或 "Qwen/Qwen3-235B-A22B-Instruct-2507"
            base_url: API端点
        """
        self.api_key = (api_key or "").strip()
        self.model_name = model_name
        self.base_url = base_url
        self.client = httpx.AsyncClient(timeout=150.0)
        
        logger.info(f"初始化ModelScope模型: {model_name}")
    
    async def generate(self, prompt: str, temperature: float = 0.7, max_tokens: int = 1500) -> str:
        """
        生成响应（非流式）
        
        Args:
            prompt: 输入提示
            temperature: 温度参数
            max_tokens: 最大token数
            
        Returns:
            生成的文本
        """
        try:
            logger.info(f"调用ModelScope API - 模型: {self.model_name}")
            
            if not self.api_key:
                logger.warning("ModelScope API key is empty; skip remote generation.")
                return "ModelScope API Key 未配置，请配置 MODELSCOPE_API_KEY 或切换到本地 Ollama 模型。"

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            
            payload = {
                "model": self.model_name,
                "messages": [
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": False
            }
            
            response = await self.client.post(
                self.base_url,
                headers=headers,
                json=payload
            )
            
            if response.status_code == 200:
                result = response.json()
                content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                logger.info(f"ModelScope响应成功，长度: {len(content)}")
                return content
            else:
                error_msg = f"ModelScope API错误: {response.status_code} - {response.text}"
                logger.error(error_msg)
                return f"抱歉，模型调用失败: {response.status_code}"
                
        except Exception as e:
            logger.error(f"调用ModelScope API失败: {e}", exc_info=True)
            return f"抱歉，遇到技术问题: {str(e)}"
    
    async def stream_generate(
        self, 
        prompt: str, 
        temperature: float = 0.7, 
        max_tokens: int = 1500
    ) -> AsyncIterator[str]:
        """
        流式生成响应
        
        Args:
            prompt: 输入提示
            temperature: 温度参数
            max_tokens: 最大token数
            
        Yields:
            生成的文本片段
        """
        try:
            logger.info(f"开始流式调用ModelScope API - 模型: {self.model_name}")
            
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            
            payload = {
                "model": self.model_name,
                "messages": [
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": True
            }
            
            if not self.api_key:
                logger.warning("ModelScope API key is empty; skip remote streaming.")
                yield "ModelScope API Key 未配置，请配置 MODELSCOPE_API_KEY 或切换到本地 Ollama 模型。"
                return

            async with self.client.stream(
                "POST",
                self.base_url,
                headers=headers,
                json=payload,
                timeout=150.0
            ) as response:
                
                if response.status_code != 200:
                    error_content = await response.aread()
                    error_msg = f"HTTP错误 {response.status_code}: {error_content.decode('utf-8', errors='ignore')}"
                    logger.error(error_msg)
                    yield f"服务器错误: {error_msg}"
                    return
                
                # 处理SSE流
                buffer = ""
                async for chunk in response.aiter_bytes():
                    try:
                        buffer += chunk.decode('utf-8', errors='ignore')
                        
                        # 处理完整的行
                        while '\n' in buffer:
                            line, buffer = buffer.split('\n', 1)
                            line = line.strip()
                            
                            if not line or line == "data: [DONE]":
                                continue
                            
                            if line.startswith("data: "):
                                line = line[6:]  # 移除"data: "前缀
                            
                            try:
                                data = json.loads(line)
                                delta = data.get("choices", [{}])[0].get("delta", {})
                                content = delta.get("content", "")
                                
                                if content:
                                    yield content
                                    
                            except json.JSONDecodeError:
                                continue
                                
                    except Exception as e:
                        logger.warning(f"处理流式数据时出错: {e}")
                        continue
                
                logger.info("ModelScope流式响应完成")
                
        except Exception as e:
            logger.error(f"流式调用ModelScope API失败: {e}", exc_info=True)
            yield f"抱歉，遇到技术问题: {str(e)}"
    
    async def close(self):
        """关闭HTTP客户端"""
        await self.client.aclose()
    
    def __del__(self):
        """析构函数，确保资源释放"""
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            if not loop.is_closed():
                loop.create_task(self.close())
        except:
            pass


class ModelScopeModelManager:
    """ModelScope模型管理器，支持多模型切换"""
    
    def __init__(self, api_key: str):
        """
        初始化模型管理器
        
        Args:
            api_key: ModelScope API密钥
        """
        self.api_key = api_key
        self.models = {}
        self.current_model = None
        
        # 预定义的可用模型
        self.available_models = {
            "glm4": "ZhipuAI/GLM-4.6",
            "qwen3": "Qwen/Qwen3-235B-A22B-Instruct-2507"
        }
        
        logger.info(f"ModelScope模型管理器初始化完成，可用模型: {list(self.available_models.keys())}")
    
    def get_model(self, model_key: str = "glm4") -> ModelScopeModel:
        """
        获取指定模型实例
        
        Args:
            model_key: 模型键名，如 "glm4" 或 "qwen3"
            
        Returns:
            ModelScopeModel实例
        """
        if model_key not in self.models:
            model_name = self.available_models.get(model_key)
            if not model_name:
                logger.warning(f"未知模型键: {model_key}，使用默认模型 glm4")
                model_name = self.available_models["glm4"]
            
            self.models[model_key] = ModelScopeModel(
                api_key=self.api_key,
                model_name=model_name
            )
            logger.info(f"创建模型实例: {model_key} -> {model_name}")
        
        self.current_model = self.models[model_key]
        return self.models[model_key]
    
    def switch_model(self, model_key: str) -> ModelScopeModel:
        """
        切换当前模型
        
        Args:
            model_key: 目标模型键名
            
        Returns:
            新的ModelScopeModel实例
        """
        logger.info(f"切换模型: {model_key}")
        return self.get_model(model_key)

