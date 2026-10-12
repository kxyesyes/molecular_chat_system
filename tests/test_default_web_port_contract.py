from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_project_web_defaults_and_reverse_proxy_use_port_6000():
    main_source = (ROOT / "main.py").read_text(encoding="utf-8")
    app_source = (ROOT / "src/web/app.py").read_text(encoding="utf-8")
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    nginx = (ROOT / "deployment/nginx-medchat.conf").read_text(encoding="utf-8")

    assert "DEFAULT_PORT = 6000" in main_source
    assert 'DEFAULT_WEB_PORT = 6000' in app_source
    assert "MEDCHAT_PORT=6000" in env_example
    assert nginx.count("proxy_pass http://127.0.0.1:6000") == 2
