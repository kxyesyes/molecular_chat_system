from __future__ import annotations

import pytest

from src.sandbox_broker.safety import is_safe_metadata_text


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
        "plain scientific warning",
    ],
)
def test_metadata_safety_allows_fractions_smiles_and_relative_text(value: str) -> None:
    assert is_safe_metadata_text(value) is True


@pytest.mark.parametrize(
    "value",
    [None, b"text", "", "   ", "warning\ntext", "warn\u200bing", "warn\u2060ing"],
)
def test_metadata_safety_is_strict_about_type_empty_and_control_text(
    value: object,
) -> None:
    assert is_safe_metadata_text(value) is False
