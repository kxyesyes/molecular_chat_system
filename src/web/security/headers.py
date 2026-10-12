"""HTTP response headers for the public MedChat web boundary."""


def apply_security_headers(response, *, secure: bool, request_path: str = "") -> None:
    """Apply headers that are safe for both the UI and JSON endpoints.

    The application still contains legacy inline UI scripts, so this policy
    deliberately limits framing, object embedding, and form/base URL
    behaviour without claiming a CSP that would silently break the page.
    HSTS is emitted only after the request was actually served over HTTPS;
    local HTTP development must not be pinned to a non-TLS port.
    Only the vendored Ketcher entrypoint is embedded by our own UI. Keep all
    other responses unframeable and never allow cross-origin embedding.
    """
    same_origin_frame = request_path == "/static/ketcher/index.html"
    frame_ancestors = "'self'" if same_origin_frame else "'none'"
    headers = response.headers
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("X-Frame-Options", "SAMEORIGIN" if same_origin_frame else "DENY")
    headers.setdefault("Referrer-Policy", "no-referrer")
    headers.setdefault("Permissions-Policy", "camera=(), geolocation=(), microphone=()")
    headers.setdefault(
        "Content-Security-Policy",
        f"frame-ancestors {frame_ancestors}; object-src 'none'; base-uri 'self'; form-action 'self'",
    )
    if secure:
        headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains",
        )
