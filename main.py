#!/usr/bin/env python3
"""
分子聊天系统 - 主启动文件
简化版本，移除复杂依赖
"""

import os
import sys
import argparse
import logging
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

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

# 设置日志
logging.basicConfig(
    handlers=[
        logging.FileHandler("logs/app.log", encoding='utf-8', mode='w'),
        logging.StreamHandler(sys.stdout)
    ],
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def load_env_file(env_path: str = ".env"):
    """Load simple KEY=VALUE pairs from .env without extra dependencies."""
    path = Path(env_path)
    if not path.exists():
        return
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception as exc:
        logger.warning(f"Unable to load env file {path}: {exc}")


def check_environment():
    """检查运行环境"""
    issues = []
    
    # 检查Python版本
    if sys.version_info < (3, 7):
        issues.append(f"Python版本过低: {sys.version}, 需要3.7+")
    
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
        response = httpx.get("http://localhost:11434/api/tags", timeout=2)
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
    """创建项目目录结构"""
    directories = [
        "src",
        "src/web",
        "src/web/templates",
        "src/web/static",
        "config",
        "logs",
        "data"
    ]
    
    for dir_path in directories:
        Path(dir_path).mkdir(parents=True, exist_ok=True)
    
    # 创建__init__.py文件
    init_files = [
        "src/__init__.py",
        "src/web/__init__.py"
    ]
    
    for init_file in init_files:
        Path(init_file).touch(exist_ok=True)
    
    logger.info("✓ 项目目录结构已创建")


def check_frontend_files():
    """检查前端文件是否存在"""
    frontend_files = {
        "src/web/templates/index.html": "HTML模板",
        "src/web/static/css/style.css": "CSS样式",
        "src/web/static/js/script.js": "JavaScript脚本"
    }
    
    missing_files = []
    for file_path, description in frontend_files.items():
        if not Path(file_path).exists():
            missing_files.append(f"{description} ({file_path})")
    
    if missing_files:
        logger.warning("⚠ 缺少前端文件:")
        for file in missing_files:
            logger.warning(f"  - {file}")
        
        # 尝试从其他位置复制
        possible_sources = [
            Path("."),  # 根目录
            Path("src/web"),  # web目录
            Path("src"),  # src目录
            Path("static"),  # 可能的static目录
            Path("templates")  # 可能的templates目录
        ]
        
        logger.info("\n正在搜索文件...")
        for source in possible_sources:
            # 查找HTML文件
            for html_file in source.glob("**/index.html"):
                target = Path("src/web/templates/index.html")
                if not target.exists():
                    import shutil
                    os.makedirs(target.parent, exist_ok=True)
                    shutil.copy2(html_file, target)
                    logger.info(f"  ✓ 复制HTML: {html_file} -> {target}")
            
            # 查找CSS文件
            for css_file in source.glob("**/style.css"):
                target = Path("src/web/static/css/style.css")
                if not target.exists():
                    import shutil
                    os.makedirs(target.parent, exist_ok=True)
                    shutil.copy2(css_file, target)
                    logger.info(f"  ✓ 复制CSS: {css_file} -> {target}")
            
            # 查找JS文件
            for js_file in source.glob("**/script.js"):
                target = Path("src/web/static/js/script.js")
                if not target.exists():
                    import shutil
                    os.makedirs(target.parent, exist_ok=True)
                    shutil.copy2(js_file, target)
                    logger.info(f"  ✓ 复制JS: {js_file} -> {target}")
    else:
        logger.info("✓ 所有前端文件已就绪")
    
    return len(missing_files) == 0


def create_default_config():
    config_path = Path("config/ollama_config.yaml")
    if config_path.exists():
        logger.info(f"Using default Ollama config: {config_path}")
        return str(config_path)

    """创建默认配置文件"""
    if not config_path.exists():
        config_content = """# Ollama配置
models:
  default: "gmm-llama:latest"
  available:
    - "gmm-llama:latest"

ollama:
  base_url: "http://localhost:11434"
  model: "gmm-llama:latest"

inference:
  stream: true
  temperature: 0.7
  max_tokens: 1000
  timeout: 60

web:
  host: "0.0.0.0"
  port: 6000
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
    parser = argparse.ArgumentParser(
        description="分子聊天系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py                    # 使用默认配置启动
  python main.py --port 8888        # 指定端口
  python main.py --debug            # 调试模式
  python main.py --no-reload        # 禁用自动重载
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
        default="0.0.0.0",
        help="服务器地址 (默认: 0.0.0.0)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=6000,
        help="服务器端口 (默认: 6000)"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="启用调试模式"
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        default=True,
        help="启用自动重载 (默认启用)"
    )
    parser.add_argument(
        "--no-reload",
        action="store_true",
        help="禁用自动重载"
    )
    
    args = parser.parse_args()
    load_env_file(os.environ.get("MEDCHAT_ENV_FILE", ".env"))
    
    # 如果指定了no-reload，则禁用reload
    if args.no_reload:
        args.reload = False
    
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
        if args.config and Path(args.config).exists():
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
        print(f"服务地址: http://{args.host}:{args.port}")
        if using_modelscope:
            print("模型后端: ModelScope (Ollama 保留为可选能力)")
        print("="*60)
        print("\n按 Ctrl+C 停止服务\n")
        
        # 启动Uvicorn服务器
        import uvicorn
        
        uvicorn.run(
            "src.web.app:app",
            host=args.host,
            port=args.port,
            reload=args.reload and not args.no_reload,
            log_level="debug" if args.debug else "info",
            access_log=args.debug
        )
        
    except KeyboardInterrupt:
        print("\n\n正在关闭服务...")
        logger.info("用户中断，服务已关闭")
    except ImportError as e:
        logger.error(f"缺少依赖: {e}")
        logger.info("请运行: pip install fastapi uvicorn httpx pyyaml jinja2 websockets python-multipart")
        return 1
    except Exception as e:
        logger.error(f"启动失败: {e}", exc_info=args.debug)
        return 1
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
