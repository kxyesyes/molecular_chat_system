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

const rawApi = sandbox.window.HomeMoleculeCandidates;
assert(rawApi && typeof rawApi.normalize === "function", "Helper must expose normalize");
const api = {
  normalize(payload) {
    sandbox.__payloadJson = JSON.stringify(payload);
    return vm.runInContext(
      "window.HomeMoleculeCandidates.normalize(JSON.parse(__payloadJson))",
      sandbox,
    );
  },
};
assert(
  typeof rawApi.createLifecycle === "function",
  "Helper must expose a pure candidate request lifecycle",
);

const helperSource = fs.readFileSync(helperPath, "utf8");
assert(
  helperSource.includes("function sha256Prefix"),
  "Helper must provide a local synchronous SHA-256 implementation",
);
const instrumentedSource = helperSource
  .replace(
    "root.HomeMoleculeCandidates = { createLifecycle, normalize };",
    "root.HomeMoleculeCandidates = { createLifecycle, normalize, sha256Prefix };",
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

const lifecycle = rawApi.createLifecycle({
  maxRuns: 2,
  maxEventsPerRun: 2,
  maxCandidates: 4,
});
assert(lifecycle.startRequest() === true, "First request must start");
assert(lifecycle.startRequest() === false, "Second active request must be rejected");
assert(lifecycle.enqueue(normalized) === true, "Active request must queue candidates");
assert(lifecycle.enqueue(normalized) === false, "Duplicate event must be deduplicated");
const drained = lifecycle.complete();
assert(
  drained.length === 1 && drained[0].candidates.length === 1,
  "Sole active trace must drain exactly once on completion",
);
assert(lifecycle.complete().length === 0, "Completed runs must not drain twice");
assert(lifecycle.enqueue(normalized) === false, "Stale events must be ignored");
lifecycle.startRequest();
lifecycle.enqueue(normalized);
lifecycle.clear();
assert(lifecycle.complete().length === 0, "Reconnect/error clear must remove stale cards");

const canonicalDedupeLifecycle = rawApi.createLifecycle({ maxEventsPerRun: 3 });
canonicalDedupeLifecycle.startRequest();
canonicalDedupeLifecycle.enqueue(normalized);
const reorderedCanonicalPayload = payloadWithCandidates(["CCN", "CCO"]);
reorderedCanonicalPayload.trace_id = normalized.traceId;
const normalizedReorderedCanonical = api.normalize(reorderedCanonicalPayload);
assert(normalizedReorderedCanonical, "Reordered-ordinal lifecycle payload must normalize");
assert(
  canonicalDedupeLifecycle.enqueue(normalizedReorderedCanonical),
  "An event with one fresh canonical candidate must remain renderable",
);
const canonicalDedupeDrain = canonicalDedupeLifecycle.complete();
assert(
  canonicalDedupeDrain.length === 2 &&
    canonicalDedupeDrain[1].candidates.length === 1 &&
    canonicalDedupeDrain[1].candidates[0].canonical_smiles === "CCN",
  "Same canonical SMILES at a different valid ordinal/ID must render only once",
);

const capacityPayload = validPayload();
capacityPayload.trace_id = "trace-capacity-2";
capacityPayload.candidate_set.candidates[0].candidate_id = exactCandidateId(1, "CCN");
capacityPayload.candidate_set.candidates[0].original_smiles = "CCN";
capacityPayload.candidate_set.candidates[0].canonical_smiles = "CCN";
capacityPayload.candidate_set.candidates[0].smiles = "CCN";
const normalizedCapacityPayload = api.normalize(capacityPayload);
assert(normalizedCapacityPayload, "Capacity test payload must normalize");

const runCapacityLifecycle = rawApi.createLifecycle({ maxRuns: 1 });
runCapacityLifecycle.startRequest();
assert(runCapacityLifecycle.enqueue(normalized), "First trace must fit run cap");
assert(
  runCapacityLifecycle.enqueue(normalizedCapacityPayload) === false,
  "Per-request trace maps must enforce the configured run cap",
);

const eventCapacityLifecycle = rawApi.createLifecycle({
  maxEventsPerRun: 1,
  maxCandidates: 4,
});
eventCapacityLifecycle.startRequest();
eventCapacityLifecycle.enqueue(normalized);
normalizedCapacityPayload.traceId = normalized.traceId;
assert(
  eventCapacityLifecycle.enqueue(normalizedCapacityPayload) === false,
  "Per-trace queues must enforce the configured event cap",
);

const candidateCapacityLifecycle = rawApi.createLifecycle({ maxCandidates: 1 });
candidateCapacityLifecycle.startRequest();
candidateCapacityLifecycle.enqueue(normalized);
assert(
  candidateCapacityLifecycle.enqueue(normalizedCapacityPayload) === false,
  "Request queues must enforce the configured candidate cap",
);

const ambiguousLifecycle = rawApi.createLifecycle({ maxRuns: 2 });
ambiguousLifecycle.startRequest();
ambiguousLifecycle.enqueue(normalized);
const secondTracePayload = validPayload();
secondTracePayload.trace_id = "trace-candidates-2";
ambiguousLifecycle.enqueue(api.normalize(secondTracePayload));
assert(
  ambiguousLifecycle.complete().length === 0,
  "Completion without a trace must not drain ambiguous runs",
);
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
    payload.extra = true;
  },
  "Extra outer event fields must be rejected",
);
rejected(
  (payload) => {
    delete payload.warnings;
  },
  "Missing outer event fields must be rejected",
);
rejected(
  (payload) => {
    payload.source.extra = true;
  },
  "Extra source fields must be rejected",
);
rejected(
  (payload) => {
    delete payload.source.model_name;
  },
  "Missing source fields must be rejected",
);
rejected(
  (payload) => {
    payload.trace_id = "";
  },
  "Empty trace IDs must be rejected",
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
{
  const payload = validPayload("partial");
  payload.candidate_set.requested_count = 10000;
  assert(
    api.normalize(payload) === null,
    "Implausibly large safe-integer counts must be rejected",
  );
}

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
accepted(
  (payload) => {
    payload.candidate_set.rejected[0].details = {
      codes: ["invalid_smiles"],
    };
  },
  "Backend-supported rejected details must normalize",
);
{
  const payload = validPayload("partial");
  payload.candidate_set.rejected[0].unexpected = true;
  assert(api.normalize(payload) === null, "Unexpected rejected fields must be rejected");
}
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

sandbox.__basePayload = JSON.stringify(validPayload());
sandbox.__tagReads = 0;
const taggedResult = vm.runInContext(
  `(() => {
    const payload = JSON.parse(__basePayload);
    Object.defineProperty(payload, Symbol.toStringTag, {
      enumerable: true,
      get() { __tagReads += 1; return "Object"; }
    });
    return window.HomeMoleculeCandidates.normalize(payload);
  })()`,
  sandbox,
);
assert(
  taggedResult === null && sandbox.__tagReads === 0,
  "Symbol.toStringTag getters must not execute during rejection",
);

const lookalikeResult = vm.runInContext(
  `(() => {
    const payload = JSON.parse(__basePayload);
    const lookalike = {};
    Object.getOwnPropertyNames(Object.prototype).forEach((key) => {
      Object.defineProperty(lookalike, key, Object.getOwnPropertyDescriptor(Object.prototype, key));
    });
    Object.setPrototypeOf(payload, lookalike);
    return window.HomeMoleculeCandidates.normalize(payload);
  })()`,
  sandbox,
);
assert(lookalikeResult === null, "Lookalike object prototypes must be rejected");

sandbox.__iteratorReads = 0;
const hostileIteratorResult = vm.runInContext(
  `(() => {
    const payload = JSON.parse(__basePayload);
    const rejected = payload.candidate_set.rejected;
    const lookalike = {};
    Object.getOwnPropertyNames(Array.prototype).forEach((key) => {
      Object.defineProperty(lookalike, key, Object.getOwnPropertyDescriptor(Array.prototype, key));
    });
    Object.defineProperty(lookalike, Symbol.iterator, {
      value: function () {
        __iteratorReads += 1;
        return Array.prototype[Symbol.iterator].call(this);
      }
    });
    Object.setPrototypeOf(rejected, lookalike);
    return window.HomeMoleculeCandidates.normalize(payload);
  })()`,
  sandbox,
);
assert(
  hostileIteratorResult === null && sandbox.__iteratorReads === 0,
  "Hostile inherited array iterators must not execute",
);

sandbox.__getterReads = 0;
const getterResult = vm.runInContext(
  `(() => {
    const payload = JSON.parse(__basePayload);
    Object.defineProperty(payload.source, "tool_name", {
      enumerable: true,
      get() { __getterReads += 1; return "forged"; }
    });
    return window.HomeMoleculeCandidates.normalize(payload);
  })()`,
  sandbox,
);
assert(
  getterResult === null && sandbox.__getterReads === 0,
  "Accessor fields must be rejected without executing getters",
);

rejected(
  (payload) => {
    payload.candidate_set.candidates[0].metadata.large = "x".repeat(70000);
  },
  "Shared text budgets must reject oversized nested metadata",
);
assert(
  api.normalize(
    payloadWithCandidates(
      Array.from({ length: 65 }, (_, index) => "C".repeat(index + 1)),
    ),
  ) === null,
  "Candidate collections must be bounded before normalization work",
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
  mainSource.includes("HomeMoleculeCandidates.createLifecycle"),
  "Main must use the bounded structured-candidate lifecycle",
);
assert(
  mainSource.includes("moleculeCandidateLifecycle.isRequestInFlight()"),
  "Main must reject a second send while a request is active",
);
assert(
  mainSource.includes("let protocolDesyncedSocket") &&
    mainSource.includes("closeProtocolSocket(socket, 1002") &&
    mainSource.includes("closeProtocolSocket(socket, 1009"),
  "Malformed and oversized frames must block the exact socket as protocol desync",
);
assert(
  mainSource.includes("const socket = new WebSocket(wsUrl)") &&
    mainSource.match(/if \(socket !== ws\) return;/g)?.length >= 3,
  "WebSocket callbacks must be correlated to their captured socket instance",
);
assert(
  !mainSource.includes("pendingMoleculeCandidatePayloads"),
  "Main must not retain the unbounded legacy candidate array",
);
const sendCall = mainSource.indexOf("ws.send(payloadStr)");
const requestStart = mainSource.indexOf("moleculeCandidateLifecycle.startRequest()", sendCall);
assert(
  sendCall >= 0 && requestStart > sendCall,
  "Request state must become active only after WebSocket send succeeds",
);
const parseCall = mainSource.indexOf("JSON.parse(data)");
const rawLengthGuard = mainSource.lastIndexOf("data.length", parseCall);
assert(
  rawLengthGuard >= 0 && rawLengthGuard < parseCall,
  "Raw WebSocket messages must be length-capped before JSON parsing",
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
  rendererSource.includes("payload.candidates.slice(0, 32)"),
  "Renderer must independently cap candidate cards",
);
assert(
  rendererSource.includes("candidateId.textContent") &&
    rendererSource.includes("smilesText.textContent") &&
    rendererSource.includes("provenanceLabel.textContent"),
  "Candidate IDs, SMILES, and provenance must render through textContent",
);
assert(
  !rendererSource.includes(".innerHTML"),
  "Structured candidate renderer must construct all fallback content with DOM APIs",
);
assert(
  !mainSource.includes("工具生成"),
  "Provenance must come from the structured source, not a hardcoded label",
);
assert(
  !rendererSource.includes("candidate.metadata.properties") &&
    !rendererSource.includes("availableProperties") &&
    !rendererSource.includes("propertyLabels"),
  "Candidate metadata properties must never be rendered as verified science",
);
assert(
  rendererSource.includes("当前候选事件未携带经独立性质工具验证的属性"),
  "Candidate cards must explicitly explain that independent property evidence is absent",
);
assert(
  rendererSource.includes('/api/utils/smiles_to_image?smiles=${encodeURIComponent('),
  "Cards must request images only through the SMILES image endpoint",
);

class FakeClassList {
  constructor(element) {
    this.element = element;
  }

  _tokens() {
    return this.element.className.split(/\s+/).filter(Boolean);
  }

  contains(token) {
    return this._tokens().includes(token);
  }

  add(...tokens) {
    this.element.className = [...new Set([...this._tokens(), ...tokens])].join(" ");
  }

  remove(...tokens) {
    this.element.className = this._tokens()
      .filter((token) => !tokens.includes(token))
      .join(" ");
  }
}

class FakeElement {
  constructor(tagName = "div") {
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.className = "";
    this.classList = new FakeClassList(this);
    this.style = { cssText: "" };
    this.attributes = new Map();
    this.innerHTML = "";
    this._textContent = "";
    this.scrollTop = 0;
    this.scrollHeight = 0;
  }

  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    this.scrollHeight = this.children.length;
    return child;
  }

  addEventListener() {}

  replaceChildren(...children) {
    this.children.forEach((child) => { child.parentNode = null; });
    this.children = [];
    children.forEach((child) => this.appendChild(child));
  }

  remove() {
    if (!this.parentNode) return;
    this.parentNode.children = this.parentNode.children.filter(
      (child) => child !== this,
    );
    this.parentNode = null;
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  getAttribute(name) {
    return this.attributes.has(name) ? this.attributes.get(name) : null;
  }

  _descendants() {
    const result = [];
    this.children.forEach((child) => {
      result.push(child, ...child._descendants());
    });
    return result;
  }

  querySelector(selector) {
    if (selector === ".assistant-wrapper:last-child .message-box") {
      const wrappers = this._descendants().filter((element) =>
        element.classList.contains("assistant-wrapper"),
      );
      const wrapper = wrappers[wrappers.length - 1];
      return wrapper
        ? wrapper._descendants().find((element) =>
            element.classList.contains("message-box"),
          ) || null
        : null;
    }
    if (selector === "div:last-child") {
      return [...this.children]
        .reverse()
        .find((child) => child.tagName === "DIV") || null;
    }
    if (selector.startsWith(".")) {
      const className = selector.slice(1);
      return this._descendants().find((element) =>
        element.classList.contains(className),
      ) || null;
    }
    return null;
  }

  closest(selector) {
    if (!selector.startsWith(".")) return null;
    const className = selector.slice(1);
    let current = this;
    while (current) {
      if (current.classList.contains(className)) return current;
      current = current.parentNode;
    }
    return null;
  }

  get textContent() {
    return this._textContent + this.children.map((child) => child.textContent).join("");
  }

  set textContent(value) {
    this._textContent = String(value);
    this.children = [];
  }
}

class FakeWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  static instances = [];

  constructor(url) {
    this.url = url;
    this.readyState = FakeWebSocket.CONNECTING;
    this.sent = [];
    this.closeCalls = [];
    FakeWebSocket.instances.push(this);
  }

  send(value) {
    this.sent.push(value);
  }

  close(code, reason) {
    this.closeCalls.push({ code, reason });
    this.readyState = FakeWebSocket.CLOSING;
  }
}

const behaviorMainSource = mainSource
  .replace(
    "function renderMoleculeCandidates(messageElement, payload) {",
    "function renderMoleculeCandidatesOriginal(messageElement, payload) {",
  )
  .replace(
    /  window\.HomeMain = \{\r?\n    init: init,\r?\n  \};/,
    `  function renderMoleculeCandidates(messageElement, payload) {
    window.__renderCalls.push({
      messageElement,
      payload,
      requestWasInFlight: moleculeCandidateLifecycle.isRequestInFlight(),
    });
  }

  window.HomeMain = {
    init: init,
    __test: {
      connectWebSocket,
      handleWebSocketMessage,
      lifecycle: moleculeCandidateLifecycle,
      sendMessage,
      renderMoleculeCandidatesOriginal,
      getSocket: () => ws,
      setElements: (value) => Object.assign(elements, value),
      setSocket: (value) => { ws = value; isConnected = true; },
    },
  };`,
  );
assert(
  behaviorMainSource.includes("__test:"),
  "Behavior test instrumentation must expose main lifecycle hooks",
);

const behaviorDocument = {
  readyState: "loading",
  body: new FakeElement("body"),
  addEventListener() {},
  createElement(tagName) { return new FakeElement(tagName); },
  querySelector() { return null; },
};
const behaviorNotifications = [];
const behaviorSandbox = {
  window: {
    location: { host: "example.test", href: "http://example.test/" },
    __renderCalls: [],
  },
  document: behaviorDocument,
  console: { log() {}, warn() {}, error() {} },
  WebSocket: FakeWebSocket,
  HomeState: { currentMessages: [] },
  HomeTheme: { init() {} },
  HomeAdvancedOptions: { getConfig: () => ({ ragCount: 3, temperature: 0.2, molCount: 2 }) },
  HomeFormatters: { formatContent: (value) => String(value ?? "") },
  HomeChatRenderer: {
    getCurrentTime: () => "12:00",
    scrollToBottom() {},
    showNotification: (...args) => behaviorNotifications.push(args),
    updateConnectionStatus() {},
  },
  clearTimeout() {},
  setTimeout: () => 1,
};
behaviorSandbox.window.window = behaviorSandbox.window;
behaviorSandbox.globalThis = behaviorSandbox;
vm.createContext(behaviorSandbox);
vm.runInContext(helperSource, behaviorSandbox, { filename: helperPath });
vm.runInContext(behaviorMainSource, behaviorSandbox, {
  filename: path.join(root, "src/web/static/js/home/main.js"),
});

const behaviorHooks = behaviorSandbox.window.HomeMain.__test;
const behaviorChat = new FakeElement("div");
const behaviorInput = { value: "second request" };
behaviorHooks.setElements({ chatContainer: behaviorChat, input: behaviorInput });

const metadataTrustPayload = api.normalize(validPayload());
metadataTrustPayload.candidates[0].metadata.properties = {
  logp: 99.9,
  qed: 0.999,
  molecular_weight: 12345,
};
const metadataTrustWrapper = new FakeElement("div");
metadataTrustWrapper.classList.add("assistant-wrapper");
const metadataTrustContainer = new FakeElement("div");
metadataTrustContainer.classList.add("message-box");
metadataTrustWrapper.appendChild(metadataTrustContainer);
behaviorHooks.renderMoleculeCandidatesOriginal(
  metadataTrustContainer,
  metadataTrustPayload,
);
assert(
  metadataTrustContainer.textContent.includes(
    "当前候选事件未携带经独立性质工具验证的属性",
  ),
  "Renderer must show the independent-property-evidence notice",
);
assert(
  !metadataTrustContainer.textContent.includes("99.9") &&
    !metadataTrustContainer.textContent.includes("0.999") &&
    !metadataTrustContainer.textContent.includes("12345") &&
    !metadataTrustContainer.textContent.includes("LogP") &&
    !metadataTrustContainer.textContent.includes("QED") &&
    !metadataTrustContainer.textContent.includes("本步骤未提供经工具验证的属性"),
  "Ordinary or malicious metadata properties must not be presented as verified values",
);

function activeSocket() {
  const socket = new FakeWebSocket("ws://example.test/ws");
  socket.readyState = FakeWebSocket.OPEN;
  behaviorHooks.setSocket(socket);
  return socket;
}

function beginCandidateRun(socket) {
  behaviorHooks.lifecycle.clear();
  assert(behaviorHooks.lifecycle.startRequest(), "Behavior run must start");
  behaviorHooks.handleWebSocketMessage(JSON.stringify(validPayload()), socket);
}

let behaviorSocket = activeSocket();
beginCandidateRun(behaviorSocket);
behaviorHooks.handleWebSocketMessage(
  JSON.stringify({ type: "complete", content: "complete result" }),
  behaviorSocket,
);
assert(
  behaviorSandbox.window.__renderCalls.length === 1 &&
    behaviorSandbox.window.__renderCalls[0].requestWasInFlight &&
    !behaviorHooks.lifecycle.isRequestInFlight(),
  "molecule_candidates followed by complete must render once and finish",
);
behaviorHooks.handleWebSocketMessage(
  JSON.stringify({ type: "complete", content: "complete result" }),
  behaviorSocket,
);
assert(
  behaviorSandbox.window.__renderCalls.length === 1,
  "Duplicate complete frames must not render candidate cards twice",
);

behaviorSandbox.window.__renderCalls.length = 0;
behaviorChat.replaceChildren();
behaviorSocket = activeSocket();
beginCandidateRun(behaviorSocket);
behaviorHooks.handleWebSocketMessage(
  JSON.stringify({ type: "message", message: "summarized result" }),
  behaviorSocket,
);
assert(
  behaviorSandbox.window.__renderCalls.length === 1 &&
    behaviorSandbox.window.__renderCalls[0].messageElement instanceof FakeElement &&
    behaviorSandbox.window.__renderCalls[0].messageElement.classList.contains("complete") &&
    behaviorSandbox.window.__renderCalls[0].messageElement.getAttribute("data-content") ===
      "summarized result" &&
    behaviorSandbox.window.__renderCalls[0].requestWasInFlight &&
    !behaviorHooks.lifecycle.isRequestInFlight(),
  "molecule_candidates followed by message must render once on its assistant box",
);
behaviorHooks.handleWebSocketMessage(
  JSON.stringify({ type: "message", message: "late summary" }),
  behaviorSocket,
);
assert(
  behaviorSandbox.window.__renderCalls.length === 1,
  "Later message frames without queued candidates must not duplicate cards",
);

behaviorHooks.connectWebSocket();
behaviorSocket = behaviorHooks.getSocket();
behaviorSocket.readyState = FakeWebSocket.OPEN;
behaviorHooks.lifecycle.startRequest();
behaviorSocket.onmessage({ data: "{" });
assert(
  behaviorSocket.closeCalls.at(-1)?.code === 1002 &&
    behaviorHooks.lifecycle.isRequestInFlight(),
  "Malformed frames must close with 1002 without reopening request sending",
);
behaviorSocket.onerror({ type: "error" });
assert(
  behaviorHooks.lifecycle.isRequestInFlight(),
  "Protocol-desync error callbacks must preserve the send block until close",
);
const malformedSentCount = behaviorSocket.sent.length;
behaviorHooks.sendMessage();
assert(
  behaviorSocket.sent.length === malformedSentCount,
  "A second send must remain blocked during malformed-frame desync",
);
behaviorSocket.onclose({ code: 1002, reason: "invalid", wasClean: true });
assert(
  !behaviorHooks.lifecycle.isRequestInFlight(),
  "Closing the desynchronized socket must release its request state",
);

behaviorHooks.connectWebSocket();
behaviorSocket = behaviorHooks.getSocket();
behaviorSocket.readyState = FakeWebSocket.OPEN;
behaviorHooks.lifecycle.startRequest();
behaviorSocket.onmessage({ data: "x".repeat(256 * 1024 + 1) });
assert(
  behaviorSocket.closeCalls.at(-1)?.code === 1009 &&
    behaviorHooks.lifecycle.isRequestInFlight(),
  "Oversized frames must close with 1009 and retain the active-request block",
);
const oversizedSentCount = behaviorSocket.sent.length;
behaviorHooks.sendMessage();
assert(
  behaviorSocket.sent.length === oversizedSentCount,
  "A second send must remain blocked during oversized-frame desync",
);
behaviorSocket.onclose({ code: 1009, reason: "large", wasClean: true });

FakeWebSocket.instances.length = 0;
behaviorHooks.connectWebSocket();
const staleSocket = behaviorHooks.getSocket();
behaviorHooks.lifecycle.startRequest();
behaviorHooks.connectWebSocket();
const currentSocket = behaviorHooks.getSocket();
assert(
  !behaviorHooks.lifecycle.isRequestInFlight(),
  "Reconnect must clear the prior current socket run",
);
behaviorHooks.lifecycle.startRequest();
currentSocket.onmessage({ data: JSON.stringify(validPayload()) });
staleSocket.onmessage({ data: "{" });
staleSocket.onerror({ type: "error" });
staleSocket.onclose({ code: 1006, reason: "stale", wasClean: false });
assert(
  behaviorHooks.lifecycle.isRequestInFlight() && staleSocket.closeCalls.length === 1,
  "Stale socket callbacks must not close or clear the current run",
);
currentSocket.onerror({ type: "error" });
assert(
  !behaviorHooks.lifecycle.isRequestInFlight(),
  "Current socket errors must clear candidate state",
);
behaviorHooks.lifecycle.startRequest();
currentSocket.onclose({ code: 1000, reason: "done", wasClean: true });
assert(
  !behaviorHooks.lifecycle.isRequestInFlight(),
  "Current socket close must clear candidate state safely",
);

const helperScript =
  "/static/js/home/molecule_candidates.js?v=20260902-candidate-correlation-v5";
const safeScript = "/static/js/shared/safe_render.js";
const mainScriptMatch = indexHtml.match(
  /\/static\/js\/home\/main\.js\?v=[^"']+/,
);
const mainScript = mainScriptMatch ? mainScriptMatch[0] : "";
assert(indexHtml.includes(helperScript), "Template must version molecule candidate helper");
assert(mainScript, "Template must version homepage main");
assert(
  indexHtml.indexOf(safeScript) < indexHtml.indexOf(helperScript) &&
    indexHtml.indexOf(helperScript) < indexHtml.indexOf(mainScript),
  "Template must load safety dependencies before candidate helper and main after it",
);

console.log("Structured molecule candidate frontend checks passed");
