"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const root = path.join(__dirname, "..");
global.window = {};
require("../src/web/static/js/home/molecule_candidates.js");
const candidates = window.HomeMoleculeCandidates;
const controllerPath = path.join(root, "src/web/static/js/home/scientific_references.js");

function event(smiles = ["CCO", "CCN"], pid = "11111111-1111-4111-8111-111111111111") {
  const rows = smiles.map((s, i) => ({candidate_id: `cand-${String(i + 1).padStart(3, "0")}-${crypto.createHash("sha256").update(s).digest("hex").slice(0, 8)}`,
    source_index: i + 1, original_smiles: s, canonical_smiles: s, smiles: s,
    validation: {valid: true, method: "RDKit"}, generation_provenance: {}, metadata: {}}));
  return {type: "molecule_candidates", trace_id: "trace", source: {tool_name: "llm_molecular_generator", model_name: "offline", status: "succeeded"},
    candidate_set: {version: "1", requested_count: rows.length, valid_count: rows.length, unique_count: rows.length,
      invalid_count: 0, duplicate_count: 0, candidates: rows, rejected: [], status: "succeeded"}, warnings: [],
    reference: {trace_id: "trace", presentation_id: pid, revision: "a".repeat(64), ordered_keys: rows.map(c => ["checkpoint", c.candidate_id])}};
}
const pointer = ref => ({trace_id: ref.trace_id, presentation_id: ref.presentation_id, revision: ref.revision});
function memory() {
  let value = null;
  return {getItem: () => value, setItem: (_, v) => { value = v; }, removeItem: () => { value = null; }, value: () => value};
}
const mounted = payload => ({mounted: true, ordered_keys: payload.reference.ordered_keys});

async function run() {
  const e = event();
  assert(candidates.normalize(e), "strict optional reference schema missing");
  const legacy = {...e}; delete legacy.reference;
  assert(candidates.normalize(legacy));
  for (const extra of [{details: []}, {details: "text"}, {details: null}, {unexpected: 1}]) {
    const rejectedShape = event(["CCO"]); delete rejectedShape.reference;
    rejectedShape.source.status = "partial";
    Object.assign(rejectedShape.candidate_set, {status: "partial", requested_count: 2, invalid_count: 1,
      rejected: [{source_index: 2, smiles: "bad", reason: "invalid_smiles", ...extra}]});
    assert.equal(candidates.normalize(rejectedShape), null);
  }
  const overBudget = event(); delete overBudget.reference;
  for (const c of overBudget.candidate_set.candidates) {
    c.generation_provenance = {};
    c.metadata = Object.fromEntries(Array.from({length: 64}, (_, i) => [String(i), "x".repeat(511)]));
  }
  overBudget.warnings = ["w".repeat(256)];
  assert.equal(candidates.normalize(overBudget), null, "warnings share the clone text budget");
  const rejectedA = event(["CCO"]); delete rejectedA.reference;
  rejectedA.candidate_set.candidates[0].metadata = {constructor: "fixture"};
  const laterB = event(["CCO", "CCN"]); delete laterB.reference;
  assert.equal(candidates.normalize(rejectedA), null);
  const auditLife = candidates.createLifecycle(); auditLife.startRequest();
  assert(auditLife.enqueue(candidates.normalize(laterB)));
  assert.equal(auditLife.complete()[0].candidates.length, 2, "rejected A cannot charge lifecycle dedupe");
  for (const change of [r => r.owner = "forged", r => r.ordered_keys.reverse(), r => r.ordered_keys[0][1] = "missing", r => r.trace_id = "other", r => r.revision = "bad"]) {
    const bad = JSON.parse(JSON.stringify(e)); change(bad.reference);
    assert.equal(candidates.normalize(bad), null);
  }
  const life = candidates.createLifecycle(); life.startRequest();
  assert(life.enqueue(candidates.normalize(e)));
  const second = event(["CCO", "CCC"], "22222222-2222-4222-8222-222222222222");
  second.reference.ordered_keys.shift();
  assert(life.enqueue(candidates.normalize(second)));
  const payloads = life.complete();
  assert.deepEqual(payloads[1].candidates.map(c => c.canonical_smiles), ["CCC"]);
  assert.deepEqual(payloads[1].reference, second.reference);
  assert(fs.existsSync(controllerPath), "independent reference controller missing");
  require(controllerPath);
  const api = window.HomeScientificReferences;
  const storage = memory(), requests = [], notices = [];
  const controller = api.createController({storage, request: async (action, body) => {
    requests.push([action, body]); return {confirmed: true};
  }, onStatus: msg => notices.push(msg)});
  await controller.present([payloads[0]], () => undefined);
  assert.equal(requests.length, 0, "normal return without mounting must not ACK");
  assert.equal(storage.value(), null);
  await controller.present([payloads[0]], () => { throw Error("render failed"); });
  assert.equal(requests.length, 0);
  await controller.present([payloads[0]], mounted);
  assert.deepEqual(JSON.parse(storage.value()), pointer(e.reference));
  assert(!storage.value().includes("CCO") && !storage.value().includes("owner") && !storage.value().includes("ordered_keys"));
  assert(controller.select(e.reference.presentation_id, e.reference.ordered_keys[1]));
  assert.equal(controller.outgoing().selection.candidate_id, e.reference.ordered_keys[1][1]);
  await controller.present(payloads, mounted);
  assert.deepEqual(controller.outgoing(), {}, "multiple collections cannot default to latest");
  assert.equal(storage.value(), null);
  assert(controller.select(second.reference.presentation_id, second.reference.ordered_keys[0]));
  controller.clear(); assert.deepEqual(controller.outgoing(), {});
  const acknowledgements = new Map();
  const concurrent = api.createController({storage, request: (_action, body) => new Promise(resolve => {
    acknowledgements.set(body.presentation_id, resolve);
  })});
  const confirming = concurrent.present(payloads, mounted);
  acknowledgements.get(e.reference.presentation_id)({confirmed: true});
  await Promise.resolve();
  assert(concurrent.select(e.reference.presentation_id, e.reference.ordered_keys[0]));
  acknowledgements.get(second.reference.presentation_id)({confirmed: true});
  await confirming;
  assert(concurrent.select(second.reference.presentation_id, second.reference.ordered_keys[0]),
    "selecting a confirmed collection must not cancel another mounted collection's ACK");
  concurrent.clear();
  let finish;
  const stale = api.createController({storage, request: () => new Promise(resolve => { finish = resolve; })});
  const pending = stale.present([payloads[0]], mounted);
  stale.clear(); finish({confirmed: true}); await pending;
  assert.equal(storage.value(), null, "late ACK cannot undo clear");
  const failed = api.createController({storage, request: async () => { throw Error("ACK failed"); }});
  await failed.present([payloads[0]], mounted);
  assert.deepEqual(failed.outgoing(), {}); assert.equal(storage.value(), null);
  storage.setItem("", JSON.stringify(pointer(second.reference)));
  let restored;
  const restore = api.createController({storage, request: async () => ({reference: second.reference, events: [second], source_status: "succeeded", warnings: [], expires_at: Date.now() / 1000 + 100})});
  await restore.restore(p => {restored = p; return mounted(p);});
  assert.deepEqual(restored.candidates.map(c => c.canonical_smiles), ["CCC"], "restore must not expand the original CandidateSet");
  assert(restore.outgoing().reference);
  const partialRestore = api.createController({storage, request: async () => ({reference: second.reference, events: [second], source_status: "partial", warnings: ["independent branch failed"], expires_at: Date.now() / 1000 + 100})});
  let partialPayload;
  await partialRestore.restore(p => {partialPayload = p; return mounted(p);});
  assert(partialPayload.warnings.includes("independent branch failed"), "restore must display run warnings");
  assert(partialPayload.warnings.some(w => w.includes("部分")), "restore must display partial source status");
  const delayed = api.createController({storage, request: () => new Promise(resolve => { finish = resolve; })});
  let renderCalls = 0;
  const restoring = delayed.restore(() => { renderCalls++; return mounted(restored); });
  delayed.startRequest(); finish({reference: second.reference, events: [second], source_status: "succeeded", warnings: [], expires_at: Date.now() / 1000 + 100});
  await restoring;
  assert.equal(renderCalls, 0, "stale restore must not render into a newer turn");
  const unavailableStorage = {getItem() {throw Error();}, setItem() {throw Error();}, removeItem() {throw Error();}};
  const noStorage = api.createController({storage: unavailableStorage, request: async () => ({confirmed: true}), onStatus: msg => notices.push(msg)});
  await noStorage.present([payloads[0]], mounted);
  assert(noStorage.outgoing().reference, "disabled storage permits current-connection selection");
  assert(notices.length);
  rendererTest(payloads[0]);
  await restoredVisibilityTest(payloads[0]);
  console.log("scientific reference schema/controller/actual mount tests passed");
}

async function restoredVisibilityTest(payload) {
  const source = fs.readFileSync(path.join(root, "src/web/static/js/home/main.js"), "utf8");
  const ready = source.slice(source.indexOf('case "connection_ready":'), source.indexOf('case "pong":'));
  let renderRestore, chatVisible = false;
  const handle = new Function("HomeChatRenderer", "moleculeCandidateLifecycle", "scientificReferences",
    "addAssistantMessage", "renderMoleculeCandidates", "enterChatMode", "message", "console",
    `let chatMode = false; switch(message.type) {${ready}}`);
  handle({updateConnectionStatus() {}, showNotification() {}}, {isRequestInFlight: () => false},
    {restore: callback => {renderRestore = callback;}}, () => ({}), () => {
      assert(chatVisible, "restored cards must be visible, not mounted in the hidden welcome-page chat container");
      return mounted(payload);
    }, () => {chatVisible = true;}, {type: "connection_ready"}, {log() {}});
  assert.equal(chatVisible, false, "a connection without a valid restored view must keep the welcome screen");
  renderRestore(payload);
  const template = fs.readFileSync(path.join(root, "src/web/templates/index.html"), "utf8");
  assert(template.includes('/static/js/home/main.js?v=20260924-reference-visible-v1'),
    "restoration visibility fix must invalidate the previous cached main script");
}

function rendererTest(payload) {
  const source = fs.readFileSync(path.join(root, "src/web/static/js/home/main.js"), "utf8");
  const renderer = source.slice(source.indexOf("function renderMoleculeCandidates(messageElement, payload)"), source.indexOf("  window.HomeMain ="));
  class Element {
    constructor(tag) {this.tag = tag; this.children = []; this.style = {}; this.attributes = {}; this.classList = {add() {}};}
    appendChild(child) {this.children.push(child); child.parentNode = this; return child;}
    setAttribute(k, v) {this.attributes[k] = v;}
    getAttribute(k) {return this.attributes[k];}
    addEventListener() {}
    replaceChildren() {this.children = [];}
    contains(child) {return this.children.includes(child);}
    get isConnected() {return this.root === true || Boolean(this.parentNode?.isConnected);}
  }
  const render = new Function("document", "scientificReferences", `${renderer}; return renderMoleculeCandidates;`)({createElement: tag => new Element(tag)}, null);
  assert(!render({closest: () => null}, payload)?.mounted);
  const box = new Element("div");
  assert(!render({closest: () => ({querySelector: () => box})}, payload)?.mounted, "detached DOM cannot confirm");
  box.root = true;
  const result = render({closest: () => ({querySelector: () => box})}, payload);
  assert.equal(result.mounted, true);
  assert.deepEqual(result.ordered_keys, payload.reference.ordered_keys);
}
run().catch(error => {console.error(error); process.exitCode = 1;});
