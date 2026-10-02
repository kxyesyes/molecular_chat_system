const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const jsPath = path.join(
  __dirname,
  "..",
  "src",
  "web",
  "static",
  "js",
  "home",
  "main.js"
);
const source = fs.readFileSync(jsPath, "utf8");
const templatePath = path.join(
  __dirname,
  "..",
  "src",
  "web",
  "templates",
  "index.html"
);
const templateSource = fs.readFileSync(templatePath, "utf8");

assert.ok(
  templateSource.includes(
    '/static/js/home/main.js?v=20260714-target-design-v2'
  ),
  "homepage must cache-bust the workflow completion fix"
);

function extractFunction(functionName) {
  const start = source.indexOf(`function ${functionName}(`);
  assert.notStrictEqual(start, -1, `Missing ${functionName}()`);

  const bodyStart = source.indexOf("{", start);
  let depth = 0;
  for (let index = bodyStart; index < source.length; index += 1) {
    if (source[index] === "{") depth += 1;
    if (source[index] === "}") depth -= 1;
    if (depth === 0) return source.slice(start, index + 1);
  }

  throw new Error(`Could not parse ${functionName}()`);
}

const resolverSource = extractFunction("resolveCompletionAction");
const resolveCompletionAction = vm.runInNewContext(`(${resolverSource})`);
const selectorSource = extractFunction("selectMoleculeCandidates");
const selectMoleculeCandidates = vm.runInNewContext(`(${selectorSource})`);

function resolve(overrides = {}) {
  return resolveCompletionAction({
    hadTypingIndicator: false,
    hasLastMessage: false,
    lastMessageComplete: false,
    lastMessageContent: "",
    finalContent: undefined,
    ...overrides,
  });
}

assert.strictEqual(
  resolve({ finalContent: "direct result" }).action,
  "create",
  "direct complete.content must create an assistant message"
);

assert.strictEqual(
  resolve({
    hasLastMessage: true,
    lastMessageContent: "partial",
    finalContent: "final result",
  }).action,
  "replace",
  "complete.content must replace divergent streamed content"
);

assert.strictEqual(
  resolve({
    hasLastMessage: true,
    lastMessageContent: "final result",
    finalContent: "final result",
  }).action,
  "finalize",
  "matching streamed content must only be finalized"
);

assert.strictEqual(
  resolve({
    hasLastMessage: true,
    lastMessageComplete: true,
    lastMessageContent: "final result",
    finalContent: "final result",
  }).action,
  "ignore",
  "duplicate complete frames must be idempotent"
);

assert.strictEqual(
  resolve({
    hadTypingIndicator: true,
    hasLastMessage: true,
    lastMessageComplete: true,
    lastMessageContent: "same result",
    finalContent: "same result",
  }).action,
  "create",
  "a new request may legitimately return the same content"
);

assert.strictEqual(
  resolve({
    hasLastMessage: true,
    lastMessageComplete: true,
    lastMessageContent: "first terminal result",
    finalContent: "late conflicting result",
  }).action,
  "ignore",
  "late complete frames must not create a second response"
);

assert.strictEqual(
  resolve().action,
  "ignore",
  "empty complete without a pending message must only clear terminal UI state"
);

assert.strictEqual(
  resolve({ hasLastMessage: true, lastMessageContent: "streamed result" })
    .action,
  "finalize",
  "legacy stream plus empty complete must remain supported"
);

assert.ok(
  source.includes("resolveCompletionAction({"),
  "completeLastMessage() must consume the tested state resolver"
);

assert.deepStrictEqual(
  Array.from(
    selectMoleculeCandidates(
      "PDE5A O76074 `1T9R` SMILES: CCO",
      [
        { smiles: "CCO", validation: { valid: true, method: "RDKit" } },
        { smiles: "CCO", validation: { valid: true, method: "RDKit" } },
        { smiles: "CCN", validation: { valid: true, method: "RDKit" } },
      ]
    ),
    (item) => item.smiles
  ),
  ["CCO", "CCN"],
  "structured workflow candidates must be deduplicated without mining target identifiers from Markdown"
);

assert.deepStrictEqual(
  Array.from(
    selectMoleculeCandidates(
      "Target PDE5A, UniProt O76074, structure `1T9R`; SMILES: CCO",
      undefined
    ),
    (item) => item.smiles
  ),
  [],
  "unvalidated text must never create molecule cards"
);

assert.deepStrictEqual(
  Array.from(
    selectMoleculeCandidates("SMILES: C1CC", undefined),
    (item) => item.smiles
  ),
  [],
  "chemically invalid text SMILES must not create molecule cards"
);

assert.ok(
  source.includes("completeLastMessage(message.content, message.molecules);"),
  "complete frames must forward structured molecule candidates"
);

assert.ok(
  source.includes(
    "detectAndRenderMolecules(lastMessage, finalContent, molecules);"
  ),
  "completion rendering must pass the structured candidate contract"
);

console.log("Homepage workflow completion behavior checks passed");
