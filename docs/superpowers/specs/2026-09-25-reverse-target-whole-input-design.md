# Reverse-target whole-input boundary

Package6 code-only residual audit reproduced a real defect: `Reverse target prediction. SMILES: CCO)((` reaches the actual ReverseTargetTool predictor as `CCO`. The current property tool rejects it. The original dirty BaseMolecularTool patch must not be merged wholesale.

Chosen design: reuse `parse_molecular_smiles` at ReverseTargetTool.should_use and execute. Do not modify the global extractor, molecule-generation/RXN callers, predictor or parser grammar. Alternatives were changing all BaseMolecularTool consumers (too broad) or writing a reverse-only parser (duplicated grammar). User delegated recommended bounded choices through package8.

Every candidate in a submitted batch is validated before selecting its first entry, preserving the tool's historical single-molecule behavior. Valid salts, bracket atoms, stereochemistry and ring labels retain exact input spelling. Never repair invalid strings, trim unmatched delimiters, drop an invalid later entry, canonicalize or substitute a prefix. The existing RDKit availability gate stays in place.

Parsing ValueError becomes a payload-free validation_error with no data; MolecularInputUnavailable becomes tool_unavailable, not an invalid-structure diagnosis. Both return before lazy predictor creation. should_use returns false on parser failure, while still requiring existing target-intent keywords. Successful/zero-hit results, threshold0.6/top_k10/combine_by_target, evidence/assay normalization, lazy predictor ownership and predictor exception handling stay unchanged. This does not assert that threshold matches prove biological activity.

Tests invoke the real tool and real RDKit with only the predictor replaced by a counting spy. Cover malformed prefix/suffix/quotes/labels, invalid later batch item, valid complicated structures, first-of-valid-batch compatibility, unavailable validation and zero-hit behavior. Preserve existing domain/registration tests. No real database/network/model required; scientific predictions are not claimed from spies.
