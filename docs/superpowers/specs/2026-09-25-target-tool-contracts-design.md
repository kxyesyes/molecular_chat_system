# Target lookup and reverse-target typed boundaries

Package4B completes two related registry contracts; it does not change search providers, cache TTL, similarity ranking or biological interpretation. Recommended bounded design is selected under the user's delegated choices through package8.

## Reproduced defect

Actual TargetDatabaseTool with a controlled search service returns `resolved` or `not_found`. Registry execution currently turns either into INVALID_OUTPUT because those are lookup outcomes, not ObservationStatus values. Two actual-module probes failed. Fixing this only in global execute_tool_compat would weaken every tool's status contract; changing the raw producer would break direct consumers. Instead use a target-only adapter mapping after raw caller validation and strict domain validation, before existing compat conversion.

## Input views

Target lookup accepts a query string, a target record or a list/tuple of such records, either directly or through the existing query wrapper. Preserve original container/data, current record-key precedence, canonical target identifiers, deduplication and five-query cap in the producer. Known identifier fields must be strings or absent/null, never coerce arbitrary objects. Permit opaque extra fields for upstream evidence. Reverse-target accepts the existing direct string or query wrapper only; its producer remains responsible for complete-SMILES validation. No new batch algorithm or request DSL.

## Output views and mapping

Use one focused module with strict non-projecting views for the two tools. Validate raw observations before compat and complete ToolResult afterward, including failure/partial observations and bounded known error.details.raw_result chains (16 embedded snapshots, reject cycles). Preserve unknown scientific extensions, warnings, artifacts, evidence, provenance, model flags and formatted text. Reject wrong identities, nonfinite known numeric fields, bool-as-number, coercible numeric strings, malformed known containers and conflicting status/success/error with payload-free INVALID_OUTPUT. Reuse TargetEvidenceValidator; structural evidence is not proof of a real database hit.

For target dictionaries only: resolved and not_found map to succeeded; ambiguous maps to invalid_input; unavailable remains unavailable; partial remains partial. Require resolved to have records and not_found to have none; failure statuses must not claim success and successful statuses must not carry errors. Existing canonical ObservationStatus values remain supported with normal consistency checks. Preserve the original lookup outcome and lookup_path in quality.lookup_status and quality.lookup_path without mutating the raw object; reject conflicting preexisting values rather than overwrite them. Keep quality.service_statuses, retryable, unknown structure counts and stale flags. Not-found is a successful search with zero matches, not proof of no biological target. Ambiguity/provider failure text must remain available and never become conclusive no-match success.

Target records validate supplied identity/structure/source fields and finite counts; structure_count may be None when evidence is unavailable. Recommended structures are dictionaries with known typed metadata and opaque extensions. Reverse records validate supplied final/Morgan/MACCS similarities in[0,1], nonnegative similar_count, stable identifier and assay shape without fabricating assay measurements or requiring an experimental assay on every similarity hit. Successful zero-hit data=None is retained. Do not turn partial records into full success.

Caller raw_validator sees the original raw result exactly once before adapter mapping/checks, and its exception classification remains unchanged. Producer execution, conversion and raw/normalized validation stay inside one existing worker, concurrency slot and timeout. Preserve lazy health, retry policy, close ownership and redaction. Reuse existing compat; a small target-local completed-invocation proxy is acceptable, no replacement framework or global enum relaxation.

## Verification

Real TargetDatabaseTool with controlled service fixtures covers all domain outcomes, records, source lookup_path, retry true/false and unavailable structures; real ReverseTargetTool uses a counted predictor and RDKit. Spy fixtures must never claim real scientific inference. Adversarial raw/canonical observations, malformed failures, caller validator precedence, no duplicate invocation, slot/deadline/cleanup, complete observation equality excluding elapsed and explicitly added lookup metadata, current direct producer tests and minimum Pydantic2.5 profile are required. Legacy invalid stub shapes may be corrected narrowly or tested with an explicitly generic adapter to keep downstream gate assertions meaningful; never weaken production validation to retain a stub.

The separate whole-SMILES producer fix and analysis contracts are independently reviewed branches; integrate their latest merged versions before final combined regression. Do not edit those worktrees or scientific assets.
