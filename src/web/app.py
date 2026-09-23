import asyncio
from contextlib import nullcontext
from datetime import datetime, timezone
import json
import logging
import os
import re
import sys
from typing import List, Dict, Any, Optional
import httpx
import yaml
from fastapi import FastAPI, WebSocket, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path
import pandas as pd
import numpy as np
import uvicorn

from .llm_runtime_config import (
    normalize_llm_config,
    public_llm_config,
)
from .user_llm_config import (
    load_user_llm_config, save_user_llm_config, user_llm_config_path,
    user_llm_signature, resolve_user_llm_request, default_user_llm_config,
)
from .models import OllamaModel, generate_for_chat
from .model_lifecycle import ModelRequestGate, close_owned_model, finish_on_cancel
from src.rag.retrieval import search_molecular_index
from .rag_index import (
    CURRENT_SCHEMA_VERSION,
    RAGIndexCompatibilityError,
    RAGIndexManifest,
    atomic_save_index_pair,
    file_sha256,
    immutable_index_snapshot,
    load_manifest,
    manifest_path,
    validate_manifest,
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



class RAGSystem:
    """Retrieval-Augmented Generation system for molecular data"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.embedding_model = None
        rag_config = self.config.get("rag", {})
        self.embedding_model_name = rag_config.get(
            "embedding_model",
            "nomic-embed-text:latest",
        )
        self.embedding_endpoint = rag_config.get(
            "embedding_endpoint", "http://localhost:11434/api/embeddings"
        )
        self._index_sha256 = None
        self.vector_index = None
        self.molecules_df: Optional[pd.DataFrame] = None
        self.csv_path = Path(
            rag_config.get("csv_path", "data/canonical_moses_5w.csv")
        )
        self.source_path = self.csv_path
        self.manifest: Optional[RAGIndexManifest] = None
        self.index_status = "uninitialized"
        self.is_initialized = False
    
    async def initialize(self):
        """Initialize the RAG system"""
        try:
            if faiss is None:
                logger.warning("FAISS is not installed; RAG retrieval is disabled.")
                self.index_status = "unavailable: faiss is not installed"
                self.is_initialized = False
                return

            # Load embedding model
            embedding_model_name = self.config.get("rag", {}).get("embedding_model", "nomic-embed-text:latest")
            logger.info(f"Loading embedding model: {embedding_model_name}")
            
            # For Ollama embeddings
            self.embedding_client = httpx.AsyncClient(timeout=60.0)
            self.embedding_model_name = embedding_model_name
            
            # Load molecular data
            self.csv_path = Path(
                self.config.get("rag", {}).get(
                    "csv_path",
                    "data/canonical_moses_5w.csv",
                )
            )
            self.source_path = self.csv_path
            if self.csv_path.exists():
                self.molecules_df = pd.read_csv(self.csv_path)
                logger.info(
                    f"Loaded {len(self.molecules_df)} molecules from {self.csv_path}"
                )
            else:
                logger.warning(f"Molecular data file not found: {self.csv_path}")
                self.molecules_df = pd.DataFrame()
            
            # Load or create vector index
            vector_store_path = self.config.get("rag", {}).get("vector_store_path", "data/molecular_faiss_index")
            await self._load_or_create_index(vector_store_path)
            
            self.is_initialized = self.vector_index is not None and self.manifest is not None
            if self.is_initialized:
                logger.info("RAG system initialized successfully")
            else:
                logger.warning(
                    "RAG system initialized without a usable index: %s",
                    self.index_status,
                )
            
        except Exception as e:
            logger.error(f"Failed to initialize RAG system: {e}")
            self.index_status = "error: initialization failed"
            self.is_initialized = False
    
    async def get_embedding(self, text: str) -> np.ndarray:
        """Get embedding for text using Ollama"""
        try:
            response = await self.embedding_client.post(
                self.embedding_endpoint,
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
        index_path = Path(f"{vector_store_path}.index")
        index_manifest_path = manifest_path(index_path)
        incompatible_reason: Optional[str] = None
        self.vector_index = None
        self.manifest = None
        self._index_sha256 = None

        if index_path.is_file() and index_manifest_path.is_file():
            try:
                candidate_manifest = load_manifest(index_manifest_path)
                with immutable_index_snapshot(index_path) as index_snapshot_path:
                    candidate_index_sha256 = file_sha256(index_snapshot_path)
                    candidate_index = faiss.read_index(str(index_snapshot_path))
                    validate_manifest(
                        candidate_manifest,
                        source_path=self.source_path,
                        index_sha256=candidate_index_sha256,
                        embedding_model=self.embedding_model_name,
                        vector_dimension=int(candidate_index.d),
                        vector_count=int(candidate_index.ntotal),
                        source_row_count=(
                            len(self.molecules_df)
                            if self.molecules_df is not None
                            else 0
                        ),
                    )
            except RAGIndexCompatibilityError as error:
                incompatible_reason = str(error)
            except Exception:
                incompatible_reason = (
                    "RAG index manifest incompatible: index cannot be read"
                )
            else:
                self.vector_index = candidate_index
                self.manifest = candidate_manifest
                self._index_sha256 = candidate_index_sha256
                self.index_status = "loaded"
                logger.info("Loaded compatible vector index and manifest")
                return
        elif index_path.exists() or index_manifest_path.exists():
            incompatible_reason = (
                "RAG index manifest incompatible: index and manifest must both exist"
            )

        if incompatible_reason:
            self.index_status = f"incompatible: {incompatible_reason}"
            logger.warning("Rejected existing RAG index: %s", incompatible_reason)

        if self.molecules_df is not None and not self.molecules_df.empty:
            await self._create_index(vector_store_path)
            if self.vector_index is not None and self.manifest is not None:
                if incompatible_reason:
                    self.index_status = "rebuilt_after_incompatible"
                return

        self.vector_index = None
        self.manifest = None
        if incompatible_reason:
            self.index_status = f"incompatible: {incompatible_reason}"
        elif self.index_status == "uninitialized":
            self.index_status = "unavailable: no molecular data"
            logger.warning("No molecular data available to create index")
    
    async def _create_index(self, vector_store_path: str):
        """Create vector index from molecular data"""
        try:
            logger.info("Creating vector index from molecular data...")
            
            # Create embeddings for molecules
            embeddings: List[np.ndarray] = []
            row_mapping: List[int] = []
            embedding_dimension: Optional[int] = None
            if self.molecules_df is not None:
                for source_position, (_, row) in enumerate(
                    self.molecules_df.iterrows()
                ):
                    # Create text representation of molecule
                    mol_text = f"SMILES: {row.get('SMILES', '')}"
                    embedding = await self.get_embedding(mol_text)

                    embedding_array = np.asarray(embedding)
                    if embedding_array.ndim != 1 or embedding_array.size == 0:
                        continue
                    if not np.all(np.isfinite(embedding_array)):
                        logger.warning(
                            "Skipping non-finite embedding for source row %s",
                            source_position,
                        )
                        continue
                    current_dimension = int(embedding_array.shape[0])
                    if embedding_dimension is None:
                        embedding_dimension = current_dimension
                    elif current_dimension != embedding_dimension:
                        logger.warning(
                            "Skipping source row %s with incompatible embedding dimension",
                            source_position,
                        )
                        continue
                    embeddings.append(embedding_array.astype(np.float32))
                    row_mapping.append(source_position)
                    
                    # Use counter for progress tracking
                    if source_position % 100 == 0:
                        logger.info(
                            f"Processed {source_position}/{len(self.molecules_df)} molecules"
                        )
            
            if embeddings:
                embeddings_array = np.vstack(embeddings)
                
                # Create FAISS index
                dimension = embeddings_array.shape[1]
                candidate_index = faiss.IndexFlatIP(dimension)  # Inner product for similarity
                
                # Normalize embeddings for cosine similarity
                embeddings_float32 = embeddings_array.astype(np.float32)
                faiss.normalize_L2(embeddings_float32)
                # Add embeddings to index - FAISS add method takes the array directly
                # Ignore type checking errors for FAISS methods
                candidate_index.add(embeddings_float32)  # type: ignore

                candidate_manifest = RAGIndexManifest(
                    schema_version=CURRENT_SCHEMA_VERSION,
                    source_path=str(self.source_path),
                    source_sha256=file_sha256(self.source_path),
                    index_sha256="",
                    embedding_model=self.embedding_model_name,
                    vector_dimension=dimension,
                    vector_count=len(row_mapping),
                    row_mapping=row_mapping,
                    created_at=datetime.now(timezone.utc).isoformat(),
                )
                persisted_manifest = atomic_save_index_pair(
                    candidate_index,
                    Path(f"{vector_store_path}.index"),
                    candidate_manifest,
                    faiss_module=faiss,
                )
                self.vector_index = candidate_index
                self.manifest = persisted_manifest
                self._index_sha256 = persisted_manifest.index_sha256
                self.index_status = "created"
                
                logger.info(f"Created and saved vector index with {len(embeddings)} embeddings")
            else:
                logger.error("No valid embeddings created")
                self.vector_index = None
                self.manifest = None
                self.index_status = "unavailable: no valid embeddings"
                
        except Exception as e:
            logger.error(f"Error creating vector index: {e}")
            self.vector_index = None
            self.manifest = None
            self.index_status = "error: index creation failed"
    
    def _require_retrieval_ready(self):
        if (not self.is_initialized or self.vector_index is None
                or self.manifest is None or self.molecules_df is None):
            raise RAGIndexCompatibilityError("RAG vector database is not fully initialized")

    def get_embedding_sync(self, text: str) -> np.ndarray:
        """Thread-safe sync transport; never reuse the async client's event loop."""
        with httpx.Client(timeout=60.0) as client:
            response = client.post(
                self.embedding_endpoint,
                json={"model": self.embedding_model_name, "prompt": text},
            )
            response.raise_for_status()
            return np.asarray(response.json().get("embedding", []))

    def _search_embedding(self, embedding, k):
        self._require_retrieval_ready()
        return search_molecular_index(
            index=self.vector_index, manifest=self.manifest, molecules=self.molecules_df,
            source_path=self.source_path, index_sha256=self._index_sha256,
            embedding_model=self.embedding_model_name, embedding=embedding, k=k,
        )

    def search_similar_molecules_sync(self, query: str, k: int = 2):
        """Agent adapter: propagate failures instead of reporting a false no-hit."""
        self._require_retrieval_ready()
        return self._search_embedding(self.get_embedding_sync(query), k)

    async def search_similar_molecules(self, query: str, k: int = 2) -> List[Dict[str, Any]]:
        """Keep the legacy list interface, using the same validated retrieval core."""
        try:
            self._require_retrieval_ready()
            return self._search_embedding(await self.get_embedding(query), k)
        except Exception:
            logger.warning("RAG retrieval unavailable; no unverified records returned")
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

        # Main chat settings are independent from checkout env and scientific tools.
        self.runtime_llm_env_path = user_llm_config_path()
        self._llm_config_lock = asyncio.Lock()
        self.model_request_gate = ModelRequestGate()
        self._llm_env_signature = self._llm_env_file_signature()
        self.active_llm_config = self._load_active_llm_config()
        self.config.setdefault("inference", {})["stream"] = self.active_llm_config["stream"]
        self.model = self._create_model_from_llm_config(self.active_llm_config)
        self._llm_watch_task = None
        logger.info(
            "Active LLM provider: %s / %s",
            self.active_llm_config.get("provider"),
            self.model.model_name,
        )
        
        self.rag_system = RAGSystem(self.config)
        
        self.agent_state_store = None
        self.agent_tool_registry = None

        # 初始化Agent系统
        try:
            self.molecular_generator_model = _init_ollama_model()
            self.agent_system = self._create_chat_agent()
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
                config=self.config,
                refresh_model_config=self._refresh_llm_config_from_env,
            )
            logger.info("✅ ChatHandler初始化成功")
            self.chat_handler.model_request_gate = self.model_request_gate
        except Exception as e:
            logger.error(f"❌ ChatHandler初始化失败: {e}")
            self.chat_handler = None
        
        # Create FastAPI app
        self.app = FastAPI(title="Molecular Chat System")
        from .agent_session_config import setup_agent_sessions
        setup_agent_sessions(self.app)
        self._setup_routes()

    def _create_chat_agent(self):
        """Create the sole chat-facing Supervisor entry point."""
        from src.agent.supervisor import SupervisorAgent
        from src.agent.tools import get_all_tools

        tools = {
            tool.name: tool
            for tool in get_all_tools(self.molecular_generator_model, rag_system=self.rag_system)
        }
        return SupervisorAgent(
            tools=tools,
            llm=self.model,
            molecular_generator_llm=self.molecular_generator_model,
            state_store=self._get_agent_state_store(),
        )

    def _get_agent_state_store(self):
        from src.agent.persistence import SQLiteAgentStateStore

        if self.agent_state_store is None:
            configured_path = Path(
                os.environ.get("AGENT_STATE_DB", "data/agent_state.sqlite3")
            )
            state_path = (
                configured_path
                if configured_path.is_absolute()
                else project_root / configured_path
            )
            self.agent_state_store = SQLiteAgentStateStore(state_path)
        return self.agent_state_store

    def _create_supervisor_agent(self):
        from src.agent.specialists import build_default_specialists
        from src.agent.supervisor import SupervisorAgent
        from src.agent.tooling import build_tool_registry
        from src.agent.tooling.registration import audit_registration

        state_store = self._get_agent_state_store()
        if self.agent_tool_registry is None:
            tools = (
                self.agent_system.tools.values()
                if self.agent_system is not None
                else []
            )
            self.agent_tool_registry = build_tool_registry(tools)
        specialists = build_default_specialists()
        self.agent_registration_report = audit_registration(self.agent_tool_registry, specialists)
        if self.agent_registration_report["errors"]:
            raise ValueError("Agent registration invalid: " + "; ".join(self.agent_registration_report["errors"]))
        return SupervisorAgent(
            tools={},  # Registry owns these tools; do not construct a second pool.
            tool_registry=self.agent_tool_registry,
            specialists=specialists,
            state_store=state_store,
        )

    def _load_active_llm_config(self) -> Dict[str, Any]:
        return load_user_llm_config(self.runtime_llm_env_path)

    def _llm_env_file_signature(self) -> str | None:
        return user_llm_signature(self.runtime_llm_env_path)

    async def _refresh_llm_config_from_env(self) -> bool:
        """Observe updates or removal in the authoritative per-user store."""
        try:
            signature = self._llm_env_file_signature()
            if signature == self._llm_env_signature:
                return False
            async with self._llm_config_lock:
                signature = self._llm_env_file_signature()
                if signature == self._llm_env_signature:
                    return False
                config = self._load_active_llm_config()
                await self._replace_llm_config(config)
                self._llm_env_signature = signature
                return True
        except (OSError, ValueError, RuntimeError):
            raise HTTPException(status_code=503, detail="本机模型配置不可用；请检查配置文件与目录权限。") from None

    async def _persist_user_llm_config(self, payload: dict) -> dict:
        async with self._llm_config_lock:
            gate = getattr(self, 'model_request_gate', None)
            async with gate.exclusive() if gate is not None else nullcontext():
                if gate is not None and gate.closed:
                    raise HTTPException(status_code=503, detail="Model service is shutting down")
                try:
                    config, signature = save_user_llm_config(
                        self.runtime_llm_env_path, payload,
                        clear_api_key=payload.get("clear_api_key") is True,
                    )
                except (OSError, ValueError, RuntimeError):
                    raise HTTPException(status_code=503, detail="模型配置未保存；请检查输入、配置文件与目录权限。") from None
                await self._apply_and_close_llm(config)
                self._llm_env_signature = signature
                return config

    async def _watch_llm_env_config(self) -> None:
        while True:
            try:
                await asyncio.sleep(1.0)
                await self._refresh_llm_config_from_env()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "Unable to refresh LLM environment config (%s)",
                    type(exc).__name__,
                )

    def _create_model_from_llm_config(self, llm_config: Dict[str, Any]):
        config = normalize_llm_config(llm_config)
        if config.get("provider") == "ollama":
            return OllamaModel(
                base_url=config.get("base_url") or "http://localhost:11434",
                model_name=config.get("model_name") or "gmm-llama:latest",
            )

        if config.get("provider") == "modelscope":
            from src.agent.modelscope_model import ModelScopeModel

            return ModelScopeModel(
                api_key=config.get("api_key", ""),
                model_name=config.get("model_name") or "ZhipuAI/GLM-5.1",
                base_url=config.get("base_url") or "https://api-inference.modelscope.cn/v1/chat/completions",
            )

        from src.agent.openai_compatible_model import OpenAICompatibleModel

        return OpenAICompatibleModel(
            api_key=config.get("api_key", ""),
            model_name=config.get("model_name") or default_user_llm_config()["model_name"],
            base_url=config.get("base_url") or default_user_llm_config()["base_url"],
            provider_name="OpenAI-compatible",
        )

    async def _replace_llm_config(self, config):
        gate = getattr(self, 'model_request_gate', None)
        if gate is None:
            return self._apply_llm_config(config)
        async with gate.exclusive():
            if gate.closed:
                raise RuntimeError('Model service is shutting down')
            return await self._apply_and_close_llm(config)

    async def _apply_and_close_llm(self, config):
        old_model = getattr(self, 'model', None)
        result = self._apply_llm_config(config)
        if old_model is not getattr(self, 'model', None):
            await finish_on_cancel(close_owned_model(old_model))
        return result

    def _apply_llm_config(self, llm_config: Dict[str, Any]) -> Dict[str, Any]:
        config = normalize_llm_config(llm_config)
        self.model = self._create_model_from_llm_config(config)
        self.active_llm_config = config
        self.config.setdefault("inference", {})["stream"] = config.get("stream", True)
        if self.chat_handler:
            self.chat_handler.model = self.model
            self.chat_handler.config = self.config
        if self.agent_system:
            if hasattr(self.agent_system, "set_llm"):
                self.agent_system.set_llm(self.model)
            else:
                self.agent_system.llm = self.model
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
            try:
                await self._refresh_llm_config_from_env()
            except HTTPException:
                await websocket.accept()
                await websocket.send_json({"type": "error", "message": "本机模型配置不可用；请检查配置文件与目录权限。"})
                await websocket.close(code=1011)
                return
            if self.chat_handler:
                # 使用新的ChatHandler（支持Agent工具）
                await self.chat_handler.handle_websocket(websocket)
            else:
                await self._reject_websocket_without_chat_handler(websocket)

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
            await self._refresh_llm_config_from_env()
            return {
                "success": True,
                "config": public_llm_config(self.active_llm_config),
            }

        @self.app.post("/api/llm/config")
        async def save_llm_config(request: Request):
            """Persist before activation; a successful save is not a connection test."""
            payload = await request.json()
            if not isinstance(payload, dict):
                raise HTTPException(status_code=422, detail="模型配置必须为对象。")
            saved = await self._persist_user_llm_config(payload)
            return {
                "success": True,
                "message": "模型配置已保存到本机用户目录并生效；连接状态请使用测试连接确认。",
                "config": public_llm_config(saved),
                "warnings": [],
            }

        @self.app.post("/api/llm/test")
        async def test_llm_config(request: Request):
            """Test without saving or borrowing credentials from another endpoint."""
            payload = await request.json()
            if not isinstance(payload, dict):
                raise HTTPException(status_code=422, detail="模型配置必须为对象。")
            await self._refresh_llm_config_from_env()
            test_model = None
            try:
                test_config = resolve_user_llm_request(
                    payload, self.active_llm_config,
                    clear_api_key=payload.get("clear_api_key") is True,
                )
                test_model = self._create_model_from_llm_config(test_config)
                response = await finish_on_cancel(generate_for_chat(
                    test_model, "Reply with exactly: CONNECTION_OK",
                    temperature=0.1, max_tokens=64,
                ))
                failure_markers = ("失败", "未配置", "HTTP ", "API Key", "Base URL", "模型名称")
                is_success = bool(response and not any(marker in response for marker in failure_markers))
                return {
                    "success": is_success,
                    "message": "连接测试成功" if is_success else "连接测试失败；请检查服务地址、模型名称和凭据。",
                    "model": test_model.model_name,
                }
            except Exception as exc:
                logger.error("LLM connection test failed (%s)", type(exc).__name__)
                return {"success": False, "message": "连接测试失败；请检查服务地址、模型名称和凭据。"}
            finally:
                await finish_on_cancel(close_owned_model(test_model))

        @self.app.post("/api/switch_model")
        async def switch_model(request: Request):
            payload = await request.json()
            if not isinstance(payload, dict):
                raise HTTPException(status_code=422, detail="模型配置必须为对象。")
            await self._refresh_llm_config_from_env()
            model_key = str(payload.get("model") or "").strip()
            model_map = {
                "glm4": "ZhipuAI/GLM-5.1", "glm5.1": "ZhipuAI/GLM-5.1",
                "qwen3": "Qwen/Qwen3-235B-A22B-Instruct-2507", "gmm-llama": "gmm-llama:latest",
            }
            next_config = dict(self.active_llm_config)
            next_config["model_name"] = model_map.get(model_key, model_key or next_config["model_name"])
            # Resolve against the saved state under the file lock, not a stale key.
            next_config["api_key"] = ""
            saved = await self._persist_user_llm_config(next_config)
            return {
                "success": True, "message": f"已切换到 {saved.get('model_name')}",
                "config": public_llm_config(saved), "warnings": [],
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
        task_runtime = None
        try:
            from src.task_runtime import TaskRuntimeBinding

            task_runtime = TaskRuntimeBinding()
            task_runtime.install(self.app, logger)
        except Exception:
            logger.warning("Durable task runtime initialization failed")
        self.task_runtime_binding = task_runtime
        setup_api_routes(
            self.app,
            docking_service,
            task_runtime=task_runtime,
        )

        # Register molecular design routes
        try:
            from .routes.design_routes import setup_design_routes
            setup_design_routes(self.app, config=self.config, model_provider=lambda: self.model,
                                model_request_gate=self.model_request_gate)
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

            setup_task_routes(self.app, task_runtime=task_runtime)
            setup_agent_workflow_routes(
                self.app,
                supervisor_factory=self._create_supervisor_agent,
                model_request_gate=self.model_request_gate,
            )
            setup_system_routes(self.app)
            logger.info("Task runtime and system metadata routes registered")
        except Exception as e:
            logger.warning(f"Task runtime routes registration failed: {e}")

    async def _reject_websocket_without_chat_handler(self, websocket: WebSocket):
        """Fail closed when the modern ChatHandler/Agent entrypoint is unavailable.

        Falling back to the legacy websocket handler can bypass SupervisorAgent,
        WorkflowOrchestrator, ToolResult normalization, and domain validators.
        """
        logger.error(
            "ChatHandler unavailable; refusing websocket connection to avoid "
            "bypassing the SupervisorAgent workflow"
        )
        await websocket.accept()
        await websocket.send_text(json.dumps({
            "type": "error",
            "message": (
                "Chat service is unavailable because the modern Agent entrypoint "
                "was not initialized. Please check server startup logs."
            ),
        }, ensure_ascii=False))
        await websocket.close(code=1011)

    async def initialize(self):
        """Initialize the application"""
        logger.info("Initializing Molecular Chat System...")

        if self._llm_watch_task is None or self._llm_watch_task.done():
            self._llm_watch_task = asyncio.create_task(self._watch_llm_env_config())
        
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

    async def shutdown(self):
        # A cancelled server shutdown must still finish closing every resource.
        await finish_on_cancel(self._shutdown())

    async def _shutdown(self):
        """Stop application-owned background tasks."""
        if self._llm_watch_task is not None:
            self._llm_watch_task.cancel()
            try:
                await self._llm_watch_task
            except asyncio.CancelledError:
                pass
            self._llm_watch_task = None

        gate = getattr(self, 'model_request_gate', None)
        if gate is not None:
            async with gate.exclusive():
                if not gate.closed:
                    gate.closed = True
                    seen = set()
                    for model in (getattr(self, 'model', None), getattr(self, 'molecular_generator_model', None)):
                        if model is not None and id(model) not in seen:
                            seen.add(id(model))
                            await finish_on_cancel(close_owned_model(model))

        registry = getattr(self, "agent_tool_registry", None)
        self.agent_tool_registry = None
        agent_system = getattr(self, "agent_system", None)
        agent_tools = list(getattr(agent_system, "tools", {}).values())
        if agent_system is not None:
            agent_system.tools = {}

        registry_tool_ids = set()
        if registry is not None:
            registry_tool_ids = {
                id(tool)
                for adapter in registry.as_mapping().values()
                if (tool := getattr(adapter, "tool", None)) is not None
            }
            registry.close()
        for tool in agent_tools:
            if id(tool) in registry_tool_ids:
                continue
            close = getattr(tool, "close", None)
            if callable(close):
                close()
    
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

        @app.on_event("shutdown")
        async def shutdown_event():
            await app_instance.shutdown()

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
