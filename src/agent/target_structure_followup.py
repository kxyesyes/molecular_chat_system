"""Reuse accepted target structure records in the owning Web session."""
import math
import re
import html
from copy import deepcopy


def accepted_target_function_evidence(result, session_id):
    from src.target_search.remote_clients import _is_uniprot_accession
    if not isinstance(session_id, str) or not session_id or not result.get('trace_id'):
        return None
    items = [item for item in result.get('tool_result_sequence', [])
             if isinstance(item, dict) and item.get('tool_name') == 'target_database_search']
    if len(items) != 1:
        return None
    item = items[0]
    quality, provenance = item.get('quality'), item.get('provenance')
    if (item.get('status') != 'succeeded' or item.get('success') is not True or item.get('error') is not None
            or not isinstance(quality, dict) or quality.get('validated') is not True or not quality.get('evidence_id')
            or quality.get('scientific_usable') is False or quality.get('demo_mode') or quality.get('fallback_used')
            or not isinstance(provenance, dict) or provenance.get('tool_name') != 'target_database_search'
            or not provenance.get('input_digest') or provenance.get('demo_mode') or provenance.get('fallback_used')):
        return None
    rows = item.get('data')
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        return None
    row = rows[0]
    accession = row.get('uniprot_id')
    if not isinstance(accession, str) or not _is_uniprot_accession(accession):
        return None
    source_url = f'https://www.uniprot.org/uniprotkb/{accession}/entry'
    authoritative = (row.get('source') == 'UniProt' and row.get('source_record_id') == accession
                     and row.get('target_stale') is not True and row.get('stale') is not True
                     and row.get('source_url') == source_url)
    function = row.get('function') if authoritative else None
    if not isinstance(function, str) or not function.strip() or len(function) > 12000:
        function = None
    return {'session_id': session_id, 'uniprot_id': accession, 'gene_symbol': row.get('gene_symbol'),
            'function': function, 'source_url': source_url if authoritative else None}


def accepted_target_evidence(result, session_id):
    if not isinstance(session_id, str) or not session_id or not result.get('trace_id'):
        return None
    records = []
    for item in result.get('tool_result_sequence', []):
        if not isinstance(item, dict) or item.get('tool_name') != 'target_database_search':
            continue
        quality, provenance = item.get('quality'), item.get('provenance')
        if (item.get('status') != 'succeeded' or item.get('success') is not True or item.get('error') is not None
                or not isinstance(quality, dict) or quality.get('validated') is not True
                or not isinstance(quality.get('evidence_id'), str) or not quality['evidence_id']
                or quality.get('demo_mode') or quality.get('fallback_used')
                or quality.get('scientific_usable') is False or not isinstance(provenance, dict)
                or provenance.get('tool_name') != 'target_database_search' or not provenance.get('input_digest')
                or provenance.get('demo_mode') or provenance.get('fallback_used')):
            continue
        for target in item.get('data') or []:
            if not isinstance(target, dict):
                continue
            for structure in target.get('recommended_structures', []):
                if not isinstance(structure, dict):
                    continue
                pdb = structure.get('structure_id')
                if (not isinstance(pdb, str) or not re.fullmatch(r'[0-9][A-Za-z0-9]{3}', pdb)
                        or structure.get('source') != 'RCSB_PDB'
                        or structure.get('structure_type') != 'experimental'):
                    continue
                resolution, ligands = structure.get('resolution'), structure.get('ligand_ids')
                if type(resolution) not in (int, float) or not math.isfinite(resolution) or resolution <= 0:
                    resolution = None
                if (not isinstance(ligands, list) or len(ligands) > 64
                        or any(not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9]{1,12}', value)
                               for value in ligands)):
                    ligands = None
                record = {'structure_id': pdb.upper(), 'resolution': resolution, 'ligand_ids': ligands}
                if structure.get('stale') is True or target.get('structures_stale') is True:
                    record['record_status'] = 'stale'
                elif (structure.get('cache_status') != 'unverified' and structure.get('stale') is False
                        and structure.get('source_url') == f'https://www.rcsb.org/structure/{pdb.upper()}'):
                    record['record_status'] = 'retrieved'
                else:
                    record['record_status'] = 'unverified'
                title = structure.get('title')
                if (structure.get('source_url') == f'https://www.rcsb.org/structure/{pdb.upper()}'
                        and structure.get('stale') is not True and isinstance(title, str)
                        and 0 < len(title) <= 500 and not re.search(r'[\x00-\x1f<>\[\]`]', title)):
                    record.update(title=title, source_url=structure['source_url'])
                target_name = target.get('gene_symbol')
                if isinstance(target_name, str) and re.fullmatch(r'[A-Za-z0-9-]{1,32}', target_name):
                    record['target'] = target_name.upper()
                accession = target.get('uniprot_id')
                from src.target_search.remote_clients import _is_uniprot_accession
                if isinstance(accession, str) and _is_uniprot_accession(accession):
                    record['uniprot_id'] = accession
                records.append(record)
    if not records or len(records) > 20:
        return None
    return {'session_id': session_id, 'trace_id': result['trace_id'], 'structures': records}


def is_resolution_docking_question(query):
    return bool(re.fullmatch(
        r'\s*(?:(?:刚才列出的|这些|上述)结构中[，,]\s*(?:哪个|哪一个)分辨率最高[？?]'
        r'\s*能直接用于(?:分子)?对接吗[？?。]?|'
        r'刚才这些结构(?:里|中)[，,]?\s*分辨率最高的是(?:哪个|哪一个)[？?]'
        r'\s*它是否因此一定最适合(?:分子)?对接[？?。]?)\s*', query))


def parse_domain_suitability_question(query):
    return re.fullmatch(
        r'\s*(?P<pdb>[0-9][A-Za-z0-9]{3})\s*(?:是否)?(?:包含|有)\s*'
        r'(?P<target>[A-Za-z0-9-]{1,32})\s*(?:的)?\s*激酶(?:结构)?域(?:吗|么)?[？?]'
        r'\s*(?:它适合作为激酶抑制剂对接的起点吗|'
        r'(?:它)?(?:能|可以|是否可以|能否)用于(?:激酶|ATP\s*竞争性)?抑制剂对接(?:吗|么)?|'
        r'(?:能|可以|是否可以|能否)用(?:它|该结构)对接\s*(?:激酶|ATP\s*竞争性)?抑制剂(?:吗|么)?)'
        r'[？?。]?\s*', query, re.I)


def structure_followup(query, history, session_id, *, enable_tools=True):
    selection = re.fullmatch(r'\s*(?:PDB\s*[:：]?\s*)?([0-9][A-Za-z0-9]{3})[。.!！]?\s*', query, re.I)
    prior = history[-1].get('target_structure_evidence') if history else None
    if (selection and isinstance(prior, dict) and session_id and prior.get('session_id') == session_id
            and prior.get('pending_ligand_site') is True):
        matches = [row for row in prior.get('structures', []) if row.get('structure_id') == selection[1].upper()]
        if len(matches) != 1:
            return {'content': '无法在本会话的结构记录中唯一核对该编号，请选择已列出的结构。',
                    'status': 'partial', 'evidence': deepcopy(prior)}
        selected = deepcopy(prior)
        if selected.get('selected_structure_id') != selection[1].upper():
            for key in ('component', 'mechanism', 'binding_sites'):
                selected.pop(key, None)
        selected['selected_structure_id'] = selection[1].upper()
        selected.pop('pending_ligand_site', None)
        resumed_query = selected.pop('pending_redocking_query', '这个结构的共晶配体是什么？它结合在哪里？')
        return structure_followup(resumed_query,
                                  [{'target_structure_evidence': selected}], session_id,
                                  enable_tools=enable_tools)
    pocket = re.fullmatch(
        r'\s*(?:其中|刚才的|上述)?\s*(?P<pdb>[0-9][A-Za-z0-9]{3})\s*(?:有|含有)'
        r'(?:配体|共晶配体)\s*(?P<ligand>[A-Za-z0-9]{1,12})[，,]\s*'
        r'(?:能否|可以|能不能)直接(?:以它|用它|以该配体)(?:确定|定义)(?:分子)?对接口袋[？?]'
        r'\s*(?:还)?需要核对哪些信息[？?]\s*(?:请)?(?:先|暂时)?(?:不要|别)(?:运行|执行)(?:分子)?对接[。.!！]?\s*',
        query, re.I)
    if pocket:
        evidence = history[-1].get('target_structure_evidence') if history else None
        if not isinstance(evidence, dict) or not session_id or evidence.get('session_id') != session_id:
            return {'content': '缺少本会话可核对的结构记录，请先检索该靶点的实验结构；本次未运行对接。', 'status': 'partial'}
        rows = [row for row in evidence.get('structures', []) if row.get('structure_id') == pocket['pdb'].upper()]
        ligand = pocket['ligand'].upper()
        if len(rows) != 1 or ligand not in (rows[0].get('ligand_ids') or []):
            return {'content': '无法在本会话的结构记录中唯一核对该结构与配体，请重新检索；本次未运行对接。', 'status': 'partial'}
        return {'content': (f"已检索的 {pocket['pdb'].upper()} 记录包含配体 ID {ligand}，但其角色、结合位置和口袋尚未核实。"
                '可以将该配体作为定位候选口袋的线索，但不能直接认定其适合目标对接。'
                '请核对目标蛋白与链、构象及突变，确认配体的化学身份、占有状态、结合位置和相关性；'
                '检查缺失残基、水和辅因子，准备受体及待对接配体的质子化状态与三维结构。'
                '核实后再依据配体坐标设置盒子中心与大小，并考虑用参考配体重对接验证。'
                '本次未下载或检查坐标，也未运行对接。'),
                'status': 'partial', 'evidence': deepcopy(evidence)}
    pair = re.fullmatch(
        r'\s*(?:刚才列出的|上一轮的|上述)\s*(?P<first>[0-9][A-Za-z0-9]{3})\s*(?:和|与)\s*'
        r'(?P<second>[0-9][A-Za-z0-9]{3})[，,]\s*(?:哪个|哪一个)分辨率更高[？?]'
        r'(?:\s*仅依据已返回的结构信息回答[，,]\s*不要假设配体结合方式[。.!！]?)?\s*', query, re.I)
    if pair:
        evidence = history[-1].get('target_structure_evidence') if history else None
        if not isinstance(evidence, dict) or not session_id or evidence.get('session_id') != session_id:
            return {'content': '缺少本会话可核对的结构记录，请先检索该靶点的实验结构。', 'status': 'partial'}
        ids = [pair['first'].upper(), pair['second'].upper()]
        records = [[row for row in evidence.get('structures', []) if row.get('structure_id') == pdb] for pdb in ids]
        if len(set(ids)) != 2 or any(len(rows) != 1 for rows in records):
            return {'content': '无法在上一轮记录中唯一核对这两个结构，请检查结构编号。', 'status': 'partial'}
        first, second = (rows[0] for rows in records)
        values = [row.get('resolution') for row in (first, second)]
        if any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0 for value in values):
            return {'content': '上一轮记录缺少可比较的分辨率，无法判断。', 'status': 'partial'}
        if values[0] == values[1]:
            comparison = '两者记录的分辨率相同。'
        else:
            comparison = f'{ids[0] if values[0] < values[1] else ids[1]} 的分辨率更高。'
        return {'content': (f'{ids[0]}：{values[0]:g} Å；{ids[1]}：{values[1]:g} Å。'
                + comparison + '分辨率数值越小，通常代表解析细节越高；这不足以单独判断结构是否适合对接。'),
                'status': 'completed', 'evidence': deepcopy(evidence)}
    candidate_question = re.fullmatch(
        r'\s*(?:这些|上述|刚才列出的)结构(?:中|里)[，,]?\s*(?:哪个|哪一个)'
        r'(?:适合|可用于|适合用于)(?:用于)?\s*(?P<target>[A-Za-z0-9-]{1,32})\s*'
        r'激酶抑制剂对接[？?](?:\s*为什么[？?。]?)?\s*', query, re.I)
    if candidate_question:
        evidence = history[-1].get('target_structure_evidence') if history else None
        if not isinstance(evidence, dict) or not session_id or evidence.get('session_id') != session_id:
            return {'content': '缺少本会话可核对的结构记录，请先检索该靶点的实验结构。', 'status': 'partial'}
        records = [dict(row) for row in evidence.get('structures', [])
                   if row.get('target') == candidate_question['target'].upper()]
        missing = [row['structure_id'] for row in records
                   if not row.get('title') and re.fullmatch(r'[0-9][A-Z0-9]{3}', row.get('structure_id', ''))]
        if missing and enable_tools:
            from src.target_search.remote_clients import BoundedJsonClient, RcsbClient, RemoteSourceError
            try:
                with BoundedJsonClient(operation_timeout=15, timeout=(3, 5), max_attempts=2) as http:
                    titles = RcsbClient(http).lookup_titles(missing)
            except RemoteSourceError:
                titles = {}
            for row in records:
                if row['structure_id'] in titles:
                    row['title'] = titles[row['structure_id']]
                    row['source_url'] = f"https://www.rcsb.org/structure/{row['structure_id']}"
        rows = [row for row in records
                if row.get('target') == candidate_question['target'].upper()
                and row.get('source_url') == f"https://www.rcsb.org/structure/{row.get('structure_id')}"
                and isinstance(row.get('title'), str)
                and re.search(r'\bkinase\b', row['title'], re.I)
                and re.search(r'\bwith\b.{0,120}\binhibitor\b', row['title'], re.I)
                and not re.search(r'\b(?:no|without|absence of)\b.{0,30}\binhibitor\b', row['title'], re.I)]
        if rows:
            lines = ['在已检索的实验结构中，可优先核查以下记录：']
            for row in rows:
                title = html.escape(row['title'])
                lines.append(f"- [RCSB {row['structure_id']}]({row['source_url']}) 题名：{title}。")
            lines.append('题名提供了激酶与抑制剂复合物的线索；尚需核对目标序列、突变、结合口袋、配体位置和受体准备，'
                         '不能据此确定最适合的结构。本次未运行对接。')
        else:
            lines = ['现有结构记录中没有可核对的激酶与抑制剂复合物题名，无法选出对接结构。'
                     '请核查目标序列、突变、激酶口袋、配体位置和受体准备；本次未运行对接。']
        saved = deepcopy(evidence)
        for key in ('selected_structure_id', 'component', 'mechanism', 'binding_sites'):
            saved.pop(key, None)
        if (len(rows) == 1 and sum(row.get('structure_id') == rows[0]['structure_id']
                                   for row in saved.get('structures', [])) == 1):
            saved['selected_structure_id'] = rows[0]['structure_id']
        return {'content': '\n'.join(lines), 'status': 'partial', 'evidence': saved}
    domain_question = parse_domain_suitability_question(query)
    if domain_question:
        evidence = history[-1].get('target_structure_evidence') if history else None
        if not isinstance(evidence, dict) or not session_id or evidence.get('session_id') != session_id:
            return {'content': '缺少本会话可核对的结构记录，请先检索该靶点的实验结构。', 'status': 'partial'}
        rows = [row for row in evidence.get('structures', [])
                if row.get('structure_id') == domain_question['pdb'].upper()
                and row.get('target') == domain_question['target'].upper()]
        if len(rows) != 1:
            return {'content': '当前记录无法唯一确认该结构与靶点的对应关系，请重新检索。', 'status': 'partial'}
        row = rows[0]
        saved = deepcopy(evidence)
        if saved.get('selected_structure_id') != row['structure_id']:
            saved.pop('component', None)
            saved.pop('mechanism', None)
            saved.pop('binding_sites', None)
        saved['selected_structure_id'] = row['structure_id']
        if not enable_tools:
            return {'content': f"{row['structure_id']} 已有实验结构记录。当前工具未启用，未核验其激酶结构域覆盖范围。",
                    'status': 'partial', 'evidence': saved}
        from src.agent.kinase_domain_evidence import kinase_domain_ranges
        comparison = kinase_domain_ranges(row['structure_id'], row.get('uniprot_id'))
        if comparison:
            ranges, domain = comparison
            covered = '、'.join(f'{start}–{end}' for start, end in ranges)
            overlap = any(start <= domain[1] and end >= domain[0] for start, end in ranges)
            full = any(start <= domain[0] and end >= domain[1] for start, end in ranges)
            if full:
                conclusion = '序列映射覆盖整个激酶结构域；这不代表所有残基均有坐标或口袋可用，仍需检查结构和配体位置。'
            elif overlap:
                conclusion = '序列映射与激酶结构域部分重叠，但不能证明结构域完整或口袋可用；仍需检查结构和配体位置。'
            else:
                conclusion = '这些范围与激酶结构域不重叠，因此该结构不适合用于该激酶结构域的抑制剂对接。'
            return {'content': (f"[RCSB](https://www.rcsb.org/structure/{row['structure_id']}) 的靶蛋白序列映射为 {covered}；"
                    f"[UniProt](https://www.uniprot.org/uniprotkb/{row['uniprot_id']}/entry) 标注的激酶结构域为 "
                    f'{domain[0]}–{domain[1]}。{conclusion}本次未运行对接。'),
                    'status': 'partial' if overlap else 'completed', 'evidence': saved}
        title = (f"记录标题：{html.escape(row['title'])}。" if row.get('title') else '')
        return {'content': (f"已找到 {row['structure_id']} 的实验结构记录。{title}"
                '现有记录未核验蛋白链与激酶结构域的覆盖范围，不能据此判断是否适合作为激酶抑制剂对接起点。'
                '请核对结构链的序列映射、激酶口袋及配体位置，再检查结构完整性和受体准备；本次未运行对接。'),
                'status': 'partial', 'evidence': saved}
    from src.agent.contracts.docking_intent import redocking_consultation
    redocking = redocking_consultation(query)
    ligand_site = re.fullmatch(
        r'\s*(?:这|该|刚才的)(?:个)?结构的(?:共晶)?配体是什么[？?]\s*'
        r'(?:它|它们)结合在哪里[？?。]?\s*', query)
    if ligand_site or redocking:
        evidence = history[-1].get('target_structure_evidence') if history else None
        if not isinstance(evidence, dict) or not session_id or evidence.get('session_id') != session_id:
            return {'content': '缺少本会话可核对的结构记录，请先检索并选择结构。', 'status': 'partial'}
        structures = evidence.get('structures', [])
        selected = evidence.get('selected_structure_id')
        rows = ([row for row in structures if row.get('structure_id') == selected]
                if selected else structures)
        if len(rows) != 1:
            saved = deepcopy(evidence)
            saved['pending_ligand_site'] = True
            if redocking:
                saved['pending_redocking_query'] = query
            else:
                saved.pop('pending_redocking_query', None)
            return {'content': '当前无法唯一确认所指结构，请提供结构编号。',
                    'status': 'partial', 'evidence': saved}
        row = rows[0]
        ligands = row.get('ligand_ids') or []
        if redocking:
            requested = redocking['ligand'].upper()
            if requested not in ligands:
                return {'content': '无法在当前结构记录中核对该配体，请核对结构编号与配体 ID；本次未运行对接。',
                        'status': 'partial', 'evidence': deepcopy(evidence)}
            ligands = [requested]
        if not ligands:
            return {'content': f"{row['structure_id']} 没有返回配体 ID，不能据此断定没有配体；结合位置未核实。",
                    'status': 'partial', 'evidence': deepcopy(evidence)}
        if not enable_tools:
            return {'content': '当前工具未启用，无法核实配体身份和结合位置；本次未运行对接。',
                    'status': 'partial', 'evidence': deepcopy(evidence)}
        from src.target_search.remote_clients import BoundedJsonClient, RcsbClient, RemoteSourceError
        from src.agent.ligand_binding_site import lookup_ligand_binding_site
        lines = [f"{row['structure_id']} 记录的配体 ID："]
        located = 0
        saved = deepcopy(evidence)
        saved.pop('pending_ligand_site', None)
        saved.pop('pending_redocking_query', None)
        saved['selected_structure_id'] = row['structure_id']
        saved['binding_sites'] = []
        with BoundedJsonClient(operation_timeout=15, timeout=(3, 5), max_attempts=2) as http:
            client = RcsbClient(http)
            for ligand in ligands:
                try:
                    component = client.lookup_component(ligand)
                except RemoteSourceError:
                    component = None
                if (isinstance(component, dict) and component.get('id') == ligand
                        and component.get('source_url') == f'https://www.rcsb.org/ligand/{ligand}'
                        and isinstance(component.get('name'), str)):
                    name = html.escape(component['name']).replace('`', r'\`').replace('[', '［').replace(']', '］')
                    lines.append(f"- [{ligand}]({component['source_url']})：{name}。")
                else:
                    lines.append(f'- {ligand}：名称暂未核实。')
                site = lookup_ligand_binding_site(row['structure_id'], row.get('uniprot_id'), ligand)
                if site:
                    positions = '、'.join(str(start) if start == end else f'{start}–{end}'
                                         for start, end in site['ranges'])
                    lines.append(f"  [PDBe]({site['source_url']}) 记录其关联的蛋白链 {site['chain']} "
                                 f"结合位点残基：{positions}（结构实体序列位置，非 UniProt 编号）。")
                    located += 1
                    saved['binding_sites'].append({'structure_id': row['structure_id'], 'uniprot_id': row.get('uniprot_id'),
                                                   'ligand': ligand, 'site': site})
        if located < len(ligands):
            lines.append('部分或全部配体的结合位置未核实，不能仅凭 ID 判定其位于激酶口袋。')
        lines.append('记录可能包含离子等组分；配体作用需另行核实，本次未运行对接。')
        if redocking:
            lines.extend(['', '尚不能直接认定该结构已可用于重对接验证。需要先检查：',
                '- 将待对接分子的化学结构与共晶配体核对，确认立体化学、质子化状态和化学形式一致；名称相近不足以确认同一化合物。',
                '- 核对蛋白链、构象、突变、缺失残基，以及配体占有率、替代构象、关键水和辅因子。',
                '- 保留共晶配体参考坐标，准备受体和配体，再依据已核实的结合位置设置搜索盒。',
                '- 对接完成后按一致的原子对应关系计算重原子 RMSD，并检查关键相互作用；对接分数本身不能证明重现了共晶姿势。',
                '本次仅核查数据库记录，未检查或准备坐标，也未运行对接或计算 RMSD。'])
        return {'content': '\n'.join(lines), 'status': 'partial', 'evidence': saved}
    number_question = re.fullmatch(
        r'\s*这些位点换成\s*([A-Za-z0-9-]{1,32})\s*的\s*UniProt\s*(?:标准)?序列编号分别是什么[？?。]?\s*',
        query, re.I)
    if number_question:
        evidence = history[-1].get('target_structure_evidence') if history else None
        if not isinstance(evidence, dict) or not session_id or evidence.get('session_id') != session_id:
            return {'content': '缺少本会话可核对的位点记录，请先查询结构配体位点。', 'status': 'partial'}
        rows = [row for row in evidence.get('structures', [])
                if row.get('structure_id') == evidence.get('selected_structure_id')
                and row.get('target') == number_question[1].upper()]
        sites = evidence.get('binding_sites')
        if (len(rows) != 1 or not isinstance(sites, list) or len(sites) != 1
                or sites[0].get('structure_id') != rows[0]['structure_id']
                or sites[0].get('uniprot_id') != rows[0].get('uniprot_id')
                or sites[0].get('ligand') not in (rows[0].get('ligand_ids') or [])):
            return {'content': '无法唯一关联上一轮位点、结构和靶点，请重新查询配体位点。', 'status': 'partial'}
        from src.agent.ligand_binding_site import map_site_to_uniprot
        mapped, unknown = map_site_to_uniprot(sites[0]['site'])
        if not mapped and not unknown:
            return {'content': '没有可用的位点映射，无法换算编号。', 'status': 'partial', 'evidence': deepcopy(evidence)}
        pairs = '、'.join(f'{position}→{reference}' for position, reference in mapped)
        text = (f"{rows[0]['structure_id']} 的 {sites[0]['ligand']} 位点按 RCSB 序列映射换算为 "
                f"UniProt {rows[0]['uniprot_id']} 编号（实体序列位置→UniProt）：{pairs}。") if mapped else ''
        if unknown:
            text += '未能唯一映射的实体序列位置：' + '、'.join(map(str, unknown)) + '。'
        return {'content': text, 'status': 'partial' if unknown else 'completed', 'evidence': deepcopy(evidence)}
    inhibitor_resolution = re.fullmatch(
        r'\s*(?:这些|上述|刚才列出的)结构中[，,]?\s*分辨率最高且(?:含有|包含|有)'
        r'(?:已知)?\s*([A-Za-z0-9-]{1,32})\s*(?:激酶)?抑制剂的是(?:哪个|哪一个)[？?。]?\s*',
        query, re.I)
    if inhibitor_resolution:
        evidence = history[-1].get('target_structure_evidence') if history else None
        if not isinstance(evidence, dict) or evidence.get('session_id') != session_id or not session_id:
            return {'content': '缺少上一轮可复核的结构记录，请先检索靶点结构。', 'status': 'partial'}
        rows = [row for row in evidence.get('structures', [])
                if row.get('structure_id') == evidence.get('selected_structure_id')
                and row.get('target') == inhibitor_resolution[1].upper()]
        component, mechanism = evidence.get('component'), evidence.get('mechanism')
        if (len(rows) == 1 and isinstance(component, dict) and isinstance(mechanism, dict)
                and component.get('id') in (rows[0].get('ligand_ids') or [])
                and component.get('inchikey') and component['inchikey'] == mechanism.get('inchikey')
                and rows[0].get('uniprot_id') and rows[0]['uniprot_id'] == mechanism.get('uniprot_id')
                and mechanism.get('action') == 'INHIBITOR'):
            resolution = rows[0].get('resolution')
            value = f'{resolution:g} Å' if resolution is not None else '未记录'
            detail = (f"已核对结构 {rows[0]['structure_id']} 的配体 {component['id']} "
                      f"与该靶点的抑制机制记录；该结构分辨率为 {value}。")
        else:
            detail = '现有记录缺少可核对的结构、配体和目标抑制机制对应关系。'
        return {'content': detail + '尚未逐一核实其他结构的配体机制，无法确定符合条件的最高分辨率结构；'
                '已检索记录也不保证覆盖全部结构。', 'status': 'partial', 'evidence': deepcopy(evidence)}
    named_ligands = re.fullmatch(
        r'\s*([0-9][A-Za-z0-9]{3})\s*中有哪些配体[？?]\s*它们是什么[，,]'
        r'\s*能确认是抑制剂吗[？?。]?\s*', query, re.I)
    if named_ligands:
        evidence = history[-1].get('target_structure_evidence') if history else None
        if not isinstance(evidence, dict) or evidence.get('session_id') != session_id or not session_id:
            return {'content': '缺少上一轮可复核的结构记录，请先检索靶点结构。', 'status': 'partial'}
        rows = [row for row in evidence['structures'] if row['structure_id'] == named_ligands[1].upper()]
        if len(rows) != 1:
            return {'content': '上一轮没有可唯一核对的该结构记录，请重新检索。', 'status': 'partial'}
        row = rows[0]
        ligands = row.get('ligand_ids') or []
        if not ligands:
            return {'content': f"{row['structure_id']} 未返回配体 ID；这不代表不存在配体。",
                    'status': 'partial', 'evidence': deepcopy(evidence)}
        if not enable_tools:
            return {'content': f"{row['structure_id']} 的已检索配体 ID：{'、'.join(ligands)}。"
                    '当前工具未启用，未进一步核实配体名称和抑制机制。',
                    'status': 'partial', 'evidence': deepcopy(evidence)}
        from src.target_search.remote_clients import BoundedJsonClient, RcsbClient, ChemblClient, RemoteSourceError
        saved = deepcopy(evidence)
        for key in ('selected_structure_id', 'component', 'mechanism'):
            saved.pop(key, None)
        verified = []
        lines = [f"{row['structure_id']} 的已检索配体 ID："]
        with BoundedJsonClient(operation_timeout=15, timeout=(3, 5), max_attempts=2) as http:
            client = RcsbClient(http)
            for ligand in ligands:
                try:
                    component = client.lookup_component(ligand)
                except RemoteSourceError:
                    component = None
                if (isinstance(component, dict) and component.get('id') == ligand
                        and component.get('source_url') == f'https://www.rcsb.org/ligand/{ligand}'
                        and isinstance(component.get('name'), str)):
                    name = html.escape(component['name']).replace('`', r'\`').replace('[', '［').replace(']', '］')
                    lines.append(f'- {ligand}：{name}。')
                    if component.get('inchikey') and row.get('uniprot_id'):
                        try:
                            mechanism = ChemblClient(http).lookup_mechanism(component['inchikey'], row['uniprot_id'])
                        except RemoteSourceError:
                            lines.append(f'  {ligand} 的作用机制查询暂不可用。')
                        else:
                            if mechanism is not None:
                                lines.append(f"  按完整 InChIKey 和靶点匹配，[ChEMBL]({mechanism['source_url']})"
                                             f"将 {mechanism['name']} 标记为 {row.get('target', row['uniprot_id'])} 抑制剂。")
                                if mechanism.get('action') == 'INHIBITOR':
                                    verified.append((component, mechanism))
                            else:
                                lines.append(f'  {ligand} 未找到可唯一确认的靶点抑制机制记录。')
                    else:
                        lines.append(f'  {ligand} 缺少可用于核验机制的化学或靶点标识。')
                else:
                    lines.append(f'- {ligand}：名称暂未核实。')
        lines.append('配体记录可能包括离子等组分；仅凭名称和结构记录不能确认它们是抑制剂或位于对接口袋。'
                     '数据库机制记录不代表本次进行了活性测定或对接。')
        if len(verified) == 1:
            component, mechanism = verified[0]
            saved['selected_structure_id'] = row['structure_id']
            saved['component'] = component
            saved['mechanism'] = {**mechanism, 'inchikey': component['inchikey'], 'uniprot_id': row['uniprot_id']}
        return {'content': '\n'.join(lines), 'status': 'partial', 'evidence': saved}
    atp_choice = re.fullmatch(
        r'(?:这些|上述|刚才列出的)结构(?:里|中)[，,]?(?:哪个|哪一个)适合(?:做|用于)\s*'
        r'([A-Za-z0-9-]{1,32})\s*(?:的\s*)?(?:(?:酪氨酸)?激酶\s*)?ATP\s*位点(?:小分子|分子)?对接[？?]'
        r'\s*(?:为什么|说明原因|选择前还需要检查什么)[？?。]?', query.strip(), re.I)
    if atp_choice:
        evidence = history[-1].get('target_structure_evidence') if history else None
        if not isinstance(evidence, dict) or evidence.get('session_id') != session_id or not session_id:
            return {'content': '缺少上一轮可核对的结构记录，请先检索靶点结构。', 'status': 'partial'}
        targets = {row.get('target') for row in evidence['structures']}
        if targets != {atp_choice[1].upper()}:
            return {'content': '请求的靶点与上一轮结构记录不一致或无法确认，请重新检索并核对靶点。',
                    'status': 'partial'}
        identifiers = '、'.join(row['structure_id'] for row in evidence['structures'])
        return {'content': (f"上一轮检索到的 {atp_choice[1].upper()} 实验结构包括 {identifiers}。"
                '现有记录尚未核实 ATP 口袋、共晶配体身份和位置、构象或结构准备状态，'
                '无法确定哪个适合对接。应核对链、突变与口袋缺失残基、共晶配体的位置及相关性，'
                '检查质子化状态、关键水和辅因子，并考虑共晶配体重对接验证；本次未执行对接，'
                '检索结果也不保证覆盖全部结构。'), 'status': 'partial', 'evidence': deepcopy(evidence)}
    docking_choice = re.fullmatch(
        r'(?:这些|上述|刚才列出的)结构(?:里|中)[，,]?(?:哪个|哪一个)最适合(?:分子)?对接[？?]'
        r'\s*(?:请)?说明选择依据和还需要核实的信息[。.!！?？]?', query.strip())
    if is_resolution_docking_question(query) or docking_choice:
        evidence = history[-1].get('target_structure_evidence') if history else None
        if not isinstance(evidence, dict) or evidence.get('session_id') != session_id or not session_id:
            return {'content': '缺少上一轮可核对的结构记录，请先检索靶点结构。', 'status': 'partial'}
        rows = [row for row in evidence['structures'] if row.get('resolution') is not None]
        if not rows:
            return {'content': '已检索结构没有可比较的分辨率，无法判断；对接准备状态也未核实。',
                    'status': 'partial', 'evidence': deepcopy(evidence)}
        best = min(row['resolution'] for row in rows)
        identifiers = '、'.join(row['structure_id'] for row in rows if row['resolution'] == best)
        if docking_choice:
            return {'content': f'目前不能确定哪个受体最适合对接。在已检索且有分辨率记录的结构中，'
                    f'{identifiers} 分辨率最高（{best:g} Å），可作为进一步核查的起点；'
                    '分辨率本身不足以确定最佳受体。还需核实目标蛋白的构象和完整性、共晶配体的身份及位置、'
                    '对接口袋、辅因子和受体准备情况；本次未执行对接，检索也未必覆盖全部结构。',
                    'status': 'partial', 'evidence': deepcopy(evidence)}
        return {'content': f'在已检索且有分辨率记录的结构中，{identifiers} 的分辨率最高（{best:g} Å）。'
                '本次检索不保证覆盖全部结构。不能仅凭分辨率确认可直接对接；还需核查结构完整性、'
                '配体与口袋，并完成受体准备。',
                'status': 'partial', 'evidence': deepcopy(evidence)}
    if re.fullmatch(r'(?:这|该|刚才的|上一轮的)(?:个)?靶点的(?:生物学)?功能(?:是什么|有哪些)[？?。]?', query.strip()):
        evidence = history[-1].get('target_function_evidence') if history else None
        if not isinstance(evidence, dict) or evidence.get('session_id') != session_id:
            return {'content': '无法确认上一轮的单一靶点，请重新检索并指定靶点。', 'status': 'partial'}
        if not evidence.get('function'):
            return {'content': f"{evidence['uniprot_id']} 的功能注释未在本次检索记录中返回，无法据此说明。",
                    'status': 'partial', 'function_evidence': deepcopy(evidence)}
        annotation = re.sub(r'\s*\((?:PubMed:\d+\s*,?\s*)+\)', '', evidence['function'])
        first_sentence = re.split(r'(?<=\.)\s+', annotation, maxsplit=1)[0]
        annotation = html.escape(first_sentence).replace('`', r'\`').replace('[', r'\[').replace(']', r'\]')
        return {'content': f"{evidence['uniprot_id']} 的功能注释（原文首句）：{annotation}",
                'status': 'completed', 'function_evidence': deepcopy(evidence)}
    ligand_list = re.fullmatch(
        r'(?:这些|上述|刚才的|上一轮的)结构(?:中)?哪些(?:含有|包含|有)(?:共晶)?配体[？?。]?',
        query.strip())
    if ligand_list:
        evidence = history[-1].get('target_structure_evidence') if history else None
        if not isinstance(evidence, dict) or evidence.get('session_id') != session_id or not session_id:
            return {'content': '缺少上一轮可复核的结构记录，请先检索靶点结构。', 'status': 'partial'}
        lines = ['上一轮实验结构中有配体 ID 记录的条目：']
        lines.extend(f"- {row['structure_id']}：{'、'.join(row['ligand_ids'])}。"
                     for row in evidence['structures'] if row.get('ligand_ids'))
        if len(lines) == 1:
            lines.append('未找到有配体 ID 记录的实验结构。')
        lines.append('这里只覆盖已检索的实验结构；未记录不代表不存在。'
                     '配体 ID 可能包含离子等组分，角色和结合位点尚未核实。')
        return {'content': '\n'.join(lines), 'status': 'partial', 'evidence': deepcopy(evidence)}
    ligand_question = re.fullmatch(
        r'([A-Za-z0-9]{1,12})\s*是什么[？?]\s*它是\s*([A-Za-z0-9-]{1,32})\s*(?:激酶)?抑制剂吗[？?。]?',
        query.strip(), re.I)
    if ligand_question:
        evidence = history[-1].get('target_structure_evidence') if history else None
        if not isinstance(evidence, dict) or evidence.get('session_id') != session_id or not session_id:
            return {'content': '缺少上一轮可复核的结构记录，请先检索靶点结构。', 'status': 'partial'}
        ligand, target = (value.upper() for value in ligand_question.groups())
        rows = [row for row in evidence['structures'] if row.get('target') == target
                and ligand in (row.get('ligand_ids') or [])]
        if len(rows) != 1:
            return {'content': '上一轮缺少可唯一核对的配体身份及结构标题，无法判断其抑制剂角色。',
                    'status': 'partial'}
        if not enable_tools:
            return {'content': f"上一轮 {rows[0]['structure_id']} 记录包含配体 ID {ligand}。"
                    '当前工具未启用，未进一步核实配体身份和抑制机制。',
                    'status': 'partial', 'evidence': deepcopy(evidence)}
        from src.target_search.remote_clients import BoundedJsonClient, RcsbClient, ChemblClient, RemoteSourceError
        saved = deepcopy(evidence)
        row = next(row for row in saved['structures'] if row['structure_id'] == rows[0]['structure_id'])
        if not row.get('title') and row.get('uniprot_id'):
            try:
                with BoundedJsonClient(operation_timeout=20, timeout=(3, 7), max_attempts=2) as http:
                    refreshed = RcsbClient(http).search(row['uniprot_id'], pdb_id=row['structure_id'])
            except RemoteSourceError:
                refreshed = []
            if len(refreshed) == 1:
                record = refreshed[0]
                title = record.get('title')
                if (record.get('structure_id') == row['structure_id']
                        and record.get('source_url') == f"https://www.rcsb.org/structure/{row['structure_id']}"
                        and ligand in record.get('ligand_evidence', [])
                        and isinstance(title, str) and 0 < len(title) <= 500
                        and not re.search(r'[\x00-\x1f<>\[\]`]', title)):
                    row.update(title=title, source_url=record['source_url'])
        if not row.get('title'):
            return {'content': '无法从 RCSB 核实该结构、靶点和配体的关联；请稍后重试。',
                    'status': 'partial', 'evidence': saved}
        saved['selected_structure_id'] = row['structure_id']
        component = evidence.get('component')
        if (not isinstance(component, dict) or component.get('id') != ligand
                or component.get('source_url') != f'https://www.rcsb.org/ligand/{ligand}'):
            try:
                with BoundedJsonClient(operation_timeout=15, timeout=(3, 5), max_attempts=2) as http:
                    component = RcsbClient(http).lookup_component(ligand)
            except RemoteSourceError:
                component = None
        title = (f"[RCSB 结构条目]({row['source_url']})的标题为：{row['title']}。")
        if component is None:
            description = 'RCSB 化学组分查询暂不可用，配体化学身份及其具体作用尚未独立核实。'
        else:
            saved['component'] = component
            name = component['name'].replace('[', '［').replace(']', '］')
            description = (f"[RCSB 化学组分]({component['source_url']})记录的 {ligand} 名称为：{name}。"
                           '该名称与结构标题提供背景，但该组分对靶点的具体作用尚未独立核实。')
            if component.get('inchikey') and row.get('uniprot_id'):
                try:
                    with BoundedJsonClient(operation_timeout=25, timeout=(3, 7), max_attempts=2) as http:
                        mechanism = ChemblClient(http).lookup_mechanism(component['inchikey'], row['uniprot_id'])
                except RemoteSourceError:
                    mechanism = None
                    description += ' ChEMBL 查询暂不可用，可稍后重试。'
                if mechanism is not None:
                    saved['mechanism'] = {**mechanism, 'inchikey': component['inchikey'],
                                          'uniprot_id': row['uniprot_id']}
                    description = (f"[RCSB 化学组分]({component['source_url']})记录的 {ligand} 名称为：{name}。"
                                   f"按完整 InChIKey 匹配，[ChEMBL]({mechanism['source_url']})记录该分子为 "
                                   f"{mechanism['name']}，并将其对 {target} 的作用标记为抑制剂。"
                                   '这是数据库机制记录，不代表本次进行了活性测定或对接。')
                    return {'content': f"上一轮 {row['structure_id']} 记录包含配体 ID {ligand}。" + title + description,
                            'status': 'completed', 'evidence': saved}
        return {'content': f"上一轮 {row['structure_id']} 记录包含配体 ID {ligand}。" + title + description,
                'status': 'partial', 'evidence': saved}
    docking_start = re.fullmatch(
        r'(?:那么)?这个结构可以作为\s*([A-Za-z0-9-]{1,32})\s*抑制剂对接的起点吗[？?。]?',
        query.strip(), re.I)
    if docking_start:
        evidence = history[-1].get('target_structure_evidence') if history else None
        if not isinstance(evidence, dict) or evidence.get('session_id') != session_id or not session_id:
            return {'content': '缺少上一轮可复核的结构和配体机制记录，请先检索并核对。', 'status': 'partial'}
        rows = [row for row in evidence.get('structures', []) if row.get('target') == docking_start[1].upper()]
        if evidence.get('selected_structure_id'):
            rows = [row for row in rows if row.get('structure_id') == evidence['selected_structure_id']]
        component, mechanism = evidence.get('component'), evidence.get('mechanism')
        if (len(rows) != 1 or not isinstance(component, dict) or not isinstance(mechanism, dict)
                or component.get('id') not in (rows[0].get('ligand_ids') or [])
                or not component.get('inchikey') or component.get('inchikey') != mechanism.get('inchikey')
                or not rows[0].get('uniprot_id') or rows[0]['uniprot_id'] != mechanism.get('uniprot_id')
                or mechanism.get('action') != 'INHIBITOR'):
            return {'content': '目前缺少能关联该结构、配体与靶点抑制作用的完整证据，暂无法判断对接起点。',
                    'status': 'partial', 'evidence': deepcopy(evidence)}
        row = rows[0]
        return {'content': (f"{row['structure_id']} 可以作为待进一步评估的对接起点：它是实验结构，"
                f"记录包含 {component['id']}，该组分与 {row['target']} 抑制剂机制记录已按化学和靶点标识关联。"
                '使用前仍需核对目标构象、蛋白链和配体结合位点，完成受体与配体准备，'
                '并用共晶配体重对接等方式验证流程；本轮未准备结构或运行对接。'),
                'status': 'completed', 'evidence': deepcopy(evidence)}
    selection = re.fullmatch(
        r'这些结构中哪(?:一)?个含有激酶抑制剂[、,，]适合作为对接起点[？?]'
        r'\s*(?:请)?说明判断依据[。.!！?？]?', query.strip())
    if selection:
        evidence = history[-1].get('target_structure_evidence') if history else None
        if not isinstance(evidence, dict) or evidence.get('session_id') != session_id or not session_id:
            return {'content': '缺少上一轮可复核的结构记录，请先检索靶点结构。', 'status': 'partial'}
        lines = ['上一轮实验结构及记录中的配体 ID：']
        for row in evidence['structures']:
            ligands = '、'.join(row['ligand_ids']) if row['ligand_ids'] else '未记录'
            lines.append(f"- {row['structure_id']}：{ligands}。")
        lines.append('这些配体的角色和结合位点尚未核实，无法据此确定哪个含激酶抑制剂或适合对接。'
                     '应核对结构条目中的蛋白链、配体身份及结合位点，再结合目标构象和结构质量选择。')
        return {'content': '\n'.join(lines), 'status': 'partial', 'evidence': deepcopy(evidence)}
    match = re.fullmatch(r'(?:刚才|上一轮|上次)(?:的)?\s*([0-9][A-Za-z0-9]{3})\s*'
                         r'分辨率是多少[？?]\s*有哪些共晶配体[？?。]?', query.strip(), re.I)
    singular = re.fullmatch(r'(?:刚才|上一轮|上次)(?:的)?这(?:个)?结构的分辨率是多少[？?]\s*'
                            r'配体\s*ID\s*是什么[？?。]?', query.strip(), re.I)
    if not match and not singular:
        return None
    evidence = history[-1].get('target_structure_evidence') if history else None
    if not isinstance(evidence, dict) or evidence.get('session_id') != session_id or not session_id:
        return {'content': '缺少上一轮可复核的结构记录，请先检索该靶点结构。', 'status': 'partial'}
    selected = match[1].upper() if match else evidence.get('selected_structure_id')
    rows = ([row for row in evidence.get('structures', []) if row['structure_id'] == selected]
            if selected else evidence.get('structures', []))
    if len(rows) != 1:
        return {'content': '上一轮没有可唯一对应的该结构记录，请重新检索并核对结构编号。', 'status': 'partial'}
    row = rows[0]
    resolution = f"{row['resolution']:g} Å" if row['resolution'] is not None else '未记录'
    ligands = '、'.join(row['ligand_ids']) if row['ligand_ids'] else '未记录'
    return {'content': f"结构 {row['structure_id']} 的分辨率：{resolution}；记录中的配体 ID：{ligands}。"
            '这些 ID 的配体角色尚未核实，不能仅凭该记录确认哪些是共晶抑制剂或适合用于对接。',
            'status': 'partial', 'evidence': deepcopy(evidence)}
