# Comparison input boundaries

## Problem and decision

Real browser testing exposed three related boundaries: the single-molecule gate
can consume a supported comparison or let an unsupported endpoint fall through;
an English sentence-final period can become part of a target; presentation can
show an unrequested ADMET column. Keep the existing executor and scientific tools.

Use a shared, non-executable comparison request view for admission and coverage.
Separate molecular operands from metric obligations and whole presentation
clauses before checking reviewed vocabulary. Never let an unsupported comparison
fall through to a broader activity workflow. Preserve the immutable original
query, positive/forbidden intent, strict explicit SMILES validation, canonical
batch identity, tool switches and the 16-candidate limit.

Operand clauses must be fully consumed by a closed list grammar. Existing
unquoted compatibility labels (阿司匹林、对乙酰氨基酚、咖啡因) are allowed before
explicit structures; arbitrary name-like prose is not. This is not name-to-
structure resolution. Other ambiguous labels must use an explicit SMILES list.

List parsing is deterministic, with strict single-field validation per operand;
finding one structure in a multi-line request is not complete consumption.
Shared molecular validation rejects non-printable/non-ASCII structure fields
before RDKit because some builds accept a prefix before a Unicode suffix.
Chinese request prose and wrappers remain supported outside structure fields.
The reviewed property vocabulary is shared with tool selection so accepting MW,
HBA or HBD cannot silently omit the property calculator.

The parser may recognize an English prose sentence terminator, but must not
repair explicit fields or malformed/disconnected structures. Display columns
come from requested tools, not available results; failed requested tools remain
visible. No numerical predictions, provenance or raw observations are changed.

## Acceptance

- Existing named/complex-SMILES comparisons and evidence ranking remain valid.
- Chinese and English property/activity comparisons work with and without ADMET.
- IC50, LogD, FooMetric and wrapped unknown obligations execute no endpoint.
- Explicit `SMILES: CCO.` remains invalid; valid disconnected SMILES remain whole.
- Requested failures remain visible; unrequested columns disappear.
- Focused/regression tests and real browser requests both verify behavior.

## Scope and rollout

No new model, framework, scientific capability, API or persistence schema.
Use the existing feature branch and preserve unrelated working-tree changes.
Do not publish or merge before review and browser verification.
