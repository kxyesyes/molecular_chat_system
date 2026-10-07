"""SSRF and DNS-rebinding guards for remote structure downloads."""

from __future__ import annotations

import pytest
import requests

from src.target_search import downloader


def _addrinfo(address: str):
    return [(2, 1, 6, "", (address, 443))]


class _Response:
    status_code = 200
    headers = {}

    def raise_for_status(self):
        pass


@pytest.mark.parametrize(
    "url",
    [
        "https://files.rcsb.org:444/download/1ABC.cif",
        "https://files.rcsb.org/download/1ABC.cif?token=unexpected",
        "https://files.rcsb.org/download/1ABC.cif#fragment",
    ],
)
def test_structure_url_rejects_noncanonical_authority(url):
    with pytest.raises(downloader.StructureDownloadError):
        downloader._validate_structure_download_url(url, {"files.rcsb.org"})


def test_structure_url_rejects_private_dns_answer(monkeypatch):
    monkeypatch.setattr(
        downloader.socket,
        "getaddrinfo",
        lambda *args, **kwargs: _addrinfo("10.0.0.7"),
    )

    with pytest.raises(downloader.StructureDownloadError, match="restricted network"):
        downloader._validate_structure_download_url(
            "https://files.rcsb.org/download/1ABC.cif",
            {"files.rcsb.org"},
        )


def test_structure_request_rejects_dns_address_change(monkeypatch):
    answers = iter([_addrinfo("93.184.216.34"), _addrinfo("93.184.216.35")])
    monkeypatch.setattr(
        downloader.socket,
        "getaddrinfo",
        lambda *args, **kwargs: next(answers),
    )
    monkeypatch.setattr(
        downloader.requests,
        "get",
        lambda *args, **kwargs: _Response(),
    )

    with pytest.raises(downloader.StructureDownloadError, match="DNS"):
        downloader._request_structure(
            "https://files.rcsb.org/download/1ABC.cif",
            {"files.rcsb.org"},
        )


def test_structure_request_keeps_redirects_disabled(monkeypatch):
    calls = []
    monkeypatch.setattr(
        downloader.socket,
        "getaddrinfo",
        lambda *args, **kwargs: _addrinfo("93.184.216.34"),
    )

    def fake_get(*args, **kwargs):
        calls.append((args, kwargs))
        return _Response()

    monkeypatch.setattr(downloader.requests, "get", fake_get)
    downloader._request_structure(
        "https://files.rcsb.org/download/1ABC.cif",
        {"files.rcsb.org"},
    )

    assert calls[0][1]["allow_redirects"] is False
    assert calls[0][1]["stream"] is True
