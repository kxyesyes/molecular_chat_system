"""Reviewed product facts projected from plain, already-captured server inputs.

No discovery, registry hooks, model attributes, environment, files or network.
A product view is not execution authority: an ordinary turn still has zero tools.
No audited readiness publisher exists here; all current readiness is unknown.
"""
from types import MappingProxyType

from src.agent.contracts.decision_bindings import (
    B1_PROFILE_REVISION, B2_PROFILE_REVISION, TOOL_FEATURE_PAIRS, B1_TOOLS, B2_TOOLS,
)
from src.agent.contracts.ordinary_admission import (
    CAPABILITY_ERROR, CapabilityFeature, CapabilitySnapshot, ProviderDescriptor,
    bounded_view,
)


CATALOG_REVISION = "ordinary-product-v1"
PROFILE_REVISION = "ordinary-semantic-original-four-v1"
ORIGINAL_FOUR = frozenset({"property_calculator", "drug_likeness_assessment",
                           "activity_predictor", "target_database_search"})
PRODUCT_CATALOG = (
    ("ordinary_chat", "Main-model qualitative conversation; not computed science."),
    ("property_calculator", "RDKit molecular properties; numerical results require the actual tool."),
    ("drug_likeness_assessment", "Rule-based drug-likeness assessment; not clinical efficacy."),
    ("activity_predictor", "Family activity prediction; registration is not proof of weights or readiness."),
    ("target_database_search", "Target lookup; registration is not proof of current database availability."),
    ("molecule_generation", "Separate local gmm molecular generation; B integration is not installed in this entry."),
    ("admet_prediction", "Product ADMET support does not imply all-method or real-backend availability."),
    ("reverse_target", "Reverse target search requires B source and ownership integration."),
    ("molecule_ranking", "Molecule ranking requires real accepted inputs and bindings, never a guessed ranking."),
    ("rag_retrieval", "RAG requires B1 actual retrieval and a source receipt."),
    ("molecular_docking", "Docking requires C structured inputs, consent and physical execution."),
)
B_CATALOG_REVISION = "ordinary-product-b-v1"
_TOOL_TO_FEATURE = MappingProxyType(dict(TOOL_FEATURE_PAIRS))
_FEATURE_TO_TOOL = MappingProxyType({feature: tool for tool, feature in TOOL_FEATURE_PAIRS})
B_PRODUCT_CATALOG = tuple(
    (name, "Separate local gmm molecular generation requires approved B2 controls; readiness is unknown."
     if name == "molecule_generation" else
     "Reverse target hypotheses require same-invocation source receipts; readiness is unknown."
     if name == "reverse_target" else
     "Local molecular retrieval requires explicit RAG permission and same-invocation source receipts; readiness is unknown."
     if name == "rag_retrieval" else description)
    for name, description in PRODUCT_CATALOG
)


def _names(value):
    if type(value) is not frozenset or len(value) > 256:
        raise ValueError(CAPABILITY_ERROR)
    if any(type(name) is not str or not name or len(name) > 128 for name in value):
        raise ValueError(CAPABILITY_ERROR)
    bounded_view(sorted(value), max_bytes=32768, reason=CAPABILITY_ERROR)


def build_capability_snapshot(*, registered_names, provider_descriptor,
        semantic_profile, intent_capable, original_four_profile, scientific_tools,
        permitted_names, model_generation, capability_generation,
        binding_profile=PROFILE_REVISION, wired_names=None, rag_enabled=False):
    """Only canonical name sets, strict server flags and safe descriptor values.

    Assembly must extract names with frozenset(registry.as_mapping()) before this
    call. Unknown names/aliases never expand this catalog. The adapter approval
    and ownership flags are server decisions, not inferred from credentials.
    B wiring and permission sets must be reviewed canonical profile subsets,
    separate from the full registry. These views never authorize execution.
    """
    try:
        _names(registered_names)
        _names(permitted_names)
        if any(type(flag) is not bool for flag in (
                semantic_profile, intent_capable, original_four_profile, scientific_tools,
                rag_enabled)):
            raise ValueError(CAPABILITY_ERROR)
        if type(binding_profile) is not str or binding_profile not in (
                PROFILE_REVISION, B1_PROFILE_REVISION, B2_PROFILE_REVISION):
            raise ValueError(CAPABILITY_ERROR)
        b_profile = binding_profile != PROFILE_REVISION
        if b_profile:
            if not semantic_profile or original_four_profile:
                raise ValueError(CAPABILITY_ERROR)
            _names(wired_names)
            profile_tools = B1_TOOLS if binding_profile == B1_PROFILE_REVISION else B2_TOOLS
            if not wired_names <= profile_tools or not permitted_names <= profile_tools:
                raise ValueError(CAPABILITY_ERROR)
        elif wired_names is not None or rag_enabled:
            raise ValueError(CAPABILITY_ERROR)
        if type(provider_descriptor) is not dict:
            raise ValueError(CAPABILITY_ERROR)
        bounded_view(provider_descriptor, max_bytes=1024, reason=CAPABILITY_ERROR)
        descriptor = ProviderDescriptor.model_validate(provider_descriptor, strict=True)
        features = []
        for name, description in B_PRODUCT_CATALOG if b_profile else PRODUCT_CATALOG:
            if b_profile and name != "ordinary_chat":
                tool = _FEATURE_TO_TOOL.get(name)
                supported = tool in profile_tools and tool in wired_names
                present = tool in registered_names
                wired = supported and present
                permission = rag_enabled if tool == "rag_search" else scientific_tools
                permitted = wired and permission and tool in permitted_names
            else:
                supported = (semantic_profile if name == "ordinary_chat" else
                             original_four_profile if name in ORIGINAL_FOUR else False)
                present = intent_capable if name == "ordinary_chat" else name in registered_names
                wired = supported and present
                permitted = wired and (name == "ordinary_chat" or (scientific_tools and name in permitted_names))
            reason = ("profile_not_supported" if not supported else "not_registered" if not present
                      else "permission_disabled" if not permitted else "readiness_unknown")
            features.append(CapabilityFeature(id=name, product_description=description,
                wired=wired, permitted=permitted, readiness="unknown", reason=reason))
        return CapabilitySnapshot(version="1",
            catalog_revision=B_CATALOG_REVISION if b_profile else CATALOG_REVISION,
            profile_revision=binding_profile, capability_generation=capability_generation,
            model_generation=model_generation, provider_descriptor=descriptor, features=tuple(features))
    except (ValueError, TypeError, RecursionError):
        raise ValueError(CAPABILITY_ERROR) from None
