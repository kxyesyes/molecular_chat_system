"""Real guard/envelope regressions using exclusively synthetic text."""
import random

import pytest

from src.agent.persistence import redaction as r
from src.agent.persistence.sqlite_store import _continuation_json


def legacy_string_guard(text):
    # Unoptimized grammar oracle, deliberately not the optimized public guard.
    return bool(
        r.looks_like_credential(text) or r._SECRET_LABEL_ASSIGNMENT.search(text)
        or r._AUTHORIZATION_CREDENTIAL.search(text) or r._BEARER.search(text)
        or r._contains_secret_url(text)
    )


def test_long_ascii_assignment_skips_proven_irrelevant_prefix(monkeypatch):
    starts = []
    pattern = r._SECRET_LABEL_ASSIGNMENT

    class ObservedPattern:
        def search(self, value, pos=0):
            starts.append(pos)
            return pattern.search(value, pos)

    monkeypatch.setattr(r, '_SECRET_LABEL_ASSIGNMENT', ObservedPattern())
    text = 'a.' * 260000 + ' "API Key"="synthetic"'
    assert r.contains_secret_material(text)
    assert starts and min(starts) >= 520000


def test_no_url_delimiter_does_not_scan_url_grammar(monkeypatch):
    def forbidden(value):
        pytest.fail('URL grammar scanned text without its required delimiter')
    monkeypatch.setattr(r, '_contains_secret_url', forbidden)
    assert r.contains_secret_material('a.' * 260000) is False


LABELS = [
    'api key', 'apikey', 'x-api-key', 'xapikey', 'access key', 'accesskeyid',
    'secret access key id', 'secretaccesskey', 'client secret', 'privatekey',
    'refresh_token', 'accesstoken', 'idtoken', 'authtoken', 'session_token',
    'setcookie', 'session-cookie', 'cookie', 'proxy-authorization',
    'authorization', 'credential', 'credentials', 'password', 'passwd',
    'pwd', 'secret', 'token',
]


@pytest.mark.parametrize('label', LABELS)
def test_ascii_label_variants_keep_original_grammar(label):
    for spelling in (label, label.upper(), label.title(), label.replace(' ', '\t')):
        for left in (' ', '"', "'", '_', '-', '9', '('):
            for tail in ('=synthetic', ':synthetic', '"="synthetic"', "'='synthetic'"):
                text = 'a.' * 550 + ' ' + left + spelling + tail
                assert r.contains_secret_material(text) == legacy_string_guard(text)


@pytest.mark.parametrize('tail', [
    'Bearer synthetic', 'bearer short', 'sk-synthetic-example',
    'AKIA' + 'A' * 16, 'ASIA' + 'A' * 16,
    *['gh' + letter + '_' + 'a' * 20 for letter in 'pousr'],
    'github_pat_' + 'a' * 20,
    'https://user:synthetic@example.invalid/path',
    'custom+scheme.-://example.invalid/?session_token=synthetic',
    'https://example.invalid/path?x_api_key=synthetic',
    'C/C=C\\O', '/tmp/pose.pdbqt', 'https://example.invalid/plain',
    'token_count=12', 'keys=ordinary', 'API Key=', 'cookie: ',
])
def test_known_shapes_urls_and_nonsecret_suffixes(tail):
    for before in (' ', '.', '_', '-', '"', '中'):
        text = 'a.' * 550 + before + tail
        assert r.contains_secret_material(text) == legacy_string_guard(text)


@pytest.mark.parametrize('text', [
    'API Key=synthetic', 'APİKEY=synthetic', 'ſecret=synthetic',
    'authorızation: Basic c3ludGhldGlj', '中cookie=synthetic',
    '中https://user:synthetic@example.invalid/', '分子 C/C=C\\O',
])
def test_unicode_case_and_boundary_semantics_remain_unchanged(text):
    text = 'a.' * 550 + ' ' + text
    assert r.contains_secret_material(text) == legacy_string_guard(text)


def test_seeded_ascii_differential_corpus():
    rng = random.Random(240923)
    alphabet = 'abcXYZ09._- \t\n\"\':=/@?&'
    for _ in range(300):
        prefix = ''.join(rng.choices(alphabet, k=1100))
        label = rng.choice(LABELS)
        text = prefix + rng.choice([' ', '_', '-', '\"']) + label + '=synthetic'
        assert r.contains_secret_material(text) == legacy_string_guard(text)


def test_continuation_keeps_full_payload_and_rejects_tail():
    text = 'a.' * 260000
    envelope = {'schema': 1, 'id': 'A', 'configuration': 'config',
                'checksum': 'digest', 'snapshot': {'text': text}}
    detached, encoded = _continuation_json(envelope)
    assert detached == envelope
    assert text in encoded
    envelope['snapshot']['text'] += ' cookie=synthetic'
    with pytest.raises(ValueError, match='credential material'):
        _continuation_json(envelope)
