"""Bounded Europe PMC retrieval; records are evidence, not verified claims."""
from html import unescape
from html.parser import HTMLParser
import re

from src.target_search.remote_clients import RemoteSourceError


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style'}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {'script', 'style'} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _plain_text(value, limit):
    if not isinstance(value, str) or len(value) > limit:
        return None
    parser = _TextExtractor()
    parser.feed(unescape(value))
    return re.sub(r'\s+', ' ', ''.join(parser.parts)).strip()


class EuropePMCClient:
    URL = 'https://www.ebi.ac.uk/europepmc/webservices/rest/search'

    def __init__(self, http):
        self._http = http

    def search(self, query, *, limit=5):
        if (not isinstance(query, str) or not query.strip() or len(query) > 512
                or type(limit) is not int or not 1 <= limit <= 10):
            raise RemoteSourceError('EuropePMC', 'invalid_request', retryable=False)
        payload = self._http.request_json('EuropePMC', 'GET', self.URL, params={
            'query': query, 'format': 'json', 'resultType': 'core', 'pageSize': limit})
        if not isinstance(payload, dict) or type(payload.get('hitCount')) is not int or payload['hitCount'] < 0:
            raise RemoteSourceError('EuropePMC', 'invalid_response', retryable=False)
        result_list = payload.get('resultList')
        results = result_list.get('result') if isinstance(result_list, dict) else None
        if not isinstance(results, list) or len(results) > limit:
            raise RemoteSourceError('EuropePMC', 'invalid_response', retryable=False)
        articles = []
        for result in results:
            if not isinstance(result, dict):
                raise RemoteSourceError('EuropePMC', 'invalid_response', retryable=False)
            pmid = result.get('pmid')
            if result.get('source') != 'MED' or not isinstance(pmid, str) or not re.fullmatch(r'[1-9][0-9]{0,11}', pmid) or result.get('id') != pmid:
                continue
            title = _plain_text(result.get('title'), 4096)
            if not title:
                continue
            types = result.get('pubTypeList')
            types = types.get('pubType', []) if isinstance(types, dict) else []
            if not isinstance(types, list) or any(not isinstance(value, str) or len(value) > 200 for value in types):
                raise RemoteSourceError('EuropePMC', 'invalid_response', retryable=False)
            year = result.get('pubYear')
            if not isinstance(year, str) or not re.fullmatch(r'[0-9]{4}', year):
                year = None
            abstract = _plain_text(result.get('abstractText'), 50000)
            pmcid = result.get('pmcid')
            if not isinstance(pmcid, str) or not re.fullmatch(r'PMC[1-9][0-9]{0,11}', pmcid):
                pmcid = None
            doi = result.get('doi')
            if not isinstance(doi, str) or len(doi) > 255 or not re.fullmatch(r'10\.[0-9]{4,9}/[A-Za-z0-9._;()/:-]+', doi, re.I):
                doi = None
            articles.append({'pmid': pmid, 'title': title, 'year': year, 'abstract': abstract,
                             'publication_types': types, 'url': f'https://pubmed.ncbi.nlm.nih.gov/{pmid}/',
                             'pmcid': pmcid, 'open_access': result.get('isOpenAccess') == 'Y', 'doi': doi})
        return {'hit_count': payload['hitCount'], 'articles': articles}
