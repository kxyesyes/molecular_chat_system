"""Read a whole batch docking request before preparing any ligand."""

import re
import sqlite3

from src.molecular_design.chemistry import canonicalize_smiles
from src.web.docking_request import OwnedDockingStructureEvidence, assess_docking_request_text
from src.web.scientific_references import view_reference


_CHINESE = re.compile(
    r"(?:请(?:帮我)?\s*)?(?:把|将|用|使用)\s*(?P<body>[\s\S]+?)"
    r"\s*(?:进行|做)?(?:一次)?(?:分子)?对接"
    r"(?:[。，；,;]\s*使用共晶配体定义口袋)?[。.!！]?",
    re.IGNORECASE,
)
_ENGLISH = re.compile(
    r"(?:please\s+)?dock\s+(?P<body>[\s\S]+?)[.!]?", re.IGNORECASE | re.ASCII,
)
_TARGET_LINK = re.compile(
    r"(?:与|到|针对)|和(?=\s*(?:PDB|[1-9][a-z0-9]{3}\s*$|刚才|上次|之前|上述|当前))"
    r"|\s+(?:against|with|to)\s+", re.I,
)
_SEPARATOR = re.compile(r"\s*(?:、|，|,|和|以及|\band\b|\r?\n)\s*", re.I | re.ASCII)
_CANDIDATE = re.compile(r"(?:候选(?:分子)?|candidates?\b)\s*#?\s*", re.I | re.ASCII)
_PDB = re.compile(r"(?:PDB\s*[:：]?\s*)?([1-9][a-z0-9]{3})", re.I | re.ASCII)
_PREVIOUS = re.compile(
    r"(?:刚才|上次|之前|上述|当前|刚查到)(?:的)?(?:实验)?(?:受体|结构)"
    r"|(?:the\s+)?(?:previous|current|last)\s+receptor", re.I | re.ASCII,
)
_SMILES = re.compile(r"[A-Za-z0-9@+\-\[\]()=#\\/%.*:$]+", re.ASCII)
_MISSING_RECEPTOR = "请提供本批次使用的一个实验受体 PDB 编号。"


def _previous_pdb(structure_context, references, session_id):
    if not structure_context or references is None:
        raise ValueError(_MISSING_RECEPTOR)
    evidence = OwnedDockingStructureEvidence(references.store)
    try:
        _, observation = evidence._current(structure_context, session_id)
    except (ValueError, TypeError, KeyError, sqlite3.Error):
        raise ValueError(_MISSING_RECEPTOR) from None
    offered = [entry.get("structure_id") for target in observation["data"]
               for entry in target.get("recommended_structures", [])]
    if len(offered) != 1 or not re.fullmatch(r"[1-9][A-Z0-9]{3}", offered[0] or ""):
        raise ValueError(_MISSING_RECEPTOR)
    # The coordinator will authenticate this ID before preparation, just as
    # for an explicitly named PDB. Never select the first of multiple offers.
    return offered[0]


def parse_batch_request(query, *, reference, references, session_id, structure_context, max_ligands):
    """Return (PDB, ordered ligand rows), None for other actions, or a clarification."""
    if not isinstance(query, str) or len(query) > 16384:
        return None
    text = query.strip()
    match = _CHINESE.fullmatch(text) or _ENGLISH.fullmatch(text)
    if not match:
        return None
    parts = _TARGET_LINK.split(match["body"], maxsplit=1)
    ligand_text = re.sub(r"(?:分别|都|一起)\s*$", "", parts[0]).strip()
    items = _SEPARATOR.split(ligand_text)
    if len(items) < 2:
        return None
    if len(parts) == 1 and assess_docking_request_text(
            text, enabled=True, enable_tools=True)["assessed"]:
        return None
    if len(items) > max_ligands:
        raise ValueError(f"本次批量对接最多支持 {max_ligands} 个配体，请缩短清单。")

    receptor = parts[1].strip() if len(parts) == 2 else ""
    pdb = _PDB.fullmatch(receptor)
    if pdb:
        pdb_id = pdb[1].upper()
    elif _PREVIOUS.fullmatch(receptor):
        pdb_id = _previous_pdb(structure_context, references, session_id)
    else:
        raise ValueError(_MISSING_RECEPTOR)

    candidates = None
    if _CANDIDATE.match(items[0]):
        view = references.get(reference, session_id=session_id) if references is not None else None
        if not view:
            raise ValueError("请先选择当前会话中已确认的候选集合。")
        # A later collection uses cumulative card numbers in the original
        # message, but local numbers when restored alone. A single pointer
        # cannot identify which of these two displays the user is reading.
        snapshot = references.store.get_scientific_report_snapshot(
            reference["trace_id"], session_id=session_id, references=[view_reference(view)])
        if not snapshot:
            raise ValueError("请重新选择已确认的候选集合，或直接填写配体 SMILES。")
        collections = snapshot["run"]["metadata"]["scientific_presentations"]
        generated = [entry for entry in snapshot["latest"].values()
                     if entry["tool_name"] == "llm_molecular_generator"
                     and entry["status"] in {"succeeded", "partial"}]
        if len(collections) != 1 or len(generated) != 1:
            raise ValueError("此轮包含多个候选集合，无法确定卡片编号。请按所需顺序填写配体 SMILES。")
        candidates = view["ordered_candidates"]

    rows = []
    for index, item in enumerate(items, 1):
        ordinal = index
        if candidates is not None:
            number = _CANDIDATE.sub("", item, count=1) if _CANDIDATE.match(item) else item
            if not re.fullmatch(r"[0-9]{1,4}", number):
                raise ValueError(f"第 {index} 项需要填写完整的候选序号。")
            ordinal = int(number)
            if not 1 <= ordinal <= len(candidates):
                raise ValueError(f"当前确认的集合中没有候选 {ordinal}，请核对第 {index} 项。")
            if any(row["ordinal"] == ordinal for row in rows):
                raise ValueError(f"第 {index} 项的候选 {ordinal} 重复，请核对清单。")
            item = candidates[ordinal - 1]["candidate"]["canonical_smiles"]
        else:
            item = item.removeprefix("配体").strip()
        if not _SMILES.fullmatch(item):
            raise ValueError(f"第 {index} 项不是完整的 SMILES，请核对后重新提交清单。")
        try:
            smiles = canonicalize_smiles(item)
        except ValueError:
            raise ValueError(f"第 {index} 项的 SMILES 无效，请核对后重新提交清单。") from None
        rows.append({"ordinal": ordinal, "smiles": smiles})
    return pdb_id, rows
