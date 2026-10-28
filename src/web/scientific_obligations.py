"""Server-owned, typed compilation of small scientific web obligations.

This module only validates and describes work.  It never selects values from
model output and never executes a workflow tool.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import replace
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationError,
    field_validator,
    model_validator,
)

from src.agent.orchestrators.base import WorkflowStep
from src.agent.planning.request_parsing import positive_intent_text
from src.agent.tools.base_tool import BaseMolecularTool
from src.agent.tools.molecular_input import (
    MolecularInputMissing,
    MolecularInputUnavailable,
)
from src.system.molecular_input_parser import parse_molecular_inputs


MAX_QUERY_CHARS = 8192
# Keep the original public name available for callers that used the first
# compiler draft; all validation uses the character-bound constant above.
MAX_QUERY_LENGTH = MAX_QUERY_CHARS
WORKFLOW_VERSION = "web-property-v1"
ADMET_WORKFLOW_VERSION = "web-admet-v1"
WorkflowVersion = Literal["web-property-v1", "web-admet-v1"]
Metric = Literal[
    "logp",
    "tpsa",
    "mw",
    "qed",
    "hba",
    "hbd",
    "rotatable_bonds",
    "formula",
]
_STANDARD_METRICS: tuple[Metric, ...] = (
    "logp",
    "tpsa",
    "mw",
    "qed",
    "hba",
    "hbd",
    "rotatable_bonds",
)
_REVIEWED_TOOL_NAMES = frozenset(
    {"property_calculator", "drug_likeness_assessment", "admet_predictor"}
)
_REVIEWED_STEP_SEMANTICS: dict[str, dict[str, Any]] = {
    "property_calculator": {
        "name": "properties",
        "output_key": "properties",
        "capability": "molecular_properties",
    },
    "drug_likeness_assessment": {
        "name": "drug_likeness",
        "output_key": "drug_likeness",
        "capability": "drug_likeness",
    },
    "admet_predictor": {
        "name": "admet",
        "output_key": "admet",
        "capability": "molecule.admet",
    },
}
_COMPILED_CANONICAL: ContextVar[str | None] = ContextVar(
    "scientific_obligation_compiled_canonical",
    default=None,
)


class ScientificObligationError(ValueError):
    """Raised when a caller attempts to provide server-owned obligations."""


class _ImmutableMetadata(dict[str, Any]):
    """Dict-compatible metadata that cannot be mutated through the plan."""

    def __setitem__(self, key: str, value: Any) -> None:
        raise TypeError("ScientificPlan step metadata is immutable")

    def __delitem__(self, key: str) -> None:
        raise TypeError("ScientificPlan step metadata is immutable")

    def clear(self) -> None:
        raise TypeError("ScientificPlan step metadata is immutable")

    def pop(self, key: str, default: Any = None) -> Any:
        raise TypeError("ScientificPlan step metadata is immutable")

    def popitem(self) -> tuple[str, Any]:
        raise TypeError("ScientificPlan step metadata is immutable")

    def setdefault(self, key: str, default: Any = None) -> Any:
        raise TypeError("ScientificPlan step metadata is immutable")

    def update(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("ScientificPlan step metadata is immutable")

    def __ior__(self, other: Mapping[str, Any]) -> "_ImmutableMetadata":
        raise TypeError("ScientificPlan step metadata is immutable")


class ScientificPlan(BaseModel):
    """Immutable result of the server-owned scientific obligation compiler."""

    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
    )

    status: Literal["ready", "clarification", "rejected"]
    original_query: str
    smiles: str | None = None
    canonical_smiles: str | None = None
    metrics: tuple[Metric, ...] = ()
    required_tools: tuple[str, ...] = ()
    missing_fields: tuple[str, ...] = ()
    missing_capabilities: tuple[str, ...] = ()
    message: str = ""
    steps: tuple[WorkflowStep, ...] = ()
    workflow_version: WorkflowVersion = WORKFLOW_VERSION

    @field_validator("original_query")
    @classmethod
    def _validate_query_length(cls, value: str) -> str:
        if len(value) > MAX_QUERY_CHARS:
            raise ValueError(
                f"original_query exceeds the maximum length of {MAX_QUERY_CHARS} characters"
            )
        return value

    @model_validator(mode="before")
    @classmethod
    def _accept_json_tuple_round_trip(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        payload = dict(value)
        for field_name in (
            "metrics",
            "required_tools",
            "missing_fields",
            "missing_capabilities",
            "steps",
        ):
            field_value = payload.get(field_name)
            if isinstance(field_value, list):
                payload[field_name] = tuple(field_value)
        return payload

    @field_validator("steps", mode="before")
    @classmethod
    def _freeze_step_metadata(cls, value: Any) -> Any:
        if value is None:
            return value
        frozen_steps: list[WorkflowStep] = []
        for step in value:
            if isinstance(step, Mapping):
                step_values = dict(step)
                preconditions = step_values.get("preconditions")
                if isinstance(preconditions, list):
                    step_values["preconditions"] = tuple(preconditions)
                try:
                    step = WorkflowStep(**step_values)
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        "steps must contain valid WorkflowStep values"
                    ) from exc
            if not isinstance(step, WorkflowStep):
                raise TypeError("steps must contain WorkflowStep instances")
            if step.metadata:
                raise ValueError("ScientificPlan step metadata must be empty")
            frozen_steps.append(
                replace(step, metadata=_ImmutableMetadata())
            )
        return tuple(frozen_steps)

    @staticmethod
    def _canonicalize_direct_smiles(smiles: str) -> str:
        if type(smiles) is not str or not smiles or smiles.strip() != smiles:
            raise ValueError("ready ScientificPlan smiles must be a non-empty SMILES string")
        if any(character.isspace() for character in smiles):
            raise ValueError("ready ScientificPlan smiles must not contain whitespace")
        try:
            from rdkit import Chem, rdBase

            params = Chem.SmilesParserParams()
            params.parseName = False
            params.allowCXSMILES = False
            with rdBase.BlockLogs():
                molecule = Chem.MolFromSmiles(smiles, params)
            if molecule is None or not molecule.GetNumAtoms():
                raise ValueError("ready ScientificPlan smiles is not a valid molecule")
            canonical = Chem.MolToSmiles(molecule, canonical=True)
        except ImportError as exc:
            raise ValueError(
                "ready ScientificPlan molecule validation requires RDKit"
            ) from exc
        except ValueError:
            raise ValueError(
                "ready ScientificPlan smiles is not a valid molecule"
            ) from None
        except Exception as exc:
            raise ValueError("ready ScientificPlan smiles is not a valid molecule") from exc
        if not canonical:
            raise ValueError("ready ScientificPlan smiles is not a valid molecule")
        return canonical

    @staticmethod
    def _step_field_is_unset(value: Any) -> bool:
        return value is None or value == ""

    @classmethod
    def _validate_reviewed_step(cls, step: WorkflowStep, smiles: str) -> None:
        semantics = _REVIEWED_STEP_SEMANTICS.get(step.tool_name)
        if semantics is None:
            raise ValueError("ScientificPlan steps must use only reviewed tools")
        if step.name != semantics["name"]:
            raise ValueError("ScientificPlan step name does not match reviewed semantics")
        if type(step.input_data) is not str or step.input_data != smiles:
            raise ValueError(
                "ScientificPlan step input_data must equal the exact plan smiles"
            )
        for field_name in ("input_from", "input_binding", "input_template"):
            if not cls._step_field_is_unset(getattr(step, field_name)):
                raise ValueError(
                    f"ScientificPlan step {field_name} must be empty for reviewed tools"
                )
        if type(step.required) is not bool or step.required is not True:
            raise ValueError("ScientificPlan reviewed steps must be required")
        expected = {
            "output_key": semantics["output_key"],
            "capability": semantics["capability"],
            "continue_on_error": None,
            "timeout_seconds": None,
            "input_transform": "identity",
            "output_contract": None,
            "preconditions": (),
        }
        for field_name, expected_value in expected.items():
            if getattr(step, field_name) != expected_value:
                raise ValueError(
                    f"ScientificPlan step {field_name} does not match reviewed semantics"
                )
        if dict(step.metadata) != {}:
            raise ValueError("ScientificPlan step metadata must be empty")

    @model_validator(mode="after")
    def _validate_step_input_boundary(self) -> "ScientificPlan":
        if self.status != "ready":
            if self.steps:
                raise ValueError(
                    "clarification and rejected ScientificPlan values cannot contain steps"
                )
            if self.required_tools:
                raise ValueError(
                    "clarification and rejected ScientificPlan values cannot contain tools"
                )
            if self.smiles is not None or self.canonical_smiles is not None or self.metrics:
                raise ValueError(
                    "clarification and rejected ScientificPlan values cannot contain ready fields"
                )
            if self.status == "clarification" and self.missing_capabilities:
                raise ValueError(
                    "clarification ScientificPlan values may only contain missing_fields"
                )
            if self.status == "rejected" and self.missing_fields:
                raise ValueError(
                    "rejected ScientificPlan values may only contain missing_capabilities"
                )
            return self

        if self.status == "ready":
            if not self.smiles:
                raise ValueError("ready ScientificPlan requires non-empty smiles")
            if not self.canonical_smiles:
                raise ValueError(
                    "ready ScientificPlan requires non-empty canonical_smiles"
                )
            if not self.metrics and tuple(self.required_tools) != ("admet_predictor",):
                raise ValueError("ready ScientificPlan requires non-empty metrics")
            if not self.required_tools:
                raise ValueError(
                    "ready ScientificPlan requires non-empty required_tools"
                )
            if not self.steps:
                raise ValueError("ready ScientificPlan requires non-empty steps")
            if self.missing_fields or self.missing_capabilities:
                raise ValueError(
                    "ready ScientificPlan cannot contain missing fields or capabilities"
                )
            has_admet = "admet_predictor" in self.required_tools
            expected_workflow = (
                ADMET_WORKFLOW_VERSION if has_admet else WORKFLOW_VERSION
            )
            if self.workflow_version != expected_workflow:
                raise ValueError(
                    "ready ScientificPlan workflow_version does not match reviewed tools"
                )

            compiled_canonical = _COMPILED_CANONICAL.get()
            if compiled_canonical is None:
                computed_canonical = self._canonicalize_direct_smiles(self.smiles)
            else:
                if type(compiled_canonical) is not str or not compiled_canonical:
                    raise ValueError("compiled ScientificPlan canonical identity is invalid")
                computed_canonical = compiled_canonical
            if computed_canonical != self.canonical_smiles:
                raise ValueError(
                    "ready ScientificPlan canonical_smiles does not match smiles"
                )

        if any(tool_name not in _REVIEWED_TOOL_NAMES for tool_name in self.required_tools):
            raise ValueError(
                "ScientificPlan required_tools must use only reviewed tools"
            )
        if len(set(self.required_tools)) != len(self.required_tools):
            raise ValueError("ScientificPlan required_tools must not contain duplicates")

        step_tool_names = tuple(step.tool_name for step in self.steps)
        if any(tool_name not in _REVIEWED_TOOL_NAMES for tool_name in step_tool_names):
            raise ValueError("ScientificPlan steps must use only reviewed tools")
        if len(set(step_tool_names)) != len(step_tool_names):
            raise ValueError("ScientificPlan steps must not contain duplicate tools")
        if tuple(self.required_tools) != step_tool_names:
            raise ValueError(
                "ScientificPlan required_tools and steps must use reviewed order"
            )

        required_tools = set(self.required_tools)
        for step in self.steps:
            if step.tool_name not in required_tools:
                raise ValueError(
                    "ScientificPlan step tool_name must be present in required_tools"
                )
            self._validate_reviewed_step(step, self.smiles)
        return self


ScientificPlan.model_rebuild(_types_namespace={"Any": Any, "WorkflowStep": WorkflowStep})


_METRIC_PATTERNS: tuple[tuple[Metric, tuple[re.Pattern[str], ...]], ...] = (
    (
        "logp",
        (re.compile(r"(?<![A-Za-z0-9_])log\s*p(?![A-Za-z0-9_])", re.I),),
    ),
    (
        "tpsa",
        (re.compile(r"(?<![A-Za-z0-9_])tpsa(?![A-Za-z0-9_])", re.I),),
    ),
    (
        "mw",
        (
            re.compile(r"(?<![A-Za-z0-9_])mw(?![A-Za-z0-9_])", re.I),
            re.compile(r"molecular\s+weight", re.I),
            re.compile(r"分子量"),
        ),
    ),
    (
        "qed",
        (re.compile(r"(?<![A-Za-z0-9_])qed(?![A-Za-z0-9_])", re.I),),
    ),
    (
        "hba",
        (re.compile(r"(?<![A-Za-z0-9_])hba(?![A-Za-z0-9_])", re.I),),
    ),
    (
        "hbd",
        (re.compile(r"(?<![A-Za-z0-9_])hbd(?![A-Za-z0-9_])", re.I),),
    ),
    (
        "rotatable_bonds",
        (
            re.compile(r"rotatable[_\s-]+bonds?", re.I),
            re.compile(r"可旋转(?:的)?键(?:数)?"),
        ),
    ),
    (
        "formula",
        (
            re.compile(r"molecular\s+formula", re.I),
            re.compile(r"分子式"),
        ),
    ),
)
_PROPERTY_REQUEST = re.compile(
    r"分子(?:的)?(?:理化)?(?:性质|属性)|理化性质|描述符|"
    r"molecular\s+(?:properties?|descriptors?)|"
    r"\b(?:properties?|descriptors?|property_calculator)\b",
    re.I,
)
_NAMED_MOLECULE_HINT = re.compile(
    r"[\u4e00-\u9fff]{2,32}的(?:分子|理化|类药|成药|LogP|TPSA|QED|分子量)|"
    r"\b(?:of|for)\s+[A-Za-z][A-Za-z0-9 -]{1,40}(?=$|[,.!?;，。；])",
    re.I,
)
_DRUG_LIKENESS_REQUEST = re.compile(
    r"类药性|成药性|药物相似性|drug[-\s]?likeness|druglike|"
    r"lipinski|rule\s+of\s+five|五规则",
    re.I,
)
_PROFILE_ACTION = re.compile(
    r"计算|分析|评估|预测|返回|报告|给出|展示|显示|"
    r"\b(?:calculate|compute|evaluate|predict|analy[sz]e|report|return|show)\b",
    re.I,
)
_UNREVIEWED_METRICS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("logd", re.compile(r"(?<![A-Za-z0-9_])log\s*d(?![A-Za-z0-9_])", re.I)),
    ("pic50", re.compile(r"(?<![A-Za-z0-9_])p\s*ic50(?![A-Za-z0-9_])", re.I)),
    ("pki", re.compile(r"(?<![A-Za-z0-9_])p\s*ki(?![A-Za-z0-9_])", re.I)),
    ("pkd", re.compile(r"(?<![A-Za-z0-9_])p\s*kd(?![A-Za-z0-9_])", re.I)),
    ("pec50", re.compile(r"(?<![A-Za-z0-9_])p\s*ec50(?![A-Za-z0-9_])", re.I)),
    ("ic50", re.compile(r"(?<![A-Za-z0-9_])ic50(?![A-Za-z0-9_])", re.I)),
    ("activity", re.compile(r"(?<![A-Za-z0-9_])activity(?![A-Za-z0-9_])|活性", re.I)),
    ("potency", re.compile(r"(?<![A-Za-z0-9_])potency(?![A-Za-z0-9_])|效力", re.I)),
    ("solubility", re.compile(r"(?<![A-Za-z0-9_])solubility(?![A-Za-z0-9_])|溶解度", re.I)),
    ("permeability", re.compile(r"(?<![A-Za-z0-9_])permeability(?![A-Za-z0-9_])|渗透性", re.I)),
    ("affinity", re.compile(r"(?<![A-Za-z0-9_])(?:binding\s+)?affinity(?![A-Za-z0-9_])|亲和力|结合亲和力", re.I)),
    ("logs", re.compile(r"(?<![A-Za-z0-9_])log\s*s(?![A-Za-z0-9_])", re.I)),
)
_METRIC_LIST_INTRODUCER = re.compile(
    r"\b(?:including|include|report|reports|return|returns|calculate|compute|"
    r"measure|predict|evaluate|and|contains?|with|plus)\b|"
    r"包括|包含|返回|报告|计算|测量|预测|评估|给出|并|以及|和",
    re.I,
)
_METRIC_LIST_END = re.compile(
    r"\b(?:for|from|of|on|with|by|using|based\s+on)\b|的|用于|针对|[。.!?；;\n]",
    re.I,
)
_METRIC_LIST_FILLER = frozenset(
    {
        "and",
        "or",
        "this",
        "molecule",
        "s",
        "as",
        "also",
        "assess",
        "assessment",
        "calculate",
        "compute",
        "contains",
        "contain",
        "evaluate",
        "include",
        "including",
        "measure",
        "predict",
        "report",
        "reports",
        "return",
        "returns",
        "explain",
        "explains",
        "explanation",
        "why",
        "matters",
        "show",
        "me",
        "tell",
        "describe",
        "result",
        "results",
        "rdkit",
        "with",
        "plus",
        "the",
        "a",
        "an",
        "properties",
        "property",
        "descriptors",
        "descriptor",
        "metrics",
        "metric",
        "指标",
        "分子性质",
        "分子属性",
        "这个分子",
        "类药性",
        "成药性",
        "药物相似性",
        "drug",
        "likeness",
        "druglike",
        "lipinski",
        "rule",
        "of",
        "five",
        "和",
        "或",
        "以及",
        "并",
        "评估",
        "解释",
        "说明",
        "展示",
        "显示",
        "结果",
        "解释结果",
        "包括",
        "包含",
        "返回",
        "报告",
        "计算",
        "测量",
        "预测",
        "给出",
        "请",
    }
)
_METRIC_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_-]*|[\u4e00-\u9fff]+")
_ADMET_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])admet?(?![A-Za-z0-9_])|"
    r"absorption|distribution|metabolism|excretion|toxicity|toxic|"
    r"吸收|分布|代谢|排泄|毒性",
    re.I,
)
_UNSUPPORTED_CAPABILITIES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "admet",
        _ADMET_PATTERN,
    ),
    ("docking", re.compile(r"docking|molecular\s+docking|对接", re.I)),
    (
        "generation",
        re.compile(
            r"molecule\s+generation|molecular\s+generation|"
            r"molecule[_\s-]+generation|molecular[_\s-]+design|"
            r"create[_\s-]+molecules?|creating[_\s-]+molecules?|"
            r"(?<![A-Za-z0-9_])(?:design|generate|generation|synthesize|synthesis)(?![A-Za-z0-9_])|"
            r"生成|设计|创建分子|创建设计|分子设计|合成",
            re.I,
        ),
    ),
    (
        "ranking",
        re.compile(
            r"candidate\s+ranking|(?<![A-Za-z0-9_])(?:rank|ranking|screen|candidate)(?![A-Za-z0-9_])|"
            r"候选(?:排序|排名)|筛选|排序|排名",
            re.I,
        ),
    ),
    (
        "target_search",
        re.compile(
            r"target[-_\s]+(?:search|database)|(?:find|search)\s+targets?|"
            r"(?:discover|discovery|identify|identification|finding|find)\s+targets?|"
            r"target\s+(?:discovery|identification|finding)|"
            r"靶点(?:检索|搜索|数据库|查找|发现|识别|鉴定)|寻找靶点|发现靶点|靶点识别|靶点鉴定",
            re.I,
        ),
    ),
    (
        "reverse_target",
        re.compile(r"reverse[-_\s]?target|target\s+deconvolution|反向寻靶|反向靶点|寻靶", re.I),
    ),
)


def _requested_metrics(query: str) -> tuple[Metric, ...]:
    requested = tuple(
        metric
        for metric, patterns in _METRIC_PATTERNS
        if any(pattern.search(query) for pattern in patterns)
    )
    if requested:
        return requested
    if _metric_list_is_explicit(query):
        return ()
    if _PROPERTY_REQUEST.search(query):
        return _STANDARD_METRICS
    return ()


def _unreviewed_metric_capabilities(query: str) -> tuple[str, ...]:
    capabilities = [
        f"metric:{metric}"
        for metric, pattern in _UNREVIEWED_METRICS
        if pattern.search(query)
    ]
    # Unsupported obligations own their capability error. Do not reinterpret
    # their domain nouns as generic metric names while scanning mixed requests.
    if _unsupported_capabilities(query):
        return tuple(dict.fromkeys(capabilities))
    if _metric_list_has_unknown_token(query):
        capabilities.append("metric:unreviewed")
    return tuple(dict.fromkeys(capabilities))


def _metric_list_is_explicit(query: str) -> bool:
    for introducer in _METRIC_LIST_INTRODUCER.finditer(query):
        # Action verbs such as "计算" often precede the property noun and
        # the named molecule ("计算阿司匹林的分子性质").  That is a request
        # introducer, not an explicit metric-list introducer; let the normal
        # property request/missing-SMILES path handle it.
        property_after_introducer = _PROPERTY_REQUEST.search(
            query, introducer.end()
        )
        if property_after_introducer is not None:
            continue
        clause = query[introducer.end():]
        end = _METRIC_LIST_END.search(clause)
        if end:
            clause = clause[:end.start()]
        for token_match in _METRIC_TOKEN.finditer(clause):
            token = token_match.group()
            if token.casefold() in _METRIC_LIST_FILLER:
                continue
            if any(
                pattern.fullmatch(token) or pattern.search(token)
                for _, patterns in _METRIC_PATTERNS
                for pattern in patterns
            ):
                return True
            if any(
                pattern.fullmatch(token) or pattern.search(token)
                for _, pattern in _UNREVIEWED_METRICS
            ):
                return True
            if (
                introducer.group().casefold()
                in {"including", "include", "包括", "包含"}
                and not (token.isupper() and len(token) <= 8)
            ):
                return True
    return False


def _metric_list_has_unknown_token(query: str) -> bool:
    """Reject explicit metric lists instead of treating unknown terms as prose."""
    # Once a property request is present, the text following that request is
    # an explicit metric context.  In particular, punctuation is not a safe
    # escape hatch: ``properties. FooMetric`` and ``properties, FooTask`` must
    # fail closed just like ``properties and FooMetric``.
    for property_request in _PROPERTY_REQUEST.finditer(query):
        clause = query[property_request.end():]
        # A named molecule commonly follows a property request ("properties
        # of aspirin" or "分子性质的阿司匹林").  The name is not a metric, so
        # stop the unknown-metric scan at that grammatical object boundary.
        # Keep punctuation boundaries active: "properties. FooMetric" must
        # remain a rejected explicit unknown metric.
        object_boundary = re.search(
            r"\b(?:for|from|of|on|by|using)\b|的|用于", clause, re.I
        )
        if object_boundary:
            clause = clause[: object_boundary.start()]
        clause = re.split(
            r"\b(?:and|or)\s+(?:explain|show|tell|describe)\b|"
            r"(?:并且?|以及)?(?:解释|说明|展示|显示)",
            clause,
            maxsplit=1,
            flags=re.I,
        )[0]
        for _, patterns in _METRIC_PATTERNS:
            for pattern in patterns:
                clause = pattern.sub(" ", clause)
        clause = _DRUG_LIKENESS_REQUEST.sub(" ", clause)
        clause = _METRIC_LIST_INTRODUCER.sub(" ", clause)
        for token_match in _METRIC_TOKEN.finditer(clause):
            token = token_match.group()
            normalized = token.casefold()
            if any(
                pattern.fullmatch(token) or pattern.search(token)
                for _, pattern in _UNREVIEWED_METRICS
            ):
                continue
            if normalized in _METRIC_LIST_FILLER or normalized in {
                "analyze",
                "analyse",
                "please",
                "show",
                "display",
                "list",
                "output",
                "result",
                "results",
                "property",
                "properties",
                "descriptor",
                "descriptors",
        "for",
                "from",
                "on",
                "by",
                "using",
        "explain",
        "explains",
        "explanation",
        "result",
        "results",
        "rdkit",
                "based",
                "根据",
                "针对",
                "用于",
            }:
                continue
            # A short all-uppercase token immediately after the property
            # context is commonly the molecule itself (for example CCO).
            if token.isupper() and len(token) <= 8:
                continue
            return True

    for introducer in _METRIC_LIST_INTRODUCER.finditer(query):
        # Do not interpret a leading action verb as a metric list when the
        # request's property noun appears later (for example, "计算阿司匹林
        # 的分子性质").  The property compiler will then issue the normal
        # missing-SMILES clarification.
        property_after_introducer = _PROPERTY_REQUEST.search(
            query, introducer.end()
        )
        if property_after_introducer is not None:
            continue
        clause = query[introducer.end():]
        # The bare subject in "计算分子的分子量" is not a metric. Consume
        # only this complete leading qualifier, so metrics after 的 (including
        # unknown ones) are still scanned rather than hidden by that boundary.
        clause = re.sub(r"^\s*分子\s*的\s*", "", clause, count=1)
        end = _METRIC_LIST_END.search(clause)
        if end:
            clause = clause[:end.start()]
        # Multi-word reviewed metrics and the reviewed drug-likeness phrase
        # are atomic safe terms. Remove them before generic token inspection.
        for _, patterns in _METRIC_PATTERNS:
            for pattern in patterns:
                clause = pattern.sub(" ", clause)
        clause = _DRUG_LIKENESS_REQUEST.sub(" ", clause)
        unknown_token_found = False
        recognized_metric_found = False
        for token_match in _METRIC_TOKEN.finditer(clause):
            token = token_match.group()
            normalized = token.casefold()
            if normalized in _METRIC_LIST_FILLER:
                continue
            if any(pattern.fullmatch(token) or pattern.search(token) for _, patterns in _METRIC_PATTERNS for pattern in patterns):
                recognized_metric_found = True
                continue
            if any(pattern.fullmatch(token) or pattern.search(token) for _, pattern in _UNREVIEWED_METRICS):
                recognized_metric_found = True
                continue
            # Short all-uppercase tokens are usually the adjacent SMILES input
            # (CCO, CCN), not an unreviewed metric name.
            if token.isupper() and len(token) <= 8:
                continue
            unknown_token_found = True
        if unknown_token_found and (
            recognized_metric_found
            or introducer.group().casefold() in {
                "including",
                "include",
                "包括",
                "包含",
                "and",
                "contains",
                "contain",
                "with",
                "plus",
                "并",
                "以及",
                "和",
                "report",
                "reports",
                "return",
                "returns",
                "calculate",
                "compute",
                "measure",
                "predict",
                "evaluate",
                "返回",
                "报告",
                "计算",
                "测量",
                "预测",
                "评估",
                "给出",
            }
        ):
            return True
    return False


def _explicit_admet_request(query: str) -> bool:
    return bool(
        _ADMET_PATTERN.search(query)
        and (
            _PROPERTY_REQUEST.search(query)
            or _DRUG_LIKENESS_REQUEST.search(query)
            or _PROFILE_ACTION.search(query)
        )
    )


def _activity_agent_request(query: str) -> bool:
    return any(
        metric in {"pic50", "ic50", "activity", "potency"}
        and pattern.search(query)
        for metric, pattern in _UNREVIEWED_METRICS
    )


def _requested_tools(
    query: str,
    metrics: tuple[Metric, ...],
    *,
    admet_requested: bool = False,
) -> tuple[str, ...]:
    tools: list[str] = []
    if metrics:
        tools.append("property_calculator")
    if _DRUG_LIKENESS_REQUEST.search(query):
        tools.append("drug_likeness_assessment")
    if admet_requested:
        tools.append("admet_predictor")
    return tuple(tools)


def _unsupported_capabilities(query: str) -> tuple[str, ...]:
    return tuple(
        capability
        for capability, pattern in _UNSUPPORTED_CAPABILITIES
        if pattern.search(query)
    )


class ScientificObligationCompiler:
    """Compile a bounded user query into a non-executable scientific plan."""

    MAX_QUERY_CHARS = MAX_QUERY_CHARS
    MAX_QUERY_LENGTH = MAX_QUERY_CHARS

    @staticmethod
    def is_bare_generic_molecule_request(query: str) -> bool:
        """Recognize a complete generic subject, never just its leading words.

        A later named/arbitrary subject cannot be replaced by a SMILES supplied
        on Continue. Keep this grammar bounded to the existing bare calculation
        form and reuse the compiler's reviewed metric/capability vocabulary.
        """
        terms = [pattern.pattern for _, patterns in _METRIC_PATTERNS for pattern in patterns]
        terms += [_PROPERTY_REQUEST.pattern, _DRUG_LIKENESS_REQUEST.pattern, _ADMET_PATTERN.pattern]
        term = '(?:' + '|'.join(terms) + ')'
        return re.fullmatch(
            r'\s*(?:请\s*)?计算\s*分子\s*的\s*' + term
            + r'(?:\s*(?:和|以及|及|与|、|，|,|and)\s*' + term + r')*\s*[。.!?！？]?\s*',
            query, flags=re.I,
        ) is not None

    def clarification_tools(self, query: str) -> tuple[str, ...]:
        """Freeze the same reviewed obligations before the subject is supplied."""
        if self.compile(query).status != 'clarification':
            raise ScientificObligationError('Molecular clarification is not applicable.')
        return _requested_tools(query, _requested_metrics(query),
                                admet_requested=_explicit_admet_request(query))

    @staticmethod
    def is_profile_candidate(query: str) -> bool:
        """Return whether the bounded property profile should own this request.

        Metric words alone also occur in explanatory chat (for example,
        ``解释 LogP 是什么``).  The Web runtime must leave those requests to
        the ordinary Pi path; an action verb or an explicit property/drug-
        likeness noun is required before this compiler can become authoritative.
        """
        if not isinstance(query, str):
            return False
        from src.agent.planning.composite_request import (
            parse_target_structure_molecule_request,
        )
        if parse_target_structure_molecule_request(query) is not None:
            # The dedicated composite workflow owns this request; the
            # property-only compiler must not reject it or drop target lookup.
            return False
        from src.agent.planning.request_parsing import looks_like_design
        if (looks_like_design(query)
                and re.search(r"优化|optimi[sz]e|lead\s+optimization|降低|提高|改善", query, re.I)):
            # Lead optimization already has a typed multi-step workflow. Do
            # not let the property-only compiler reject its generation bound.
            return False
        from src.agent.contracts.property_ranking_request import property_only_design_request
        if property_only_design_request(query):
            return False
        positive = positive_intent_text(query)
        if (looks_like_design(query)
                and not _PROPERTY_REQUEST.search(query)
                and not _explicit_admet_request(query)
                and not _unreviewed_metric_capabilities(query)):
            # Lipinski/LogP/weight bounds are generation constraints when the
            # user asks to generate molecules. Do not let the narrow property
            # compiler reject the request before molecular_design can invoke
            # the dedicated generator tool.
            return False
        from src.agent.evidence_followup import comparison_tools
        if comparison_tools(query):
            from src.agent.planning.comparison_request import comparison_request
            # This decision is terminal: an unsupported comparison must never
            # fall through to the generic activity-only or exclusion paths.
            return not comparison_request(query).supported
        if positive.strip() != query.strip():
            # Exclusions and restarts belong to the full Web planner's shared
            # positive/forbidden contract. This small profile must not count a
            # prohibited endpoint as a requested capability or bypass that gate.
            unsupported = _unreviewed_metric_capabilities(positive) + tuple(
                capability for capability in _unsupported_capabilities(positive)
                if capability != 'admet' or not _explicit_admet_request(positive))
            if not unsupported:
                return False
            # Keep this profile's rejection gate for unsupported *positive*
            # obligations; the broader planner cannot silently drop LogD, etc.
            # Candidate selection uses positive intent, never prohibited names.
            query = positive
        if _PROPERTY_REQUEST.search(query) or _DRUG_LIKENESS_REQUEST.search(query):
            return True
        if _activity_agent_request(query) and not _explicit_admet_request(query):
            # Activity/pIC50 requests are owned by the full-Web activity
            # workflow.  Keep this profile scoped to property/ADMET work so
            # it cannot reject an otherwise valid activity tool request.
            return False
        # Unsupported capabilities only make this profile authoritative when
        # they are mixed with a reviewed scientific obligation.  A generation-
        # only, docking-only, or target-search-only request belongs to its own
        # Agent profile; rejecting it here would prevent the dedicated tool
        # path from ever seeing the request.
        return bool(
            _PROFILE_ACTION.search(query)
            and (
                _requested_metrics(query)
                or _unreviewed_metric_capabilities(query)
                or _explicit_admet_request(query)
            )
        )

    @staticmethod
    def _plan(
        *,
        status: Literal["ready", "clarification", "rejected"],
        original_query: str,
        smiles: str | None = None,
        canonical_smiles: str | None = None,
        metrics: tuple[Metric, ...] = (),
        required_tools: tuple[str, ...] = (),
        missing_fields: tuple[str, ...] = (),
        missing_capabilities: tuple[str, ...] = (),
        message: str = "",
        steps: tuple[WorkflowStep, ...] = (),
        workflow_version: WorkflowVersion = WORKFLOW_VERSION,
        _validated_canonical: str | None = None,
    ) -> ScientificPlan:
        payload = {
            "status": status,
            "original_query": original_query,
            "smiles": smiles,
            "canonical_smiles": canonical_smiles,
            "metrics": metrics,
            "required_tools": required_tools,
            "missing_fields": missing_fields,
            "missing_capabilities": missing_capabilities,
            "message": message,
            "steps": steps,
            "workflow_version": workflow_version,
        }
        if _validated_canonical is None:
            return ScientificPlan(**payload)
        token = _COMPILED_CANONICAL.set(_validated_canonical)
        try:
            return ScientificPlan.model_validate(payload)
        finally:
            _COMPILED_CANONICAL.reset(token)

    def compile(
        self,
        original_query: str,
        *,
        model_fields: Mapping[str, object] | None = None,
    ) -> ScientificPlan:
        if model_fields is not None:
            raise ScientificObligationError(
                "Scientific obligations are authoritative and must be compiled "
                "from the user query, not supplied by model_fields."
            )
        if not isinstance(original_query, str):
            raise ScientificObligationError("The scientific query must be a string.")

        if len(original_query) > self.MAX_QUERY_CHARS:
            return self._plan(
                status="rejected",
                # ScientificPlan itself refuses oversized strings.  Keep the
                # compiler's safe terminal result while retaining only the
                # bounded prefix in its public DTO.
                original_query=original_query[: self.MAX_QUERY_CHARS],
                missing_capabilities=("query_too_long",),
            )

        admet_requested = _explicit_admet_request(original_query)
        unsupported = tuple(
            capability
            for capability in _unsupported_capabilities(original_query)
            if capability != "admet" or not admet_requested
        )
        unreviewed_metrics = _unreviewed_metric_capabilities(original_query)
        if "metric:ic50" in unreviewed_metrics:
            # IC50 is an unsupported endpoint, not a candidate SMILES. Reject
            # the whole obligation before the molecular parser can mistake the
            # uppercase endpoint token for an invalid structure.
            return self._plan(
                status="rejected",
                original_query=original_query,
                missing_capabilities=unsupported + unreviewed_metrics,
            )
        metrics = _requested_metrics(original_query)
        required_tools = _requested_tools(
            original_query,
            metrics,
            admet_requested=admet_requested,
        )
        if not metrics and "drug_likeness_assessment" in required_tools:
            # A ready plan always carries a non-empty reviewed metric set;
            # drug-likeness assessment uses the standard property basis even
            # when the user did not spell out a separate property request.
            metrics = _STANDARD_METRICS
        parser = BaseMolecularTool("scientific_obligation_compiler", "")
        try:
            parsed_values = parse_molecular_inputs(original_query, parser)
        except MolecularInputMissing:
            missing_input_hint = _NAMED_MOLECULE_HINT.search(original_query)
            if (
                (required_tools or missing_input_hint)
                and not (unsupported or unreviewed_metrics)
            ):
                return self._plan(
                    status="clarification",
                    original_query=original_query,
                    missing_fields=("smiles",),
                )
            if unsupported or unreviewed_metrics:
                return self._plan(
                    status="rejected",
                    original_query=original_query,
                    missing_capabilities=unsupported + unreviewed_metrics,
                )
            return self._plan(
                status="rejected",
                original_query=original_query,
                missing_capabilities=("molecular_properties",),
            )
        except MolecularInputUnavailable:
            return self._plan(
                status="rejected",
                original_query=original_query,
                missing_capabilities=("rdkit_unavailable",),
                message="分子结构验证能力暂不可用。",
            )
        except ValueError:
            return self._plan(
                status="rejected",
                original_query=original_query,
                missing_capabilities=("invalid_smiles",),
                message="无法验证分子结构；请提供有效的 SMILES。",
            )

        if unsupported or unreviewed_metrics:
            return self._plan(
                status="rejected",
                original_query=original_query,
                missing_capabilities=unsupported + unreviewed_metrics,
            )

        if len(parsed_values) != 1:
            return self._plan(status="rejected", original_query=original_query)

        parsed = parsed_values[0]
        smiles = parsed.smiles
        canonical_smiles = parsed.canonical_smiles
        if not required_tools:
            return self._plan(
                status="rejected",
                original_query=original_query,
                missing_capabilities=("molecular_properties",),
            )

        steps: list[WorkflowStep] = []
        if "property_calculator" in required_tools:
            steps.append(
                WorkflowStep(
                    "properties",
                    "property_calculator",
                    input_data=smiles,
                    output_key="properties",
                    capability="molecular_properties",
                )
            )
        if "drug_likeness_assessment" in required_tools:
            steps.append(
                WorkflowStep(
                    "drug_likeness",
                    "drug_likeness_assessment",
                    input_data=smiles,
                    output_key="drug_likeness",
                    capability="drug_likeness",
                )
            )
        if "admet_predictor" in required_tools:
            steps.append(
                WorkflowStep(
                    "admet",
                    "admet_predictor",
                    input_data=smiles,
                    output_key="admet",
                    capability="molecule.admet",
                )
            )
        workflow_version = (
            ADMET_WORKFLOW_VERSION
            if "admet_predictor" in required_tools
            else WORKFLOW_VERSION
        )
        return self._plan(
            status="ready",
            original_query=original_query,
            smiles=smiles,
            canonical_smiles=canonical_smiles,
            metrics=metrics,
            required_tools=required_tools,
            steps=tuple(steps),
            workflow_version=workflow_version,
            _validated_canonical=canonical_smiles,
        )


__all__ = [
    "ScientificObligationCompiler",
    "ScientificObligationError",
    "ScientificPlan",
    "MAX_QUERY_CHARS",
    "MAX_QUERY_LENGTH",
]
