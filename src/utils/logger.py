#!/usr/bin/env python3
"""
增强的日志系统 - 为终端提供详细的处理信息
"""

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


class ColoredFormatter(logging.Formatter):
    """带颜色的日志格式化器"""

    # ANSI颜色代码
    COLORS = {
        'DEBUG': '\033[36m',      # 青色
        'INFO': '\033[32m',       # 绿色
        'WARNING': '\033[33m',    # 黄色
        'ERROR': '\033[31m',      # 红色
        'CRITICAL': '\033[35m',   # 紫色
        'RESET': '\033[0m'        # 重置
    }

    def format(self, record):
        # 添加颜色
        color = self.COLORS.get(record.levelname, self.COLORS['RESET'])
        reset = self.COLORS['RESET']

        # 格式化时间
        timestamp = datetime.fromtimestamp(record.created).strftime('%H:%M:%S')

        # 构建彩色日志消息
        colored_level = f"{color}{record.levelname:<8}{reset}"
        colored_name = f"{color}{record.name}{reset}"

        # 特殊处理不同类型的消息
        if hasattr(record, 'event_type'):
            event_type = getattr(record, 'event_type', '')
            if event_type == 'user_message':
                prefix = "👤 用户输入"
            elif event_type == 'ai_response':
                prefix = "🤖 AI回复"
            elif event_type == 'system':
                prefix = "⚙️ 系统"
            elif event_type == 'rag':
                prefix = "🔍 检索"
            elif event_type == 'agent':
                prefix = "🔧 智能代理"
            elif event_type == 'error':
                prefix = "❌ 错误"
            else:
                prefix = "ℹ️ 信息"

            return f"{timestamp} {prefix:<10} {record.getMessage()}"
        else:
            return f"{timestamp} {colored_level} {colored_name:<20} {record.getMessage()}"


class TerminalLogger:
    """终端日志管理器"""

    def __init__(self, name: str = "MolecularChat", level: str = "INFO"):
        self.logger = logging.getLogger(name)
        self.logger.setLevel(getattr(logging, level.upper()))

        # 清除现有的处理器
        self.logger.handlers.clear()

        # 创建控制台处理器
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)

        # 创建格式化器
        formatter = ColoredFormatter()
        console_handler.setFormatter(formatter)

        # 添加处理器
        self.logger.addHandler(console_handler)

        # 创建文件处理器（可选）
        self._setup_file_logging()

        # 阻止日志向上传播，避免重复输出
        self.logger.propagate = False

    def _setup_file_logging(self):
        """设置文件日志记录"""
        try:
            # 创建日志目录
            log_dir = Path("logs")
            log_dir.mkdir(exist_ok=True)

            # 创建文件处理器
            log_file = log_dir / f"chat_{datetime.now().strftime('%Y%m%d')}.log"
            file_handler = logging.FileHandler(log_file, encoding='utf-8')
            file_handler.setLevel(logging.DEBUG)

            # 文件日志格式（更详细）
            file_formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s'
            )
            file_handler.setFormatter(file_formatter)

            self.logger.addHandler(file_handler)

        except Exception as e:
            print(f"Warning: Could not setup file logging: {e}")

    def user_message(self, message: str, user_id: Optional[str] = None):
        """记录用户消息"""
        extra = {'event_type': 'user_message'}
        if user_id:
            msg = f"[{user_id}] {message[:100]}{'...' if len(message) > 100 else ''}"
        else:
            msg = f"{message[:100]}{'...' if len(message) > 100 else ''}"
        self.logger.info(msg, extra=extra)

    def ai_response(self, response: str, model: str = None):
        """记录AI回复"""
        extra = {'event_type': 'ai_response'}
        model_info = f"[{model}] " if model else ""
        msg = f"{model_info}响应长度: {len(response)} 字符"
        self.logger.info(msg, extra=extra)

    def system_event(self, event: str, details: str = None):
        """记录系统事件"""
        extra = {'event_type': 'system'}
        msg = f"{event}"
        if details:
            msg += f" - {details}"
        self.logger.info(msg, extra=extra)

    def rag_search(self, query: str, results_count: int = 0):
        """记录RAG检索事件"""
        extra = {'event_type': 'rag'}
        msg = f"检索查询: '{query[:50]}{'...' if len(query) > 50 else ''}' -> 找到 {results_count} 个结果"
        self.logger.info(msg, extra=extra)

    def agent_action(self, action: str, tools_used: list = None, success: bool = True):
        """记录智能代理操作"""
        extra = {'event_type': 'agent'}
        status = "成功" if success else "失败"
        tools_info = f" (工具: {', '.join(tools_used)})" if tools_used else ""
        msg = f"{action} - {status}{tools_info}"
        self.logger.info(msg, extra=extra)

    def websocket_event(self, event: str, client_info: str = None):
        """记录WebSocket事件"""
        extra = {'event_type': 'system'}
        client_part = f" [{client_info}]" if client_info else ""
        msg = f"WebSocket {event}{client_part}"
        self.logger.info(msg, extra=extra)

    def model_event(self, event: str, model_name: str = None, duration: float = None):
        """记录模型相关事件"""
        extra = {'event_type': 'system'}
        model_part = f" [{model_name}]" if model_name else ""
        duration_part = f" ({duration:.2f}s)" if duration is not None else ""
        msg = f"模型{event}{model_part}{duration_part}"
        self.logger.info(msg, extra=extra)

    def error(self, error: str, exception: Exception = None):
        """记录错误"""
        extra = {'event_type': 'error'}
        msg = f"{error}"
        if exception:
            msg += f" - {type(exception).__name__}: {str(exception)}"
        self.logger.error(msg, extra=extra)

    def performance(self, operation: str, duration: float, details: str = None):
        """记录性能信息"""
        extra = {'event_type': 'system'}
        msg = f"性能: {operation} 耗时 {duration:.3f}s"
        if details:
            msg += f" ({details})"
        self.logger.info(msg, extra=extra)

    def debug(self, message: str):
        """调试信息"""
        self.logger.debug(message)

    def info(self, message: str):
        """一般信息"""
        self.logger.info(message)

    def warning(self, message: str):
        """警告信息"""
        self.logger.warning(message)


# 创建全局日志实例
terminal_logger = TerminalLogger()

# 便捷函数
def log_user_message(message: str, user_id: Optional[str] = None):
    terminal_logger.user_message(message, user_id)

def log_ai_response(response: str, model: str = None):
    terminal_logger.ai_response(response, model)

def log_system_event(event: str, details: str = None):
    terminal_logger.system_event(event, details)

def log_rag_search(query: str, results_count: int = 0):
    terminal_logger.rag_search(query, results_count)

def log_agent_action(action: str, tools_used: list = None, success: bool = True):
    terminal_logger.agent_action(action, tools_used, success)

def log_websocket_event(event: str, client_info: str = None):
    terminal_logger.websocket_event(event, client_info)

def log_model_event(event: str, model_name: str = None, duration: float = None):
    terminal_logger.model_event(event, model_name, duration)

def log_error(error: str, exception: Exception = None):
    terminal_logger.error(error, exception)

def log_performance(operation: str, duration: float, details: str = None):
    terminal_logger.performance(operation, duration, details)