# Invalid-SMILES decision prompt repair

## Evidence and scope

The unchanged four-case DeepSeek acceptance run reproduced a scientific finish
with an empty evidence list after the real RDKit property tool rejected invalid
SMILES. Strict decision validation correctly rejected that proposal. The model
then clarified after schema correction. A standalone run clarified immediately,
so the provider behavior is intermittent, not a deterministic parser defect.

The initial scientific-session prompt requests scientific finish but omits the
missing-evidence clarification rule already present in schema correction.

## Approved approach

Update only the initial decision instructions: when inputs are invalid or usable
evidence is absent, ask for clarification rather than claiming completion.
Scientific finish requires nonempty, unique IDs from actual usable observations;
chat finish remains exclusive to chat requests. Preserve the existing narrowly
authorized family-disagreement review-only completion (not scientific success).
Missing input may be clarified immediately; supplied SMILES must be checked by
the authorized molecular tool, not rejected by the model's own assessment.
Do not invent evidence or turn
failed tool observations into successful science. Preserve authorized execution
of molecular tools and the existing valid-result completion path.

Do not change the decision schema, validators, acceptance criteria, network
policy, tool permissions, retry budgets, dependencies, or deployment. Work only
in the isolated task branch; preserve the original checkout's existing edits.

## Verification and delivery

First add failing regression coverage for the initial prompt and strict rejection
of the reproduced malformed finish. Exercise invalid SMILES with real RDKit and
the native decision protocol using deterministic provider fixtures. Keep provider
schema errors disqualifying in acceptance even if later clarification succeeds.

Run focused and affected agent regressions and compileall. Then run the unchanged
four-case DeepSeek acceptance suite with the real property tool and bounded model
calls. Repeat invalid-input verification to sample intermittent behavior; report
every outcome without treating a passing sample as a universal guarantee.
Only record sanitized structural diagnostics and usage counters, never keys or
raw model responses. No automatic deployment or merge.

The alternative of restructuring the public JSON Schema is deferred: it expands
the contract surface and is unnecessary for this prompt-only repair. Rollback is
reverting the prompt change; the strict runtime checks remain unchanged.
