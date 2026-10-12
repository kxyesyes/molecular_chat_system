from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_project_web_defaults_and_reverse_proxy_use_port_6000():
    main_source = (ROOT / "main.py").read_text(encoding="utf-8")
    app_source = (ROOT / "src/web/app.py").read_text(encoding="utf-8")
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    nginx = (ROOT / "deployment/nginx-medchat.conf").read_text(encoding="utf-8")

    assert "DEFAULT_PORT = 6000" in main_source
    assert 'DEFAULT_WEB_PORT = 6000' in app_source
    assert "MEDCHAT_PORT=6000" in env_example
    assert "proxy_pass http://127.0.0.1:6000;" in nginx
    assert "proxy_pass http://127.0.0.1:6000/ws;" in nginx


@pytest.mark.parametrize("path", [
    "main.py", "config/deployment.yaml", "config/ollama_config.yaml",
    "config/modelscope_config.yaml",
])
def test_generated_and_checked_in_config_defaults_use_port_6000(path):
    source = (ROOT / path).read_text(encoding="utf-8")
    assert "${MEDCHAT_PORT:-6000}" in source
    assert "${MEDCHAT_PORT:-6001}" not in source


def test_browser_tunnel_uses_safe_local_port_and_default_backend_port():
    guide = (ROOT / "docs/DEPLOYMENT_TUTORIAL.md").read_text(encoding="utf-8")
    assert "ssh -L 6001:127.0.0.1:6000" in guide
    assert "ERR_UNSAFE_PORT" in guide
