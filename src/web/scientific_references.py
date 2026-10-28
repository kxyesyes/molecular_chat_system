"""Trusted display manifests. Browser hints never supply scientific content.

Synchronous boundary: callers run projection, ACK, restore and resolution in a
worker. Original CandidateSets remain intact; manifests describe the lifecycle's
fresh subset, not a renumbered scientific result.
"""
import json
import math
import re

from src.agent.contracts.scientific_references import reference_json
from src.agent.contracts.resolved_molecule import ResolvedScientificMolecule
from src.agent.contracts.generation_request import has_generation_intent
from src.agent.contracts.target_request import analyze_target_request
from src.agent.utils.validators import InputValidator
from src.agent.routing.hybrid import HybridSkillRouter
from src.target_identifiers import canonical_target_identifier


REFERENCE_CLARIFICATION = "请重新选择一个已确认的候选集合及其中一个分子（序号 1–32）。"


def scientific_intent(query):
    if re.search(r"什么是|是什么|是什么意思|解释|介绍|\b(?:explain|what\s+(?:is|are)|define)\b", query, re.I):
        return False
    # Generation can use a prior molecule. Resolve distinguishes source
    # references from output counts, independently of endpoint/router scores.
    if has_generation_intent(query):
        return True
    # Reuse the existing local routing policy, without an LLM or memory. A
    # docking-readiness search is not a request to calculate the selected ligand.
    if HybridSkillRouter().decide(query).selected_skill in {
        "target_database_search", "rag_search", "molecular_design", "target_driven_design",
    }:
        return False
    return (InputValidator().contains_calculation_request(query)
            or bool(re.search(r"对接|活性|性质|反向寻靶|体检|\b(?:dock(?:ing)?|admet|logp|qed)\b", query, re.I)))


def explicit_ordinal(query):
    # Only small, explicit singular ordinals; never guess a range or a list.
    noun = r"(?:candidates?|molecules?)"
    word = (
        r"(?:(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)[ -]+)?"
        r"(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|"
        r"eleventh|twelfth|thirteenth|fourteenth|fifteenth|sixteenth|seventeenth|"
        r"eighteenth|nineteenth|twentieth|thirtieth|fortieth|fiftieth|sixtieth|"
        r"seventieth|eightieth|ninetieth|hundredth|thousandth)"
    )
    number = rf"(?:#?\s*\d+|{word})"
    separator = r"(?:[-–—,，/&]|\band\b|\bor\b|\bto\b|\bthrough\b)"
    if re.search(rf"\b(?:{noun}\s*{number}|{word}(?:\s+{noun})?)\s*"
                 rf"{separator}\s*(?:{noun}\s*)?{number}\b", query, re.I):
        raise ValueError(REFERENCE_CLARIFICATION)
    chinese = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
               "七": 7, "八": 8, "九": 9, "十": 10}
    numbers = {str(i): i for i in range(1, 33)}
    for i in range(1, 33):
        if i <= 10:
            name = next(k for k, v in chinese.items() if v == i)
        else:
            name = ("" if i // 10 == 1 else next(k for k, v in chinese.items() if v == i // 10)) + "十"
            if i % 10:
                name += next(k for k, v in chinese.items() if v == i % 10)
        numbers[name] = i
    # Detect the complete ordinal phrase before parsing it. A spaced list or
    # unsupported form is an invalid reference, not an absent reference.
    phrases = re.findall(r"第([^，。；;\r\n]*?)(个|候选|分子)", query)
    if any(suffix != "个" for _, suffix in phrases):
        raise ValueError(REFERENCE_CLARIFICATION)
    matches = [value.strip() for value, _ in phrases]
    english_tokens = re.findall(
        r"\b(?:candidate|molecule)\s*(?:#\s*([\w.+-]*)|([+-]?\d[\w.+-]*))",
        query, re.I)
    # Keep complete tokens: candidate 1.5 / -1 / #unknown must not select #1
    # or become an absent ordinal. Only sentence-final periods are punctuation.
    english = [(marked or plain).rstrip(".") for marked, plain in english_tokens]
    words = re.findall(rf"\b({word})\s+{noun}\b", query, re.I)
    if (re.search(rf"\b\d+(?:st|nd|rd|th)\s+{noun}\b", query, re.I)
            or re.search(rf"\b{word}\s+(?:candidates|molecules)\b", query, re.I)
            or re.search(r"\b(?:candidates|molecules)\s*#?\s*\d", query, re.I)):
        raise ValueError(REFERENCE_CLARIFICATION)
    matches += english + [str({"first": 1, "second": 2, "third": 3}.get(w.lower())) for w in words]
    if not matches:
        return None
    if len(matches) != 1 or matches[0] not in numbers:
        raise ValueError(REFERENCE_CLARIFICATION)
    return numbers[matches[0]]


def prior_molecule_reference(query):
    # Source relations are references even without a known demonstrative: e.g.
    # "based on the previously optimized candidate". Do not cross a generation
    # verb into its output noun, or confuse a target source with a molecule.
    chinese_source = (
        r"(?:基于|根据|参照|参考|使用|利用|沿用|以|从)"
        # "使用分子生成工具" names the requested tool, not a prior molecule
        # reference.  Keep that noun phrase out of the source relation before
        # looking for a later candidate/molecule token.
        r"(?!(?:分子)?(?:生成|设计|创建|制作|构建|合成)(?:工具|模型|模块)?)"
        r"(?:(?!(?:生成|创建|设计|制作|构建|合成))[^，。；;\r\n]){0,64}?"
        r"(?:候选|分子(?!量))"
    )
    english_source = (
        r"\b(?:from|using|like|based\s+on)\s+"
        r"(?:(?!(?:and|then|targets?|generate|design|create|produce|make|build|synthesize)\b)"
        r"[a-z][a-z'’-]*\s+){0,8}(?:candidates?|molecules?)\b"
    )
    # Standalone source pronouns still need selection/ACK; determiners before
    # a target noun ("from these targets") must remain fresh generation.
    english_pronoun_source = (
        r"\b(?:from|using|like|based\s+on)\s+(?:these|those)"
        r"(?=\s*(?:[.,;!?]|$|\b(?:and|then|with)\b))"
    )
    # "Is it possible to generate ..." has no molecular source. Bind "it"
    # locally to a source/property relation or an action's object instead of
    # letting a bare grammatical subject borrow an old card's authority.
    english_it_relation = (
        r"\b(?:from|using|like|based\s+on|similar\s+to|for|of|on|with)\s+it\b|"
        r"\b(?:us(?:e|ing)|optimi[sz](?:e|ing)|modif(?:y|ying)|improv(?:e|ing)|"
        r"evaluat(?:e|ing)|assess(?:ing)?|analy[sz](?:e|ing)|dock(?:ing)?|"
        r"redesign(?:ing)?|calculat(?:e|ing)|predict(?:ing)?)"
        r"\s+(?:(?:also|only|further)\s+){0,2}it\b"
    )
    return bool(re.search(
        r"刚才|之前|上一个|前一个|(?:这|那)(?:个|些)(?:候选|分子)|"
        r"(?:上面|前面|上文|上次|上述|该|选中|已确认)的?\s*(?:候选|分子)|"
        r"\b(?:previous|earlier|"
        r"(?:above|selected|confirmed|last|former|latter|this|that|these|those)\s+"
        r"(?:candidates?|molecules?))\b|"
        + chinese_source + "|" + english_source + "|" + english_pronoun_source
        + "|" + english_it_relation, query, re.I))


def valid_pointer(value, *, manifest=False):
    fields = {"trace_id", "presentation_id", "revision"}
    if manifest:
        fields.add("ordered_keys")
    if type(value) is not dict or set(value) != fields:
        return False
    if any(type(value[k]) is not str or not 0 < len(value[k]) <= 128
           or not value[k].strip() for k in fields - {"ordered_keys"}):
        return False
    if not re.fullmatch(r"[0-9a-f]{64}", value["revision"]):
        return False
    if manifest:
        keys = value["ordered_keys"]
        if (type(keys) is not list or not 1 <= len(keys) <= 32
                or any(type(key) is not list or len(key) != 2
                       or any(type(v) is not str or not 0 < len(v) <= 128 for v in key)
                       for key in keys)
                or len({tuple(key) for key in keys}) != len(keys)):
            return False
    try:
        reference_json(value)
    except (TypeError, ValueError, RecursionError):
        return False
    return True


def view_reference(view):
    return {"trace_id": view["source_trace_id"], "presentation_id": view["presentation_id"],
            "revision": view["revision"], "ordered_keys": [
                [c["observation_id"], c["candidate"]["candidate_id"]]
                for c in view["ordered_candidates"]]}


def _display_compatible(event):
    """Conservative subset of the strict browser normalizer, not a second
    scientific validator. If uncertain, suppress references for the whole turn:
    a rejected event must never charge dedupe/budget for a later visible event.
    Keep the original legacy events and let the browser validate/display them.
    """
    def safe(value, limit, empty=False):
        return (type(value) is str and len(value) <= limit and (empty or value.strip())
                and not re.search(r"[\x00-\x1f\x7f\ud800-\udfff\U00010000-\U0010ffff]", value))
    nodes = text = 0
    def cloneable(value, depth=0):
        nonlocal nodes, text
        nodes += 1
        if depth > 6 or nodes > 512:
            return False
        if type(value) is str:
            text += len(value.encode("utf-16-le")) // 2
            return text <= 65536
        if value is None or type(value) is bool:
            return True
        if type(value) in (int, float):
            return math.isfinite(value)
        if type(value) not in (dict, list) or len(value) > 64:
            return False
        if type(value) is dict:
            if any(k in {"__proto__", "constructor", "prototype"} for k in value):
                return False
            return all(cloneable(v, depth + 1) for v in value.values())
        return all(cloneable(v, depth + 1) for v in value)
    try:
        if not safe(event["trace_id"], 128) or len(json.dumps(event, ensure_ascii=False)) > 240000:
            return False
        if any(not safe(event["source"][k], 128, True) for k in ("tool_name", "model_name")):
            return False
        data = event["candidate_set"]
        if event["source"]["status"] != data["status"]:
            return False
        if any(data[k] > 128 for k in ("requested_count", "valid_count", "unique_count", "invalid_count", "duplicate_count")):
            return False
        if not 1 <= len(data["candidates"]) <= 64 or len(data["rejected"]) > 64:
            return False
        for c in data["candidates"]:
            if not safe(c["original_smiles"], 512) or not safe(c["canonical_smiles"], 512):
                return False
            # Guaranteed browser-supported atoms. More exotic valid chemistry
            # still displays via legacy handling, without guessing eligibility.
            s = c["canonical_smiles"]
            s = re.sub(r"\[\d*(?:Cl|Br|[BCNOPSFIbcnops])(?:[A-Za-z0-9@+\-:.]*)\]", "C", s)
            s = s.replace("Cl", "C").replace("Br", "B")
            if not re.fullmatch(r"[BCNOPSFIbcnops0-9@+\-()=#.%\\/:*]+", s):
                return False
            if not cloneable(c["generation_provenance"]) or not cloneable(c["metadata"]):
                return False
        for rejected in data["rejected"]:
            fields = {"source_index", "smiles", "reason"}
            if set(rejected) not in (fields, fields | {"details"}):
                return False
            if not safe(rejected["smiles"], 512, rejected["reason"] == "invalid_smiles"):
                return False
            if "details" in rejected and (
                type(rejected["details"]) is not dict or not cloneable(rejected["details"])
            ):
                return False
        warnings = event["warnings"]
        return (len(warnings) <= 32 and all(safe(w, 256, True) for w in warnings)
                and text + sum(len(w.encode("utf-16-le")) // 2 for w in warnings) <= 65536)
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        return False


class ScientificReferenceService:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def _event(observation, trace_id):
        # One legacy projection, including its redaction/validation rules.
        from src.web.chat_handler import ChatHandler
        return ChatHandler._candidate_event_from_observation(observation, trace_id=trace_id)

    def project(self, result, *, session_id):
        if result.get("status") not in {None, "completed", "partial"}:
            return []
        sequence = result.get("tool_result_sequence")
        if type(sequence) is not list:
            return []
        trace = result.get("trace_id")
        try:
            sources = self.store.get_scientific_sources(trace, session_id=session_id) or []
        except Exception:
            sources = []  # scientific display still works without reference storage
        projected = []
        for observation in sequence:
            try:
                event = self._event(observation, trace)
                if event is not None:
                    projected.append((observation, event))
            except Exception:
                continue
        if any(not _display_compatible(event) for _, event in projected):
            return [event for _, event in projected]
        events, ids, smiles = [], set(), set()
        count = accepted = 0
        for observation, event in projected:
            events.append(event)
            fresh = [c for c in event["candidate_set"]["candidates"]
                     if c["candidate_id"] not in ids and c["canonical_smiles"] not in smiles]
            if not fresh or accepted >= 8 or count + len(fresh) > 32:
                continue  # reject entire event, do not charge its IDs or capacity
            accepted += 1
            count += len(fresh)
            ids.update(c["candidate_id"] for c in fresh)
            smiles.update(c["canonical_smiles"] for c in fresh)
            quality = observation.get("quality", {})
            matches = [s for s in sources
                       if s["step_id"] == quality.get("step_id")
                       and s["observation"].get("quality", {}).get("evidence_id") == quality.get("evidence_id")]
            if len(matches) != 1:
                continue
            source = matches[0]
            try:
                # Compare actual scientific data AND evidence, not just IDs or a digest.
                observed = dict(observation)
                # AgentResult's sequence adds a transport step_id; quality remains
                # the authoritative mapping, never the sequence's synthetic ID.
                observed.pop("step_id", None)
                if reference_json(observed) != reference_json(source["observation"]):
                    continue
                if event != self._event(source["observation"], trace):
                    continue
                view = self.store.publish_scientific_presentation(trace, session_id=session_id,
                    selections=[{"observation_id": source["observation_id"],
                                 "candidate_id": c["candidate_id"]} for c in fresh])
                # Publication revalidates within its own transaction. A changed source
                # between read and write must not attach a different scientific view.
                if (view and view["source_version"] == source["source_version"]
                        and [c["candidate"] for c in view["ordered_candidates"]] == fresh):
                    event["reference"] = view_reference(view)
            except Exception:
                continue
        return events

    def confirm(self, payload, *, session_id):
        if not valid_pointer(payload, manifest=True):
            return False
        try:
            return self.store.confirm_scientific_presentation(**payload, session_id=session_id)
        except Exception:
            return False

    def get(self, pointer, *, session_id):
        if not valid_pointer(pointer):
            return None
        try:
            return self.store.get_scientific_presentation(**pointer, session_id=session_id)
        except Exception:
            return None

    def restore(self, pointer, *, session_id):
        view = self.get(pointer, session_id=session_id)
        if not view:
            return None
        try:
            sources = self.store.get_scientific_sources(pointer["trace_id"], session_id=session_id) or []
            by_id = {s["observation_id"]: s for s in sources}
            events = []
            reference = view_reference(view)
            for oid in dict.fromkeys(key[0] for key in reference["ordered_keys"]):
                event = self._event(by_id[oid]["observation"], pointer["trace_id"])
                if event is None:
                    return None
                events.append({**event, "reference": reference})
            # Re-read after retrieving source events to reject revocation races.
            if self.get(pointer, session_id=session_id) != view:
                return None
            from src.web.chat_handler import ChatHandler
            return {"reference": reference, "events": events,
                    "source_status": view["source_status"],
                    "warnings": ChatHandler._sanitize_agent_warnings(view["warnings"]),
                    "expires_at": view["expires_at"]}
        except Exception:
            return None

    def continuation_query(self, query, resolved_molecule=None, *, session_id):
        """Carry a confirmed source target into an otherwise target-free optimization.

        Call with the original current query, before routing_query adds SMILES.
        This is a server-only boundary, not browser metadata or history recovery.
        """
        if resolved_molecule is None:
            return query
        if type(query) is not str or type(resolved_molecule) is not ResolvedScientificMolecule:
            raise ValueError(REFERENCE_CLARIFICATION)
        pointer = resolved_molecule.pointer()
        view = self.get(pointer, session_id=session_id)
        if not view or not any(
                row["observation_id"] == resolved_molecule.observation_id
                and row["candidate"]["candidate_id"] == resolved_molecule.candidate_id
                and row["candidate"]["canonical_smiles"] == resolved_molecule.canonical_smiles
                for row in view["ordered_candidates"]):
            raise ValueError(REFERENCE_CLARIFICATION)
        optimization = r"优化|\b(?:optimi[sz](?:e|ation|ing)|hit-to-lead)\b"
        if (not re.search(optimization, query, re.I)
                or re.search(r"(?:不(?:要|需要)?|无需|别|勿|停止)\s*(?:再)?\s*优化|"
                             r"\b(?:not|never|don't)\s+(?:\w+\s+){0,3}optimi[sz]|"
                             r"解释|介绍|什么是|\b(?:explain|define|what\s+(?:is|are))\b",
                             query, re.I)
                or InputValidator().analyze_molecular_input(query).potential_smiles):
            return query
        request = analyze_target_request(query)
        # Even an unknown or incomplete current target instruction wins over
        # inheritance. Let the existing target boundary clarify it normally.
        if (request.targets or request.unknown or request.explicit or request.qualified
                or re.search(r"靶点|针对|\b(?:targets?|targeting|against)\b", query, re.I)):
            return query
        target = canonical_target_identifier(view["target"])
        if target is None:
            return query
        # Reject a revocation/source change during the read/analysis window.
        if self.get(pointer, session_id=session_id) != view:
            raise ValueError(REFERENCE_CLARIFICATION)
        return query + "\n靶点: " + target

    def resolve(self, query, pointer=None, selection=None, *, session_id, enable_tools):
        if not enable_tools or not scientific_intent(query):
            return None
        # New valid OR invalid explicit structure stays authoritative and goes
        # through the normal input validator. Never rescue it with an old molecule.
        if InputValidator().analyze_molecular_input(query).potential_smiles:
            return None
        ordinal = explicit_ordinal(query)
        prior = prior_molecule_reference(query)
        # "Generate three candidates" names outputs, not candidate #3. A stale
        # browser card must not turn a new generation request into a reference.
        # Explicit ordinals/deictic references still require the confirmed view.
        if has_generation_intent(query) and ordinal is None and not prior:
            return None
        refers = prior or bool(re.search(r"候选|\b(?:candidate|molecule)\b", query, re.I))
        if pointer is None and selection is None and ordinal is None and not refers:
            return None
        view = self.get(pointer, session_id=session_id)
        if not view:
            raise ValueError(REFERENCE_CLARIFICATION)
        rows = view["ordered_candidates"]
        row = None
        if selection is not None:
            if type(selection) is not dict:
                raise ValueError(REFERENCE_CLARIFICATION)
            if set(selection) == {"ordinal"} and type(selection["ordinal"]) is int:
                selected = selection["ordinal"]
                if ordinal is not None and ordinal != selected:
                    raise ValueError(REFERENCE_CLARIFICATION)
                ordinal = selected
            elif set(selection) == {"observation_id", "candidate_id"}:
                row = next((r for r in rows if r["observation_id"] == selection["observation_id"]
                            and r["candidate"]["candidate_id"] == selection["candidate_id"]), None)
                if row is None or (ordinal is not None and (not 1 <= ordinal <= len(rows) or rows[ordinal - 1] != row)):
                    raise ValueError(REFERENCE_CLARIFICATION)
            else:
                raise ValueError(REFERENCE_CLARIFICATION)
        if row is None:
            if ordinal is None or not 1 <= ordinal <= min(32, len(rows)):
                raise ValueError(REFERENCE_CLARIFICATION)
            row = rows[ordinal - 1]
        return ResolvedScientificMolecule(**pointer, observation_id=row["observation_id"],
            candidate_id=row["candidate"]["candidate_id"], canonical_smiles=row["candidate"]["canonical_smiles"])
