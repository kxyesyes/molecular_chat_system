"""Bind goal proposals to read-only tools and project checked observations."""
from dataclasses import dataclass
import json
import re

from src.agent.contracts import ObservationStatus
from src.agent.evidence import EvidenceLedger
from src.agent.evidence_followup import MAX_CANDIDATES
from src.agent.scientific_session.decision_execution import SingleAttemptTool
from src.agent.tools.base_tool import BaseMolecularTool
from src.agent.tools.molecular_input import _MARKER, parse_molecular_smiles
from src.agent.tools.target_observation import target_planning_data
from src.agent.validators.result_validator import AgentResultValidator
from src.target_search.remote_clients import _is_uniprot_accession


_MOLECULE_ARGS = {"smiles": ["one or more complete, literal user-supplied SMILES, in requested order"]}
_CATALOG = {
    "property_calculator": {"description": "Calculate RDKit descriptors including molecular weight, logP, TPSA and QED.",
                            "arguments": _MOLECULE_ARGS},
    "drug_likeness_assessment": {"description": "Evaluate Lipinski, Veber and lead-likeness rules and QED.",
                                "arguments": _MOLECULE_ARGS},
    "target_database_search": {"description": "Look up a UniProt accession, recorded structure counts and up to 20 experimental PDB structures with the best measured resolution.",
                               "arguments": {"uniprot_id": "one literal user-supplied UniProt accession"}},
}
GOAL_TOOL_NAMES = frozenset(_CATALOG)
_DELIMITERS = re.compile(r"""[\s,;，；。、！？：（）“”‘’「」`"'\u3400-\u9fff]+""")
_ACCESSION_LABEL = re.compile(r"(?<![a-z0-9_])UniProt(?:\s+ID)?\s*[:=]", re.I)


@dataclass(frozen=True)
class BoundStep:
    tool_name: str
    version: str
    _arguments_json: str
    _input_json: str
    key: str

    @property
    def arguments(self):
        return json.loads(self._arguments_json)


class GoalTools:
    def __init__(self, registry):
        self.registry = registry

    @property
    def catalog(self):
        available = {}
        for name, description in _CATALOG.items():
            try:
                adapter = self.registry.resolve(name, require_available=False)
            except KeyError:
                continue
            if adapter.health()["available"] is not False:
                available[name] = description
        return available

    def bind(self, tool_name, arguments, *, user_texts):
        if tool_name not in _CATALOG or type(arguments) is not dict:
            raise ValueError("目标分析不支持这项操作。")
        try:
            adapter = self.registry.resolve(tool_name, require_available=False)
        except KeyError:
            raise ValueError("指定工具不可用。") from None
        fields = set()
        for text in user_texts:
            for field in _DELIMITERS.split(_ACCESSION_LABEL.sub(" ", _MARKER.sub(" ", text))):
                if field.endswith((".", "?", "!")):
                    field = field[:-1]
                if field.startswith("(") and field.endswith(")"):
                    field = field[1:-1]
                if field:
                    fields.add(field)
        if tool_name == "target_database_search":
            value = arguments.get("uniprot_id")
            if (set(arguments) != {"uniprot_id"} or not isinstance(value, str)
                    or not _is_uniprot_accession(value.upper()) or value.upper() not in {f.upper() for f in fields}):
                raise ValueError("请提供完整的 UniProt 编号。")
            arguments = {"uniprot_id": value.upper()}
            payload = {"query": {"uniprot_id": value.upper()}, "resolution_limit": 20}
        else:
            values = arguments.get("smiles")
            if (set(arguments) != {"smiles"} or type(values) is not list or not 1 <= len(values) <= MAX_CANDIDATES
                    or any(type(s) is not str or s not in fields for s in values)):
                raise ValueError("配体必须是用户提供的完整 SMILES。")
            query = "\n".join(values)
            parsed = parse_molecular_smiles(query, BaseMolecularTool("goal_inputs", ""))
            if parsed != values:
                raise ValueError("配体输入未通过完整结构校验。")
            payload = {"query": query}
        version = adapter.spec.version
        key = EvidenceLedger.output_digest([tool_name, version, payload])
        return BoundStep(tool_name, version, json.dumps(arguments), json.dumps(payload), key)

    def execute(self, step, *, dispatch_guard=None):
        adapter = self.registry.resolve(step.tool_name, require_available=False)
        receipt = dict(ok=False, tool_name=step.tool_name, tool_version=step.version,
                       facts=[], provenance={}, message="工具未返回与本次输入匹配的完整结果。")
        if adapter.spec.version != step.version:
            receipt["message"] = "工具版本已变更，请重新规划。"
            return receipt
        superseded = False
        def check_dispatch():
            nonlocal superseded
            if not dispatch_guard():
                superseded = True
                raise _GoalSuperseded()
        try:
            result = SingleAttemptTool(adapter, dispatch_guard=check_dispatch if dispatch_guard else None
                                       ).execute(json.loads(step._input_json))
        except _GoalSuperseded:
            return None
        # If the adapter's queued worker observed the revision change, its
        # standard error normalization still represents zero physical calls.
        if superseded:
            return None
        result = AgentResultValidator().validate_tool_result(result)
        if (result.tool_name != step.tool_name or not result.success or result.status != ObservationStatus.SUCCEEDED
                or result.quality.get("validated") is not True or not isinstance(result.data, list) or not result.data):
            if result.error:
                receipt["message"] = f"工具未能完成本步（{result.error.code.value}）。"
            return receipt
        output_digest = EvidenceLedger.output_digest(result.data)
        if result.provenance and (result.provenance.tool_name != step.tool_name
                                  or result.provenance.tool_version != step.version
                                  or result.provenance.output_digest not in (None, output_digest)):
            return receipt
        provenance = EvidenceLedger(step.key).prepare_provenance(step.key, result)
        if provenance.demo_mode or provenance.fallback_used:
            return receipt
        if step.tool_name == "target_database_search":
            accession = step.arguments["uniprot_id"]
            if (len(result.data) != 1 or result.data[0].get("uniprot_id") != accession
                    or any(result.data[0].get(k) for k in ("stale", "target_stale", "structures_stale"))
                    or result.data[0].get("structure_evidence_status") == "unavailable"):
                return receipt
            facts, message = _target_facts(result.data[0], accession)
        else:
            if ([row.get("smiles") for row in result.data] != step.arguments["smiles"]
                    or any(row.get("status", "succeeded") != "succeeded" for row in result.data)):
                return receipt
            facts, message = _molecular_facts(step.tool_name, result.data)
        return dict(receipt, ok=True, facts=facts, message=message, provenance={
            "tool_name": step.tool_name, "tool_version": step.version, "input_digest": step.key,
            "output_digest": output_digest})


class _GoalSuperseded(Exception):
    pass


def _molecular_facts(name, rows):
    facts, display = [], []
    for row in rows:
        smiles = row["smiles"]
        if name == "property_calculator":
            props = row["properties"]
            facts.append({"smiles": smiles, **{k: props[k] for k in (
                "molecular_weight", "logp", "tpsa", "hba", "hbd", "rotatable_bonds", "qed")},
                "units": {"molecular_weight": "Da", "tpsa": "Å²"}})
            display.append(f"{smiles}：分子量 {props['molecular_weight']} Da，logP {props['logp']}，"
                           f"TPSA {props['tpsa']} Å²，QED {props['qed']}。")
        else:
            assessment = row["assessment"]
            count = assessment["lipinski_rule_of_five"]["violation_count"]
            veber = assessment["veber_rules"]["overall_compliance"]
            lead = assessment["lead_likeness"]["overall_compliance"]
            qed = assessment["qed_score"]
            facts.append(dict(smiles=smiles, lipinski_violations=count, veber_pass=veber,
                              lead_likeness_pass=lead, qed=qed))
            display.append(f"{smiles}：Lipinski 违反 {count} 项，Veber {'通过' if veber else '未通过'}，"
                           f"先导化合物规则{'通过' if lead else '未通过'}，QED {qed}。")
    return facts, "\n".join(display)


def _target_facts(row, accession):
    projected = target_planning_data([row])[0]
    keys = ("structure_count", "experimental_structure_count", "alphafold_structure_count")
    fact = {"uniprot_id": accession, **{key: projected[key] for key in keys if key in projected}}
    if "comparable_structure_count" in row:
        fact["comparable_structure_count"] = row["comparable_structure_count"]
    # Only public PDB identifiers and observed resolution enter the model context.
    structures = []
    for record in (row.get("recommended_structures") or [])[:20]:
        pdb = record.get("structure_id")
        if (isinstance(pdb, str) and re.fullmatch(r"[1-9][A-Za-z0-9]{3}", pdb)
                and record.get("structure_type") == "experimental" and record.get("source") == "RCSB_PDB"
                and not record.get("stale")):
            structures.append({"pdb": pdb.upper(), "resolution_A": record.get("resolution")})
    fact["experimental_structure_preview"] = structures
    display = [f"UniProt {accession}"]
    if "structure_count" in fact:
        display.append(f"记录结构数 {fact['structure_count']}")
    if structures:
        display.append("实验结构记录 " + "、".join(s["pdb"] + (
            f"（{s['resolution_A']} Å）" if s["resolution_A"] is not None else "") for s in structures))
    return [fact], "；".join(display) + "。"
