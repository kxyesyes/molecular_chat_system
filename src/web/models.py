"""
LLM 模型接口模块 - 优化版
"""
import logging
import httpx
import asyncio
from typing import Optional
import time

logger = logging.getLogger(__name__)


class OllamaModel:
    """Ollama model interface for chat completion - 性能优化版"""

    def __init__(self, base_url: str = "http://localhost:11434", model_name: str = "gmm-llama:latest"):
        self.base_url = base_url
        self.model_name = model_name
        # 优化1: 增加连接池大小，启用HTTP/2，减少超时时间
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=10.0, read=60.0),  # 分别设置连接和读取超时
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),  # 连接池优化
            http2=True  # 启用HTTP/2以提高性能
        )
        # 优化2: 添加响应缓存（简单的内存缓存）
        self._cache = {}
        self._cache_ttl = 300  # 缓存5分钟
        # 优化3: 性能监控
        self._request_count = 0
        self._total_time = 0.0

    async def generate(self, prompt: str, temperature: float = 0.7, max_tokens: int = 1500) -> str:
        """Generate response from Ollama model - 优化版"""
        start_time = time.time()
        
        try:
            # 优化4: 检查缓存（对于相同的prompt）
            cache_key = f"{prompt[:100]}_{temperature}_{max_tokens}"
            if cache_key in self._cache:
                cached_data, cached_time = self._cache[cache_key]
                if time.time() - cached_time < self._cache_ttl:
                    logger.info(f"使用缓存响应，节省时间: {time.time() - start_time:.2f}秒")
                    return cached_data
            
            # 优化5: 减少payload大小，只发送必要字段
            payload = {
                "model": self.model_name,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": temperature,
                    "num_predict": max_tokens,
                    "num_ctx": 2048,  # 限制上下文窗口大小
                    "num_thread": 4   # 使用多线程加速
                }
            }
            
            response = await self.client.post(
                f"{self.base_url}/api/generate",
                json=payload
            )

            elapsed = time.time() - start_time
            self._request_count += 1
            self._total_time += elapsed
            
            if response.status_code == 200:
                result = response.json()
                response_text = result.get("response", "")
                
                # 缓存结果
                self._cache[cache_key] = (response_text, time.time())
                
                # 清理过期缓存
                if len(self._cache) > 100:
                    self._cleanup_cache()
                
                logger.info(f"生成完成，耗时: {elapsed:.2f}秒，平均: {self._total_time/self._request_count:.2f}秒")
                return response_text
            else:
                logger.error(f"Ollama API error: {response.status_code}")
                return "抱歉，模型响应出错，请稍后重试。"

        except asyncio.TimeoutError:
            logger.error(f"请求超时，耗时: {time.time() - start_time:.2f}秒")
            return "请求超时，模型响应时间过长。请尝试简化问题或稍后重试。"
        except Exception as e:
            logger.error(f"Error calling Ollama: {e}")
            return "抱歉，遇到技术问题，请稍后重试。"
    
    def _cleanup_cache(self):
        """清理过期缓存"""
        current_time = time.time()
        expired_keys = [
            key for key, (_, cached_time) in self._cache.items()
            if current_time - cached_time > self._cache_ttl
        ]
        for key in expired_keys:
            del self._cache[key]
        logger.info(f"清理了 {len(expired_keys)} 个过期缓存项")

    async def stream_generate(self, prompt: str, temperature: float = 0.7, max_tokens: int = 1500):
        """Stream response from Ollama model - 性能优化版"""
        import json
        
        start_time = time.time()
        logger.info(f"开始流式生成 - 模型: {self.model_name}")

        try:
            # 优化6: 精简请求payload，添加性能参数
            request_data = {
                "model": self.model_name,
                "prompt": prompt,
                "stream": True,
                "options": {
                    "temperature": temperature,
                    "num_predict": max_tokens,
                    "num_ctx": 2048,      # 限制上下文窗口
                    "num_thread": 4,      # 多线程加速
                    "num_gpu": 1,         # 使用GPU加速（如果可用）
                    "num_batch": 512,     # 批处理大小
                    "top_k": 40,          # 减少采样空间
                    "top_p": 0.9,         # 核采样
                    "repeat_penalty": 1.1 # 重复惩罚
                }
            }

            # 优化7: 使用更短的超时时间，更快失败
            async with self.client.stream(
                "POST",
                f"{self.base_url}/api/generate",
                json=request_data,
                timeout=httpx.Timeout(60.0, connect=5.0, read=60.0)  # 连接超时5秒
            ) as response:

                if response.status_code != 200:
                    error_content = await response.aread()
                    error_msg = f"HTTP错误 {response.status_code}: {error_content[:200]}"
                    logger.error(error_msg)
                    yield f"服务器错误 (HTTP {response.status_code})"
                    return

                response.encoding = 'utf-8'
                buffer = ""
                total_chars = 0
                chunk_count = 0
                last_yield_time = time.time()
                
                # 优化8: 批量yield，减少网络往返
                batch_buffer = []
                batch_size = 5  # 每5个token批量发送一次

                async for chunk in response.aiter_bytes():
                    try:
                        decoded = chunk.decode('utf-8', errors='ignore')
                        buffer += decoded
                        lines = buffer.split('\n')
                        buffer = lines[-1]

                        for line in lines[:-1]:
                            line = line.strip()
                            if not line:
                                continue

                            try:
                                data = json.loads(line)
                                if "response" in data:
                                    chunk_text = data["response"]
                                    total_chars += len(chunk_text)
                                    chunk_count += 1
                                    
                                    # 批量发送
                                    batch_buffer.append(chunk_text)
                                    if len(batch_buffer) >= batch_size:
                                        yield ''.join(batch_buffer)
                                        batch_buffer = []
                                        last_yield_time = time.time()
                                    
                                if data.get("done", False):
                                    # 发送剩余的批量数据
                                    if batch_buffer:
                                        yield ''.join(batch_buffer)
                                    
                                    generation_time = time.time() - start_time
                                    tokens_per_sec = total_chars / generation_time if generation_time > 0 else 0
                                    logger.info(f"流式生成完成: {total_chars}字符, {chunk_count}块, "
                                              f"耗时{generation_time:.2f}秒, "
                                              f"速度{tokens_per_sec:.1f}字符/秒")
                                    return
                            except json.JSONDecodeError:
                                continue
                    except Exception as e:
                        logger.warning(f"处理chunk时出错: {e}")
                        continue

                # 处理缓冲区剩余数据
                if batch_buffer:
                    yield ''.join(batch_buffer)
                    
                if buffer.strip():
                    try:
                        data = json.loads(buffer)
                        if "response" in data:
                            yield data["response"]
                    except json.JSONDecodeError:
                        pass

        except httpx.ReadTimeout:
            logger.error("请求超时")
            yield "请求超时，模型响应时间过长。请稍后重试。"
        except httpx.ConnectTimeout:
            logger.error("连接超时")
            yield "连接超时，无法连接到AI模型服务。请确保Ollama正在运行。"
        except Exception as e:
            logger.error(f"流式生成异常: {e}")
            yield f"技术错误: {str(e)[:100]}"
