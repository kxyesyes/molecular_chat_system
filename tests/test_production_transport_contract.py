from pathlib import Path
import os
import stat

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_main_fastapi_factory_disables_public_schema_routes():
    source = (ROOT / "src/web/app.py").read_text(encoding="utf-8")
    assert "docs_url=None" in source
    assert "redoc_url=None" in source
    assert "openapi_url=None" in source


def test_nginx_production_template_redirects_http_and_supports_wss():
    config = (ROOT / "deployment/nginx-medchat.conf").read_text(encoding="utf-8")
    assert "listen 80;" in config
    assert "return 301 https://$host$request_uri;" in config
    assert "listen 443 ssl;" in config
    assert "ssl_certificate /etc/letsencrypt/live/medchat.example.com/fullchain.pem;" in config
    assert "ssl_certificate_key /etc/letsencrypt/live/medchat.example.com/privkey.pem;" in config
    assert "proxy_set_header Upgrade $http_upgrade;" in config
    assert 'proxy_set_header Connection "upgrade";' in config
    assert "proxy_set_header X-Forwarded-Proto https;" in config


def test_main_explicitly_trusts_only_the_local_reverse_proxy_for_forwarded_scheme():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "proxy_headers=True" in source
    assert 'forwarded_allow_ips=os.environ.get("MEDCHAT_FORWARDED_ALLOW_IPS", "127.0.0.1")' in source


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits are not enforceable on Windows")
def test_target_storage_is_private_on_posix(tmp_path):
    from src.target_search.database import get_cache_dir, get_db_path, init_db

    init_db(tmp_path)
    db_mode = stat.S_IMODE(get_db_path(tmp_path).stat().st_mode)
    cache_mode = stat.S_IMODE(get_cache_dir(tmp_path).stat().st_mode)
    assert db_mode & 0o077 == 0
    assert cache_mode & 0o077 == 0
