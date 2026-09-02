(function (root) {
  "use strict";

  const ObjectPrototype = Object.prototype;
  const ArrayPrototype = Array.prototype;
  const getPrototypeOf = Object.getPrototypeOf;
  const getOwnPropertyDescriptor = Object.getOwnPropertyDescriptor;
  const ownKeys = Reflect.ownKeys;
  const arrayIsArray = Array.isArray;
  const forbiddenKeys = new Set(["__proto__", "constructor", "prototype"]);
  const eventFields = ["type", "trace_id", "source", "candidate_set", "warnings"];
  const sourceFields = ["tool_name", "model_name", "status"];
  const rejectedFields = ["source_index", "smiles", "reason"];
  const rejectedFieldsWithDetails = rejectedFields.concat("details");
  const candidateSetFields = [
    "version",
    "requested_count",
    "valid_count",
    "unique_count",
    "invalid_count",
    "duplicate_count",
    "candidates",
    "rejected",
    "status",
  ];
  const candidateFields = [
    "candidate_id",
    "source_index",
    "original_smiles",
    "canonical_smiles",
    "validation",
    "generation_provenance",
    "metadata",
    "smiles",
  ];
  const rejectedReasons = new Set([
    "invalid_smiles",
    "invalid_candidate_metadata",
    "duplicate_smiles",
    "excess_candidate",
  ]);
  const maxCollectionItems = 64;
  const maxWarnings = 32;
  const maxCloneDepth = 6;
  const maxCloneNodes = 512;
  const maxCloneTextChars = 65536;
  const sha256Constants = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5,
    0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
    0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc,
    0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
    0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
    0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3,
    0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5,
    0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
    0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
  ];
  const allowedElements = new Set([
    "Ac", "Ag", "Al", "Am", "Ar", "As", "At", "Au", "Ba", "Be", "Bh",
    "Bi", "Bk", "Br", "Ca", "Cd", "Ce", "Cf", "Cl", "Cm", "Cn", "Co",
    "Cr", "Cs", "Cu", "Db", "Ds", "Dy", "Er", "Es", "Eu", "Fe", "Fl",
    "Fm", "Fr", "Ga", "Gd", "Ge", "He", "Hf", "Hg", "Ho", "Hs", "In",
    "Ir", "Kr", "La", "Li", "Lr", "Lu", "Lv", "Mc", "Md", "Mg", "Mn",
    "Mo", "Mt", "Na", "Nb", "Nd", "Ne", "Nh", "Ni", "No", "Np", "Os",
    "Pa", "Pb", "Pd", "Pm", "Po", "Pr", "Pt", "Ra", "Rb", "Re", "Rf",
    "Rg", "Rh", "Rn", "Ru", "Sb", "Sc", "Se", "Sg", "Si", "Sm", "Sn",
    "Sr", "Ta", "Tb", "Tc", "Te", "Th", "Ti", "Tl", "Tm", "Ts", "Xe",
    "Yb", "Zn", "Zr", "B", "C", "N", "O", "P", "S", "F", "I", "H",
    "b", "c", "n", "o", "p", "s",
  ]);

  function isPlainRecord(value) {
    if (
      value === null ||
      typeof value !== "object" ||
      arrayIsArray(value) ||
      getPrototypeOf(value) !== ObjectPrototype
    ) {
      return false;
    }
    const keys = ownKeys(value);
    if (keys.length > maxCollectionItems) return false;
    return keys.every((key) => {
      if (typeof key !== "string" || forbiddenKeys.has(key)) return false;
      const descriptor = getOwnPropertyDescriptor(value, key);
      return Boolean(descriptor && descriptor.enumerable && "value" in descriptor);
    });
  }

  function isPlainArray(value) {
    if (!arrayIsArray(value) || getPrototypeOf(value) !== ArrayPrototype) return false;
    const lengthDescriptor = getOwnPropertyDescriptor(value, "length");
    if (!lengthDescriptor || !("value" in lengthDescriptor)) return false;
    const length = lengthDescriptor.value;
    if (!Number.isSafeInteger(length) || length < 0 || length > maxCollectionItems) {
      return false;
    }
    const keys = ownKeys(value);
    if (keys.length !== length + 1 || !keys.includes("length")) return false;
    for (let index = 0; index < length; index += 1) {
      const key = String(index);
      if (!keys.includes(key)) return false;
      const descriptor = getOwnPropertyDescriptor(value, key);
      if (!descriptor || !descriptor.enumerable || !("value" in descriptor)) {
        return false;
      }
    }
    return keys.every((key) =>
      key === "length" ||
      (typeof key === "string" && /^(0|[1-9]\d*)$/.test(key))
    );
  }

  function hasExactFields(value, expectedFields) {
    if (!isPlainRecord(value)) return false;
    const actual = ownKeys(value).slice().sort();
    const expected = expectedFields.slice().sort();
    return (
      actual.length === expected.length &&
      actual.every((key, index) => key === expected[index])
    );
  }

  function ownValue(record, key) {
    if (!isPlainRecord(record)) return undefined;
    const descriptor = getOwnPropertyDescriptor(record, key);
    return descriptor && "value" in descriptor ? descriptor.value : undefined;
  }

  function arrayLength(value) {
    if (!isPlainArray(value)) return -1;
    return getOwnPropertyDescriptor(value, "length").value;
  }

  function arrayValue(value, index) {
    const descriptor = getOwnPropertyDescriptor(value, String(index));
    return descriptor && "value" in descriptor ? descriptor.value : undefined;
  }

  function isSafeString(value, maxLength, allowEmpty) {
    return (
      typeof value === "string" &&
      value.length <= maxLength &&
      (allowEmpty || value.trim().length > 0) &&
      !/[\u0000-\u001f\u007f]/.test(value) &&
      !/[\ud800-\udfff]/.test(value)
    );
  }

  function isNonnegativeInteger(value) {
    return Number.isSafeInteger(value) && value >= 0;
  }

  function isPositiveInteger(value) {
    return Number.isSafeInteger(value) && value > 0;
  }

  function utf8Bytes(value) {
    const bytes = [];
    for (let index = 0; index < value.length; index += 1) {
      let codePoint = value.charCodeAt(index);
      if (codePoint >= 0xd800 && codePoint <= 0xdbff) {
        const low = value.charCodeAt(index + 1);
        if (low >= 0xdc00 && low <= 0xdfff) {
          codePoint =
            0x10000 + ((codePoint - 0xd800) << 10) + (low - 0xdc00);
          index += 1;
        } else {
          codePoint = 0xfffd;
        }
      } else if (codePoint >= 0xdc00 && codePoint <= 0xdfff) {
        codePoint = 0xfffd;
      }

      if (codePoint <= 0x7f) {
        bytes.push(codePoint);
      } else if (codePoint <= 0x7ff) {
        bytes.push(0xc0 | (codePoint >>> 6), 0x80 | (codePoint & 0x3f));
      } else if (codePoint <= 0xffff) {
        bytes.push(
          0xe0 | (codePoint >>> 12),
          0x80 | ((codePoint >>> 6) & 0x3f),
          0x80 | (codePoint & 0x3f)
        );
      } else {
        bytes.push(
          0xf0 | (codePoint >>> 18),
          0x80 | ((codePoint >>> 12) & 0x3f),
          0x80 | ((codePoint >>> 6) & 0x3f),
          0x80 | (codePoint & 0x3f)
        );
      }
    }
    return bytes;
  }

  function rotateRight(value, amount) {
    return (value >>> amount) | (value << (32 - amount));
  }

  function sha256Prefix(value) {
    const bytes = utf8Bytes(value);
    const bitLength = bytes.length * 8;
    bytes.push(0x80);
    while (bytes.length % 64 !== 56) bytes.push(0);
    const highBits = Math.floor(bitLength / 0x100000000);
    const lowBits = bitLength >>> 0;
    for (let shift = 24; shift >= 0; shift -= 8) {
      bytes.push((highBits >>> shift) & 0xff);
    }
    for (let shift = 24; shift >= 0; shift -= 8) {
      bytes.push((lowBits >>> shift) & 0xff);
    }

    const hash = [
      0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
      0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
    ];
    const words = new Uint32Array(64);
    for (let offset = 0; offset < bytes.length; offset += 64) {
      for (let index = 0; index < 16; index += 1) {
        const wordOffset = offset + index * 4;
        words[index] =
          ((bytes[wordOffset] << 24) |
            (bytes[wordOffset + 1] << 16) |
            (bytes[wordOffset + 2] << 8) |
            bytes[wordOffset + 3]) >>> 0;
      }
      for (let index = 16; index < 64; index += 1) {
        const previous = words[index - 15];
        const prior = words[index - 2];
        const sigma0 =
          rotateRight(previous, 7) ^
          rotateRight(previous, 18) ^
          (previous >>> 3);
        const sigma1 =
          rotateRight(prior, 17) ^
          rotateRight(prior, 19) ^
          (prior >>> 10);
        words[index] =
          (words[index - 16] + sigma0 + words[index - 7] + sigma1) >>> 0;
      }

      let [a, b, c, d, e, f, g, h] = hash;
      for (let index = 0; index < 64; index += 1) {
        const sum1 = rotateRight(e, 6) ^ rotateRight(e, 11) ^ rotateRight(e, 25);
        const choice = (e & f) ^ (~e & g);
        const temporary1 =
          (h + sum1 + choice + sha256Constants[index] + words[index]) >>> 0;
        const sum0 = rotateRight(a, 2) ^ rotateRight(a, 13) ^ rotateRight(a, 22);
        const majority = (a & b) ^ (a & c) ^ (b & c);
        const temporary2 = (sum0 + majority) >>> 0;
        h = g;
        g = f;
        f = e;
        e = (d + temporary1) >>> 0;
        d = c;
        c = b;
        b = a;
        a = (temporary1 + temporary2) >>> 0;
      }
      hash[0] = (hash[0] + a) >>> 0;
      hash[1] = (hash[1] + b) >>> 0;
      hash[2] = (hash[2] + c) >>> 0;
      hash[3] = (hash[3] + d) >>> 0;
      hash[4] = (hash[4] + e) >>> 0;
      hash[5] = (hash[5] + f) >>> 0;
      hash[6] = (hash[6] + g) >>> 0;
      hash[7] = (hash[7] + h) >>> 0;
    }
    return hash
      .map((word) => word.toString(16).padStart(8, "0"))
      .join("")
      .slice(0, 8);
  }

  function createCloneBudget() {
    return { nodes: 0, textChars: 0 };
  }

  function cloneJson(value, depth, budget) {
    budget.nodes += 1;
    if (depth > maxCloneDepth || budget.nodes > maxCloneNodes) {
      return { ok: false, value: null };
    }
    if (typeof value === "string") {
      budget.textChars += value.length;
      return budget.textChars <= maxCloneTextChars
        ? { ok: true, value }
        : { ok: false, value: null };
    }
    if (value === null || typeof value === "boolean") {
      return { ok: true, value };
    }
    if (typeof value === "number") {
      return Number.isFinite(value)
        ? { ok: true, value }
        : { ok: false, value: null };
    }
    if (isPlainArray(value)) {
      const length = arrayLength(value);
      if (length < 0 || length > maxCollectionItems) {
        return { ok: false, value: null };
      }
      const output = [];
      for (let index = 0; index < length; index += 1) {
        const cloned = cloneJson(arrayValue(value, index), depth + 1, budget);
        if (!cloned || !cloned.ok) return { ok: false, value: null };
        output.push(cloned.value);
      }
      return { ok: true, value: output };
    }
    if (!isPlainRecord(value)) {
      return { ok: false, value: null };
    }
    const keys = ownKeys(value);
    if (keys.length > maxCollectionItems) return { ok: false, value: null };
    const output = {};
    for (let index = 0; index < keys.length; index += 1) {
      const key = keys[index];
      const cloned = cloneJson(ownValue(value, key), depth + 1, budget);
      if (!cloned || !cloned.ok) return { ok: false, value: null };
      output[key] = cloned.value;
    }
    return { ok: true, value: output };
  }

  function isBracketAtom(content) {
    if (!content || !/^[A-Za-z0-9@+\-:.]*$/.test(content)) return false;
    const atom = /^\d*(\*|[A-Z][a-z]?|[bcnops])/.exec(content);
    return Boolean(atom && (atom[1] === "*" || allowedElements.has(atom[1])));
  }

  function isConservativeSmiles(value) {
    if (
      !isSafeString(value, 512, false) ||
      !/^[A-Za-z0-9@+\-\[\]()=#.%\\\/:*]+$/.test(value)
    ) {
      return false;
    }

    let parentheses = 0;
    for (let index = 0; index < value.length; index += 1) {
      const character = value[index];
      if (character === "(") {
        parentheses += 1;
        continue;
      }
      if (character === ")") {
        parentheses -= 1;
        if (parentheses < 0) return false;
        continue;
      }
      if (character === "[") {
        const closing = value.indexOf("]", index + 1);
        if (closing < 0 || !isBracketAtom(value.slice(index + 1, closing))) {
          return false;
        }
        index = closing;
        continue;
      }
      if (character === "]") return false;
      if (/[A-Za-z]/.test(character)) {
        const pair = value.slice(index, index + 2);
        if (pair === "Cl" || pair === "Br") {
          index += 1;
        } else if (!"BCNOPSFIbcnops".includes(character)) {
          return false;
        }
      }
    }
    return parentheses === 0;
  }

  function normalizeCandidate(value, expectedOrdinal, cloneBudget) {
    if (!hasExactFields(value, candidateFields)) return null;
    const candidateId = ownValue(value, "candidate_id");
    const sourceIndex = ownValue(value, "source_index");
    const originalSmiles = ownValue(value, "original_smiles");
    const canonicalSmiles = ownValue(value, "canonical_smiles");
    const smiles = ownValue(value, "smiles");
    const validation = ownValue(value, "validation");
    const generationProvenance = ownValue(value, "generation_provenance");
    const metadata = ownValue(value, "metadata");
    if (
      !isSafeString(candidateId, 128, false) ||
      !isPositiveInteger(sourceIndex) ||
      !isSafeString(originalSmiles, 512, false) ||
      !isConservativeSmiles(canonicalSmiles) ||
      !/^cand-\d{3,}-[0-9a-f]{8}$/.test(candidateId) ||
      candidateId !==
        `cand-${String(expectedOrdinal).padStart(3, "0")}-${sha256Prefix(
          canonicalSmiles
        )}` ||
      smiles !== canonicalSmiles ||
      !hasExactFields(validation, ["valid", "method"]) ||
      ownValue(validation, "valid") !== true ||
      ownValue(validation, "method") !== "RDKit" ||
      !isPlainRecord(generationProvenance) ||
      !isPlainRecord(metadata)
    ) {
      return null;
    }
    const clonedProvenance = cloneJson(generationProvenance, 0, cloneBudget);
    const clonedMetadata = cloneJson(metadata, 0, cloneBudget);
    if (
      !clonedProvenance ||
      !clonedProvenance.ok ||
      !clonedMetadata ||
      !clonedMetadata.ok
    ) {
      return null;
    }
    return {
      candidate_id: candidateId,
      source_index: sourceIndex,
      original_smiles: originalSmiles,
      canonical_smiles: canonicalSmiles,
      validation: { valid: true, method: "RDKit" },
      generation_provenance: clonedProvenance.value,
      metadata: clonedMetadata.value,
      smiles: canonicalSmiles,
    };
  }

  function normalizeRejected(value, cloneBudget) {
    const hasDetails = hasExactFields(value, rejectedFieldsWithDetails);
    if (!hasDetails && !hasExactFields(value, rejectedFields)) return null;
    const sourceIndex = ownValue(value, "source_index");
    const smiles = ownValue(value, "smiles");
    const reason = ownValue(value, "reason");
    if (
      !isPositiveInteger(sourceIndex) ||
      typeof smiles !== "string" ||
      !isSafeString(smiles, 512, reason === "invalid_smiles") ||
      !rejectedReasons.has(reason) ||
      (reason !== "invalid_smiles" && smiles.trim().length === 0)
    ) {
      return null;
    }
    let details;
    if (hasDetails) {
      details = cloneJson(ownValue(value, "details"), 0, cloneBudget);
      if (!details || !details.ok || !isPlainRecord(details.value)) return null;
    }
    const normalized = { source_index: sourceIndex, smiles, reason };
    if (hasDetails) normalized.details = details.value;
    return normalized;
  }

  function normalize(payload) {
    try {
      if (
        !hasExactFields(payload, eventFields) ||
        ownValue(payload, "type") !== "molecule_candidates"
      ) {
        return null;
      }
      const traceId = ownValue(payload, "trace_id");
      const source = ownValue(payload, "source");
      const candidateSet = ownValue(payload, "candidate_set");
      const warnings = ownValue(payload, "warnings");
      if (
        !isSafeString(traceId, 128, false) ||
        !hasExactFields(source, sourceFields) ||
        !hasExactFields(candidateSet, candidateSetFields) ||
        !isPlainArray(warnings)
      ) {
        return null;
      }

      const sourceStatus = ownValue(source, "status");
      const candidateStatus = ownValue(candidateSet, "status");
      const tool = ownValue(source, "tool_name");
      const model = ownValue(source, "model_name");
      if (
        !["succeeded", "partial"].includes(sourceStatus) ||
        candidateStatus !== sourceStatus ||
        !isSafeString(tool, 128, true) ||
        !isSafeString(model, 128, true) ||
        ownValue(candidateSet, "version") !== "1"
      ) {
        return null;
      }

      const countNames = [
        "requested_count",
        "valid_count",
        "unique_count",
        "invalid_count",
        "duplicate_count",
      ];
      const counts = {};
      for (const name of countNames) {
        const value = ownValue(candidateSet, name);
        if (
          !isNonnegativeInteger(value) ||
          value > maxCollectionItems * 2
        ) {
          return null;
        }
        counts[name] = value;
      }

      const rawCandidates = ownValue(candidateSet, "candidates");
      const rawRejected = ownValue(candidateSet, "rejected");
      const candidateLength = arrayLength(rawCandidates);
      const rejectedLength = arrayLength(rawRejected);
      const warningLength = arrayLength(warnings);
      if (
        candidateLength <= 0 ||
        candidateLength > maxCollectionItems ||
        rejectedLength < 0 ||
        rejectedLength > maxCollectionItems ||
        warningLength < 0 ||
        warningLength > maxWarnings
      ) {
        return null;
      }
      if (
        counts.valid_count !== candidateLength ||
        counts.unique_count !== candidateLength ||
        counts.requested_count < candidateLength ||
        (candidateStatus === "succeeded" &&
          counts.requested_count !== candidateLength) ||
        (candidateStatus === "partial" &&
          counts.requested_count <= candidateLength)
      ) {
        return null;
      }

      const candidates = [];
      const candidateIds = new Set();
      const canonicalSmiles = new Set();
      const sourceIndexes = [];
      const cloneBudget = createCloneBudget();
      for (let index = 0; index < candidateLength; index += 1) {
        const candidate = normalizeCandidate(
          arrayValue(rawCandidates, index),
          index + 1,
          cloneBudget
        );
        if (
          !candidate ||
          candidateIds.has(candidate.candidate_id) ||
          canonicalSmiles.has(candidate.canonical_smiles)
        ) {
          return null;
        }
        candidateIds.add(candidate.candidate_id);
        canonicalSmiles.add(candidate.canonical_smiles);
        sourceIndexes.push(candidate.source_index);
        candidates.push(candidate);
      }

      let invalidCount = 0;
      let duplicateCount = 0;
      for (let index = 0; index < rejectedLength; index += 1) {
        const rejected = normalizeRejected(
          arrayValue(rawRejected, index),
          cloneBudget
        );
        if (!rejected) return null;
        sourceIndexes.push(rejected.source_index);
        if (
          rejected.reason === "invalid_smiles" ||
          rejected.reason === "invalid_candidate_metadata"
        ) {
          invalidCount += 1;
        } else if (rejected.reason === "duplicate_smiles") {
          duplicateCount += 1;
        }
      }
      if (
        invalidCount !== counts.invalid_count ||
        duplicateCount !== counts.duplicate_count ||
        new Set(sourceIndexes).size !== sourceIndexes.length
      ) {
        return null;
      }
      const orderedSourceIndexes = sourceIndexes
        .slice()
        .sort((left, right) => left - right);
      if (
        orderedSourceIndexes.some(
          (sourceIndex, index) => sourceIndex !== index + 1
        )
      ) {
        return null;
      }

      const normalizedWarnings = [];
      for (let index = 0; index < warningLength; index += 1) {
        const warning = arrayValue(warnings, index);
        if (!isSafeString(warning, 256, true)) return null;
        cloneBudget.textChars += warning.length;
        if (cloneBudget.textChars > maxCloneTextChars) return null;
        normalizedWarnings.push(warning);
      }

      return {
        traceId,
        source: { tool, model, status: sourceStatus },
        candidates,
        counts,
        warnings: normalizedWarnings,
      };
    } catch (_) {
      return null;
    }
  }

  function createLifecycle(options) {
    const settings = options || {};
    function boundedOption(value, fallback, upperBound) {
      return Number.isSafeInteger(value) && value > 0
        ? Math.min(value, upperBound)
        : fallback;
    }
    const maxRuns = boundedOption(settings.maxRuns, 4, 8);
    const maxEventsPerRun = boundedOption(settings.maxEventsPerRun, 8, 16);
    const maxCandidates = boundedOption(settings.maxCandidates, 32, 64);
    const runs = new Map();
    let requestInFlight = false;
    let queuedCandidates = 0;

    function clear() {
      runs.clear();
      queuedCandidates = 0;
      requestInFlight = false;
    }

    function startRequest() {
      if (requestInFlight) return false;
      clear();
      requestInFlight = true;
      return true;
    }

    function enqueue(payload) {
      if (
        !requestInFlight ||
        !payload ||
        typeof payload.traceId !== "string" ||
        payload.traceId.length === 0 ||
        !arrayIsArray(payload.candidates) ||
        payload.candidates.length === 0
      ) {
        return false;
      }
      let run = runs.get(payload.traceId);
      if (!run) {
        if (runs.size >= maxRuns) return false;
        run = {
          candidateIds: new Set(),
          canonicalSmiles: new Set(),
          events: [],
        };
        runs.set(payload.traceId, run);
      }
      if (run.events.length >= maxEventsPerRun) return false;

      const freshCandidates = [];
      const freshCandidateIds = new Set();
      const freshCanonicalSmiles = new Set();
      for (let index = 0; index < payload.candidates.length; index += 1) {
        const candidate = payload.candidates[index];
        const candidateId = candidate && candidate.candidate_id;
        const canonicalSmiles = candidate && candidate.canonical_smiles;
        if (
          typeof candidateId !== "string" ||
          typeof canonicalSmiles !== "string" ||
          run.candidateIds.has(candidateId) ||
          freshCandidateIds.has(candidateId) ||
          run.canonicalSmiles.has(canonicalSmiles) ||
          freshCanonicalSmiles.has(canonicalSmiles)
        ) {
          continue;
        }
        freshCandidateIds.add(candidateId);
        freshCanonicalSmiles.add(canonicalSmiles);
        freshCandidates.push(candidate);
      }
      if (
        freshCandidates.length === 0 ||
        queuedCandidates + freshCandidates.length > maxCandidates
      ) {
        return false;
      }
      for (let index = 0; index < freshCandidates.length; index += 1) {
        run.candidateIds.add(freshCandidates[index].candidate_id);
        run.canonicalSmiles.add(freshCandidates[index].canonical_smiles);
      }
      queuedCandidates += freshCandidates.length;
      run.events.push({
        traceId: payload.traceId,
        source: payload.source,
        candidates: freshCandidates,
        counts: payload.counts,
        warnings: payload.warnings,
      });
      return true;
    }

    function drain() {
      let drained = [];
      if (requestInFlight && runs.size === 1) {
        const run = runs.values().next().value;
        drained = run.events.slice();
      }
      runs.clear();
      queuedCandidates = 0;
      return drained;
    }

    function complete() {
      const drained = drain();
      requestInFlight = false;
      return drained;
    }

    return {
      clear,
      complete,
      drain,
      enqueue,
      isRequestInFlight: () => requestInFlight,
      startRequest,
    };
  }

  root.HomeMoleculeCandidates = { createLifecycle, normalize };
})(window);
