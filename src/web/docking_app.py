"""Trusted opt-in assembly. No model, scientific command or service is started here."""
import asyncio
import os
from pathlib import Path
from uuid import uuid4


class _DockingAssembly:
    def __init__(self, *, task_runtime, runtime, coordinator, controller, toolchain):
        from src.task_runtime.runtime import TaskRuntimeBinding
        self.task_runtime, self.runtime = task_runtime, runtime
        self.coordinator, self.controller, self.toolchain = coordinator, controller, toolchain
        self.binding = TaskRuntimeBinding(factory=lambda: task_runtime, shutdown=self._shutdown)
        self.transport_closed = False

    async def _shutdown(self, runtime):
        from src.system.model_lifecycle import finish_on_cancel
        try:
            if not self.transport_closed:
                # Stop admission first, retaining every already-owned operation.
                self.controller.enabled = False
                self.coordinator._enabled = False
                self.runtime.closing = True
                for sender in tuple(self.runtime.sockets):
                    await finish_on_cancel(self.controller.disconnect(sender))
                await finish_on_cancel(self.runtime.shutdown())
                for root in tuple(self.coordinator._roots.values()):
                    await finish_on_cancel(self.coordinator._close_owned_root(root))
                while self.coordinator._http_control_tasks:
                    await finish_on_cancel(asyncio.gather(
                        *tuple(self.coordinator._http_control_tasks.values()),
                        return_exceptions=True))
                # A completed wrapper or observation timeout is not proof of
                # physical cleanup, nor of a successful durable revocation.
                for root in self.coordinator._roots.values():
                    # Share the admission invariant: a no-worker terminal is
                    # settled only with confirmed cleanup and no receipt/candidate.
                    if not self.controller._terminal_settled(root):
                        raise RuntimeError('docking_shutdown_unresolved')
                self.transport_closed = True
            await finish_on_cancel(runtime.close())
        except asyncio.CancelledError:
            raise
        except Exception:
            raise RuntimeError('docking_shutdown_unresolved') from None


def assemble_docking_app(application, docking_service, *, logger=None):
    """Build and publish C atomically, optionally installing lifecycle hooks.

    Constructors do not start workers or retain open database connections. All
    fallible validation precedes activation; failed routing is rolled back.
    The normal decision profile and its handler dependency are never changed.
    """
    if os.environ.get('MEDCHAT_CHAT_DOCKING_ENABLED') != '1':
        return None

    from src.docking.chat_toolchain import ChatDockingToolchain
    from src.docking.molecular_docking_adapter import MOLECULAR_DOCKING_ADAPTER_VERSION
    from src.task_runtime.config import TaskRuntimeConfig
    from src.task_runtime.docking_consent import DockingConsentError, DockingConsentPolicy
    from src.task_runtime.docking_private_output import private_output_root, require_local_output_construction
    from src.task_runtime.runtime import TaskRuntime
    from src.system.model_clients import OpenAICompatibleModel
    from .decision_runtime import WebDecisionRuntime
    from .docking_coordinator import DockingRequestCoordinator
    from .docking_chat_controller import DockingChatController
    from .routes.docking_consent_routes import setup_docking_consent_routes

    routes = list(application.app.router.routes)
    middleware = list(application.app.user_middleware)
    startup, shutdown = list(application.app.router.on_startup), list(application.app.router.on_shutdown)
    try:
        if getattr(application, 'docking_assembly', None) is not None:
            raise ValueError
        config = TaskRuntimeConfig.from_env()
        if config.backend != 'local' or config.warnings or application.chat_handler is None:
            raise ValueError
        require_local_output_construction()
        toolchain = ChatDockingToolchain(docking_service)
        timeout = toolchain.runtime_config()['docking']['vina_timeout_seconds']
        if not 0 < timeout <= 300 or int(timeout) != timeout:
            raise ValueError
        policy = DockingConsentPolicy(toolchain.digest, MOLECULAR_DOCKING_ADAPTER_VERSION,
            'local', uuid4().hex, uuid4().hex, int(timeout), 420)
        output_root = private_output_root(os.environ.get('MEDCHAT_CHAT_DOCKING_OUTPUT_ROOT',
            str(Path(config.staging_root).resolve().parent / 'chat_docking_outputs')),
            staging_root=config.staging_root)
        state = application._get_agent_state_store()
        runtime = application.decision_runtime
        if runtime is None:
            runtime = WebDecisionRuntime(application, wire_mode=application.decision_wire_mode)
        if type(runtime) is not WebDecisionRuntime or runtime.store is not state:
            raise ValueError
        task_runtime = TaskRuntime(config=config, docking_consent_policy=policy,
            docking_consent_output_root=output_root, docking_chat_toolchain=toolchain)
        private = task_runtime._docking_private_output

        def capture_current():
            if (task_runtime._docking_consent_policy is not policy
                    or task_runtime._docking_private_output is not private
                    or private.chat_toolchain is not toolchain
                    or task_runtime.config is not config
                    or task_runtime._closing or task_runtime._closed
                    or runtime.closing or type(application.model) is not OpenAICompatibleModel):
                raise DockingConsentError('consent_policy_unavailable')
            private.require(task_runtime._docking_consent_execution, policy)
            # Capture the actual current model, never a surrogate adapter. The
            # coordinator/proposal checks enforce its captured generations.
            return dict(model=application.model, policy=policy,
                model_generation=application.model_generation,
                capability_generation=application.capability_generation)

        coordinator = DockingRequestCoordinator(task_store=task_runtime.store,
            state_store=state, gate=application.model_request_gate,
            capture_current=capture_current, enabled=True)
        controller = DockingChatController(runtime, coordinator, enabled=True)
        assembly = _DockingAssembly(task_runtime=task_runtime, runtime=runtime,
            coordinator=coordinator, controller=controller, toolchain=toolchain)
        setup_docking_consent_routes(application.app, coordinator=coordinator,
            task_runtime=task_runtime, enabled=True)
        if logger is not None:
            assembly.binding.install(application.app, logger)
        # Binding already owns the exact runtime passed to HTTP and generic APIs.
        assembly.binding.start()
        application.docking_runtime = runtime
        application.docking_assembly = assembly
        application.chat_handler.docking_controller = controller
        return assembly
    except Exception:
        if logger is not None:
            logger.exception("Chat docking assembly failed; details redacted from client")
        application.app.router.routes[:] = routes
        application.app.user_middleware[:] = middleware
        application.app.router.on_startup[:] = startup
        application.app.router.on_shutdown[:] = shutdown
        raise DockingConsentError('consent_policy_unavailable') from None
