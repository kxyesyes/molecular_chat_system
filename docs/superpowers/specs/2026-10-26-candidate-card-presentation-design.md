# Candidate card and evidence presentation

## Observed issue

A real, explicitly requested one-molecule generation displayed the raw candidate ID, internal generator tool name and model name on the card, and a verbose description of missing property evidence. This conflicts with the project rule that default result content should not expose internal identifiers or tool-source details. The existing evidence report can also show those technical identifiers directly.

## Design and invariants

Use compact human-readable candidate numbering, while keeping the existing candidate IDs, references, ACK order, selection mapping and canonical SMILES unchanged in structured state. Numbering must be unambiguous across multiple displayed collections and match any visible ranking labels; do not derive or invent scientific values. Keep the image, copy, selection and expandable SMILES actions accessible. Replace the generic property explanation with a concise truthful unavailable state; show validated descriptor values and any specific partial or failure status without technical source identifiers.

For a valid scientific report, show decision-relevant status, ranking and concrete warnings in the default view; move raw source names, evidence IDs, technical step identifiers and complete audit details to a closed, user-openable disclosure. Never hide a failed or incomplete step, missing evidence, disagreement, truncation or unrankable candidate behind the disclosure. Preserve immutable report payloads, source bindings and XSS-safe text handling, and do not transform predicted results into experimental conclusions.

## Verification

RED-first DOM tests cover the actual candidate frame and report: no raw internal ID/tool/model in the default visible view; audit details retain them; valid values and concrete failures remain visible; multi-collection numbering, selection ACK, copy/SMILES controls, invalid evidence, inaccessible storage and hostile text remain safe. Run Node syntax and related contracts, Python integration, and a real UI generation plus selection/follow-up where available. Do not alter the configured Agent model or generation tool model.
