"""Launch an explicit loopback acceptance app; never loads .env or production app."""
import argparse
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.agent.openai_compatible_model import OpenAICompatibleModel
from src.web.decision_lab import create_decision_lab
from scripts.run_decision_chat_acceptance import validate_provider_endpoint


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=6012)
    parser.add_argument('--mode', choices=('native', 'json'), default='native')
    args = parser.parse_args(argv)
    key = os.environ.get('OPENAI_COMPATIBLE_API_KEY', '').strip()
    base = os.environ.get('OPENAI_COMPATIBLE_BASE_URL', '').strip()
    name = os.environ.get('OPENAI_COMPATIBLE_MODEL', '').strip()
    if not all((key, base, name)):
        print(json.dumps({'status': 'skipped', 'reason': 'missing_runtime_configuration'}))
        return 2
    try:
        validate_provider_endpoint(base)
        if not 1024 <= args.port <= 65535:
            raise ValueError('Unsafe lab configuration')
        import httpx
        import uvicorn
        with tempfile.TemporaryDirectory(prefix='medchat-browser-lab-') as directory:
            client = httpx.AsyncClient(timeout=90, follow_redirects=False, trust_env=False)
            try:
                model = OpenAICompatibleModel(key, name, base, client=client)
                app = create_decision_lab(model, Path(directory) / 'state.sqlite', port=args.port, mode=args.mode)
                lab_lifespan = app.router.lifespan_context

                @asynccontextmanager
                async def lifespan(app):
                    # Close HTTP connections on the server's own loop, after lab tasks drain.
                    async with client:
                        async with lab_lifespan(app):
                            yield

                app.router.lifespan_context = lifespan
                print(f'Isolated acceptance only: http://127.0.0.1:{args.port}/decision-lab/', flush=True)
                uvicorn.run(app, host='127.0.0.1', port=args.port, proxy_headers=False,
                            access_log=False, log_level='warning', ws_max_size=16384,
                            timeout_graceful_shutdown=5)
            finally:
                # Startup may fail before lifespan ever opens the client.
                if not client.is_closed:
                    asyncio.run(client.aclose())
        return 0
    except Exception:
        # No provider configuration, exception body or credential in console output.
        print(json.dumps({'status': 'failed', 'reason': 'isolated_browser_lab_unavailable'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
