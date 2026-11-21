const { createHash } = require("node:crypto");

module.exports = function candidateEvent(smiles = ["CCO", "CCN"], presentationId = "11111111-1111-4111-8111-111111111111", observationId = "checkpoint") {
  const candidates = smiles.map((s, i) => ({
    candidate_id: `cand-${String(i + 1).padStart(3, "0")}-${createHash("sha256").update(s).digest("hex").slice(0, 8)}`,
    source_index: i + 1, original_smiles: s, canonical_smiles: s, smiles: s,
    validation: {valid: true, method: "RDKit"}, generation_provenance: {}, metadata: {},
  }));
  return {
    type: "molecule_candidates", trace_id: "trace", warnings: [],
    source: {tool_name: "llm_molecular_generator", model_name: "offline", status: "succeeded"},
    candidate_set: {
      version: "1", requested_count: candidates.length, valid_count: candidates.length, unique_count: candidates.length,
      invalid_count: 0, duplicate_count: 0, candidates, rejected: [], status: "succeeded",
    },
    reference: {
      trace_id: "trace", presentation_id: presentationId, revision: "a".repeat(64),
      ordered_keys: candidates.map(c => [observationId, c.candidate_id]),
    },
  };
};
