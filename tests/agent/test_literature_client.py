import pytest

from src.literature.europe_pmc import EuropePMCClient
from src.target_search.remote_clients import BoundedJsonClient, RemoteSourceError, _is_allowlisted_request


def test_search_preserves_article_identity_abstract_and_type(monkeypatch):
    def request(self, source, method, url, **kwargs):
        assert source == 'EuropePMC' and kwargs['params']['query'] == 'EGFR T790M ATP'
        return {'hitCount': 1, 'resultList': {'result': [{'source': 'MED', 'id': '18227510',
            'pmid': '18227510', 'title': 'ATP <i>affinity</i>', 'pubYear': '2008', 'doi': '10.1000/example.1',
            'abstractText': 'A <i>mutation</i> changes ATP affinity.',
            'pubTypeList': {'pubType': ['Journal Article']}}]}}
    monkeypatch.setattr(BoundedJsonClient, 'request_json', request)
    with BoundedJsonClient() as http:
        result = EuropePMCClient(http).search('EGFR T790M ATP')
    assert result['hit_count'] == 1 and len(result['articles']) == 1
    article = result['articles'][0]
    assert article['title'] == 'ATP affinity' and article['abstract'] == 'A mutation changes ATP affinity.'
    assert article['url'] == 'https://pubmed.ncbi.nlm.nih.gov/18227510/'
    assert article['publication_types'] == ['Journal Article'] and article['year'] == '2008'
    assert article['doi'] == '10.1000/example.1'


def test_search_does_not_publish_malformed_doi(monkeypatch):
    monkeypatch.setattr(BoundedJsonClient, 'request_json', lambda *args, **kwargs: {
        'hitCount': 1, 'resultList': {'result': [{'source': 'MED', 'id': '123', 'pmid': '123',
            'title': 'Study', 'doi': '10.1000/example\n<script>bad</script>'}]}})
    with BoundedJsonClient() as http:
        assert EuropePMCClient(http).search('study')['articles'][0]['doi'] is None


def test_search_does_not_treat_unvalidated_ids_as_citations(monkeypatch):
    monkeypatch.setattr(BoundedJsonClient, 'request_json', lambda *args, **kwargs: {
        'hitCount': 2, 'resultList': {'result': [
            {'source': 'MED', 'id': 'x/redirect', 'pmid': 'x/redirect', 'title': 'Bad'},
            {'source': 'PPR', 'id': '123', 'title': 'Preprint'}]}})
    with BoundedJsonClient() as http:
        assert EuropePMCClient(http).search('EGFR')['articles'] == []


def test_search_invalid_response_and_allowlist(monkeypatch):
    assert _is_allowlisted_request('EuropePMC', 'GET', 'https://www.ebi.ac.uk/europepmc/webservices/rest/search')
    assert not _is_allowlisted_request('EuropePMC', 'GET', 'https://www.ebi.ac.uk/europepmc/webservices/rest/search/other')
    assert not _is_allowlisted_request('EuropePMC', 'POST', 'https://www.ebi.ac.uk/europepmc/webservices/rest/search')
    monkeypatch.setattr(BoundedJsonClient, 'request_json', lambda *args, **kwargs: {'resultList': {}})
    with BoundedJsonClient() as http, pytest.raises(RemoteSourceError):
        EuropePMCClient(http).search('EGFR')


def test_search_cleans_encoded_markup_and_script_content(monkeypatch):
    monkeypatch.setattr(BoundedJsonClient, 'request_json', lambda *args, **kwargs: {
        'hitCount': 1, 'resultList': {'result': [{'source': 'MED', 'id': '123', 'pmid': '123',
            'title': 'EGFR&lt;sup&gt;T790M&lt;/sup&gt;',
            'abstractText': 'Result <script>untrusted()</script> confirmed.'}]}})
    with BoundedJsonClient() as http:
        article = EuropePMCClient(http).search('EGFR')['articles'][0]
    assert article['title'] == 'EGFRT790M'
    assert article['abstract'] == 'Result confirmed.'


def test_search_returns_validated_open_access_pmc_metadata(monkeypatch):
    monkeypatch.setattr(BoundedJsonClient, 'request_json', lambda *args, **kwargs: {
        'hitCount': 2, 'resultList': {'result': [
            {'source': 'MED', 'id': '123', 'pmid': '123', 'title': 'Study', 'pmcid': 'PMC456', 'isOpenAccess': 'Y'},
            {'source': 'MED', 'id': '789', 'pmid': '789', 'title': 'Other', 'pmcid': 'PMC456/../../x', 'isOpenAccess': 'Y'}]}})
    with BoundedJsonClient() as http:
        articles = EuropePMCClient(http).search('study')['articles']
    assert articles[0]['pmcid'] == 'PMC456' and articles[0]['open_access'] is True
    assert articles[1]['pmcid'] is None


def test_bioc_allowlist_is_limited_to_pmc_full_text_json():
    valid = 'https://www.ncbi.nlm.nih.gov/research/bionlp/RESTful/pmcoa.cgi/BioC_json/PMC9649653/unicode'
    assert _is_allowlisted_request('PMC_BioC', 'GET', valid)
    assert not _is_allowlisted_request('PMC_BioC', 'POST', valid)
    assert not _is_allowlisted_request('PMC_BioC', 'GET', valid + '?token=x')
    assert not _is_allowlisted_request('PMC_BioC', 'GET', valid.replace('PMC9649653', '../secret'))


@pytest.mark.parametrize('endpoint', ['EuropePMC', 'TDC'])
def test_literature_clients_reject_redirect_without_reading_body(monkeypatch, endpoint):
    import io
    import requests
    from src.literature.model_sources import _fetch_dataset_page

    class UnreadBody(io.BytesIO):
        def read(self, *args):
            pytest.fail('redirect body was read outside the download limit')

    body = UnreadBody(b'ignored')

    def redirect(adapter, request, **kwargs):
        response = requests.Response()
        response.status_code = 302
        response.request = request
        response.url = request.url
        response.headers['Location'] = '/moved'
        response.raw = body
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', redirect)
    if endpoint == 'EuropePMC':
        with BoundedJsonClient() as http, pytest.raises(RemoteSourceError) as failure:
            EuropePMCClient(http).search('EGFR')
        assert failure.value.status_code == 302
    else:
        with pytest.raises(ValueError, match='dataset_http_failure'):
            _fetch_dataset_page()
    assert body.closed
