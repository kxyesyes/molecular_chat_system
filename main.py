#!/usr/bin/env python3
"""
分子聊天系统 - 主启动文件
简化版本，移除复杂依赖
"""

import os
import sys
import argparse
import logging
from html.parser import HTMLParser
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlsplit
from src.system.env import load_env_file as _load_env_file

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 6000

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# Uvicorn uses multiprocessing spawn on both Windows and Linux. Spawn replays
# this entrypoint as __mp_main__ BEFORE Process.target starts its heartbeat
# thread. App-import bootstrap alone is too late for --workers/MEDCHAT_WORKERS.
if __name__ == "__mp_main__":
    from src.system.rdkit_logging import initialize_rdkit_logging
    initialize_rdkit_logging()

# Windows终端UTF-8编码设置
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# 设置警告过滤器
try:
    from warning_handler import setup_warning_filters
    setup_warning_filters()
except ImportError:
    pass

Path("logs").mkdir(parents=True, exist_ok=True)

# 设置日志
logging.basicConfig(
    handlers=[
        RotatingFileHandler(
            "logs/app.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        ),
        logging.StreamHandler(sys.stdout)
    ],
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def load_env_file(env_path: str = ".env"):
    """Load the shared env parser while preserving startup logging behavior."""
    try:
        _load_env_file(env_path)
    except Exception as exc:
        logger.warning(f"Unable to load env file {env_path}: {exc}")


def _parse_bool(value: str | bool | None, default: bool = False) -> bool:
    """Parse common truthy/falsy env strings."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _env_int(name: str, default: int) -> int:
    raw_value = os.environ.get(name)
    if not raw_value:
        return default
    try:
        return int(raw_value)
    except ValueError:
        logger.warning(f"Invalid integer for {name}: {raw_value!r}; using {default}")
        return default


def check_environment():
    """检查运行环境"""
    issues = []
    
    # 检查Python版本
    if sys.version_info < (3, 10):
        issues.append(f"Python版本过低: {sys.version}, 需要3.10+")
    
    # 检查必要的包
    required_packages = {
        "fastapi": "FastAPI",
        "uvicorn": "Uvicorn",
        "httpx": "HTTPX",
        "yaml": "PyYAML",
        "jinja2": "Jinja2",
        "websockets": "WebSockets"
    }
    
    missing_packages = []
    for package, name in required_packages.items():
        try:
            __import__(package)
        except ImportError:
            missing_packages.append(name)
    
    if missing_packages:
        issues.append(f"缺少依赖包: {', '.join(missing_packages)}")
        logger.info("安装命令: pip install fastapi uvicorn httpx pyyaml jinja2 websockets python-multipart")
    
    # 检查Ollama连接
    try:
        import httpx
        base_url = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
        response = httpx.get(f"{base_url}/api/tags", timeout=2)
        if response.status_code == 200:
            logger.info("✓ Ollama服务正常")
            models = response.json().get("models", [])
            if models:
                logger.info(f"  可用模型: {', '.join([m['name'] for m in models])}")
            else:
                logger.warning("  警告: Ollama中没有安装模型")
                logger.info("  请先确认已创建本地模型: ollama list | findstr gmm-llama")
        else:
            logger.warning("⚠ Ollama服务异常")
    except Exception as e:
        logger.warning("⚠ 无法连接到Ollama服务")
        logger.info("  请确保Ollama正在运行: ollama serve")
    
    return len(issues) == 0, issues


def setup_project_structure():
    """Create writable startup directories without modifying installed source files."""
    for directory in ("config", "logs", "data"):
        Path(directory).mkdir(parents=True, exist_ok=True)


class _HomeAssets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = set()

    def handle_starttag(self, tag, attrs):
        attribute = {"script": "src", "link": "href"}.get(tag)
        url = dict(attrs).get(attribute) if attribute else None
        if url and url.startswith("/static/"):
            self.paths.add(urlsplit(url).path.lstrip("/"))


def check_frontend_files():
    web = project_root / "src" / "web"
    template = web / "templates" / "index.html"
    if not template.is_file():
        logger.warning("缺少首页模板: %s", template)
        return False

    assets = _HomeAssets()
    assets.feed(template.read_text(encoding="utf-8"))
    assets.close()
    missing = sorted(path for path in assets.paths if not (web / path).is_file())
    for path in missing:
        logger.warning("缺少首页静态文件: %s", web / path)
    return not missing


def create_default_config():
    config_path = Path("config/ollama_config.yaml")
    if config_path.exists():
        logger.info(f"Using default Ollama config: {config_path}")
        return str(config_path)

    config_content = """# Ollama配置
models:
  default: "${OLLAMA_MODEL:-gmm-llama:latest}"
  available:
    - "gmm-llama:latest"

ollama:
  base_url: "${OLLAMA_BASE_URL:-http://127.0.0.1:11434}"
  model: "${OLLAMA_MODEL:-gmm-llama:latest}"

inference:
  stream: true
  temperature: 0.7
  max_tokens: 1000
  timeout: 60

web:
  host: "${MEDCHAT_HOST:-127.0.0.1}"
  port: ${MEDCHAT_PORT:-6000}
  debug: false

logging:
  level: "INFO"
  file: "./logs/app.log"
"""
    config_path.write_text(config_content, encoding='utf-8')
    logger.info(f"✓ 创建默认配置文件: {config_path}")
    
    return str(config_path)


def main():
    """主函数"""
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument(
        "--env-file",
        default=os.environ.get("MEDCHAT_ENV_FILE", ".env"),
        help="环境变量文件路径 (默认: .env)"
    )
    pre_args, _ = pre_parser.parse_known_args()
    load_env_file(pre_args.env_file)

    parser = argparse.ArgumentParser(
        description="分子聊天系统",
        parents=[pre_parser],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py                    # 使用默认配置启动
  python main.py --port 6000        # 指定端口
  python main.py --debug            # 调试模式
  python main.py --reload           # 开发模式启用自动重载
        """
    )
    
    parser.add_argument(
        "--config",
        default=None,
        help="配置文件路径 (默认: config/ollama_config.yaml, 可选: config/modelscope_config.yaml)"
    )
    parser.add_argument(
        "--modelscope",
        action="store_true",
        help="使用ModelScope配置文件（魔搭社区模型）"
    )
    parser.add_argument(
        "--host",
        default=None,
        help=f"服务器地址 (默认读取 MEDCHAT_HOST，未设置则 {DEFAULT_HOST})"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"服务器端口 (默认读取 MEDCHAT_PORT，未设置则 {DEFAULT_PORT})"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="启用调试模式"
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="启用自动重载 (开发模式使用，生产请关闭)"
    )
    parser.add_argument(
        "--no-reload",
        action="store_true",
        help="禁用自动重载"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Uvicorn worker 数量 (默认读取 MEDCHAT_WORKERS，未设置则 1)"
    )
    
    args = parser.parse_args()
    if args.config is not None and not Path(args.config).is_file():
        logger.error("指定的配置文件不存在或不是文件: %s", args.config)
        return 1
    
    host = args.host or os.environ.get("MEDCHAT_HOST", DEFAULT_HOST)
    port = args.port if args.port is not None else _env_int("MEDCHAT_PORT", DEFAULT_PORT)
    debug = args.debug or _parse_bool(os.environ.get("MEDCHAT_DEBUG"), False)
    reload_enabled = args.reload or _parse_bool(os.environ.get("MEDCHAT_RELOAD"), False)
    workers = args.workers if args.workers is not None else _env_int("MEDCHAT_WORKERS", 1)

    # Match the app's exact opt-in, after env-file/CLI resolution and before
    # reload can silently reduce workers. Chat task ownership is process-local.
    if os.environ.get("MEDCHAT_CHAT_DOCKING_ENABLED") == "1" and workers > 1:
        logger.error(
            "MEDCHAT_CHAT_DOCKING_ENABLED=1 requires exactly one worker: "
            "chat docking task ownership and HTTP controls are process-local; "
            "use --workers 1 or MEDCHAT_WORKERS=1. Refusing startup."
        )
        return 1

    if args.no_reload:
        reload_enabled = False
    if reload_enabled and workers != 1:
        logger.warning("Uvicorn reload mode does not support multiple workers; using workers=1")
        workers = 1
    
    try:
        print("="*60)
        print("分子聊天系统 v1.0")
        print("="*60)
        
        # 设置项目结构
        setup_project_structure()
        
        # 检查环境
        env_ok, issues = check_environment()
        if not env_ok:
            logger.error("环境检查失败:")
            for issue in issues:
                logger.error(f"  - {issue}")
            return 1
        
        # 检查前端文件
        check_frontend_files()
        
        # 创建或使用配置文件
        if args.config is not None:
            config_path = args.config
        elif args.modelscope:
            # 使用ModelScope配置
            config_path = "config/modelscope_config.yaml"
            if not Path(config_path).exists():
                logger.error(f"ModelScope配置文件不存在: {config_path}")
                logger.info("将使用默认Ollama配置")
                config_path = create_default_config()
            else:
                logger.info(f"使用ModelScope配置: {config_path}")
        else:
            config_path = create_default_config()
        
        # 设置环境变量（供app.py使用）
        os.environ["MOLECULAR_CHAT_CONFIG"] = config_path
        using_modelscope = "modelscope" in Path(config_path).name.lower()
        
        print("\n" + "="*60)
        print(f"配置文件: {config_path}")
        print(f"服务地址: http://{host}:{port}")
        print(f"自动重载: {'开启' if reload_enabled else '关闭'}")
        print(f"Worker数量: {workers}")
        if using_modelscope:
            print("模型后端: ModelScope (Ollama 保留为可选能力)")
        print("="*60)
        print("\n按 Ctrl+C 停止服务\n")
        
        # 启动Uvicorn服务器
        import uvicorn
        
        uvicorn.run(
            "src.web.app:app",
            host=host,
            port=port,
            reload=reload_enabled,
            workers=workers,
            proxy_headers=True,
            forwarded_allow_ips=os.environ.get("MEDCHAT_FORWARDED_ALLOW_IPS", "127.0.0.1"),
            log_level="debug" if debug else os.environ.get("MEDCHAT_LOG_LEVEL", "info"),
            access_log=debug
        )
        
    except KeyboardInterrupt:
        print("\n\n正在关闭服务...")
        logger.info("用户中断，服务已关闭")
    except ImportError as e:
        logger.error(f"缺少依赖: {e}")
        logger.info("请运行: pip install fastapi uvicorn httpx pyyaml jinja2 websockets python-multipart")
        return 1
    except Exception as e:
        logger.error(f"启动失败: {e}", exc_info=debug)
        return 1
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
