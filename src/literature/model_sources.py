"""Verify the dataset documentation linked by an accepted model prediction."""
from datetime import datetime, timezone
from html import escape
from html.parser import HTMLParser
import re
import time

import requests

from src.literature.europe_pmc import EuropePMCClient
from src.target_search.remote_clients import BoundedJsonClient, RemoteSourceError


HERG_URL = 'https://tdcommons.ai/single_pred_tasks/tox/#herg-blockers'


class _HergSection(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.active = False
        self.parts = []
        self.dois = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {'h1', 'h2', 'h3'}:
            self.active = attrs.get('id') == 'herg-blockers'
        if self.active and tag == 'a':
            match = re.fullmatch(r'https://pubs\.acs\.org/doi/(?:abs/)?'
                r'(10\.[0-9]{4,9}/[A-Za-z0-9._-]+)(?:\?[^#]*)?', attrs.get('href', ''))
            if match:
                self.dois.append(match[1])

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)


def parse_herg_section(page):
    parser = _HergSection()
    parser.feed(page)
    text = re.sub(r'\s+', ' ', ' '.join(parser.parts)).strip()
    if ('Binary classification.' not in text or 'blocks (1) or not blocks (0)' not in text
            or len(set(parser.dois)) != 1):
        return None
    return {'dataset_url': HERG_URL, 'label_definition': '1=blocker; 0=non-blocker',
            'doi': parser.dois[0], 'section_text': text}


def _fetch_dataset_page():
    # The URL is fixed, never derived from user text or a returned hyperlink.
    with requests.Session() as session:
        session.trust_env = False
        deadline = time.monotonic() + 20
        with session.get(HERG_URL.split('#')[0], timeout=(3.05, 10), stream=True,
                         allow_redirects=False) as response:
            if response.status_code != 200:
                raise ValueError('dataset_http_failure')
            payload = bytearray()
            for chunk in response.iter_content(16384):
                payload.extend(chunk)
                if len(payload) > 1_000_000 or time.monotonic() > deadline:
                    raise ValueError('dataset_read_limit')
            return payload.decode('utf-8')


def verify_herg_sources():
    record = {'checked_at': datetime.now(timezone.utc).isoformat(), 'dataset_url': HERG_URL,
              'assay_cutoff_status': 'unverified', 'fulltext_status': 'not_retrieved'}
    try:
        section = parse_herg_section(_fetch_dataset_page())
    except (requests.RequestException, ValueError):
        section = None
    if section is None:
        record['dataset_status'] = 'unverified'
        return {'content': '当前预测的模型包指向 [TDC hERG blockers](' + HERG_URL + ')，'
                '但本次未能核实官方标签章节。训练标签与原始文献均未核实；未重新预测。',
                'status': 'partial', 'source_record': record}
    record.update(section, dataset_status='verified')
    content = ('当前预测记录来自 ADMET-AI 1.4.0，模型包的 hERG 元数据指向 '
        '[TDC hERG blockers](' + HERG_URL + ')。\n\n'
        '已核实该官方页面的标签定义：二分类，1 表示阻断，0 表示不阻断。'
        '这是数据集文档定义，尚未逐条审计本地训练样本标签。\n\n')
    doi = section['doi']
    content += f'该章节引用的原始论文：[DOI {doi}](https://doi.org/{doi})。'
    try:
        with BoundedJsonClient(operation_timeout=15, max_attempts=1) as http:
            result = EuropePMCClient(http).search(f'DOI:"{doi}"', limit=3)
        articles = [item for item in result['articles'] if (item.get('doi') or '').casefold() == doi.casefold()]
    except RemoteSourceError:
        articles = []
    if len(articles) == 1:
        article = articles[0]
        record['article'] = article
        title = escape(article['title']).replace('[', '［').replace(']', '］').replace('`', '')
        content += f"\n\n数据库核对：[{title}]({article['url']})（{article['year'] or '年份未核实'}；PMID {article['pmid']}）。"
    else:
        content += '\n\n本次未能通过文献数据库核对该 DOI 的题名和摘要。'
    content += ('\n\n**未核实：**原始论文全文中的实验 IC50 截点、测定协议及样本标注细节。'
        '不能用预测筛查阈值 0.5 代替实验浓度截点，也不能套用其他 hERG 数据集的定义。未重新预测。')
    return {'content': content, 'status': 'partial', 'source_record': record}


def model_source_followup(query, session_id, history, *, enable_tools):
    match = re.fullmatch(
        r'\s*(?:请)?(?:核查|核实|查证)(?:刚才|上一轮)(?:这个|的)?\s*hERG\s*'
        r'模型的训练标签定义(?:和|与|及)原始文献[，,]\s*(?:给出|提供)可访问来源[。.?？]?'
        r'(?:\s*找不到就说未核实[，,]\s*不要凭经验补\s*IC50\s*截点[，,]\s*也不要重新预测[。.]?)?\s*',
        query, re.I)
    if not match:
        return None
    evidence = history[-1].get('candidate_evidence') if history else None
    rows = (evidence.get('candidates', []) if isinstance(evidence, dict)
            and session_id and evidence.get('session_id') == session_id and evidence.get('trace_id') else [])
    linked = [row for row in rows if row.get('admet', {}).get('model_version') == '1.4.0'
        and row.get('admet', {}).get('endpoints', {}).get('hERG', {}).get('dataset_url') == HERG_URL
        and any(source.get('tool_name') == 'admet_predictor' and source.get('evidence_id')
                for source in row.get('sources', []))]
    if not rows or len(linked) != len(rows):
        return {'content': '当前会话缺少可追溯到该 hERG 模型的数据集来源记录；训练标签和原始文献未核实。'
                '本次未重新预测，也未用其他模型的资料代替。', 'status': 'partial'}
    if not enable_tools:
        return {'content': '当前工具未启用，本次未检索来源；训练标签和原始文献未核实。', 'status': 'partial',
                'reused_evidence': evidence}
    result = verify_herg_sources()
    result['reused_evidence'] = evidence
    return result
