"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const helper = path.join(__dirname, "../src/web/static/js/home/evidence_report.js");
assert(fs.existsSync(helper), "ScientificReport@1 helper is missing");
global.window = {};
require(helper);
const api = window.HomeEvidenceReport;
assert(api && api.normalize && api.createLifecycle);
assert.equal(api.normalize({}), null);
const malicious = {};
Object.defineProperty(malicious, "type", {enumerable: true, get() {throw Error("getter must never run");}});
assert.equal(api.normalize(malicious), null);
assert.equal(api.normalize(Object.create({type: "scientific_report"})), null);
const cycle = {}; cycle.self = cycle;
assert.equal(api.normalize(cycle), null);
assert.equal(api.normalize({x: "\ud800"}), null);
const clone = v => JSON.parse(JSON.stringify(v));

class Element {
  constructor(tag) {
    this.tag = tag; this.children = []; this.style = {}; this.attributes = {}; this.textContent = "";
    const classes = new Set(); this.classList = {add: v => classes.add(v), remove: v => classes.delete(v), contains: v => classes.has(v)};
  }
  appendChild(c) {this.children.push(c); c.parentNode = this; return c;}
  replaceChildren(...children) {this.children = []; children.forEach(c => this.appendChild(c));}
  setAttribute(k, v) {this.attributes[k] = v;}
  getAttribute(k) {return this.attributes[k];}
  addEventListener() {}
  contains(c) {return this.children.includes(c) || this.children.some(x => x.contains(c));}
  get isConnected() {return this.root === true || Boolean(this.parentNode?.isConnected);}
  querySelector() {return null;}
  closest() {return {querySelector: () => this};}
}
function allText(el) {return [el.textContent, ...el.children.map(allText)].join("\n");}
function allElements(el) {return [el, ...el.children.flatMap(allElements)];}

function validateCases(report, event) {
  const p = api.normalize(report); assert(p, "same Python-produced DTO must normalize");
  const life = api.createLifecycle(); life.startRequest(); assert(!life.enqueue(p), "report cannot self-adopt trace");
  life.observeTrace(p.trace_id); assert(life.enqueue(p)); assert(!life.enqueue(p)); assert(life.take(p.trace_id)); assert(!life.enqueue(p));
  life.startRequest(); life.observeTrace(p.trace_id); life.enqueue(p);
  const conflict = clone(p); conflict.projection_id = "f".repeat(64); life.enqueue(conflict); assert.equal(life.take(p.trace_id), null);
  life.startRequest(); life.observeTrace("new-trace"); assert(!life.enqueue(p)); life.clear(); assert.equal(life.take(p.trace_id), null);
  const mutations = [
    q => q.extra = 1, q => q.schema_version = 1, q => q.trace_id = "other",
    q => q.sources.push(clone(q.sources[0])), q => q.property_rows.push(clone(q.property_rows[0])),
    q => q.property_rows[0].values.molecular_weight = true,
    q => q.property_rows[0].values.molecular_weight = "46.07",
    q => q.property_rows[0].values.molecular_weight = NaN,
    q => q.property_rows[0].values.tpsa = -1, q => q.property_rows[0].values.qed = 2,
    q => q.property_rows[0].state = "unavailable", q => q.property_rows[0].source_row_index = -1,
    q => q.property_rows[0].source_observation_id = "other",
    q => q.generations[0].displayed_count++, q => q.target.label = "x".repeat(129),
    q => q.ranking.top_candidates[0].score = "1", q => q.ranking.top_candidates[0].missing_evidence = ["admet", "admet"],
    q => q.omitted.steps = Number.MAX_SAFE_INTEGER + 1, q => q.warnings.push("\ud800")
  ];
  for (const mutate of mutations) {const q = clone(report); mutate(q); assert.equal(api.normalize(q), null);}
  const row = p.property_rows[0], ref = event.reference;
  const candidate = event.candidate_set.candidates[0];
  assert(api.findPropertyRow(p, ref, row.generator_observation_id, candidate));
  for (const mutate of [r => r.trace_id = "other", r => r.presentation_id = "other", r => r.revision = "a".repeat(64), r => r.ordered_keys.reverse()]) {
    const r = clone(ref); mutate(r); assert.equal(api.findPropertyRow(p, r, row.generator_observation_id, candidate), null);
  }
  assert.equal(api.findPropertyRow(p, ref, "other-generator", candidate), null);
  assert.equal(api.findPropertyRow(p, ref, row.generator_observation_id, {...candidate, canonical_smiles:"CCC"}), null);
  const hostile = clone(report); hostile.target.label = '<img src=x onerror="bad()">'; hostile.warnings.push("<script>bad()</script>");
  const box = new Element("div"); api.renderReport(box, api.normalize(hostile));
  assert(allText(box).includes(hostile.target.label));
  assert(!allElements(box).some(e => ["img", "script"].includes(e.tag)), "report content is inert text");
}

async function actualMain(frames, {failReport = false, failProperties = false, failAck = false} = {}) {
  require("../src/web/static/js/home/molecule_candidates.js");
  require("../src/web/static/js/home/scientific_references.js");
  global.document = {createElement: tag => new Element(tag)};
  const box = new Element("div"); box.root = true;
  const acks = [], pending = []; let stored = null;
  const controller = window.HomeScientificReferences.createController({
    storage: {getItem: () => stored, setItem: (_, v) => {stored = v;}, removeItem: () => {stored = null;}},
    request: async (action, body) => {assert.equal(action, "confirm"); acks.push(body); return {confirmed: !failAck};}});
  const scientificReferences = {present: (...args) => {const p = controller.present(...args); pending.push(p); return p;}, select: (...args) => controller.select(...args)};
  const moleculeCandidateLifecycle = window.HomeMoleculeCandidates.createLifecycle(); moleculeCandidateLifecycle.startRequest();
  const evidenceReportLifecycle = api.createLifecycle(); evidenceReportLifecycle.startRequest();
  const evidenceReportViews = new WeakMap();
  const source = fs.readFileSync(path.join(__dirname, "../src/web/static/js/home/main.js"), "utf8");
  const section = (start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
  const renderer = section("function renderMoleculeCandidates(messageElement, payload)", "  window.HomeMain =");
  const display = section("function displayCandidateCollections(element, payloads)", "  // DOM元素缓存");
  const resolve = section("function resolveCompletionAction(", "  // 完成最后一条消息");
  const complete = section("function completeLastMessage(content)", "  // 显示状态消息");
  const cases = section('case "molecule_candidates":', 'case "rag_info":');
  const handler = new Function("window", "document", "moleculeCandidateLifecycle", "evidenceReportLifecycle", "evidenceReportViews", "scientificReferences", "box", `
    const elements = {chatContainer: {querySelector: selector => selector.includes("typing") ? null : box}};
    const HomeFormatters = {formatContent: v => v}; const HomeState = {}; const currentMessages = [];
    const HomeChatRenderer = {showNotification() {}};
    function removeTypingIndicator() {} function updateToolbarControlStates() {} function clearToolStatus() {} function showToolStatus() {}
    function appendToLastMessage(content) {box.setAttribute("data-content", content);}
    function addAssistantMessage(content) {box.setAttribute("data-content", content); return box;}
    ${display}\n${resolve}\n${complete}\n${renderer}
    return message => {switch(message.type) {${cases}}};
  `)(window, document, moleculeCandidateLifecycle, evidenceReportLifecycle, evidenceReportViews, scientificReferences, box);
  const originalReport = api.renderReport, originalProps = api.renderProperties;
  if (failReport) api.renderReport = () => {throw Error("display fixture failure");};
  if (failProperties) api.renderProperties = () => {throw Error("property fixture failure");};
  try {for (const frame of frames) if (["molecule_candidates", "scientific_report", "complete"].includes(frame.type)) handler(frame);}
  finally {api.renderReport = originalReport; api.renderProperties = originalProps;}
  await Promise.all(pending);
  return {box, acks, stored, outgoing: controller.outgoing(), controller};
}

async function frameTests(frames) {
  const report = frames.find(f => f.type === "scientific_report");
  const event = frames.find(f => f.type === "molecule_candidates");
  global.document = {createElement: tag => new Element(tag)};
  validateCases(report, event);
  const result = await actualMain(frames);
  const text = allText(result.box);
  assert(text.includes("科研证据报告") && text.includes(report.target.label));
  assert(text.includes("46.07") && text.includes("-0.001") && text.includes("20.23") && text.includes("0.407"));
  assert(text.includes(`实际 Top-${report.ranking.requested_top_n}`));
  if (report.run_status === "partial") assert(text.includes("部分完成"));
  assert.deepEqual(result.acks, [event.reference]);
  assert.deepEqual(allElements(result.box).filter(e => e.className === "molecule-card").map(e => e.getAttribute("data-candidate-id")), event.reference.ordered_keys.map(k => k[1]));
  assert(result.stored && !result.stored.includes("46.07") && !result.stored.includes("property"));
  const reportFailure = await actualMain(frames, {failReport:true}); assert.deepEqual(reportFailure.acks, result.acks);
  const propFailure = await actualMain(frames, {failProperties:true}); assert.deepEqual(propFailure.acks, result.acks);
  assert(allText(propFailure.box).includes("当前候选事件未携带经独立性质工具验证的属性"));
  const failedAck = await actualMain(frames, {failAck:true}); assert.deepEqual(failedAck.outgoing, {});
  assert(allText(failedAck.box).includes("46.07"), "ACK failure changes selection, not accepted property evidence");
  const noReport = await actualMain(frames.filter(f => f.type !== "scientific_report"));
  assert(allText(noReport.box).includes("当前候选事件未携带经独立性质工具验证的属性"));
  assert(!allText(noReport.box).includes("46.07"));
  const late = await actualMain([...frames.filter(f => f.type !== "scientific_report"), report]);
  assert(!allText(late.box).includes("46.07"));
  const failedTerminal = await actualMain(frames.map(f => f.type === "complete" ? {...f, status:"failed"} : f));
  assert(!allText(failedTerminal.box).includes("46.07"), "failed terminal must discard pending numeric report");
  return {acks: result.acks, card_count: event.reference.ordered_keys.length, chinese: true, descriptors: true};
}

if (process.argv.includes("--frames-stdin")) {
  frameTests(JSON.parse(fs.readFileSync(0, "utf8"))).then(result => process.stdout.write(JSON.stringify(result))).catch(e => {console.error(e); process.exitCode = 1;});
} else {
  console.log("evidence report hostile preflight tests passed (positive same-frame suite runs through Python driver)");
}
