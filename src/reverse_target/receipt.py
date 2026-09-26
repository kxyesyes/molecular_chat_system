"""Pure consumer binding, not source authentication or current eligibility.

Receipt-free callers do not import the scientific producer dependencies.
"""
import hashlib
from typing import Any, Dict, Mapping


CONTROLS = dict(threshold=0.6, top_k=10, combine_by_target=True, organism_filter='')
REVISION = 'reverse-target-record-v1'
SOURCE = 'local_reverse_target_fingerprints'
ERROR = 'Invalid reverse-target prediction observation.'


def normalize_target_record(record: Mapping[str, Any]) -> Dict[str, Any]:
    normalized = dict(record)
    target_identifier = next(
        (
            str(record.get(key)).strip()
            for key in (
                "target_chembl_id",
                "target_identifier",
                "uniprot_id",
                "target_id",
            )
            if record.get(key) not in (None, "")
        ),
        "",
    )
    if not target_identifier:
        stable_source = "|".join(
            (
                str(record.get("target_name") or "").strip().casefold(),
                str(record.get("organism") or "").strip().casefold(),
            )
        )
        digest = hashlib.sha256(stable_source.encode("utf-8")).hexdigest()
        target_identifier = f"name-sha256:{digest}"
    normalized["target_identifier"] = target_identifier

    assay_fields = {
        "type": record.get("standard_type"),
        "relation": record.get("standard_relation"),
        "value": record.get("standard_value"),
        "units": record.get("standard_units"),
    }
    normalized["assay"] = {
        key: value
        for key, value in assay_fields.items()
        if value not in (None, "")
    }
    return normalized


def validate_prediction_observation(data, evidence, *, smiles=None):
    """Return a full native-JSON digest iff an optional strict proof is valid."""
    if not isinstance(evidence, list) or not any(
        isinstance(entry, dict) and 'prediction_receipt' in entry for entry in evidence
    ):
        return None
    # Reuse the producer's closed schema and bounded codec, never hash repair.
    from .owned_source import (
        ReverseSourceSnapshot, _native_bounded, canonical_json, json_sha256,
        validate_envelope,
    )
    try:
        complete = dict(data=data, evidence=evidence)
        _native_bounded(complete)
        entries = [entry for entry in evidence if 'prediction_receipt' in entry]
        if len(entries) != 1:
            raise ValueError(ERROR)
        entry = entries[0]
        if set(entry) != {'source', 'normalization_revision', 'input_smiles',
                          'record_count', 'records', 'prediction_receipt'}:
            raise ValueError(ERROR)
        if (entry['source'] != SOURCE or entry['normalization_revision'] != REVISION
                or type(entry['record_count']) is not int
                or type(entry['records']) is not list
                or entry['record_count'] != len(entry['records'])
                or (smiles is not None and entry['input_smiles'] != smiles)):
            raise ValueError(ERROR)
        receipt = entry['prediction_receipt']
        expected = ReverseSourceSnapshot(receipt['source']['generation_id'],
                                        receipt['source_sha256'], receipt['configuration_sha256'])
        envelope = validate_envelope(
            dict(records=entry['records'], receipt=receipt),
            smiles=entry['input_smiles'], controls=CONTROLS, expected=expected)
        normalized = [normalize_target_record(row) for row in envelope['records']]
        if canonical_json(data) != canonical_json(normalized):
            raise ValueError(ERROR)
        return json_sha256(complete)
    except Exception:
        raise ValueError(ERROR) from None
