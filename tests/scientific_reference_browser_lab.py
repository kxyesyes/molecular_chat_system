"""Opt-in loopback UI fixture. Synthetic generation; real RDKit, no model API.

Run with MedChat Python -B -m tests.scientific_reference_browser_lab --state-dir
<dedicated temporary directory>. This is NOT a production launch command.
"""
import argparse
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
NOTICE = 'OFFLINE REFERENCE LAB — 合成候选，仅验证引用链路；性质来自 RDKit；未调用生成模型。'


def create_lab(directory):
    from fastapi import FastAPI, Request, Response
    from fastapi.responses import HTMLResponse
    from fastapi.staticfiles import StaticFiles
    from fastapi.templating import Jinja2Templates
    from src.agent.contracts import CandidateRecord, CandidateSet, ToolResult
    from src.agent.persistence import SQLiteAgentStateStore
    from src.agent.supervisor import SupervisorAgent
    from src.agent.tools.property_calculator import PropertyCalculator
    from src.agent.tools.drug_likeness_assessment import DrugLikenessAssessment
    from src.web.agent_session import AgentSessionStore
    from src.web.agent_session_config import AgentEntrySessionMiddleware
    from src.web.chat_handler import ChatHandler
    from src.web.routes.scientific_reference_routes import setup_scientific_reference_routes
    from src.web.routes.websocket_routes import setup_websocket_routes
    from src.web.scientific_references import ScientificReferenceService

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    calls = {name: [] for name in ('llm_molecular_generator', 'property_calculator', 'drug_likeness_assessment')}

    class Generator:
        name = 'llm_molecular_generator'
        def execute(self, value):
            calls[self.name].append(value)
            candidates = tuple(CandidateRecord.from_smiles(i, i, s, s, {'model': 'offline-fixture'})
                               for i, s in enumerate(('CCO', 'CCN', 'CCC', 'CCCO', 'CCCN'), 1))
            return ToolResult.success_result(self.name, data=CandidateSet(5, candidates).to_dict(),
                                             warnings=[NOTICE], evidence=[{'source': 'offline-test-fixture'}])

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

    store = SQLiteAgentStateStore(directory / 'agent.sqlite')
    service = ScientificReferenceService(store)
    tools = {tool.name: tool for tool in (Generator(), Properties(), Likeness())}
    handler = ChatHandler(OfflineModel(), None, SupervisorAgent(tools=tools, state_store=store),
                          {'inference': {'stream': False}}, scientific_references=service)
    app = FastAPI()
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
    os.environ['AGENT_HARNESS_MODE'] = 'legacy'
    os.environ['MEDCHAT_USER_CONFIG_DIR'] = str(args.state_dir.resolve() / 'unused-user-config')
    logging.disable(logging.CRITICAL)
    import uvicorn
    print('OFFLINE REFERENCE LAB http://127.0.0.1:' + str(args.port), flush=True)
    uvicorn.run(create_lab(args.state_dir), host='127.0.0.1', port=args.port,
                access_log=False, log_level='critical', proxy_headers=False,
                ws_max_size=16384, timeout_graceful_shutdown=5)


if __name__ == '__main__':
    main()
