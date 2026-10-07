from __future__ import annotations

import base64
import struct
import zlib

import pytest
from fastapi import HTTPException

from src.web.routes.api_routes import _validate_report_base64_payload
from src.web.routes.report_generator import generate_report




def _png_chunk(kind: bytes, data: bytes) -> bytes:
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


_PNG = (
    b"\x89PNG\r\n\x1a\n"
    + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    + _png_chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff"))
    + _png_chunk(b"IEND", b"")
)
_INVALID_PNG = b"\x89PNG\r\n\x1a\n" + b"synthetic-png-payload"


def test_report_image_validation_accepts_png_payloads():
    viewer = base64.b64encode(_PNG).decode("ascii")
    ligand = base64.b64encode(_PNG).decode("ascii")

    result = _validate_report_base64_payload(viewer, [ligand])

    assert result == (viewer, [ligand])


@pytest.mark.parametrize("value", ["not-base64", base64.b64encode(b"<svg>").decode("ascii")])
def test_report_image_validation_rejects_invalid_or_non_png_payloads(value):
    with pytest.raises(HTTPException) as error:
        _validate_report_base64_payload(value, [])

    assert error.value.status_code == 422


def test_report_image_validation_rejects_non_string_data_url():
    with pytest.raises(HTTPException) as error:
        _validate_report_base64_payload("data:image/png;base64," + base64.b64encode(_PNG).decode("ascii"), [])

    assert error.value.status_code == 422


def test_report_image_validation_rejects_png_header_with_invalid_chunks():
    value = base64.b64encode(_INVALID_PNG).decode("ascii")

    with pytest.raises(HTTPException) as error:
        _validate_report_base64_payload(value, [])

    assert error.value.status_code == 422


def test_html_report_escapes_configuration_text():
    response = generate_report(
        "job-1",
        [],
        ["<script>alert('x')</script>"],
        "html",
        None,
        [],
        ".",
    )

    body = response.body.decode("utf-8")
    assert "<script>alert('x')</script>" not in body
    assert "&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;" in body
