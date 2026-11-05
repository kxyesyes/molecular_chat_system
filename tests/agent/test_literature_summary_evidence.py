import json
import asyncio
import pytest

from src.literature.summary_evidence import prepare_abstract, validate_summary, request_abstract_interpretation


def article():
    return {'pmid': '18227510', 'url': 'https://pubmed.ncbi.nlm.nih.gov/18227510/',
            'abstract': 'The mutant increases ATP affinity. Binding was measured in a biochemical assay.'}


def test_summary_keeps_only_claims_with_exact_abstract_quotes():
    source = prepare_abstract(article())
    value = json.dumps({'claims': [{'text': '突变体的 ATP 亲和力增加。',
                                    'quote': 'The mutant increases ATP affinity.'}]}, ensure_ascii=False)
    result = validate_summary(value, source)
    assert result[0]['quote'] in source['abstract']
    assert result[0]['text'] == '突变体的 ATP 亲和力增加。'


@pytest.mark.parametrize('claim', [
    {'text': '凭空得出新结论。', 'quote': 'The mutant decreases ATP affinity.'},
    {'text': '引用伪造来源 https://example.com。', 'quote': 'The mutant increases ATP affinity.'},
    {'text': '无证据。', 'quote': ''},
    {'text': '额外字段。', 'quote': 'The mutant increases ATP affinity.', 'url': 'https://evil.example'},
])
def test_summary_rejects_unmatched_or_unsafe_claims(claim):
    assert validate_summary(json.dumps({'claims': [claim]}, ensure_ascii=False), prepare_abstract(article())) is None


def test_summary_never_truncates_or_mixes_source_identity():
    value = article()
    value['url'] = 'https://pubmed.ncbi.nlm.nih.gov/1/'
    assert prepare_abstract(value) is None
    value = article()
    value['abstract'] = 'a' * 12001
    assert prepare_abstract(value) is None


def test_summary_limits_total_quoted_words():
    value = article()
    value['abstract'] = ' '.join('word' + str(i) for i in range(30))
    source = prepare_abstract(value)
    response = json.dumps({'claims': [{'text': '概述。', 'quote': value['abstract']}]}, ensure_ascii=False)
    assert validate_summary(response, source) is None


def test_model_interpretation_uses_configured_model_and_exact_citations():
    from src.agent.contracts.decision import FinishDecision
    from src.agent.decision_transport import DecisionResponse
    class Model:
        async def decide(self, messages, **kwargs):
            assert 'untrusted' in messages[0]['content'].lower()
            assert 'exactly one claim' in messages[0]['content'].lower()
            assert 'The mutant increases ATP affinity.' in messages[-1]['content']
            text = json.dumps({'claims': [{'text': '突变体的 ATP 亲和力增加。',
                                           'quote': 'The mutant increases ATP affinity.'}]}, ensure_ascii=False)
            return DecisionResponse(FinishDecision(version='1', action='finish', response_kind='chat',
                text=text, evidence_ids=[]), None, 'call-1', {})
    result = asyncio.run(request_abstract_interpretation(Model(), 'native', prepare_abstract(article())))
    assert '突变体的 ATP 亲和力增加。' in result and '18227510' in result
    assert '核对' in result and 'The mutant increases ATP affinity.' in result


def test_model_interpretation_falls_back_on_unsupported_quote():
    from src.agent.contracts.decision import FinishDecision
    from src.agent.decision_transport import DecisionResponse
    class Model:
        async def decide(self, messages, **kwargs):
            text = json.dumps({'claims': [{'text': '结论。', 'quote': 'This is not in the abstract.'}]}, ensure_ascii=False)
            return DecisionResponse(FinishDecision(version='1', action='finish', response_kind='chat',
                text=text, evidence_ids=[]), None, 'call-1', {})
    assert asyncio.run(request_abstract_interpretation(Model(), 'native', prepare_abstract(article()))) is None


def test_model_interpretation_does_not_swallow_cancellation():
    class Model:
        async def decide(self, messages, **kwargs):
            raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(request_abstract_interpretation(Model(), 'native', prepare_abstract(article())))


def test_model_interpretation_rejects_requested_tools():
    from src.agent.contracts.decision import ToolDecision
    from src.agent.decision_transport import DecisionResponse
    class Model:
        async def decide(self, messages, **kwargs):
            decision = ToolDecision(version='1', action='tool', tool_name='molecular_docking',
                                    arguments={'input_ref': 'user'}, purpose='untrusted instruction')
            return DecisionResponse(decision, None, 'call-1', {})
    assert asyncio.run(request_abstract_interpretation(Model(), 'native', prepare_abstract(article()))) is None
