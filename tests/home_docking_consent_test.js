"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.listeners = {}; this.attributes = {}; this.disabled = false; this.value = ""; this._text = ""; }
  appendChild(node) { this.children.push(node); node.parentNode = this; return node; }
  removeChild(node) { this.children = this.children.filter(child => child !== node); node.parentNode = null; }
  replaceChildren(...nodes) { this.children = []; nodes.forEach(node => this.appendChild(node)); this._text = ""; }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  addEventListener(key, action) { this.listeners[key] = action; }
  removeEventListener(key, action) { if (this.listeners[key] === action) delete this.listeners[key]; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(node => node.textContent).join(" "); }
  set innerHTML(_) { throw new Error("HTML injection forbidden"); }
  click() { if (!this.disabled) return this.listeners.click?.({isTrusted: true}); }
}
function all(node) { return [node, ...node.children.flatMap(all)]; }
function button(host, text) { return all(host).find(node => node.tagName === "button" && node.textContent === text); }
const hash = "a".repeat(64), root = "root-1", task = "task-1", prep = "prep-1", turn = "turn-1";
const parameters = {center: [1, 2, 3], size: [20, 20, 20], exhaustiveness: 8, num_modes: 9};
function consent() { return {schema: "DockingConsent@1", preparation_id: prep, task_id: task, trace_id: root,
  binding_digest: hash, expires_at_ms: 20000, receptor: {name: "receptor.pdb", size: 80, sha256: hash},
  ligand: {name: "ligand.sdf", size: 90, sha256: hash}, parameters: {...parameters, energy_range: 3},
  tool_policy: {label: "molecular_docking/local", digest: hash, execution_backend: "local", adapter_contract_version: "test-v1", vina_limit_seconds: 300, operation_limit_seconds: 420}}; }
function view(patch = {}) { return {type: "docking_event", schema: "DockingView@1", request_id: root,
  root_trace_id: root, task_id: task, preparation_id: prep, origin_turn_id: turn, revision: 0,
  sequence: 1, send_generation: 1, phase: "awaiting_input", segment_complete: true, terminal: false,
  view_status: "AWAITING_INPUT", task_status: "unavailable", cleanup_status: "settled",
  reason_code: "clarification_required", persistence_pending: false, missing_fields: ["receptor_ref"], preview: null, result: null, ...patch}; }
function prepared() { return {input_kind: "explicit-residue-conformer-v1", source_sha256: hash,
  derived_sha256: hash, ligand_sha256: hash, selection_sha256: hash, refinement_revision: 0,
  pocket_rule: "test-pocket", pocket_margin: 5, pocket_atom_count: 10, input_ready: false, execution_authorized: false,
  receptor_ref: `sha256:${hash}`, ligand_ref: `sha256:${hash}`, parameters_suggestion: parameters,
  confirmed_parameters: parameters, reference_residue: {model: 1, chain: "A", resname: "ASP", resseq: 5, icode: ""},
  ligand_provenance: {input_kind: "smiles", method: "ETKDG", rdkit_version: "test", random_seed: 42,
    embedding_attempts: 1, explicit_hydrogens: true, optimizer: "UFF", max_iterations: 200, optimizer_return_code: 0}}; }
function success(patch = {}) { return view({sequence: 10, send_generation: 10, terminal: true,
  phase: "terminal", view_status: "SUCCEEDED", reason_code: "status_succeeded", missing_fields: [],
  result: {tool_name: "molecular_docking", tool_version: "Vina test", execution_backend: "local",
    input_hash: hash, config_hash: hash, tool_policy_digest: hash, source_digest: hash,
    original_receptor_digest: hash, ligand_identity_digest: hash, projection_digest: hash,
    score_kind: "vina_affinity", pose_count: 2, best_energy_kcal_mol: -7.2, energy_unit: "kcal/mol",
    pose: {artifact_id: "pose", size_bytes: 100, sha256: hash,
      download_path: `/api/agent/docking/tasks/${task}/artifacts/pose`}}, ...patch}); }
const filename = path.join(__dirname, "../src/web/static/js/home/docking_consent.js");
assert(fs.existsSync(filename), "D6 docking consent lifecycle module is absent");
const source = fs.readFileSync(filename, "utf8");
function rig() {
  const document = {createElement: tag => {const el = new Element(tag); el.ownerDocument = document; return el;}};
  const window = {document};
  Object.defineProperty(window, "localStorage", {get() { throw new Error("No consent persistence"); }});
  Object.defineProperty(window, "sessionStorage", {get() { throw new Error("No consent persistence"); }});
  vm.runInNewContext(source, {window, document, console, TextEncoder, URL, setTimeout, clearTimeout});
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../src/web/static/js/home/docking_source_selection.js"), "utf8"), {window, document, console});
  let now = 1000;
  const host = new Element("div"), socket = {}, sent = [], requests = [], segments = [], busy = [], timers = new Map();
  let nextTimer = 0, responder = async () => ({});
  const ui = window.HomeDockingConsent.create({document, now: () => now,
    setTimer: fn => {timers.set(++nextTimer, fn); return nextTimer;}, clearTimer: key => timers.delete(key),
    send: (sock, payload) => {sent.push({sock, payload: JSON.parse(JSON.stringify(payload))}); return true;},
    request: async (url, body) => {requests.push({url, body: body === undefined ? undefined : JSON.parse(JSON.stringify(body))}); return responder(url, body);},
    onSegment: event => segments.push(event), onBusy: value => busy.push(value)});
  ui.configure(socket, true);
  ui.begin(socket, host);
  ui.accepted({type: "request_accepted", trace_id: root, turn_id: turn}, socket);
  return {ui, host, socket, sent, requests, segments, busy, timers,
    setNow: value => {now = value;}, respond: fn => {responder = fn;}};
}
async function run() {
  {
    const r = rig();
    r.ui.timeout(r.socket);
    r.ui.handle(success(), r.socket);
    assert(!r.host.textContent.includes("-7.2") && !r.host.textContent.includes("下载已验证构象"),
      "explicit timeout before the first view must remain sticky");
    assert.strictEqual(r.segments.length, 1, "a later confirmed drain may release admission without presenting science");
    assert.strictEqual(r.requests.length, 0);
  }
  {
    const r = rig();
    assert(r.ui.handle(view(), r.socket));
    assert(r.host.textContent.includes("等待补充输入"));
    assert.strictEqual(r.segments.length, 1);
    assert.strictEqual(r.requests.length, 0, "event alone must never prepare or approve");
    assert(button(r.host, "补充本次请求"));
  }
  {
    const r = rig();
    r.ui.handle(view({reason_code: "scientific_selection_required", preview: {source: {
      pdb_id: "1ABC", target: {uniprot_id: "P06276", taxonomy_id: 9606}, source_sha256: hash,
      revision_version: "1.0", input_ready: false, execution_authorized: false,
      choice_counts: {protein_selection: 1, reference_residue: 0, hetero_choices: 0,
        conformer_choices: 0, residues: 1, conformer_constraints: 0, connections: 0, limitations: 0}
    }}}), r.socket);
    assert(r.host.textContent.includes("没有经核验的合格参考配体"));
    assert(r.host.textContent.includes("等待补充输入"));
    assert(!r.host.textContent.includes("本次确认已失效"));
    assert.strictEqual(r.requests.length, 0);
    assert.strictEqual(r.sent.length, 0);
    assert(button(r.host, "确认结构选择并生成输入").disabled);
  }
  for (const patch of [{task_id: "foreign", root_trace_id: "foreign"}, {origin_turn_id: "foreign"},
    {sequence: -1}, {sequence: 1.5}, {send_generation: 0}, {revision: Number.MAX_SAFE_INTEGER + 1},
    {schema: "Other"}, {terminal: "false"}, {preview: {consent: {...consent(), approval_nonce: hash}}},
    {phase: "awaiting_consent", view_status: "READY", preview: {consent: {...consent(), expires_at_ms: Number.MAX_SAFE_INTEGER}}},
    {preview: {source: {private_path: "/secret"}}}, {unknown: true}, {view_status: "SUCCEEDED"}]) {
    const r = rig(); r.ui.handle(view(patch), r.socket);
    assert.strictEqual(r.host.children.length, 0, `invalid event was rendered: ${JSON.stringify(patch)}`);
  }
  {
    const r = rig(); r.ui.handle(view(), r.socket);
    const before = r.host.textContent;
    r.ui.handle(view({task_id: "other", sequence: 2, send_generation: 2}), r.socket);
    r.ui.handle(view({sequence: 0, send_generation: 2}), r.socket);
    r.ui.handle(view({sequence: 2, send_generation: 1}), r.socket);
    r.ui.handle(success(), {});
    assert.strictEqual(r.host.textContent, before);
  }
  {
    const r = rig(); r.ui.handle(view({phase: "running", view_status: "ACTIVE", segment_complete: false}), r.socket);
    assert.strictEqual(r.segments.length, 0); assert(r.ui.blocked());
    r.ui.handle(view({sequence: 2, send_generation: 2, cleanup_status: "pending"}), r.socket);
    assert.strictEqual(r.segments.length, 0); assert(r.ui.blocked());
    r.ui.handle(view({sequence: 3, send_generation: 3}), r.socket);
    assert.strictEqual(r.segments.length, 1); assert(!r.ui.blocked());
  }
  {
    const r = rig(), input = prepared(); input.source_sha256 = "c".repeat(64);
    r.ui.handle(view({preview: {prepared: input}, reason_code: "inputs_confirmed_staging_required"}), r.socket);
    assert(r.host.textContent.includes(hash));
    assert(r.host.textContent.includes("c".repeat(64)), "original prepared source hash must remain visible without a separate source preview");
    r.respond(async () => ({root_id: root, task_id: task, preparation_id: prep, phase: "READY",
      consent_preview: {...consent(), approval_nonce: "b".repeat(64)}}));
    await button(r.host, "生成本次确认单").click();
    assert.strictEqual(r.requests.length, 1);
    assert.strictEqual(r.requests[0].url, "/api/agent/docking/preparations");
    assert.deepStrictEqual(r.requests[0].body, {root, revision: 0, receptor_ref: `sha256:${hash}`, ligand_ref: `sha256:${hash}`});
    r.ui.handle(view({sequence: 2, send_generation: 2, phase: "awaiting_consent", view_status: "READY",
      reason_code: "status_ready", preview: {consent: consent()}, missing_fields: []}), r.socket);
    const approve = button(r.host, "确认并运行 Vina");
    assert(approve && !approve.disabled);
    assert(r.host.textContent.includes("test-v1"), "the exact adapter contract version must be visible at approval");
    assert(!r.host.textContent.includes("b".repeat(64)), "nonce must never enter DOM");
    assert.strictEqual(r.requests.length, 1);
    r.respond(async () => ({}));
    const first = approve.click(); const duplicate = approve.click();
    await Promise.all([first, duplicate]);
    assert.strictEqual(r.requests.length, 2, "one clicked consent, one dispatch");
    assert.deepStrictEqual(r.requests[1], {url: `/api/agent/docking/preparations/${prep}/approve`,
      body: {approval_nonce: "b".repeat(64), binding_digest: hash, confirm: true}});
  }
  {
    const r = rig(); r.ui.handle(view({phase: "awaiting_consent", view_status: "READY", preview: {consent: consent()}}), r.socket);
    assert(!button(r.host, "确认并运行 Vina") || button(r.host, "确认并运行 Vina").disabled,
      "WS READY without private one-time nonce is not approval authority");
    assert(r.host.textContent.includes("重新发起"));
  }
  {
    const r = rig(); r.ui.handle(view({preview: {prepared: prepared()}, reason_code: "inputs_confirmed_staging_required"}), r.socket);
    let finish;
    r.respond(() => new Promise(resolve => {finish = resolve;}));
    const pending = button(r.host, "生成本次确认单").click();
    assert(r.ui.blocked(), "in-flight staging cannot release the composer");
    finish({root_id: root, task_id: task, preparation_id: prep, phase: "READY", consent_preview: {...consent(), approval_nonce: "b".repeat(64)}});
    await pending;
    assert(r.ui.blocked(), "HTTP receipt alone is not a drained socket segment");
    const changed = consent(); changed.receptor.name = "different.pdb";
    r.ui.handle(view({sequence: 2, send_generation: 2, phase: "awaiting_consent", view_status: "READY", preview: {consent: changed}}), r.socket);
    assert(!button(r.host, "确认并运行 Vina") || button(r.host, "确认并运行 Vina").disabled,
      "all displayed consent facts must match the one-time preparation response");
  }
  for (const action of ["cancel", "disconnect", "expire", "changed", "timeout"]) {
    const r = rig(); r.ui.handle(view({preview: {prepared: prepared()}, reason_code: "inputs_confirmed_staging_required"}), r.socket);
    r.respond(async () => ({root_id: root, task_id: task, preparation_id: prep, phase: "READY", consent_preview: {...consent(), approval_nonce: "b".repeat(64)}}));
    await button(r.host, "生成本次确认单").click();
    r.ui.handle(view({sequence: 2, send_generation: 2, phase: "awaiting_consent", view_status: "READY", preview: {consent: consent()}}), r.socket);
    const approve = button(r.host, "确认并运行 Vina");
    if (action === "cancel") await button(r.host, "撤销本次确认").click();
    if (action === "disconnect") r.ui.disconnect(r.socket);
    if (action === "expire") r.setNow(20001);
    if (action === "changed") r.ui.handle(view({sequence: 3, send_generation: 3, revision: 1}), r.socket);
    if (action === "timeout") r.ui.timeout(r.socket);
    await approve.click();
    assert(!r.requests.some(row => row.url.endsWith("/approve")), `stale approval after ${action}`);
    if (["cancel", "disconnect", "timeout"].includes(action)) {
      r.ui.handle(success(), r.socket);
      assert(!r.host.textContent.includes("-7.2"), `late success after ${action}`);
    }
  }
  {
    const r = rig(); r.ui.handle(view(), r.socket); r.ui.disconnect(r.socket);
    const replacement = {}; r.ui.configure(replacement, true);
    assert.strictEqual(r.sent.length, 0, "reconnect cannot dispatch automatically");
    await button(r.host, "刷新状态（只读）").click();
    assert.deepStrictEqual(r.sent[0].payload, {type: "docking_watch", task_id: task});
    assert.strictEqual(r.sent[0].sock, replacement);
    r.ui.handle(success({sequence: 1, send_generation: 1}), replacement);
    assert(!r.host.textContent.includes("-7.2"), "transport loss is sticky");
  }
  {
    const r = rig(); r.ui.handle(success(), r.socket);
    assert(r.host.textContent.includes("-7.2") && r.host.textContent.includes("预测"));
    const link = all(r.host).find(node => node.tagName === "a");
    assert.strictEqual(link.attributes.href, `/api/agent/docking/tasks/${task}/artifacts/pose`);
  }
  for (const mutate of [v => {v.result.pose.download_path = "javascript:alert(1)";},
    v => {v.result.best_energy_kcal_mol = NaN;}, v => {v.result.pose_count = true;},
    v => {v.result.pose.sha256 = "bad";}, v => {v.result.extra = "path";}]) {
    const r = rig(), v = success(); mutate(v); r.ui.handle(v, r.socket);
    assert.strictEqual(r.host.children.length, 0, "unverified science must not render");
  }
  if (process.argv.includes("--captured")) {
    const captured = JSON.parse(fs.readFileSync(0, "utf8"));
    for (const event of captured.views) {
      const r = rig(); r.ui.handle(event, r.socket);
      assert(r.host.textContent.includes("MOLECULAR DOCKING"), `actual ${event.view_status} was not mounted`);
    }
    const r = rig();
    r.respond(async (url, body) => {
      assert(url.endsWith("/source-page"));
      const page = captured.pages[body.section].find(page => page.offset === body.offset);
      assert(page, "all actual source pages must be consumed");
      return page;
    });
    r.ui.handle(captured.source, r.socket);
    await button(r.host, "查看结构并选择").click();
    assert(r.host.textContent.includes("结构事实已完整加载"), "actual complete source inventory was not accepted");
    assert(button(r.host, "确认结构选择并生成输入").disabled, "actual source cannot auto-select science");
    assert.strictEqual(r.requests.length, Object.values(captured.pages).reduce((sum, pages) => sum + pages.length, 0));
  }
  console.log("home_docking_consent_test: lifecycle, consent, identity, XSS and races passed");
}
run().catch(error => {console.error(error); process.exitCode = 1;});
