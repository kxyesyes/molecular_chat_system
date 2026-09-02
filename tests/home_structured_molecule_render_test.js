const fs = require("fs");
const path = require("path");
const vm = require("vm");
const crypto = require("crypto");

const root = path.join(__dirname, "..");

function read(relPath) {
  return fs.readFileSync(path.join(root, relPath), "utf8");
}

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function exactCandidateId(index, smiles) {
  const digest = crypto.createHash("sha256").update(smiles, "utf8").digest("hex");
  return `cand-${String(index).padStart(3, "0")}-${digest.slice(0, 8)}`;
}

function validPayload(status = "succeeded") {
  const candidates = [
    {
      candidate_id: "cand-001-ab1de819",
      source_index: 1,
      original_smiles: "CCO",
      canonical_smiles: "CCO",
      validation: { valid: true, method: "RDKit" },
      generation_provenance: {},
      metadata: {
        properties: { molecular_weight: 46.07, logp: -0.3 },
      },
      smiles: "CCO",
    },
  ];
  const requestedCount = status === "partial" ? 2 : 1;
  return {
    type: "molecule_candidates",
    trace_id: "trace-candidates-1",
    source: {
      tool_name: "llm_molecular_generator",
      model_name: "gmm-llama:latest",
      status,
    },
    candidate_set: {
      version: "1",
      requested_count: requestedCount,
      valid_count: 1,
      unique_count: 1,
      invalid_count: status === "partial" ? 1 : 0,
      duplicate_count: 0,
      candidates,
      rejected:
        status === "partial"
          ? [{ source_index: 2, smiles: "CC(C)X", reason: "invalid_smiles" }]
          : [],
      status,
    },
    warnings: status === "partial" ? ["one candidate was rejected"] : [],
  };
}

const helperPath = path.join(
  root,
  "src",
  "web",
  "static",
  "js",
  "home",
  "molecule_candidates.js",
);
assert(fs.existsSync(helperPath), "Missing molecule_candidates.js helper");

const sandbox = { window: {}, console };
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(helperPath, "utf8"), sandbox, {
  filename: helperPath,
});

const api = sandbox.window.HomeMoleculeCandidates;
assert(api && typeof api.normalize === "function", "Helper must expose normalize");

const helperSource = fs.readFileSync(helperPath, "utf8");
assert(
  helperSource.includes("function sha256Prefix"),
  "Helper must provide a local synchronous SHA-256 implementation",
);
const instrumentedSource = helperSource
  .replace(
    "root.HomeMoleculeCandidates = { normalize };",
    "root.HomeMoleculeCandidates = { normalize, sha256Prefix };",
  );
assert(
  instrumentedSource.includes("normalize, sha256Prefix"),
  "Test instrumentation must expose the local synchronous SHA-256 helper",
);
const hashSandbox = { window: {}, console };
hashSandbox.globalThis = hashSandbox;
vm.createContext(hashSandbox);
vm.runInContext(instrumentedSource, hashSandbox, { filename: helperPath });
const unicodeHashInput = "分子🧬";
assert(
  hashSandbox.window.HomeMoleculeCandidates.sha256Prefix(unicodeHashInput) ===
    crypto.createHash("sha256").update(unicodeHashInput, "utf8").digest("hex").slice(0, 8),
  "Local SHA-256 helper must encode Unicode input as standards-compliant UTF-8",
);

const normalized = api.normalize(validPayload());
assert(normalized, "Valid succeeded payload must normalize");
assert(normalized !== validPayload(), "Normalizer must return a fresh object");
assert(normalized.traceId === "trace-candidates-1", "Trace ID must normalize");
assert(
  normalized.source.tool === "llm_molecular_generator" &&
    normalized.source.model === "gmm-llama:latest" &&
    normalized.source.status === "succeeded",
  "Source provenance must normalize",
);
assert(
  normalized.counts.valid_count === 1 &&
    normalized.counts.unique_count === 1,
  "Validated candidate counts must normalize",
);
assert(
  normalized.candidates[0].canonical_smiles === "CCO" &&
    normalized.candidates[0].source_index === 1 &&
    normalized.candidates[0].original_smiles === "CCO" &&
    normalized.candidates[0].generation_provenance &&
    normalized.candidates[0].metadata.properties.molecular_weight === 46.07,
  "Complete validated candidate records and metadata must normalize",
);

const original = validPayload();
const fresh = api.normalize(original);
original.source.tool_name = "tampered";
original.candidate_set.candidates[0].generation_provenance.model = "tampered";
original.candidate_set.candidates[0].metadata.properties.molecular_weight = 999;
assert(
  fresh.source.tool === "llm_molecular_generator" &&
    fresh.candidates[0].generation_provenance.model === undefined &&
    fresh.candidates[0].metadata.properties.molecular_weight === 46.07,
  "Normalized output must not retain untrusted input objects",
);

assert(api.normalize(validPayload("partial")), "Valid partial payload must normalize");
const failed = validPayload();
failed.source.status = "failed";
failed.candidate_set.status = "failed";
assert(api.normalize(failed) === null, "Failed payload must be rejected");

function payloadWithCandidates(smilesValues) {
  const payload = validPayload();
  payload.candidate_set.candidates = smilesValues.map((smiles, index) => ({
    ...clone(payload.candidate_set.candidates[0]),
    candidate_id: exactCandidateId(index + 1, smiles),
    source_index: index + 1,
    original_smiles: smiles,
    canonical_smiles: smiles,
    smiles,
  }));
  payload.candidate_set.requested_count = smilesValues.length;
  payload.candidate_set.valid_count = smilesValues.length;
  payload.candidate_set.unique_count = smilesValues.length;
  return payload;
}

function rejected(mutator, message) {
  const payload = validPayload();
  mutator(payload);
  assert(api.normalize(payload) === null, message);
}

function accepted(mutator, message) {
  const payload = validPayload("partial");
  mutator(payload);
  assert(api.normalize(payload), message);
}

assert(
  api.normalize(payloadWithCandidates(["CCO", "CCN"])),
  "Exact SHA-256 candidate identities in array order must normalize",
);
assert(
  api.normalize(payloadWithCandidates(["C".repeat(80)])),
  "Candidate identity hashing must remain correct across SHA-256 blocks",
);
accepted(
  (payload) => {
    payload.candidate_set.candidates[0].source_index = 2;
    payload.candidate_set.rejected[0].source_index = 1;
  },
  "Candidate ordinal must be independent from a valid source index",
);
rejected(
  (payload) => {
    payload.candidate_set.candidates[0].candidate_id = "candidate-001-ab1de819";
  },
  "Malformed candidate ID format must be rejected",
);
rejected(
  (payload) => {
    payload.candidate_set.candidates[0].candidate_id = "cand-001-AB1DE819";
  },
  "Uppercase candidate hashes must be rejected",
);
rejected(
  (payload) => {
    payload.candidate_set.candidates[0].candidate_id = "cand-0001-ab1de819";
  },
  "Candidate ordinal formatting must match Python zero padding exactly",
);
rejected(
  (payload) => {
    payload.candidate_set.candidates[0].candidate_id = "cand-001-deadbeef";
  },
  "Candidate IDs with the wrong SHA-256 prefix must be rejected",
);
rejected(
  (payload) => {
    payload.candidate_set.candidates[0].candidate_id = "cand-002-ab1de819";
  },
  "Candidate ordinal must follow array position rather than source index",
);

const skippedOrdinal = payloadWithCandidates(["CCO", "CCN"]);
skippedOrdinal.candidate_set.candidates[1].candidate_id =
  `cand-003-${exactCandidateId(2, "CCN").slice(-8)}`;
assert(
  api.normalize(skippedOrdinal) === null,
  "Skipped candidate ordinals must be rejected",
);
const reorderedOrdinal = payloadWithCandidates(["CCO", "CCN"]);
reorderedOrdinal.candidate_set.candidates.reverse();
assert(
  api.normalize(reorderedOrdinal) === null,
  "Reordered candidate ordinals must be rejected even when source indexes remain valid",
);
rejected(
  (payload) => {
    const candidate = payload.candidate_set.candidates[0];
    candidate.canonical_smiles = "C".repeat(513);
    candidate.original_smiles = candidate.canonical_smiles;
    candidate.smiles = candidate.canonical_smiles;
  },
  "Oversized canonical SMILES must be rejected before identity hashing",
);

rejected(
  (payload) => {
    delete payload.candidate_set.rejected;
  },
  "Missing candidate-set fields must be rejected",
);
rejected(
  (payload) => {
    payload.candidate_set.extra = true;
  },
  "Extra candidate-set fields must be rejected",
);
rejected(
  (payload) => {
    delete payload.candidate_set.candidates[0].source_index;
  },
  "Incomplete candidate records must be rejected",
);
rejected(
  (payload) => {
    payload.candidate_set.candidates[0].extra = true;
  },
  "Extra candidate record fields must be rejected",
);
rejected(
  (payload) => {
    payload.candidate_set.candidates[0].source_index = 0;
  },
  "Candidate source indexes must be positive",
);
rejected(
  (payload) => {
    payload.candidate_set.invalid_count = Number.MAX_SAFE_INTEGER + 1;
  },
  "Impossible high counts must be rejected",
);

rejected(
  (payload) => {
    payload.candidate_set.valid_count = 2;
  },
  "Counts mismatch must be rejected",
);
rejected(
  (payload) => {
    const duplicate = clone(payload.candidate_set.candidates[0]);
    duplicate.candidate_id = "cand-002-ab1de819";
    duplicate.source_index = 2;
    payload.candidate_set.candidates.push(duplicate);
    payload.candidate_set.requested_count = 2;
    payload.candidate_set.valid_count = 2;
    payload.candidate_set.unique_count = 2;
  },
  "Duplicate canonical SMILES must be rejected",
);
rejected(
  (payload) => {
    payload.candidate_set.candidates[0].validation.valid = false;
  },
  "Invalid validation must be rejected",
);
rejected(
  (payload) => {
    const candidate = payload.candidate_set.candidates[0];
    candidate.original_smiles = "MarvinSketch";
    candidate.canonical_smiles = "MarvinSketch";
    candidate.smiles = "MarvinSketch";
    candidate.validation = { valid: false, method: "MarvinSketch" };
  },
  "MarvinSketch with invalid validation must be rejected",
);
rejected(
  (payload) => {
    const candidate = payload.candidate_set.candidates[0];
    candidate.original_smiles = "CC(C)X";
    candidate.canonical_smiles = "CC(C)X";
    candidate.smiles = "CC(C)X";
  },
  "Invalid CC(C)X candidate must be rejected",
);
rejected(
  (payload) => {
    payload.candidate_set.candidates[0].generation_provenance = null;
  },
  "Generation provenance must be an object",
);

[
  ["invalid_smiles", 1, 0],
  ["invalid_candidate_metadata", 1, 0],
  ["duplicate_smiles", 0, 1],
  ["excess_candidate", 0, 0],
].forEach(([reason, invalidCount, duplicateCount]) => {
  accepted(
    (payload) => {
      payload.candidate_set.rejected[0].reason = reason;
      payload.candidate_set.invalid_count = invalidCount;
      payload.candidate_set.duplicate_count = duplicateCount;
    },
    `Rejected reason ${reason} must be accepted with matching counts`,
  );
});
rejected(
  (payload) => {
    payload.candidate_set.rejected = [
      { source_index: 2, smiles: "bad", reason: "unknown_reason" },
    ];
    payload.candidate_set.invalid_count = 1;
  },
  "Unknown rejection reasons must be rejected",
);
rejected(
  (payload) => {
    payload.candidate_set.rejected = [
      { source_index: 2, reason: "invalid_smiles" },
    ];
  },
  "Incomplete rejected records must be rejected",
);
rejected(
  (payload) => {
    payload.candidate_set.invalid_count = 1;
    payload.candidate_set.rejected = [];
  },
  "Invalid counts must match rejected reasons",
);
rejected(
  (payload) => {
    payload.candidate_set.duplicate_count = 1;
    payload.candidate_set.rejected = [];
  },
  "Duplicate counts must match rejected reasons",
);
rejected(
  (payload) => {
    payload.candidate_set.candidates[0].source_index = 2;
  },
  "Source indexes must be contiguous",
);
rejected(
  (payload) => {
    payload.candidate_set.candidates[0].source_index = 2;
    payload.candidate_set.rejected = [
      { source_index: 2, smiles: "bad", reason: "invalid_smiles" },
    ];
    payload.candidate_set.requested_count = 2;
    payload.candidate_set.status = "partial";
    payload.source.status = "partial";
    payload.candidate_set.invalid_count = 1;
  },
  "Candidate and rejected source indexes must be unique",
);

const prototypePayload = JSON.parse(
  JSON.stringify(validPayload()).replace(
    '"metadata":{"properties"',
    '"metadata":{"__proto__":{"polluted":true},"properties"',
  ),
);
assert(
  api.normalize(prototypePayload) === null,
  "Prototype-trick payload values must be rejected",
);

const mainSource = read("src/web/static/js/home/main.js");
const indexHtml = read("src/web/templates/index.html");
const rendererStart = mainSource.indexOf(
  "function renderMoleculeCandidates(messageElement, payload)",
);
const rendererEnd = mainSource.indexOf("window.HomeMain", rendererStart);
const rendererSource = mainSource.slice(rendererStart, rendererEnd);
assert(
  mainSource.includes('case "molecule_candidates":'),
  "WebSocket handler must have a structured molecule_candidates case",
);
assert(
  mainSource.includes("pendingMoleculeCandidatePayloads"),
  "Structured candidate payloads must be queued until completion",
);
assert(
  mainSource.includes("function renderMoleculeCandidates(messageElement, payload)"),
  "Main must render normalized candidate records",
);
assert(
  !mainSource.includes("detectAndRenderMolecules"),
  "Assistant completion text must not be scanned for SMILES",
);
assert(
  !mainSource.includes("fetchMoleculePropertiesForToolMolecule"),
  "Structured cards must not fetch unverified molecule properties",
);
assert(
  !mainSource.includes("/api/molecule/properties"),
  "Homepage must not contain the dead molecule properties endpoint path",
);
assert(
  rendererSource.includes("candidate.canonical_smiles"),
  "Image and copy actions must use canonical candidate SMILES",
);
assert(
  rendererSource.includes("candidateId.textContent") &&
    rendererSource.includes("smilesText.textContent") &&
    rendererSource.includes("provenanceLabel.textContent"),
  "Candidate IDs, SMILES, and provenance must render through textContent",
);
assert(
  !mainSource.includes("工具生成"),
  "Provenance must come from the structured source, not a hardcoded label",
);
assert(
  rendererSource.includes("本步骤未提供经工具验证的属性"),
  "Cards without allowlisted metadata properties must explain their absence",
);
assert(
  rendererSource.includes('/api/utils/smiles_to_image?smiles=${encodeURIComponent('),
  "Cards must request images only through the SMILES image endpoint",
);

const helperScript =
  "/static/js/home/molecule_candidates.js?v=20260902-candidate-identity-v3";
const safeScript = "/static/js/shared/safe_render.js";
const mainScript = "/static/js/home/main.js?v=";
assert(indexHtml.includes(helperScript), "Template must version molecule candidate helper");
assert(indexHtml.includes(mainScript), "Template must version homepage main");
assert(
  indexHtml.indexOf(safeScript) < indexHtml.indexOf(helperScript) &&
    indexHtml.indexOf(helperScript) < indexHtml.indexOf(mainScript),
  "Template must load safety dependencies before candidate helper and main after it",
);

console.log("Structured molecule candidate frontend checks passed");
