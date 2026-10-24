"""Opt-in loopback UI fixture. Scripted model boundary; real Pi/tools/RDKit.

Run with MedChat Python -B -m tests.scientific_reference_browser_lab --state-dir
<dedicated temporary directory>. This is NOT a production launch command.
"""
import argparse
from contextlib import asynccontextmanager
import json
from pathlib import Path
import re
from types import SimpleNamespace

import httpx


ROOT = Path(__file__).resolve().parents[1]
NOTICE = 'OFFLINE REFERENCE LAB — 模型响应为测试脚本；真实 Pi、生成工具与 RDKit 校验；未调用外部模型服务。'


def create_lab(directory):
    from fastapi import FastAPI, Request, Response
    from fastapi.responses import HTMLResponse
    from fastapi.staticfiles import StaticFiles
    from fastapi.templating import Jinja2Templates
    from src.agent.contracts.decision import ToolDecision, FinishDecision
    from src.agent.decision_transport import DecisionResponse
    from src.agent.persistence import SQLiteAgentStateStore
    from src.agent.tooling.factory import build_tool_registry
    from src.agent.tools.llm_molecular_generator import LLMMolecularGenerator
    from src.system.model_clients import OllamaModel
    from src.agent.tools.property_calculator import PropertyCalculator
    from src.agent.tools.drug_likeness_assessment import DrugLikenessAssessment
    from src.web.agent_session import AgentSessionStore
    from src.web.agent_session_config import AgentEntrySessionMiddleware
    from src.web.chat_handler import ChatHandler
    from src.web.routes.scientific_reference_routes import setup_scientific_reference_routes
    from src.web.routes.websocket_routes import setup_websocket_routes
    from src.web.scientific_references import ScientificReferenceService
    from src.web.decision_runtime import WebDecisionRuntime
    from src.web.model_lifecycle import ModelRequestGate

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    calls = {name: [] for name in ('llm_molecular_generator', 'property_calculator', 'drug_likeness_assessment')}

    class Generator(LLMMolecularGenerator):
        def execute(self, value):
            calls[self.name].append(value)
            return super().execute(value)

    class Properties(PropertyCalculator):
        def execute(self, value):
            calls[self.name].append(value)
            return super().execute(value)

    class Likeness(DrugLikenessAssessment):
        def execute(self, value):
            calls[self.name].append(value)
            return super().execute(value)

    class OfflineModel:
        model_name = 'offline-browser-fixture'
        async def generate(self, *args, **kwargs):
            return NOTICE

        async def decide(self, messages, **kwargs):
            # Script only the model boundary. All actions still traverse Pi,
            # compiled authorization, real adapters and real scientific tools.
            marker = '. Immutable result requirements (not a tool sequence): '
            requirements = json.loads(messages[0]['content'].split(marker, 1)[1])
            observations = []
            for message in messages[1:]:
                try:
                    value = json.loads(message.get('content', ''))
                except (ValueError, TypeError):
                    continue
                if isinstance(value, dict) and isinstance(value.get('quality'), dict):
                    if value['quality'].get('evidence_id'):
                        observations.append(value)
            steps = requirements['steps']
            if len(observations) < len(steps):
                step = steps[len(observations)]
                decision = ToolDecision(version='1', action='tool', tool_name=step['tool_name'],
                    arguments={'step_ref': step['name']}, purpose='Execute the compiled lab action')
            else:
                decision = FinishDecision(version='1', action='finish',
                    response_kind='scientific' if steps else 'chat', text=NOTICE,
                    evidence_ids=[item['quality']['evidence_id'] for item in observations])
            return DecisionResponse(decision, None, 'lab-call-' + str(len(messages)),
                                    {'model': self.model_name, 'request_attempts': 1})

    store = SQLiteAgentStateStore(directory / 'agent.sqlite')
    service = ScientificReferenceService(store)
    generation_model = OllamaModel(base_url='https://example.invalid')
    generation_model.sync_client.close()
    generation_model.sync_client = httpx.Client(transport=httpx.MockTransport(lambda request:
        httpx.Response(200, json={'done': True, 'response': 'CCO\nCCN\nCCC\nCCCO\nCCCN'})))
    tools = [Generator(llm_model=generation_model), Properties(), Likeness()]
    registry = build_tool_registry(tools)
    model = OfflineModel()
    handler = ChatHandler(model, None, None,
                          {'inference': {'stream': False}}, scientific_references=service)

    async def refresh():
        return None

    application = SimpleNamespace(model=model, model_generation='offline-lab-v1',
        ordinary_chat_policy='medchat-web-v1', model_request_gate=ModelRequestGate(),
        _refresh_llm_config_from_env=refresh, _get_agent_tool_registry=lambda: registry,
        _get_agent_state_store=lambda: store, _get_scientific_references=lambda: service)
    runtime = WebDecisionRuntime(application, wire_mode='native')
    handler.decision_runtime = runtime

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            await runtime.shutdown()
            await generation_model.close()
            registry.close()

    app = FastAPI(lifespan=lifespan)
    app.state.calls = calls
    app.state.reference_service = service
    app.add_middleware(AgentEntrySessionMiddleware, store=AgentSessionStore(directory / 'sessions.sqlite'))
    app.mount('/static', StaticFiles(directory=ROOT / 'src/web/static'), name='static')
    templates = Jinja2Templates(directory=ROOT / 'src/web/templates')

    @app.get('/', response_class=HTMLResponse)
    async def home(request: Request):
        html = templates.get_template('index.html').render(request=request)
        banner = '<aside role="note" style="position:fixed;top:0;left:0;z-index:99999;background:#ffed99;color:#111;padding:4px">' + NOTICE + '</aside>'
        return re.sub(r'(<body\b[^>]*>)', lambda m: m[1] + banner, html, count=1)

    @app.get('/api/agent/workflows/lab/stats')
    async def stats():
        return {'offline_fixture': True, 'calls': calls}

    @app.get('/api/utils/smiles_to_image')
    async def depiction(smiles: str):
        from rdkit import Chem
        from rdkit.Chem.Draw import rdMolDraw2D
        mol = Chem.MolFromSmiles(smiles) if len(smiles) < 2048 else None
        if mol is None:
            return Response(status_code=422)
        drawer = rdMolDraw2D.MolDraw2DSVG(320, 200)
        drawer.DrawMolecule(mol)
        drawer.FinishDrawing()
        return Response(drawer.GetDrawingText(), media_type='image/svg+xml')

    setup_scientific_reference_routes(app, service)
    setup_websocket_routes(app, handler)
    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--port', type=int, default=6017)
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error('port must be 1024..65535')
    # Clear provider/configuration environment BEFORE importing application code.
    import os
    import logging
    keep = {k: os.environ[k] for k in ('SYSTEMROOT', 'WINDIR', 'PATH', 'TEMP', 'TMP', 'COMSPEC') if k in os.environ}
    os.environ.clear()
    os.environ.update(keep)
    os.environ['MEDCHAT_USER_CONFIG_DIR'] = str(args.state_dir.resolve() / 'unused-user-config')
    logging.disable(logging.CRITICAL)
    import uvicorn
    print('OFFLINE REFERENCE LAB http://127.0.0.1:' + str(args.port), flush=True)
    uvicorn.run(create_lab(args.state_dir), host='127.0.0.1', port=args.port,
                access_log=False, log_level='critical', proxy_headers=False,
                ws_max_size=16384, timeout_graceful_shutdown=5)


if __name__ == '__main__':
    main()
