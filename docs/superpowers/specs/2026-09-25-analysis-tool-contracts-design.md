# Analysis tool non-projecting contracts (package 4A)

## Scope and delegated choice

Part of the authorized remaining work through step 8. The user delegated recommended choices and quality-gated merges. No deployment or model activation. This increment covers only property_calculator, drug_likeness_assessment and admet_predictor. RAG/activity/docking contracts remain unchanged.

Three approaches were considered: serialize all tools through new common DTOs (risks dropping scientific extensions/defaulting missing observations); three independent adapters (repeats identical lifecycle code); one analysis-only adapter with three strict validation views (chosen). Reuse LegacyPythonToolAdapter and the existing activity contract's non-projecting pattern, not a new execution framework.

## Input boundary

Actual legacy producers consume a string. Accept direct strings and the existing query wrapper with a strict string query; do not add structured smiles/batch transport that producers do not implement. Existing parse_molecular_smiles remains the sole complete-input parser (batch limit, invalid member rejection and service-unavailable distinctions unchanged). Do not initialize tools, models or scientific assets during registration. Extra wrapper metadata may be retained for validation but only query reaches these string-based producers, as it did for supported wrappers.

## Output boundary

Add src/agent/tooling/analysis_contract.py. Validate raw envelopes before compatibility conversion and whole normalized ToolResult afterwards, including failures and partial observations. Validate named scientific payloads even in failure snapshots along error.details.raw_result (bounded 16 embedded snapshots, cycle rejection). Do not recursively interpret opaque metadata. Never return a model_dump of a validation view or change numerical precision, formatted text, status, warnings, artifacts, evidence, quality, provenance or scientific extensions.

Properties rows require smiles and the actual descriptors (formula, finite MW/LogP/TPSA, nonnegative integer HBA/HBD/rotatable bonds, QED in [0,1]). Likeness rows require actual assessment shape: QED, molecular_properties, Lipinski/Veber/lead rule detail dictionaries, overall score/components and labels; validate types/ranges but do not reimplement thresholds or weighted calculation. Reject booleans/numeric strings masquerading as numbers and NaN/Inf.

ADMET rows require smiles, prediction_method and backend_version plus the six existing section dictionaries. rdkit_rules validates the actual deterministic producer fields. adme_py sections can be sparse: validate known supplied leaves without inserting values or demanding every backend implement every metric; retain unknown extension fields. Reuse ADMETResultValidator for scientific method/provenance policy. Do not convert rules into experimental evidence or promise unverified adme_py versions. This boundary is structural, not proof a model is real.

Raw and normalized envelope views preserve success/partial/failure semantics; success requires usable data and cannot conflict with error/status. Legitimate failures may contain no data. Source/provenance identity must match the invoked tool. Known scientific evidence slots, if any, must be inspected from current producers/validators and documented; arbitrary evidence extensions stay opaque.

## Execution and error behavior

Validate within existing invocation worker/deadline/concurrency slot. Call the producer once; caller raw_validator sees the untouched result first and retains its exception classification. Use payload-free INVALID_INPUT / INVALID_OUTPUT on contract errors; never include validation input or scientific/private payload in errors. Normal dependency failures and timeouts retain their existing error/quality. Do not change retry counts, readiness, aliases, closure or resource ownership.

No producer calculation/batch-skipping behavior changes in this increment. Missing candidate alignment remains the existing downstream responsibility. No route, Planner, Web or scientific threshold changes. The legacy properties HTTP endpoint's unsupported ADMET labels are a separate recorded issue, not fixed or endorsed by this work.

## Acceptance

TDD failing tests must demonstrate malformed observations currently accepted and factory contracts absent. Characterize actual RDKit outputs and legacy failures before implementation. Test raw and normalized outputs, partials, failed raw_result, malformed metadata, unknown extensions, both input forms, complete invalid SMILES/batches, unavailable backends, timeout slot release, caller validator ordering and close semantics. Keep existing scientific assertions unchanged except typed fixtures demonstrably outside the actual producer contract; document any adjustment.

Focused and full Agent regression use the isolated runner from docs/superpowers/plans/2026-09-24-rag-service-extraction.md with this worktree, no actual assets/secrets/network. Test minimum supported Pydantic 2.5.0 using the existing isolated CI profile if feasible. Independent SPEC then QUALITY; exact-head CI7/7 before PR merge. Package4 remains incomplete until target/reverse, generation/ranking and helper inventory are finished.

