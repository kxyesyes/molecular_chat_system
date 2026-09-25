import asyncio
from contextlib import AsyncExitStack, nullcontext
import inspect
import json
import logging
import os
import re
import sys
from typing import Dict, Any, Optional
import yaml
from fastapi import FastAPI, WebSocket, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path
from uuid import uuid4
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
from src.rag.service import RAGSystem

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



class MolecularChatApp:
    """Main application class"""
    
    def __init__(self, config_path: str = "config/ollama_config.yaml", *,
                 normal_chat_mode='legacy', decision_wire_mode='native',
                 ordinary_chat_policy='a1_closed'):
        self._validate_chat_profile(normal_chat_mode, decision_wire_mode, ordinary_chat_policy)
        if ordinary_chat_policy == 'semantic_v1':
            raise ValueError('Semantic assembly requires create_async')
        self._assemble(config_path, normal_chat_mode=normal_chat_mode,
                       decision_wire_mode=decision_wire_mode, ordinary_chat_policy=ordinary_chat_policy)

    @staticmethod
    def _validate_chat_profile(normal_chat_mode, decision_wire_mode, ordinary_chat_policy):
        if (type(normal_chat_mode) is not str or normal_chat_mode not in {'legacy', 'decision_a2'}
                or type(decision_wire_mode) is not str or decision_wire_mode not in {'native', 'json'}
                or type(ordinary_chat_policy) is not str or ordinary_chat_policy not in {'a1_closed', 'semantic_v1'}):
            raise ValueError('Invalid normal chat or decision wire mode')
        if ordinary_chat_policy == 'semantic_v1' and normal_chat_mode != 'decision_a2':
            raise ValueError('Semantic assembly requires decision_a2')

    @classmethod
    async def create_async(cls, config_path: str = "config/ollama_config.yaml", *,
                           normal_chat_mode='legacy', decision_wire_mode='native',
                           ordinary_chat_policy='a1_closed'):
        """Assemble only; never start initialize/watcher/index or preload work."""
        cls._validate_chat_profile(normal_chat_mode, decision_wire_mode, ordinary_chat_policy)
        instance = cls.__new__(cls)
        stack = AsyncExitStack()
        instance._assembly_stack = stack
        instance._assembly_owner_ids = set()
        instance._ordinary_owned_tools = ()
        try:
            instance._assemble(config_path, normal_chat_mode=normal_chat_mode,
                               decision_wire_mode=decision_wire_mode, ordinary_chat_policy=ordinary_chat_policy)
        except BaseException:
            try:
                await finish_on_cancel(stack.aclose())
            except asyncio.CancelledError:
                # finish_on_cancel has already drained the stack. Retain the
                # original assembly failure, even if cancellation arrived later.
                pass
            raise
        finally:
            instance._assembly_stack = None
            instance._assembly_owner_ids.clear()
        stack.pop_all()  # Transfer owners to the fully assembled app's shutdown.
        instance._assembly_owners_transferred = True
        return instance

    @staticmethod
    async def _close_assembly_owner(owner):
        try:
            close = getattr(owner, 'close', None)
            if callable(close):
                result = close()
                if inspect.isawaitable(result):
                    await result
        except BaseException:
            logger.warning('Assembly owner cleanup failed; exception details omitted')

    def _register_assembly_owner(self, owner):
        stack = getattr(self, '_assembly_stack', None)
        if stack is not None and id(owner) not in self._assembly_owner_ids:
            self._assembly_owner_ids.add(id(owner))
            stack.push_async_callback(self._close_assembly_owner, owner)

    @staticmethod
    def _validate_ordinary_adapter(config, model=None):
        # Only reviewed public in-memory fields; no credentials/URL/health probes.
        if config.get('provider') not in {'openai_compatible', 'custom'}:
            raise ValueError('Semantic assembly requires an approved provider')
        if model is not None:
            from src.agent.openai_compatible_model import OpenAICompatibleModel
            if (type(model) is not OpenAICompatibleModel or any(
                    not callable(getattr(model, name, None)) for name in
                    ('propose_ordinary_intent', 'decide', 'generate', 'stream_generate', 'close'))):
                raise ValueError('Semantic assembly requires the approved intent adapter')

    def _assemble(self, config_path, *, normal_chat_mode, decision_wire_mode, ordinary_chat_policy):
        self.ordinary_chat_policy = ordinary_chat_policy
        self.capability_generation = uuid4().hex
        self.ordinary_capability_base = None
        self.normal_chat_mode = normal_chat_mode
        self.decision_wire_mode = decision_wire_mode
        self.decision_runtime = None
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
        if ordinary_chat_policy == 'semantic_v1':
            self._validate_ordinary_adapter(self.active_llm_config)
        self.config.setdefault("inference", {})["stream"] = self.active_llm_config["stream"]
        self.model = self._create_model_from_llm_config(self.active_llm_config)
        self._register_assembly_owner(self.model)
        if ordinary_chat_policy == 'semantic_v1':
            self._validate_ordinary_adapter(self.active_llm_config, self.model)
        self.model_generation = uuid4().hex
        self._llm_watch_task = None
        logger.info(
            "Active LLM provider: %s / %s",
            self.active_llm_config.get("provider"),
            self.model.model_name,
        )
        
        self.rag_system = RAGSystem(self.config)
        self._register_assembly_owner(self.rag_system)
        
        self.agent_state_store = None
        self.agent_tool_registry = None

        # 初始化Agent系统
        try:
            self.molecular_generator_model = _init_ollama_model()
            self._register_assembly_owner(self.molecular_generator_model)
            self.agent_system = self._create_chat_agent()
            logger.info("✅ Agent系统初始化成功")
        except Exception as e:
            if ordinary_chat_policy == 'semantic_v1':
                raise
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
                scientific_references=self._get_scientific_references(),
            )
            logger.info("✅ ChatHandler初始化成功")
            self.chat_handler.model_request_gate = self.model_request_gate
        except Exception as e:
            if ordinary_chat_policy == 'semantic_v1':
                raise
            logger.error(f"❌ ChatHandler初始化失败: {e}")
            self.chat_handler = None
        
        if normal_chat_mode == 'decision_a2':
            from .decision_runtime import WebDecisionRuntime
            if self.chat_handler is None:
                raise ValueError('Decision mode requires a chat handler')
            self.decision_runtime = WebDecisionRuntime(self, wire_mode=decision_wire_mode)
            self.chat_handler.decision_runtime = self.decision_runtime

        if ordinary_chat_policy == 'semantic_v1':
            self.ordinary_capability_base = self._build_ordinary_capability_base(
                self.active_llm_config, self.model, self.model_generation, self.capability_generation)
            # Web requests now enter the supervised semantic runtime. The legacy
            # direct API cannot supply that admission and remains closed; the
            # decision bridge itself requires server-owned carry and exchange.
            self.chat_handler._process_message = self._reject_unassembled_ordinary_dispatch

        # Create FastAPI app
        self.app = FastAPI(title="Molecular Chat System")
        from .agent_session_config import setup_agent_sessions
        setup_agent_sessions(self.app)
        self._setup_routes()

    async def _reject_unassembled_ordinary_websocket(self, websocket, *, handler=None):
        """No ready/accepted/answer frames: this profile is assembly-only."""
        await websocket.accept()
        try:
            await websocket.send_json({
                'type': 'error', 'code': 'ordinary_semantic_not_assembled',
                'message': 'Semantic chat admission and display gates are not assembled.',
            })
        finally:
            await websocket.close(code=1013)

    async def _reject_unassembled_ordinary_dispatch(self, *args, **kwargs):
        """Also reject server-side direct handler calls before any provider use."""
        raise HTTPException(status_code=503, detail='ordinary_semantic_not_assembled')

    def _build_ordinary_capability_base(self, config, model, model_generation, capability_generation):
        """Reviewed assembly facts only, never runtime readiness or authority."""
        from .ordinary_capabilities import ORIGINAL_FOUR, build_capability_snapshot
        self._validate_ordinary_adapter(config, model)
        return build_capability_snapshot(
            registered_names=frozenset(self.agent_tool_registry.as_mapping()),
            provider_descriptor={'provider': config.get('provider'), 'model': model.model_name,
                                 'mode': self.decision_wire_mode},
            semantic_profile=True, intent_capable=True, original_four_profile=True,
            scientific_tools=True, permitted_names=ORIGINAL_FOUR,
            model_generation=model_generation, capability_generation=capability_generation)

    @staticmethod
    def project_ordinary_capabilities(base, *, scientific_tools, permitted_names):
        """Project a base captured ONCE under the caller's request lease.

        This method never rereads the app/model/registry. Task7B must retain this
        same snapshot (and its digest) through intent, answer and binding.
        """
        from src.agent.contracts.ordinary_admission import CAPABILITY_ERROR, CapabilitySnapshot
        from .ordinary_capabilities import ORIGINAL_FOUR, build_capability_snapshot
        if type(base) is not CapabilitySnapshot:
            raise ValueError(CAPABILITY_ERROR)
        base = CapabilitySnapshot.model_validate(base, strict=True)
        facts = dict(registered_names=frozenset(f.id for f in base.features if f.wired),
                     provider_descriptor=base.provider_descriptor.model_dump(),
                     semantic_profile=True, intent_capable=True, original_four_profile=True,
                     model_generation=base.model_generation, capability_generation=base.capability_generation)
        # Reject partial schemas, readiness claims or a request projection used
        # as a base. Reuse the reviewed contract/builder, not a second catalog.
        if base != build_capability_snapshot(**facts, scientific_tools=True, permitted_names=ORIGINAL_FOUR):
            raise ValueError(CAPABILITY_ERROR)
        return build_capability_snapshot(**facts, scientific_tools=scientific_tools, permitted_names=permitted_names)

    async def _replace_ordinary_capability_base(self, base):
        """The only independent base publisher; a pending writer bars readers."""
        from src.agent.contracts.ordinary_admission import CAPABILITY_ERROR, CapabilitySnapshot
        async with self.model_request_gate.exclusive():
            if self.model_request_gate.closed or self.ordinary_chat_policy != 'semantic_v1':
                raise ValueError(CAPABILITY_ERROR)
            if type(base) is not CapabilitySnapshot:
                raise ValueError(CAPABILITY_ERROR)
            base = CapabilitySnapshot.model_validate(base, strict=True)
            expected = self._build_ordinary_capability_base(
                self.active_llm_config, self.model, self.model_generation, base.capability_generation)
            if base != expected:
                raise ValueError(CAPABILITY_ERROR)
            generation = uuid4().hex
            staged = CapabilitySnapshot.model_validate(dict(base.model_dump(), capability_generation=generation), strict=True)
            self.ordinary_capability_base = staged
            self.capability_generation = generation
            return staged

    def _create_chat_agent(self):
        """Create the sole chat-facing Supervisor entry point."""
        from src.agent.supervisor import SupervisorAgent
        from src.agent.tools import get_all_tools

        acquired_tools = get_all_tools(self.molecular_generator_model, rag_system=self.rag_system)
        # Retain the returned pool before Supervisor or registry construction.
        # Tools borrow the injected generator/RAG; those owners close separately.
        unique_tools = tuple({id(tool): tool for tool in acquired_tools}.values())
        if getattr(self, '_assembly_stack', None) is not None:
            self._ordinary_owned_tools = unique_tools
        for tool in unique_tools:
            self._register_assembly_owner(tool)
        tools = {tool.name: tool for tool in unique_tools}
        return SupervisorAgent(
            tools=tools,
            llm=self.model,
            molecular_generator_llm=self.molecular_generator_model,
            state_store=self._get_agent_state_store(),
        )

    def _get_scientific_references(self):
        from .scientific_references import ScientificReferenceService
        if getattr(self, "scientific_references", None) is None:
            self.scientific_references = ScientificReferenceService(self._get_agent_state_store())
        return self.scientific_references

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

    def _get_agent_tool_registry(self):
        from src.agent.specialists import build_default_specialists
        from src.agent.tooling import build_tool_registry
        from src.agent.tooling.registration import audit_registration

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
        return self.agent_tool_registry

    def _create_supervisor_agent(self):
        from src.agent.specialists import build_default_specialists
        from src.agent.supervisor import SupervisorAgent
        registry = self._get_agent_tool_registry()
        return SupervisorAgent(
            tools={},  # Registry owns these tools; do not construct a second pool.
            tool_registry=registry,
            specialists=build_default_specialists(),
            state_store=self._get_agent_state_store(),
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
        config = normalize_llm_config(config)
        model = self._create_model_from_llm_config(config)
        try:
            result = self._publish_llm_config(config, model)
        except BaseException:
            if model is not old_model:
                await finish_on_cancel(close_owned_model(model))
            raise
        if old_model is not getattr(self, 'model', None):
            await finish_on_cancel(close_owned_model(old_model))
        return result

    def _apply_llm_config(self, llm_config: Dict[str, Any]) -> Dict[str, Any]:
        config = normalize_llm_config(llm_config)
        return self._publish_llm_config(config, self._create_model_from_llm_config(config))

    def _publish_llm_config(self, config, model):
        # The async caller already owns the writer. Validate all semantic values
        # and epochs BEFORE binding any consumers; never acquire a nested writer.
        generation = uuid4().hex
        capability_generation = getattr(self, 'capability_generation', None)
        base = getattr(self, 'ordinary_capability_base', None)
        if getattr(self, 'ordinary_chat_policy', 'a1_closed') == 'semantic_v1':
            capability_generation = uuid4().hex
            base = self._build_ordinary_capability_base(config, model, generation, capability_generation)
        # The writer holds admission closed. Stage the real consumer bindings
        # first; their setter may fail after updating only part of the graph.
        # Roll back known main-model consumers directly, without invoking the
        # failed setter again. The separately owned generator is never touched.
        agent = self.agent_system
        bindings = []
        inference = self.config.get('inference', {})
        if type(inference) is not dict:
            raise ValueError('Invalid inference configuration')
        stream = config.get('stream', True)
        handler = self.chat_handler
        previous_handler = (handler.model, handler.config) if handler else None
        if agent:
            consumers = [agent] + [tool for name, tool in agent.tools.items()
                                   if name != 'llm_molecular_generator']
            bindings = [(consumer, consumer.llm) for consumer in consumers
                        if hasattr(consumer, 'llm')]
        try:
            if agent:
                if hasattr(agent, 'set_llm'):
                    agent.set_llm(model)
                else:
                    agent.llm = model
            if handler:
                handler.model = model
                handler.config = self.config
        except BaseException:
            for consumer, previous in bindings:
                consumer.llm = previous
            if handler:
                handler.model, handler.config = previous_handler
            raise
        self.model = model
        self.model_generation = generation
        self.ordinary_capability_base = base
        self.capability_generation = capability_generation
        self.active_llm_config = config
        inference['stream'] = stream
        self.config['inference'] = inference
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
            if self.decision_runtime is not None:
                await self.chat_handler.handle_websocket(websocket)
                return
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
        from .routes.scientific_reference_routes import setup_scientific_reference_routes
        setup_scientific_reference_routes(self.app, self._get_scientific_references())

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
        runtime = getattr(self, 'decision_runtime', None)
        if runtime is not None:
            await runtime.shutdown()
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

        if getattr(self, '_assembly_owners_transferred', False):
            self._assembly_owners_transferred = False
            # Do not also close registry adapters: they wrap these same owners,
            # and registry.close stops at its first error.
            tools, self._ordinary_owned_tools = self._ordinary_owned_tools, ()
            seen = {id(getattr(self, name, None)) for name in ('model', 'molecular_generator_model')}
            for owner in (*tools, self.rag_system):
                if id(owner) not in seen:
                    seen.add(id(owner))
                    await self._close_assembly_owner(owner)
            return

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
