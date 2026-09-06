"""AI recommendation helpers for molecular design."""

from __future__ import annotations

import html
import inspect
import json
import re
from typing import Any, Dict, List, Optional

from .fragments import infer_label_filters


LABEL_DISPLAY_NAMES = {
    "label_lipophilic": "亲脂片段",
    "label_hydrophilic": "亲水片段",
    "label_amphiphilic": "两亲片段",
    "label_basic": "碱性片段",
    "label_acidic": "酸性片段",
    "label_has_aromatic_ring": "芳香环片段",
    "label_has_halogen": "含卤片段",
    "label_has_heterocycle": "杂环片段",
    "label_has_amide": "酰胺片段",
    "label_has_ester": "酯基片段",
    "label_bioisostere": "生物等排片段",
}


async def call_design_model(model, prompt: str) -> str:
    if model is None:
        return ""
    result = model.generate(prompt, temperature=0.3, max_tokens=800)
    if inspect.isawaitable(result):
        result = await result
    return "" if result is None else str(result)


def looks_like_llm_failure(reply_text: str) -> bool:
    if not reply_text:
        return True
    lowered = reply_text.lower()
    markers = (
        "nonetype",
        "technical difficulties",
        "having trouble",
        "api error",
        "error calling",
        "服务暂时不可用",
        "调用失败",
        "模型失败",
        "服务出现问题",
    )
    return any(marker in lowered for marker in markers)


def validate_fragment_smiles(smiles: str) -> Optional[str]:
    """Return a canonical fragment SMILES when it is usable for substitution."""

    if not smiles or "[*]" not in smiles:
        return None
    try:
        from rdkit import Chem

        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return None
        if not any(atom.GetAtomicNum() == 0 for atom in mol.GetAtoms()):
            return None
        return Chem.MolToSmiles(mol, canonical=True)
    except Exception:
        return None


def _extract_json_payload(text: str) -> Optional[Any]:
    if not text:
        return None
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", stripped, re.DOTALL | re.IGNORECASE)
    if fenced:
        stripped = fenced.group(1).strip()

    candidates = [stripped]
    first_obj = stripped.find("{")
    last_obj = stripped.rfind("}")
    if first_obj >= 0 and last_obj > first_obj:
        candidates.append(stripped[first_obj : last_obj + 1])
    first_arr = stripped.find("[")
    last_arr = stripped.rfind("]")
    if first_arr >= 0 and last_arr > first_arr:
        candidates.append(stripped[first_arr : last_arr + 1])

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except Exception:
            continue
    return None


def parse_structured_fragments(reply_text: str) -> List[Dict[str, Any]]:
    payload = _extract_json_payload(reply_text)
    if isinstance(payload, dict):
        raw_fragments = payload.get("fragments") or payload.get("recommended_fragments") or []
    elif isinstance(payload, list):
        raw_fragments = payload
    else:
        raw_fragments = []

    parsed: List[Dict[str, Any]] = []
    seen = set()
    for item in raw_fragments:
        if isinstance(item, str):
            raw_smiles = item
            name = ""
            reason = ""
        elif isinstance(item, dict):
            raw_smiles = item.get("fragment_smiles") or item.get("smiles") or item.get("fragment") or ""
            name = item.get("name") or item.get("label") or ""
            reason = item.get("reason") or item.get("rationale") or ""
        else:
            continue
        canonical = validate_fragment_smiles(str(raw_smiles).strip())
        if not canonical or canonical in seen:
            continue
        seen.add(canonical)
        parsed.append(
            {
                "fragment_smiles": canonical,
                "smi": canonical,
                "label": name or canonical,
                "name": name or canonical,
                "reason": reason,
                "source": "llm",
            }
        )
    return parsed


def _merge_recommendations(
    structured_fragments: List[Dict[str, Any]],
    repository_fragments: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    seen = set()
    for fragment in [*structured_fragments, *repository_fragments]:
        smiles = fragment.get("fragment_smiles") or fragment.get("smi")
        if not smiles or smiles in seen:
            continue
        seen.add(smiles)
        next_fragment = dict(fragment)
        next_fragment.setdefault("fragment_smiles", smiles)
        next_fragment.setdefault("smi", smiles)
        next_fragment.setdefault("source", "fragment_db")
        merged.append(next_fragment)
    return merged


def build_prompt(command: str, current_smiles: str, current_props: Dict[str, Any]) -> str:
    props_str = ""
    if current_props:
        props_str = (
            f"当前分子属性: MW={current_props.get('mw', '?')} Da, "
            f"LogP={current_props.get('logp', '?')}, "
            f"QED={current_props.get('qed', '?')}, "
            f"TPSA={current_props.get('tpsa', '?')} A2, "
            f"HBD={current_props.get('hbd', '?')}, HBA={current_props.get('hba', '?')}"
        )

    return f"""你是一位经验丰富的药物化学家，正在帮助用户进行基于片段的分子设计。

当前母体分子SMILES: {current_smiles if current_smiles else '未知'}
{props_str}

用户指令: {command}

请完成以下任务：
1. 分析用户指令的优化方向
2. 推荐3-5个具体的官能团片段，给出SMILES和中文名称
3. 解释每个片段如何影响目标属性
4. 给出操作建议

请优先只返回 JSON，不要使用 Markdown。JSON 格式如下：
{{
  "fragments": [
    {{"fragment_smiles": "[*]O", "name": "羟基", "reason": "提高极性"}}
  ],
  "summary": "一句话说明优化思路"
}}
片段 SMILES 必须包含 [*] 作为连接点。"""


def build_fallback_reply(
    command: str,
    current_smiles: str,
    current_props: Dict[str, Any],
    recommended_fragments: List[Dict[str, Any]],
) -> str:
    labels = infer_label_filters(command, LABEL_DISPLAY_NAMES.keys())
    if labels:
        direction = "、".join(LABEL_DISPLAY_NAMES.get(label, label) for label in labels[:3])
    else:
        direction = "与当前优化目标相关的常用片段"

    lines = [
        "当前已切换为规则推荐模式。",
        f"优化目标判断：{command}",
        f"建议优先尝试：{direction}。",
    ]
    if current_smiles:
        lines.append(f"当前分子：{current_smiles}")
    if current_props:
        summary = []
        for key in ("mw", "logp", "qed", "tpsa"):
            value = current_props.get(key)
            if value not in (None, ""):
                summary.append(f"{key.upper()}={value}")
        if summary:
            lines.append("当前性质：" + ", ".join(summary))
    examples = [frag.get("fragment_smiles") for frag in recommended_fragments[:3] if frag.get("fragment_smiles")]
    if examples:
        lines.append("可优先尝试的片段：" + "，".join(examples))
    lines.append("建议先做 1 到 2 个片段替换，再结合 LogP、QED、TPSA 的变化继续迭代。")
    return "\n".join(lines)


def to_safe_html(text: str) -> str:
    return html.escape(text or "").replace("\n", "<br>").replace("  ", "&nbsp;&nbsp;")


async def recommend(
    *,
    model,
    command: str,
    current_smiles: str,
    current_props: Dict[str, Any],
    recommended_fragments: List[Dict[str, Any]],
) -> Dict[str, Any]:
    if not command:
        raise ValueError("指令不能为空")

    prompt = build_prompt(command, current_smiles, current_props)
    warning: Optional[str] = None
    try:
        reply_text = await call_design_model(model, prompt)
    except Exception as exc:
        reply_text = f"AI 推荐服务出现问题: {exc}"

    structured_fragments: List[Dict[str, Any]] = []
    response_failed = looks_like_llm_failure(reply_text)
    if not response_failed:
        structured_fragments = parse_structured_fragments(reply_text)

    if response_failed:
        warning = "AI model response was unavailable, fallback enabled"
        reply_text = build_fallback_reply(command, current_smiles, current_props, recommended_fragments)
    elif not structured_fragments:
        warning = "AI model response did not contain valid fragment JSON; local fragments appended"

    merged_fragments = _merge_recommendations(structured_fragments, recommended_fragments)

    return {
        "success": True,
        "reply": to_safe_html(reply_text),
        "recommended_fragments": merged_fragments,
        "structured_fragments": structured_fragments,
        "command": command,
        "warning": warning,
        "fallback_used": response_failed,
    }
