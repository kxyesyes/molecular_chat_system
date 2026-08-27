from __future__ import annotations

import pytest

from src.sandbox_broker.safety import (
    is_safe_container_image_uri,
    is_safe_metadata_text,
)


UNICODE_PATH_CONFUSABLES = (
    "\u29f5",  # REVERSE SOLIDUS OPERATOR
    "\u29f9",  # BIG REVERSE SOLIDUS
    "\u2044",  # FRACTION SLASH
    "\u2215",  # DIVISION SLASH
    "\u29f8",  # BIG SOLIDUS
    "\uff0f",  # FULLWIDTH SOLIDUS
    "\ufe68",  # SMALL REVERSE SOLIDUS
    "\uff3c",  # FULLWIDTH REVERSE SOLIDUS
    "\uff1a",  # FULLWIDTH COLON
    "\ufe13",  # PRESENTATION FORM FOR VERTICAL COLON
)


@pytest.mark.parametrize(
    "value",
    [
        "prefix/home/runner/result.json",
        "prefix/home]",
        r"prefix\home\runner\result.json",
        r"prefixC:\Users\runner\result.json",
        r"prefix\\server\share\result.json",
        "PREFIX/HOME/runner/result.json",
        "prefixZ:/var/lib/result.json",
        "prefix\x00/home/runner/result.json",
        "\x1b[31mprefix/home/runner/result.json\x1b[0m",
        "prefix/data/model.bin",
        "prefix/srv/worker/result.json",
        "prefix/proc/self/status",
        "prefix/dev/null",
        "prefix/random/text",
        "prefix/\u200bdata/model.bin",
        "prefix\u2060C:\\Users\\runner\\result.json",
        "prefix/standalone.ext",
        "/数据/结果",
        "prefix/é/ß",
        "prefix/ｅ́/ｓｓ",
        "prefix/１２/结果",
        "C:\\数据\\结果",
        r"\\服务器\共享\结果",
        "prefix／tmp／result.json",
        "prefix∕etc∕passwd",
    ],
)
def test_metadata_safety_rejects_absolute_path_fragments_anywhere(value: str) -> None:
    assert is_safe_metadata_text(value) is False


@pytest.mark.parametrize(
    "value",
    [
        "score ratio 1/2",
        "decimal ratio 0.5/1.25",
        "signed ratio -1/2",
        "plain scientific warning",
    ],
)
def test_metadata_safety_allows_numeric_fractions_and_plain_text(value: str) -> None:
    assert is_safe_metadata_text(value) is True


@pytest.mark.parametrize(
    "value",
    [None, b"text", "", "   ", "warning\ntext", "warn\u200bing", "warn\u2060ing"],
)
def test_metadata_safety_is_strict_about_type_empty_and_control_text(
    value: object,
) -> None:
    assert is_safe_metadata_text(value) is False


@pytest.mark.parametrize("confusable", UNICODE_PATH_CONFUSABLES)
def test_metadata_safety_rejects_unicode_named_path_confusables(
    confusable: str,
) -> None:
    assert is_safe_metadata_text(f"1{confusable}2") is False
    assert is_safe_metadata_text(f"prefix{confusable}数据") is False


def test_metadata_safety_does_not_grant_fraction_exemption_after_nfkc() -> None:
    assert is_safe_metadata_text("１/２") is False


def test_metadata_safety_does_not_reject_plain_unicode_scientific_text() -> None:
    assert is_safe_metadata_text("科学计算结果正常 αβ é ß") is True


@pytest.mark.parametrize(
    "value",
    [
        "medchat-docking",
        "registry.example/medchat/vina:1",
        "127.0.0.1:5000/medchat-docking",
        "localhost:5000/team/medchat_docking:release-1",
    ],
)
def test_container_image_uri_accepts_strict_registry_references(value: str) -> None:
    assert is_safe_container_image_uri(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "/etc/passwd",
        "../medchat-docking",
        "registry.example/../secret",
        "registry.example/medchat@sha256:deadbeef",
        "registry.example/medchat image",
        "registry.example/secret-token",
        "registry.example/medchat\u200b-docking",
    ],
)
def test_container_image_uri_rejects_paths_secrets_and_ambiguous_text(value: str) -> None:
    assert is_safe_container_image_uri(value) is False
