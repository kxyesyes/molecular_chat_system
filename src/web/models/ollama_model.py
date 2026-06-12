#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ollama模型接口
提供与Ollama API的同步和异步通信能力
"""

import json
import logging
import httpx

# 尝试导入增强日志系统
try:
    from src.utils.logger import (
        log_model_event,
        log_error,
        log_performance
    )
    ENHANCED_LOGGING = True
except ImportError:
    ENHANCED_LOGGING = False

logger = logging.getLogger(__name__)


class OllamaModel:
    """
    Ollama模型接口
    
    提供与Ollama API的通信功能：
    - 同步生成：用于工具调用
    - 异步生成：用于主聊天
    - 流式生成：用于实时响应
    """

    def __init__(self, base_url: str = "http://localhost:11434", 
                 model_name: str = "gmm-llama:latest"):
        """
        初始化Ollama模型客户端
        
        Args:
            base_url: Ollama API的基础URL
            model_name: 要使用的模型名称
        """
        self.base_url = base_url
        self.model_name = model_name
        # 增加超时时间到150秒
        self.client = httpx.AsyncClient(timeout=150.0)
        # 创建同步客户端用于同步调用
        self.sync_client = httpx.Client(timeout=150.0)

    def generate(self, prompt: str, temperature: float = 0.7, 
                 max_tokens: int = 1500) -> str:
        """
        同步生成响应（用于工具调用）
        
        Args:
            prompt: 输入提示词
            temperature: 温度参数 (0.0-1.0)
            max_tokens: 最大生成token数
            
        Returns:
            str: 生成的文本响应
        """
        try:
            response = self.sync_client.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model_name,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": temperature,
                        "num_predict": max_tokens
                    }
                }
            )

            if response.status_code == 200:
                result = response.json()
                return result.get("response", "")
            else:
                logger.error(f"Ollama API error: {response.status_code}")
                return "I apologize, but I'm having trouble generating a response right now."

        except Exception as e:
            logger.error(f"Error calling Ollama: {e}")
            return "I apologize, but I'm experiencing technical difficulties."

    async def generate_async(self, prompt: str, temperature: float = 0.7, 
                            max_tokens: int = 1500) -> str:
        """
        异步生成响应（用于主聊天）
        
        Args:
            prompt: 输入提示词
            temperature: 温度参数 (0.0-1.0)
            max_tokens: 最大生成token数
            
        Returns:
            str: 生成的文本响应
        """
        try:
            response = await self.client.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model_name,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": temperature,
                        "num_predict": max_tokens
                    }
                }
            )

            if response.status_code == 200:
                result = response.json()
                return result.get("response", "")
            else:
                logger.error(f"Ollama API error: {response.status_code}")
                return "I apologize, but I'm having trouble generating a response right now."

        except Exception as e:
            logger.error(f"Error calling Ollama: {e}")
            return "I apologize, but I'm experiencing technical difficulties."

    async def stream_generate(self, prompt: str, temperature: float = 0.7, 
                             max_tokens: int = 1500):
        """
        流式生成响应
        
        Args:
            prompt: 输入提示词
            temperature: 温度参数 (0.0-1.0)
            max_tokens: 最大生成token数
            
        Yields:
            str: 生成的文本片段
        """
        import time
        start_time = time.time()

        if ENHANCED_LOGGING:
            log_model_event("开始流式生成", self.model_name)
            log_performance("流式生成准备", 0, f"模型: {self.model_name}, URL: {self.base_url}")

        logger.info(f"开始流式生成 - 模型: {self.model_name}, URL: {self.base_url}")
        logger.debug(f"请求参数: prompt长度={len(prompt)}, temp={temperature}, max_tokens={max_tokens}")

        try:
            request_data = {
                "model": self.model_name,
                "prompt": prompt,
                "stream": True,
                "options": {
                    "temperature": temperature,
                    "num_predict": max_tokens
                }
            }
            logger.debug(f"请求JSON: {request_data}")

            async with self.client.stream(
                "POST",
                f"{self.base_url}/api/generate",
                json=request_data,
                timeout=150.0
            ) as response:

                logger.info(f"收到响应 - 状态码: {response.status_code}")
                logger.debug(f"响应头: {dict(response.headers)}")

                if ENHANCED_LOGGING:
                    log_model_event("收到模型响应", self.model_name)

                if response.status_code != 200:
                    error_content = await response.aread()
                    error_msg = f"HTTP错误 {response.status_code}: {error_content}"
                    logger.error(error_msg)
                    if ENHANCED_LOGGING:
                        log_error("模型HTTP错误", Exception(error_msg))
                    yield f"服务器错误 (HTTP {response.status_code}): {error_content[:200]}"
                    return

                # 强制设置UTF-8编码
                response.encoding = 'utf-8'

                line_count = 0
                buffer = ""  # 用于处理不完整的行
                total_chars = 0

                # 使用字节流来避免编码问题
                async for chunk in response.aiter_bytes():
                    try:
                        # 手动解码，忽略错误字符
                        decoded = chunk.decode('utf-8', errors='ignore')
                        buffer += decoded

                        # 按行分割
                        lines = buffer.split('\n')

                        # 保留最后一行（可能不完整）
                        buffer = lines[-1]

                        # 处理完整的行
                        for line in lines[:-1]:
                            line = line.strip()
                            if not line:
                                continue

                            line_count += 1
                            logger.debug(f"处理第{line_count}行: {line[:100]}...")

                            try:
                                data = json.loads(line)
                                if "response" in data:
                                    chunk_text = data["response"]
                                    total_chars += len(chunk_text)
                                    yield chunk_text
                                if data.get("done", False):
                                    generation_time = time.time() - start_time
                                    logger.info(f"流式生成完成，共处理{line_count}行，生成{total_chars}字符")
                                    if ENHANCED_LOGGING:
                                        log_performance("流式生成完成", generation_time, f"{total_chars}字符")
                                        log_model_event("流式生成完成", self.model_name, generation_time)
                                    return
                            except json.JSONDecodeError as json_err:
                                logger.warning(f"JSON解析警告 - 行{line_count}: {json_err}")
                                logger.debug(f"问题行内容: {repr(line[:200])}")
                                continue

                    except UnicodeDecodeError as decode_err:
                        logger.warning(f"Unicode解码错误: {decode_err}")
                        continue
                    except Exception as chunk_err:
                        logger.warning(f"处理chunk时出错: {chunk_err}")
                        continue

                # 处理缓冲区中剩余的数据
                if buffer.strip():
                    try:
                        data = json.loads(buffer)
                        if "response" in data:
                            yield data["response"]
                    except json.JSONDecodeError:
                        logger.debug(f"缓冲区中的非JSON数据: {buffer[:100]}")

        except httpx.ReadTimeout:
            error_msg = "请求超时 - Ollama响应时间过长"
            logger.error(error_msg)
            if ENHANCED_LOGGING:
                log_error("模型请求超时", Exception(error_msg))
            yield "请求超时，模型响应时间过长。请稍后重试或尝试更短的输入。"
        except httpx.ConnectTimeout:
            error_msg = "连接超时 - 无法连接到Ollama服务"
            logger.error(error_msg)
            if ENHANCED_LOGGING:
                log_error("模型连接超时", Exception(error_msg))
            yield "连接超时，无法连接到AI模型服务。请确保Ollama正在运行。"
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP状态错误 - {e}")
            if ENHANCED_LOGGING:
                log_error("模型HTTP状态错误", e)
            yield f"服务响应错误: {e}"
        except Exception as e:
            import traceback
            logger.error(f"异常类型: {type(e).__name__}")
            logger.error(f"异常消息: {str(e)}")
            logger.error(f"异常详情: {repr(e)}")
            logger.error(f"完整堆栈:\n{traceback.format_exc()}")
            if ENHANCED_LOGGING:
                log_error("模型生成异常", e)
            yield f"技术错误: {type(e).__name__}: {str(e)[:100]}"

    async def close(self):
        """关闭HTTP客户端连接"""
        await self.client.aclose()
        self.sync_client.close()

