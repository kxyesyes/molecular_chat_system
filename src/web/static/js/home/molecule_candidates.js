(function (root) {
  "use strict";

  const hasOwn = (value, key) =>
    Object.prototype.hasOwnProperty.call(value, key);
  const forbiddenKeys = new Set(["__proto__", "constructor", "prototype"]);
  const objectPrototypeKeys = Object.getOwnPropertyNames(Object.prototype)
    .slice()
    .sort()
    .join("\u0000");
  const arrayPrototypeKeys = Object.getOwnPropertyNames(Array.prototype)
    .slice()
    .sort()
    .join("\u0000");
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
      Array.isArray(value) ||
      Object.prototype.toString.call(value) !== "[object Object]"
    ) {
      return false;
    }
    const prototype = Object.getPrototypeOf(value);
    if (
      prototype === null ||
      Object.getOwnPropertyNames(prototype).slice().sort().join("\u0000") !==
        objectPrototypeKeys
    ) {
      return false;
    }
    return Reflect.ownKeys(value).every((key) => {
      if (typeof key !== "string" || forbiddenKeys.has(key)) return false;
      const descriptor = Object.getOwnPropertyDescriptor(value, key);
      return Boolean(descriptor && descriptor.enumerable && "value" in descriptor);
    });
  }

  function isPlainArray(value) {
    if (!Array.isArray(value)) return false;
    const prototype = Object.getPrototypeOf(value);
    if (
      prototype === null ||
      Object.getOwnPropertyNames(prototype).slice().sort().join("\u0000") !==
        arrayPrototypeKeys
    ) {
      return false;
    }
    const keys = Reflect.ownKeys(value);
    if (keys.length !== value.length + 1 || !keys.includes("length")) return false;
    for (let index = 0; index < value.length; index += 1) {
      const key = String(index);
      if (!keys.includes(key)) return false;
      const descriptor = Object.getOwnPropertyDescriptor(value, key);
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
    const actual = Reflect.ownKeys(value).slice().sort();
    const expected = expectedFields.slice().sort();
    return (
      actual.length === expected.length &&
      actual.every((key, index) => key === expected[index])
    );
  }

  function ownValue(record, key) {
    if (!isPlainRecord(record) || !hasOwn(record, key)) return undefined;
    const descriptor = Object.getOwnPropertyDescriptor(record, key);
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

  function cloneJson(value, depth) {
    if (depth > 8) return null;
    if (value === null || typeof value === "boolean" || typeof value === "string") {
      return { ok: true, value };
    }
    if (typeof value === "number") {
      return Number.isFinite(value)
        ? { ok: true, value }
        : { ok: false, value: null };
    }
    if (isPlainArray(value)) {
      if (value.length > 100) return { ok: false, value: null };
      const output = [];
      for (const item of value) {
        const cloned = cloneJson(item, depth + 1);
        if (!cloned || !cloned.ok) return { ok: false, value: null };
        output.push(cloned.value);
      }
      return { ok: true, value: output };
    }
    if (!isPlainRecord(value) || Reflect.ownKeys(value).length > 100) {
      return { ok: false, value: null };
    }
    const output = {};
    for (const key of Reflect.ownKeys(value)) {
      const cloned = cloneJson(ownValue(value, key), depth + 1);
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

  function normalizeCandidate(value) {
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
      smiles !== canonicalSmiles ||
      !hasExactFields(validation, ["valid", "method"]) ||
      ownValue(validation, "valid") !== true ||
      ownValue(validation, "method") !== "RDKit" ||
      !isPlainRecord(generationProvenance) ||
      !isPlainRecord(metadata)
    ) {
      return null;
    }
    const clonedProvenance = cloneJson(generationProvenance, 0);
    const clonedMetadata = cloneJson(metadata, 0);
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

  function normalizeRejected(value) {
    if (!isPlainRecord(value)) return null;
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
    const cloned = cloneJson(value, 0);
    return cloned && cloned.ok
      ? { source_index: sourceIndex, smiles, reason }
      : null;
  }

  function normalize(payload) {
    try {
      if (!isPlainRecord(payload) || ownValue(payload, "type") !== "molecule_candidates") {
        return null;
      }
      const traceId = ownValue(payload, "trace_id");
      const source = ownValue(payload, "source");
      const candidateSet = ownValue(payload, "candidate_set");
      const warnings = ownValue(payload, "warnings");
      if (
        !isSafeString(traceId, 128, true) ||
        !isPlainRecord(source) ||
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
        if (!isNonnegativeInteger(value)) return null;
        counts[name] = value;
      }

      const rawCandidates = ownValue(candidateSet, "candidates");
      const rawRejected = ownValue(candidateSet, "rejected");
      if (
        !isPlainArray(rawCandidates) ||
        rawCandidates.length === 0 ||
        !isPlainArray(rawRejected)
      ) {
        return null;
      }
      if (
        counts.valid_count !== rawCandidates.length ||
        counts.unique_count !== rawCandidates.length ||
        counts.requested_count < rawCandidates.length ||
        (candidateStatus === "succeeded" &&
          counts.requested_count !== rawCandidates.length) ||
        (candidateStatus === "partial" &&
          counts.requested_count <= rawCandidates.length)
      ) {
        return null;
      }

      const candidates = [];
      const candidateIds = new Set();
      const canonicalSmiles = new Set();
      const sourceIndexes = [];
      for (const rawCandidate of rawCandidates) {
        const candidate = normalizeCandidate(rawCandidate);
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
      for (const rawRejectedItem of rawRejected) {
        const rejected = normalizeRejected(rawRejectedItem);
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
      for (const warning of warnings) {
        if (!isSafeString(warning, 256, true)) return null;
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

  root.HomeMoleculeCandidates = { normalize };
})(window);
