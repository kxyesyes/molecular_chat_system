import asyncio
import json
import logging
import os
import re
import sys
from typing import List, Dict, Any, Optional
import httpx
import yaml
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path
import pandas as pd
import numpy as np
import uvicorn

from .llm_runtime_config import (
    load_runtime_config,
    normalize_llm_config,
    public_llm_config,
    save_runtime_config,
)

try:
    import faiss  # type: ignore
except Exception:  # pragma: no cover - optional dependency handling
    faiss = None

# Add project root to path for imports
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# Windows终端UTF-8编码设置
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except:
        pass

# Setup logging first
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}")
DEFAULT_WEB_HOST = "127.0.0.1"
DEFAULT_WEB_PORT = 6001


def load_env_file(env_path: str | Path = ".env") -> None:
    """Load simple KEY=VALUE pairs without adding a runtime dependency."""
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


def expand_env_placeholders(value: Any) -> Any:
    """Recursively expand ${VAR:-default} placeholders in YAML config values."""
    if isinstance(value, dict):
        return {key: expand_env_placeholders(item) for key, item in value.items()}
    if isinstance(value, list):
        return [expand_env_placeholders(item) for item in value]
    if not isinstance(value, str):
        return value

    def replace(match: re.Match[str]) -> str:
        env_name = match.group(1)
        default = match.group(2) if match.group(2) is not None else ""
        return os.environ.get(env_name, default)

    return _ENV_PATTERN.sub(replace, value)


def env_int(name: str, default: int) -> int:
    """Read an integer environment variable with a safe fallback."""
    value = os.environ.get(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning(f"Invalid integer for {name}: {value!r}; using {default}")
        return default


def env_bool(name: str, default: bool = False) -> bool:
    """Read a boolean environment variable with a safe fallback."""
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}



class OllamaModel:
    """Ollama model interface for chat completion"""
    
    def __init__(self, base_url: str = "http://localhost:11434", model_name: str = "gmm-llama:latest"):
        self.base_url = base_url
        self.model_name = model_name
        # 增加超时时间到150秒，与内存中的配置一致
        self.client = httpx.AsyncClient(timeout=150.0)
    
    async def generate(self, prompt: str, temperature: float = 0.7, max_tokens: int = 1500) -> str:
        """Generate response from Ollama model"""
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
    
    async def stream_generate(self, prompt: str, temperature: float = 0.7, max_tokens: int = 1500):
        """Stream response from Ollama model"""
        try:
            async with self.client.stream(
                "POST",
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model_name,
                    "prompt": prompt,
                    "stream": True,
                    "options": {
                        "temperature": temperature,
                        "num_predict": max_tokens
                    }
                }
            ) as response:
                
                if response.status_code == 200:
                    async for line in response.aiter_lines():
                        if line:
                            try:
                                data = json.loads(line)
                                if "response" in data:
                                    yield data["response"]
                                if data.get("done", False):
                                    break
                            except json.JSONDecodeError:
                                continue
                else:
                    logger.error(f"Ollama streaming API error: {response.status_code}")
                    yield "I apologize, but I'm having trouble generating a response right now."
                    
        except Exception as e:
            logger.error(f"Error streaming from Ollama: {e}")
            yield "I apologize, but I'm experiencing technical difficulties."

class RAGSystem:
    """Retrieval-Augmented Generation system for molecular data"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.embedding_model = None
        self.vector_index = None
        self.molecules_df: Optional[pd.DataFrame] = None
        self.is_initialized = False
    
    async def initialize(self):
        """Initialize the RAG system"""
        try:
            if faiss is None:
                logger.warning("FAISS is not installed; RAG retrieval is disabled.")
                self.is_initialized = False
                return

            # Load embedding model
            embedding_model_name = self.config.get("rag", {}).get("embedding_model", "nomic-embed-text:latest")
            logger.info(f"Loading embedding model: {embedding_model_name}")
            
            # For Ollama embeddings
            self.embedding_client = httpx.AsyncClient(timeout=60.0)
            self.embedding_model_name = embedding_model_name
            
            # Load molecular data
            csv_path = self.config.get("rag", {}).get("csv_path", "data/canonical_moses_5w.csv")
            if os.path.exists(csv_path):
                self.molecules_df = pd.read_csv(csv_path)
                logger.info(f"Loaded {len(self.molecules_df)} molecules from {csv_path}")
            else:
                logger.warning(f"Molecular data file not found: {csv_path}")
                self.molecules_df = pd.DataFrame()
            
            # Load or create vector index
            vector_store_path = self.config.get("rag", {}).get("vector_store_path", "data/molecular_faiss_index")
            await self._load_or_create_index(vector_store_path)
            
            self.is_initialized = True
            logger.info("RAG system initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize RAG system: {e}")
            self.is_initialized = False
    
    async def get_embedding(self, text: str) -> np.ndarray:
        """Get embedding for text using Ollama"""
        try:
            response = await self.embedding_client.post(
                "http://localhost:11434/api/embeddings",
                json={
                    "model": self.embedding_model_name,
                    "prompt": text
                }
            )
            
            if response.status_code == 200:
                result = response.json()
                return np.array(result.get("embedding", []))
            else:
                logger.error(f"Embedding API error: {response.status_code}")
                return np.array([])
                
        except Exception as e:
            logger.error(f"Error getting embedding: {e}")
            return np.array([])
    
    async def _load_or_create_index(self, vector_store_path: str):
        """Load existing vector index or create new one"""
        try:
            if os.path.exists(f"{vector_store_path}.index"):
                # Load existing index
                self.vector_index = faiss.read_index(f"{vector_store_path}.index")
                logger.info("Loaded existing vector index")
            else:
                # Create new index
                if self.molecules_df is not None and not self.molecules_df.empty:
                    await self._create_index(vector_store_path)
                else:
                    logger.warning("No molecular data available to create index")
                    self.vector_index = None
                    
        except Exception as e:
            logger.error(f"Error with vector index: {e}")
            self.vector_index = None
    
    async def _create_index(self, vector_store_path: str):
        """Create vector index from molecular data"""
        try:
            logger.info("Creating vector index from molecular data...")
            
            # Create embeddings for molecules
            embeddings = []
            if self.molecules_df is not None:
                count = 0
                for _, row in self.molecules_df.iterrows():
                    # Create text representation of molecule
                    mol_text = f"SMILES: {row.get('SMILES', '')}"
                    embedding = await self.get_embedding(mol_text)
                    
                    if len(embedding) > 0:
                        embeddings.append(embedding)
                    
                    # Use counter for progress tracking
                    if count % 100 == 0 and self.molecules_df is not None:
                        logger.info(f"Processed {count}/{len(self.molecules_df)} molecules")
                    count += 1
            
            if embeddings:
                embeddings_array = np.vstack(embeddings)
                
                # Create FAISS index
                dimension = embeddings_array.shape[1]
                self.vector_index = faiss.IndexFlatIP(dimension)  # Inner product for similarity
                
                # Normalize embeddings for cosine similarity
                embeddings_float32 = embeddings_array.astype(np.float32)
                faiss.normalize_L2(embeddings_float32)
                if self.vector_index is not None:
                    # Add embeddings to index - FAISS add method takes the array directly
                    # Ignore type checking errors for FAISS methods
                    self.vector_index.add(embeddings_float32)  # type: ignore
                
                # Save index
                os.makedirs(os.path.dirname(vector_store_path), exist_ok=True)
                if self.vector_index is not None:
                    faiss.write_index(self.vector_index, f"{vector_store_path}.index")
                
                logger.info(f"Created and saved vector index with {len(embeddings)} embeddings")
            else:
                logger.error("No valid embeddings created")
                
        except Exception as e:
            logger.error(f"Error creating vector index: {e}")
            self.vector_index = None
    
    async def search_similar_molecules(self, query: str, k: int = 2) -> List[Dict[str, Any]]:
        """Search for similar molecules based on query"""
        if not self.is_initialized or self.vector_index is None or self.molecules_df is None or self.molecules_df.empty:
            return []
        
        try:
            # Get query embedding
            query_embedding = await self.get_embedding(query)
            if len(query_embedding) == 0:
                return []
            
            # Normalize query embedding
            query_embedding = query_embedding.reshape(1, -1).astype(np.float32)
            faiss.normalize_L2(query_embedding)
            
            # Search with proper parameters
            if self.vector_index is not None:
                # Search with proper parameters - FAISS search method takes the query and k
                # Ignore type checking errors for FAISS methods
                distances, labels = self.vector_index.search(query_embedding, k)  # type: ignore
                scores = distances
                indices = labels
            else:
                return []
            
            # Return results
            results = []
            if self.molecules_df is not None:
                for i, (score, idx) in enumerate(zip(scores[0], indices[0])):
                    if idx < len(self.molecules_df):
                        mol_data = self.molecules_df.iloc[idx].to_dict()
                        mol_data['similarity_score'] = float(score)
                        results.append(mol_data)
            
            return results
            
        except Exception as e:
            logger.error(f"Error searching molecules: {e}")
            return []

class MolecularChatApp:
    """Main application class"""
    
    def __init__(self, config_path: str = "config/ollama_config.yaml"):
        self.config = self._load_config(config_path)
        
        # 检测配置类型并初始化对应的模型
        def _init_ollama_model():
            ollama_config = self.config.get("ollama", {})
            return OllamaModel(
                base_url=ollama_config.get("base_url", "http://localhost:11434"),
                model_name=ollama_config.get("model", "gmm-llama:latest")
            )

        modelscope_config = self.config.get("modelscope", {})
        modelscope_api_key = str(modelscope_config.get("api_key", "") or "").strip()
        if modelscope_config and modelscope_api_key:
            # 使用ModelScope API模型
            from src.agent.modelscope_model import ModelScopeModel
            default_model = modelscope_config.get("default_model", "glm4")
            model_name = modelscope_config.get("models", {}).get(default_model, {}).get("name", "ZhipuAI/GLM-4.6")
            
            self.model = ModelScopeModel(
                api_key=modelscope_api_key,
                model_name=model_name,
                base_url=modelscope_config.get("base_url", "https://api-inference.modelscope.cn/v1/chat/completions")
            )
            logger.info(f"✅ 使用ModelScope API模型: {model_name}")
        else:
            # 使用Ollama本地模型
            if modelscope_config:
                logger.warning("ModelScope API key is empty; falling back to local Ollama model.")
            self.model = _init_ollama_model()
            logger.info(f"✅ 使用Ollama本地模型: {self.model.model_name}")
        self.runtime_llm_config_path = Path(
            os.environ.get("MEDCHAT_LLM_CONFIG_PATH", "scratch/llm_runtime_config.json")
        )
        self.active_llm_config = self._load_active_llm_config()
        self.model = self._create_model_from_llm_config(self.active_llm_config)
        logger.info(
            "Active LLM provider: %s / %s",
            self.active_llm_config.get("provider"),
            self.model.model_name,
        )
        
        self.rag_system = RAGSystem(self.config)
        
        # 初始化Agent系统
        try:
            from src.agent.react_agent import ReActMolecularAgent
            self.agent_system = ReActMolecularAgent(llm=None)  # Agent工具使用自己的gmm-llama
            logger.info("✅ Agent系统初始化成功")
        except Exception as e:
            logger.warning(f"⚠️ Agent系统初始化失败: {e}")
            self.agent_system = None
        
        # 初始化ChatHandler
        try:
            from src.web.chat_handler import ChatHandler
            self.chat_handler = ChatHandler(
                model=self.model,
                rag_service=self.rag_system,
                agent_system=self.agent_system,
                config=self.config
            )
            logger.info("✅ ChatHandler初始化成功")
        except Exception as e:
            logger.error(f"❌ ChatHandler初始化失败: {e}")
            self.chat_handler = None
        
        self.conversation_history = []
        
        # Create FastAPI app
        self.app = FastAPI(title="Molecular Chat System")
        self._setup_routes()

    def _llm_config_from_yaml(self) -> Dict[str, Any]:
        modelscope_config = self.config.get("modelscope", {})
        modelscope_api_key = str(modelscope_config.get("api_key", "") or "").strip()
        if modelscope_config and modelscope_api_key:
            default_model = modelscope_config.get("default_model", "glm4")
            model_name = modelscope_config.get("models", {}).get(default_model, {}).get("name", "ZhipuAI/GLM-5.1")
            return normalize_llm_config(
                {
                    "provider": "modelscope",
                    "base_url": modelscope_config.get(
                        "base_url",
                        "https://api-inference.modelscope.cn/v1/chat/completions",
                    ),
                    "model_name": model_name,
                    "api_key": modelscope_api_key,
                    "stream": self.config.get("inference", {}).get("stream", True),
                }
            )

        ollama_config = self.config.get("ollama", {})
        return normalize_llm_config(
            {
                "provider": "ollama",
                "base_url": ollama_config.get("base_url", "http://localhost:11434"),
                "model_name": ollama_config.get("model", "gmm-llama:latest"),
                "stream": self.config.get("inference", {}).get("stream", True),
            }
        )

    def _load_active_llm_config(self) -> Dict[str, Any]:
        config = load_runtime_config(self.runtime_llm_config_path) or self._llm_config_from_yaml()
        if not config.get("base_url"):
            config["base_url"] = (
                "http://localhost:11434"
                if config.get("provider") == "ollama"
                else "https://api-inference.modelscope.cn/v1/chat/completions"
            )
        if not config.get("model_name"):
            config["model_name"] = "gmm-llama:latest" if config.get("provider") == "ollama" else "ZhipuAI/GLM-5.1"
        return normalize_llm_config(config)

    def _create_model_from_llm_config(self, llm_config: Dict[str, Any]):
        config = normalize_llm_config(llm_config)
        if config.get("provider") == "ollama":
            return OllamaModel(
                base_url=config.get("base_url") or "http://localhost:11434",
                model_name=config.get("model_name") or "gmm-llama:latest",
            )

        from src.agent.modelscope_model import ModelScopeModel

        return ModelScopeModel(
            api_key=config.get("api_key", ""),
            model_name=config.get("model_name") or "ZhipuAI/GLM-5.1",
            base_url=config.get("base_url") or "https://api-inference.modelscope.cn/v1/chat/completions",
        )

    def _apply_llm_config(self, llm_config: Dict[str, Any]) -> Dict[str, Any]:
        config = normalize_llm_config(llm_config)
        self.model = self._create_model_from_llm_config(config)
        self.active_llm_config = config
        self.config.setdefault("inference", {})["stream"] = config.get("stream", True)
        if self.chat_handler:
            self.chat_handler.model = self.model
            self.chat_handler.config = self.config
        return config
    
    def _load_config(self, config_path: str) -> Dict[str, Any]:
        """Load configuration from YAML file"""
        try:
            load_env_file(os.environ.get("MEDCHAT_ENV_FILE", ".env"))
            with open(config_path, 'r', encoding='utf-8') as f:
                return expand_env_placeholders(yaml.safe_load(f) or {})
        except Exception as e:
            logger.error(f"Error loading config: {e}")
            return self._default_config()
    
    def _default_config(self) -> Dict[str, Any]:
        """Return default configuration"""
        return {
            "ollama": {
                "base_url": os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
                "model": os.environ.get("OLLAMA_MODEL", "gmm-llama:latest")
            },
            "rag": {
                "enabled": True,
                "csv_path": "data/canonical_moses_5w.csv",
                "embedding_model": "nomic-embed-text:latest",
                "vector_store_path": "data/molecular_faiss_index",
                "default_k": 2
            },
            "inference": {
                "stream": True,
                "temperature": 0.7,
                "max_tokens": 1500
            },
            "web": {
                "host": os.environ.get("MEDCHAT_HOST", DEFAULT_WEB_HOST),
                "port": env_int("MEDCHAT_PORT", DEFAULT_WEB_PORT),
                "debug": env_bool("MEDCHAT_DEBUG", False)
            }
        }
    
    def _setup_routes(self):
        """Setup FastAPI routes"""
        
        # 🔥 关键修复：获取 app.py 文件所在目录的绝对路径
        base_dir = os.path.dirname(os.path.abspath(__file__))
        
        # Serve static files - 使用绝对路径
        static_dir = os.path.join(base_dir, "static")
        if os.path.exists(static_dir):
            self.app.mount("/static", StaticFiles(directory=static_dir), name="static")
            logger.info(f"✅ Static files mounted: /static -> {static_dir}")
        else:
            # 尝试备用路径（相对于项目根目录）
            alt_static = "src/web/static"
            if os.path.exists(alt_static):
                self.app.mount("/static", StaticFiles(directory=alt_static), name="static")
                logger.info(f"✅ Static files mounted: /static -> {alt_static}")
            else:
                logger.warning(f"❌ Static directory not found! Tried: {static_dir} and {alt_static}")

        # Templates - 使用绝对路径
        templates = None
        template_dir = os.path.join(base_dir, "templates")
        if os.path.exists(template_dir):
            templates = Jinja2Templates(directory=template_dir)
            logger.info(f"✅ Templates directory: {template_dir}")
        else:
            # 尝试备用路径
            alt_templates = "src/web/templates"
            if os.path.exists(alt_templates):
                templates = Jinja2Templates(directory=alt_templates)
                logger.info(f"✅ Templates directory: {alt_templates}")

        @self.app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            """WebSocket endpoint for chat - 使用ChatHandler"""
            if self.chat_handler:
                # 使用新的ChatHandler（支持Agent工具）
                await self.chat_handler.handle_websocket(websocket)
            else:
                # 回退到旧的处理方式
                await self._handle_websocket(websocket)

        @self.app.get("/health")
        async def health_check():
            """Health check endpoint"""
            return {
                "status": "ok",
                "model": self.model.model_name,
                "rag_enabled": self.rag_system.is_initialized,
                "agent_enabled": True,
                "timestamp": __import__('time').time()
            }

        @self.app.get("/api/llm/config")
        async def get_llm_config():
            """Return public LLM connection configuration without leaking API keys."""
            return {
                "success": True,
                "config": public_llm_config(self.active_llm_config),
            }

        @self.app.post("/api/llm/config")
        async def save_llm_config(request: Request):
            """Save and activate runtime LLM connection configuration."""
            payload = await request.json()
            next_config = normalize_llm_config(payload)
            clear_api_key = bool(payload.get("clear_api_key"))
            if (
                not clear_api_key
                and not next_config.get("api_key")
                and next_config.get("provider") == self.active_llm_config.get("provider")
            ):
                next_config["api_key"] = self.active_llm_config.get("api_key", "")

            saved_config = save_runtime_config(self.runtime_llm_config_path, next_config)
            self._apply_llm_config(saved_config)
            return {
                "success": True,
                "message": "模型接入配置已保存并生效",
                "config": public_llm_config(saved_config),
            }

        @self.app.post("/api/llm/test")
        async def test_llm_config(request: Request):
            """Test a submitted LLM connection without saving it."""
            payload = await request.json()
            test_config = normalize_llm_config(payload)
            if not test_config.get("api_key") and test_config.get("provider") != "ollama":
                test_config["api_key"] = self.active_llm_config.get("api_key", "")

            try:
                test_model = self._create_model_from_llm_config(test_config)
                response = await test_model.generate(
                    "请用一句中文回复：连接测试成功。",
                    temperature=0.1,
                    max_tokens=64,
                )
                is_success = bool(response and "失败" not in response and "未配置" not in response)
                return {
                    "success": is_success,
                    "message": "连接测试成功" if is_success else response,
                    "model": test_model.model_name,
                }
            except Exception as e:
                logger.error(f"LLM connection test failed: {e}", exc_info=True)
                return {"success": False, "message": f"连接测试失败: {str(e)}"}

        @self.app.post("/api/switch_model")
        async def switch_model(request: Request):
            """Switch model name for the current provider."""
            payload = await request.json()
            model_key = str(payload.get("model") or "").strip()
            model_map = {
                "glm4": "ZhipuAI/GLM-5.1",
                "glm5.1": "ZhipuAI/GLM-5.1",
                "qwen3": "Qwen/Qwen3-235B-A22B-Instruct-2507",
                "gmm-llama": "gmm-llama:latest",
            }
            next_config = dict(self.active_llm_config)
            next_config["model_name"] = model_map.get(model_key, model_key or next_config.get("model_name"))
            saved_config = save_runtime_config(self.runtime_llm_config_path, next_config)
            self._apply_llm_config(saved_config)
            return {
                "success": True,
                "message": f"已切换到 {saved_config.get('model_name')}",
                "config": public_llm_config(saved_config),
            }

        # Register additional page routes
        from .routes.main_routes import register_main_routes
        register_main_routes(self.app, templates)

        # Register API routes
        docking_config = self.config.get("docking", {})
        if docking_config.get("root_dir"):
            os.environ["MOLECULAR_DOCKING_ROOT"] = docking_config["root_dir"]
        if docking_config.get("vina_exe"):
            os.environ["MOLECULAR_DOCKING_VINA"] = docking_config["vina_exe"]
        if docking_config.get("adfrsuite_bin"):
            os.environ["MOLECULAR_DOCKING_ADFR_BIN"] = docking_config["adfrsuite_bin"]
        if docking_config.get("prepare_ligand_cmd"):
            os.environ["MOLECULAR_DOCKING_PREPARE_LIGAND"] = docking_config["prepare_ligand_cmd"]
        if docking_config.get("prepare_receptor_cmd"):
            os.environ["MOLECULAR_DOCKING_PREPARE_RECEPTOR"] = docking_config["prepare_receptor_cmd"]

        from .routes.api_routes import setup_api_routes
        from ..docking import docking_service
        docking_service.configure(docking_config)
        setup_api_routes(self.app, docking_service)

        # Register molecular design routes
        try:
            from .routes.design_routes import setup_design_routes
            setup_design_routes(self.app, model=self.model, config=self.config)
            logger.info("✅ 分子设计模块路由注册成功")
        except Exception as e:
            logger.warning(f"⚠️ 分子设计模块路由注册失败: {e}")

        # Register local target-search demo routes.
        try:
            from src.target_search.routes import setup_target_search_routes
            setup_target_search_routes(self.app)
            logger.info("✅ 靶点搜索 demo 路由注册成功")
        except Exception as e:
            logger.warning(f"⚠️ 靶点搜索 demo 路由注册失败: {e}")
        # Register task runtime and system metadata routes for Agent workflows.
        try:
            from src.task_runtime.routes import setup_task_routes
            from .routes.agent_workflow_routes import setup_agent_workflow_routes
            from .routes.system_routes import setup_system_routes

            setup_task_routes(self.app)
            setup_agent_workflow_routes(self.app)
            setup_system_routes(self.app)
            logger.info("Task runtime and system metadata routes registered")
        except Exception as e:
            logger.warning(f"Task runtime routes registration failed: {e}")

    async def _handle_websocket(self, websocket: WebSocket):
        """Handle WebSocket connections"""
        await websocket.accept()
        
        try:
            while True:
                # Receive message
                data = await websocket.receive_text()
                message_data = json.loads(data)
                
                message = message_data.get("message", "")
                enable_rag = message_data.get("enable_rag", True)
                
                if not message.strip():
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "message": "Please enter a message"
                    }))
                    continue
                
                # Send acknowledgment
                await websocket.send_text(json.dumps({
                    "type": "status",
                    "message": "Processing your message..."
                }))

                # Check for agent actions first
                agent_used = False
                agent_response = ""

                # Retrieve relevant molecules if RAG is enabled
                retrieved_molecules = []
                rag_context = ""
                
                if enable_rag and self.rag_system.is_initialized:
                    await websocket.send_text(json.dumps({
                        "type": "status",
                        "message": "Searching for relevant molecular data..."
                    }))
                    
                    k = self.config.get("rag", {}).get("default_k", 2)
                    retrieved_molecules = await self.rag_system.search_similar_molecules(message, k)
                    
                    if retrieved_molecules:
                        rag_context = self._format_rag_context(retrieved_molecules)
                
                # Build prompt with agent context
                if agent_used and agent_response:
                    prompt = self._build_prompt_with_agent(message, agent_response, rag_context, retrieved_molecules)
                else:
                    prompt = self._build_prompt(message, rag_context, retrieved_molecules)
                
                # Send generation status
                await websocket.send_text(json.dumps({
                    "type": "status",
                    "message": "AI is generating response..."
                }))
                
                # Initialize response variable (THIS FIXES THE MAIN ISSUE)
                full_response = ""
                
                # Generate response
                if self.config.get("inference", {}).get("stream", True):
                    # Streaming generation
                    async for chunk in self.model.stream_generate(
                        prompt,
                        temperature=self.config.get("inference", {}).get("temperature", 0.7),
                        max_tokens=self.config.get("inference", {}).get("max_tokens", 1500)
                    ):
                        if chunk:
                            full_response += chunk
                            await websocket.send_text(json.dumps({
                                "type": "stream",
                                "content": chunk
                            }))
                            await asyncio.sleep(0.01)
                    
                    # Send completion signal
                    await websocket.send_text(json.dumps({
                        "type": "complete",
                        "content": full_response
                    }))
                else:
                    # Non-streaming generation
                    full_response = await self.model.generate(
                        prompt,
                        temperature=self.config.get("inference", {}).get("temperature", 0.7),
                        max_tokens=self.config.get("inference", {}).get("max_tokens", 1500)
                    )
                    await websocket.send_text(json.dumps({
                        "type": "message",
                        "message": full_response
                    }))
                
                # Save conversation history (full_response is now always defined)
                self.conversation_history.append({
                    "user": message,
                    "agent_used": agent_used,
                    "agent_response": agent_response if agent_used else None,
                    "rag_enabled": enable_rag,
                    "molecules_retrieved": len(retrieved_molecules),
                    "assistant": full_response
                })
                
                # Send RAG info if molecules were retrieved
                if retrieved_molecules:
                    await websocket.send_text(json.dumps({
                        "type": "rag_info",
                        "molecules": [
                            {
                                "smiles": mol.get("SMILES", ""),
                                "similarity": mol.get("similarity_score", 0),
                                "properties": {k: v for k, v in mol.items() 
                                            if k not in ["SMILES", "similarity_score"]}
                            }
                            for mol in retrieved_molecules[:3]  # Show top 3
                        ]
                    }))
                
        except WebSocketDisconnect:
            logger.info("WebSocket disconnected")
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
            try:
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "message": "An error occurred while processing your message"
                }))
            except:
                pass
    
    def _format_rag_context(self, molecules: List[Dict[str, Any]]) -> str:
        """Format retrieved molecules as context"""
        if not molecules:
            return ""
        
        context_parts = ["Relevant molecular data found:"]
        
        for i, mol in enumerate(molecules, 1):
            smiles = mol.get("SMILES", "Unknown")
            score = mol.get("similarity_score", 0)
            
            context_parts.append(f"{i}. SMILES: {smiles} (similarity: {score:.3f})")
            
            # Add other properties if available
            for key, value in mol.items():
                if key not in ["SMILES", "similarity_score"] and pd.notna(value):
                    context_parts.append(f"   {key}: {value}")
        
        return "\n".join(context_parts)
    
    def _build_prompt(self, user_message: str, rag_context: str, retrieved_molecules: List[Dict[str, Any]]) -> str:
        """Build the complete prompt for the model"""

        # 根据模型选择合适的系统提示词
        if "gmm-llama" in self.model.model_name.lower():
            # 英文模型使用英文提示词
            system_prompt = """You are an AI assistant specialized in molecular science and chemistry. You help users understand molecules, their properties, structures, and applications.

Key guidelines:
- Provide accurate, scientific information about molecules and chemistry
- If molecular data is provided in the context, use it to enhance your responses
- Explain complex concepts clearly and accessibly
- When discussing SMILES notation, explain what it represents
- If you're unsure about specific molecular properties, acknowledge the uncertainty
- Be helpful and engaging while maintaining scientific accuracy
- Please respond in Chinese for Chinese questions, and in English for English questions"""
        else:
            # 中文模型使用中文提示词
            system_prompt = """你是一个专门从事分子科学和化学的AI助手。你帮助用户理解分子、它们的性质、结构和应用。

主要指导原则:
- 提供关于分子和化学的准确科学信息
- 如果上下文中提供了分子数据,请使用它来增强你的回答
- 清晰易懂地解释复杂概念
- 当讨论SMILES表示法时,解释它代表什么
- 如果你对特定分子性质不确定,请承认不确定性
- 保持科学准确性的同时要有帮助性和吸引力
- 对于中文问题请用中文回答,对于英文问题请用英文回答"""

        # Build conversation context
        conversation_context = ""
        if self.conversation_history:
            recent_history = self.conversation_history[-3:]  # Last 3 exchanges
            conversation_context = "\nRecent conversation:\n"
            for entry in recent_history:
                conversation_context += f"Human: {entry['user']}\n"
                conversation_context += f"Assistant: {entry['assistant']}\n"

        # Build final prompt
        prompt_parts = [system_prompt]

        if conversation_context:
            prompt_parts.append(conversation_context)

        if rag_context:
            prompt_parts.append(f"\nContext from molecular database:\n{rag_context}")

        prompt_parts.append(f"\nHuman: {user_message}")
        prompt_parts.append("Assistant:")

        return "\n".join(prompt_parts)

    def _build_prompt_with_agent(self, user_message: str, agent_response: str, rag_context: str, retrieved_molecules: List[Dict[str, Any]]) -> str:
        """Build prompt with agent analysis results"""

        # 根据模型选择合适的系统提示词
        if "gmm-llama" in self.model.model_name.lower():
            system_prompt = """You are an AI assistant specialized in molecular science and chemistry. You help users understand molecules, their properties, structures, and applications.

An intelligent agent has already performed analysis on the user's request. Please use this analysis to provide a comprehensive response.

Key guidelines:
- Integrate the agent's analysis into your response naturally
- Provide additional context and explanation where helpful
- If molecular data is provided in the context, use it to enhance your responses
- Explain complex concepts clearly and accessibly
- Please respond in Chinese for Chinese questions, and in English for English questions"""
        else:
            system_prompt = """你是一个专门从事分子科学和化学的AI助手。你帮助用户理解分子、它们的性质、结构和应用。

智能代理已经对用户的请求进行了分析。请使用这个分析来提供全面的回答。

主要指导原则:
- 自然地将代理的分析整合到你的回答中
- 在有帮助的地方提供额外的上下文和解释
- 如果上下文中提供了分子数据,请使用它来增强你的回答
- 清晰易懂地解释复杂概念
- 对于中文问题请用中文回答,对于英文问题请用英文回答"""

        # Build conversation context
        conversation_context = ""
        if self.conversation_history:
            recent_history = self.conversation_history[-3:]  # Last 3 exchanges
            conversation_context = "\nRecent conversation:\n"
            for entry in recent_history:
                conversation_context += f"Human: {entry['user']}\n"
                conversation_context += f"Assistant: {entry['assistant']}\n"

        # Build final prompt
        prompt_parts = [system_prompt]

        if conversation_context:
            prompt_parts.append(conversation_context)

        if rag_context:
            prompt_parts.append(f"\nContext from molecular database:\n{rag_context}")

        # Add agent analysis
        prompt_parts.append(f"\nAgent Analysis Results:\n{agent_response}")

        prompt_parts.append(f"\nHuman: {user_message}")
        prompt_parts.append("Assistant:")

        return "\n".join(prompt_parts)



    async def initialize(self):
        """Initialize the application"""
        logger.info("Initializing Molecular Chat System...")
        
        # Initialize RAG system
        if self.config.get("rag", {}).get("enabled", True):
            await self.rag_system.initialize()
        
        # 预加载反向寻靶数据库（在后台线程中执行，避免阻塞启动）
        try:
            import threading
            def preload_reverse_target():
                try:
                    from src.reverse_target.predictor import get_predictor
                    logger.info("🔄 预加载反向寻靶数据库...")
                    predictor = get_predictor()
                    logger.info(f"✅ 反向寻靶数据库预加载完成")
                except Exception as e:
                    logger.warning(f"⚠️ 反向寻靶数据库预加载失败: {e}")
            
            # 在后台线程中预加载，不阻塞应用启动
            preload_thread = threading.Thread(target=preload_reverse_target, daemon=True)
            preload_thread.start()
        except Exception as e:
            logger.warning(f"⚠️ 无法启动反向寻靶预加载: {e}")
        
        logger.info("Molecular Chat System initialized successfully")
    
    def run(self, host: Optional[str] = None, port: Optional[int] = None, debug: bool = False):
        """Run the application"""
        host = host or self.config.get("web", {}).get("host", DEFAULT_WEB_HOST)
        port = port or self.config.get("web", {}).get("port", DEFAULT_WEB_PORT)
        debug = debug or self.config.get("web", {}).get("debug", False)
        
        uvicorn.run(
            "src.web.app:app",
            host=host,
            port=port,
            reload=debug,
            log_level="info"
        )

# Create app instance at module level
app_instance = None
app = None

def create_app_sync():
    """Create app synchronously for uvicorn"""
    global app_instance, app
    if app_instance is None:
        # 从环境变量读取配置文件路径
        config_path = os.environ.get("MOLECULAR_CHAT_CONFIG", "config/ollama_config.yaml")
        logger.info(f"🔧 加载配置文件: {config_path}")
        
        app_instance = MolecularChatApp(config_path=config_path)
        app = app_instance.app

        # Add startup event to initialize async components
        @app.on_event("startup")
        async def startup_event():
            await app_instance.initialize()

    return app

# For uvicorn
app = create_app_sync()

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Molecular Chat System")
    parser.add_argument("--host", default=os.environ.get("MEDCHAT_HOST", DEFAULT_WEB_HOST), help="Host to bind to")
    parser.add_argument("--port", type=int, default=env_int("MEDCHAT_PORT", DEFAULT_WEB_PORT), help="Port to bind to")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    parser.add_argument("--no-reload", action="store_true", help="Disable auto-reload")
    
    args = parser.parse_args()
    
    # App is already created globally for uvicorn compatibility
    # But if run directly, we can just use the global app
    uvicorn.run(
        "src.web.app:app",
        host=args.host,
        port=args.port,
        reload=args.debug and not args.no_reload,
        log_level="info"
    )
