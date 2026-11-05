"""Read-only PDB polymer-source consultation."""
import re


_QUERY = re.compile(
    r'\s*(?:请)?(?:查询|检索|核对)\s*PDB\s*(?P<id>[0-9][A-Za-z0-9]{3})\s*'
    r'的(?:结构)?(?:来源和物种|物种和来源|来源|物种)[。.!！?？]?\s*', re.I)
_NATURAL_QUERY = re.compile(
    r'\s*(?:请问\s*)?(?:PDB\s*)?(?P<id>[0-9][A-Za-z0-9]{3})\s*(?:的)?(?:结构|蛋白)?\s*'
    r'(?:是(?:哪个|什么|哪种)物种的(?:蛋白|结构)?|来自(?:哪个|什么|哪种)物种|'
    r'是(?:不是)?人源的?吗)[？?。.!！]?\s*', re.I)
_HUMAN_FOLLOWUP = re.compile(r'\s*(?:它|这个结构|该结构)是(?:不是)?人源的?吗[？?。.!！]?\s*')
_DOCKING_QUESTION = re.compile(
    r'\s*我想用\s*PDB\s*(?P<id>[0-9][A-Za-z0-9]{3})\s*的\s*'
    r'[A-Za-z][A-Za-z0-9_-]*\s*结构与[\w\u4e00-\u9fff-]{1,64}做对接[。.!！]'
    r'这个结构是人源的吗[？?]对接分数可以直接说明它对人体\s*'
    r'[A-Za-z][A-Za-z0-9_-]*\s*的活性吗[？?]'
    r'先核对结构来源并解释[，,]不要运行对接[。.!！]?\s*', re.I)


def _human_origin_statement(entities):
    taxa = {taxid for entity in entities for _, taxid in entity['organisms']}
    if all(entity['organisms'] for entity in entities) and taxa == {9606}:
        return '已返回聚合物的来源均为人。'
    if all(entity['organisms'] for entity in entities) and 9606 not in taxa:
        return '已返回聚合物的来源均不是人。'
    return '结构包含不同或未确认的聚合物来源；需要结合具体链确认目标蛋白的物种。'


def answer_pdb_origin(query, *, enable_tools, history=(), session_id=None):
    if _HUMAN_FOLLOWUP.fullmatch(query):
        evidence = history[-1].get('pdb_origin_evidence') if history else None
        if not isinstance(evidence, dict) or not session_id or evidence.get('session_id') != session_id:
            return None
        return {'content': f"根据上一轮 PDB {evidence['pdb_id']} 的记录，"
                + _human_origin_statement(evidence['entities']), 'status': 'partial', 'evidence': evidence}
    mixed = re.fullmatch(r'\s*(?P<origin>.+?[。.!！?？])\s*(?:然后|再)\s*'
                         r'(?:请)?(?:执行|运行|进行|做)(?:分子)?对接[。.!！]?\s*', query, re.I)
    mixed_origin = _QUERY.fullmatch(mixed['origin']) if mixed else None
    natural = _NATURAL_QUERY.fullmatch(query)
    match = _QUERY.fullmatch(query) or natural or _DOCKING_QUESTION.fullmatch(query) or mixed_origin
    if not match:
        return None
    if not enable_tools:
        message = '当前工具未启用，本次未查询结构来源。'
        if mixed_origin:
            message += '本次未执行对接。'
        return {'content': message, 'status': 'not_calculated'}
    from src.target_search.remote_clients import BoundedJsonClient, RcsbClient, RemoteSourceError
    pdb_id = match['id'].upper()
    try:
        with BoundedJsonClient(operation_timeout=15, timeout=(3, 5), max_attempts=2) as http:
            entities = RcsbClient(http).lookup_entry_origins(pdb_id)
    except RemoteSourceError:
        message = f'暂时无法核实 PDB {pdb_id} 的结构来源，请稍后重试。'
        if mixed_origin:
            message += '本次未执行对接。'
        return {'content': message, 'status': 'partial'}
    lines = [f'[{pdb_id} 的 RCSB PDB 记录](https://www.rcsb.org/structure/{pdb_id})中的聚合物来源：']
    for entity in entities:
        names = '、'.join(f'{name}（分类号 {taxid}）' for name, taxid in entity['organisms'])
        lines.append(f"- {entity['entity_id']}：{names or '未提供物种信息'}。")
    if _DOCKING_QUESTION.fullmatch(query) or (natural and '人源' in query):
        lines.append(_human_origin_statement(entities))
    if _DOCKING_QUESTION.fullmatch(query):
        lines.append('不能仅凭对接评分直接确定人源靶点活性；还需核对具体蛋白和物种，并进行相应实验验证。本次未执行对接。')
    if mixed_origin:
        lines.append('请在分子对接页面提供受体结构文件、配体结构，以及对接盒中心和大小。'
                     'PDB 编号本身不是已准备的受体文件；本次未执行对接。')
    return {'content': '\n'.join(lines), 'status': 'partial',
            'evidence': {'session_id': session_id, 'pdb_id': pdb_id, 'entities': entities}}
