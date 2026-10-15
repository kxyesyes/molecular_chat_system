"""Lightweight public diagnostic codes shared by producers and presentation.

No scientific runtimes are imported here. Sanitization never reads nested raw
results or promotes them to evidence; their validation remains caller-owned.
"""

from types import MappingProxyType


REVERSE_PROOF_DIAGNOSTICS = MappingProxyType({
    "proof": ("proof", "validation_failed"),
    "snapshot": ("proof", "invalid_snapshot"),
    "envelope": ("proof", "invalid_envelope"),
    "receipt_schema": ("receipt", "schema_mismatch"),
    "receipt_field": ("receipt", "invalid_field"),
    "controls": ("receipt", "controls_mismatch"),
    "count": ("receipt", "count_mismatch"),
    "digest": ("digest", "digest_mismatch"),
    "record_type": ("records", "invalid_type"),
    "record_schema": ("records", "schema_mismatch"),
    "record_field": ("records", "invalid_field"),
    "provenance": ("records", "invalid_provenance"),
    "ordering": ("records", "ordering_or_grouping_mismatch"),
    "bounds": ("records", "bounds_mismatch"),
})
REVERSE_TARGET_DIAGNOSTIC_FIELDS = frozenset((
    "snapshot envelope receipt schema_version validation_revision invocation_id source "
    "source_sha256 input_sha256 configuration_sha256 result_sha256 controls weights "
    "weight_resolution weight_resolution_revision record_count status score_dtype records "
    "target_name organism canonical_smiles molecule_chembl_id standard_type chembl_search_url "
    "uniprot_search_url standard_value score_semantics similarity_metric evidence provenance "
    "morgan_similarity maccs_similarity final_similarity row_index similar_count"
).split()) | frozenset("source." + name for name in (
    "revision generation_id source_name tsv_sha256 row_count morgan_sha256 maccs_sha256 "
    "morgan_popcounts_sha256 maccs_popcounts_sha256 morgan_bits maccs_bits morgan_radius "
    "rdkit_version numpy_version array_codec row_alignment_verified cache_origins"
).split())
_REVERSE_TARGET_DIAGNOSTIC_PAIRS = frozenset(REVERSE_PROOF_DIAGNOSTICS.values()) | frozenset(
    (stage, reason)
    for stage in ("source", "proof", "runtime_observation", "formatting", "postflight")
    for reason in ("validation_failed", "timeout", "source_unavailable", "execution_failed")
)


def sanitize_reverse_target_diagnostics(value) -> dict[str, str]:
    """Return only allowlisted stage/reason and optional field, never raw values.

    Unknown stage/reason pairs produce an empty mapping. An unknown field is
    omitted without losing the known reason. The returned mapping is detached.
    """
    if type(value) is not dict:
        return {}
    stage, reason = value.get("stage"), value.get("reason")
    if (type(stage) is not str or type(reason) is not str
            or (stage, reason) not in _REVERSE_TARGET_DIAGNOSTIC_PAIRS):
        return {}
    details = dict(stage=stage, reason=reason)
    field = value.get("field")
    if type(field) is str and field in REVERSE_TARGET_DIAGNOSTIC_FIELDS:
        details["field"] = field
    return details
