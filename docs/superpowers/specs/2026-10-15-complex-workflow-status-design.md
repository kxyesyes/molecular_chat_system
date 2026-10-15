# Complex workflow truthfulness and progress

## Approved scope

Repair complex workflow allocation, target phrase recognition, reverse-target diagnostics,
dependency-blocked reporting, and mixed activity evidence propagation. Keep the existing
FastAPI/Jinja/native-JavaScript architecture and scientific validation boundaries.

## Allocation

Before any scientific tool starts, allocation has a default 30-second deadline and
5-second progress updates. Expiry produces one explicit timeout terminal response.
No expired request may later dispatch a tool or overwrite that response. Cancellation
must not abandon an unbounded number of model workers. Tool execution itself is not
subject to this allocation deadline. Request state and progress must be isolated.

## Target wording

Equivalent forms such as `以 PDE 为靶点设计三个候选分子`, including whitespace
before the design verb, and `基于 PDE 设计三个候选分子` resolve to the same family.
Unknown, multiple, negated, switched, and selective targets remain clarification cases.
Do not turn arbitrary prose into a valid target or change model selection.

## Failure provenance

Reverse-target validation stays fail-closed. Return safe structured diagnostic stage,
reason and relevant field where known, without raw exceptions, paths, credentials,
or unvalidated prediction claims. An unexecuted dependent step is `not_calculated`
with dependency-blocked metadata, not a tool execution failure. Preserve the upstream
failure as the cause and avoid a fabricated tool-started event.

## Partial activity evidence

Preserve each candidate's validated raw regression and classification outputs,
provenance, consistency flag, execution status and review status. Mixed batches remain
`partial`, never fully successful. Validate and align partial family-activity rows before
publishing them to downstream bindings. Ranking may use only individually eligible
rows; it must identify exclusion reasons for review/failed/missing rows. Retain the
existing scoring weights and completeness rules. Final reports show raw observed
values even when excluded from ranking, distinguishing predictions from experiments.

Malformed, forged, demo/fallback or mismatched evidence must not become usable merely
because the enclosing result says partial. Classification/regression disagreements
remain visible; do not alter predictions, model weights, thresholds or checks.

## Verification and delivery

Write and observe focused failing regressions before implementation. Cover deterministic
timeout/progress/cancellation, phrase equivalence and conservative guards, safe reverse
diagnostics, dependency non-execution, mixed activity binding/ranking/final reports,
and browser task labels. Run relevant Python and Node suites plus compilation checks.
Test doubles are contract fixtures, not real scientific validation claims.

Use one isolated branch and one draft PR against main. Preserve all unrelated assets.
Do not automatically merge. Real-model consistency calibration remains separate work.
