"""HTTP response headers for the public MedChat web boundary."""


def apply_security_headers(response, *, secure: bool) -> None:
    """Apply headers that are safe for both the UI and JSON endpoints.

    The application still contains legacy inline UI scripts, so this policy
    deliberately limits framing, object embedding, and form/base URL
    behaviour without claiming a CSP that would silently break the page.
    HSTS is emitted only after the request was actually served over HTTPS;
    local HTTP development must not be pinned to a non-TLS port.
    """
    headers = response.headers
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("X-Frame-Options", "DENY")
    headers.setdefault("Referrer-Policy", "no-referrer")
    headers.setdefault("Permissions-Policy", "camera=(), geolocation=(), microphone=()")
    headers.setdefault(
        "Content-Security-Policy",
        "frame-ancestors 'none'; object-src 'none'; base-uri 'self'; form-action 'self'",
    )
    if secure:
        headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains",
        )
