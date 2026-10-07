from types import SimpleNamespace

from src.web.security.headers import apply_security_headers


def test_security_headers_are_applied_without_enabling_hsts_on_local_http():
    response = SimpleNamespace(headers={})

    apply_security_headers(response, secure=False)

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Permissions-Policy"] == "camera=(), geolocation=(), microphone=()"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert "object-src 'none'" in response.headers["Content-Security-Policy"]
    assert "Strict-Transport-Security" not in response.headers


def test_security_headers_enable_hsts_only_for_https():
    response = SimpleNamespace(headers={})

    apply_security_headers(response, secure=True)

    assert response.headers["Strict-Transport-Security"] == "max-age=31536000; includeSubDomains"
