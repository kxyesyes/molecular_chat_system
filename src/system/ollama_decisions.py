"""One injected protocol transaction for Ollama, independent of generation I/O.

No Agent imports, model loop, retry, tool execution or borrowed-client mutation.
The application injects the existing protocol module and its docking guard.
"""
import asyncio

from src.system.llm_transport import create_ollama_async_client, validate_ollama_endpoint
from src.system.model_lifecycle import finish_on_cancel
from src.system.openai_compatible_model import OpenAICompatibleModel


async def request_ollama_protocol(model, method, messages, **options):
    protocol_method = {
        'decide': 'request_decision',
        'propose_ordinary_intent': 'request_ordinary_intent',
        'propose_docking_preparation': 'request_docking_preparation',
    }[method]
    transport = getattr(model, '_decision_transport', None)
    if transport is None or not callable(getattr(transport, protocol_method, None)):
        raise RuntimeError('Agent decision transport is not configured or incomplete')
    # Capture the chosen local model before any await; never borrow generation
    # clients, their cookies/headers, or ambient cloud credentials.
    base_url, model_name = model.base_url, model.model_name
    validate_ollama_endpoint(base_url)
    hooks = None
    if method == 'propose_docking_preparation':
        guard = getattr(transport, 'docking_preparation_response_guard', None)
        if not callable(guard):
            raise RuntimeError('Docking decision transport guard is not configured')
        hooks = {'response': [guard]}
    # The protocol validates and applies its per-request timeout. A fixed
    # default here cannot preempt protocol validation of malformed options.
    client = create_ollama_async_client(60.0, event_hooks=hooks)
    cancelled = False
    try:
        facade = OpenAICompatibleModel('medchat-local-protocol', model_name, base_url,
            provider='ollama', enforce_url_policy=True, client=client,
            decision_transport=transport)
        return await getattr(facade, method)(messages, **options)
    except asyncio.CancelledError:
        cancelled = True
        raise
    finally:
        try:
            # Close exactly once and retain ownership through repeated cancels.
            # Docking's injected response guard settles its stream first.
            await finish_on_cancel(client.aclose())
        except Exception:
            if not cancelled:
                raise
