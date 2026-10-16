"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const filename = path.join(__dirname, "../src/web/static/js/home/docking_source_selection.js");
const tests = [];
const test = (name, fn) => tests.push([name, fn]);
const clone = value => JSON.parse(JSON.stringify(value));
const sections = "protein_selection reference_residue hetero_choices conformer_choices residues conformer_constraints connections limitations".split(" ");
const digest = "a".repeat(64);
const identity = (chain, resname, resseq) => ({model: 1, chain, resname, resseq, icode: ""});
const protein = identity("A", "ALA", 1), ligand = identity("A", "LIG", 2);
const water = identity("B", "HOH", 3), other = identity("B", "ALA", 4);
function fixture() {
  const atom = (serial, altloc = "") => ({serial, name: "C", element: "C", altloc, occupancy_hundredths: 100});
  const residue = (residue, role, atoms) => ({residue, role, atoms, required_heavy_atoms: [{name: "C", element: "C"}]});
  const data = {
    protein_selection: ["A", "B"].map(chain => ({model: 1, chain, entity_id: chain, uniprot_id: "P12345", taxonomy_id: 9606, eligible: true})),
    reference_residue: [ligand],
    hetero_choices: [ligand, water].map(residue => ({residue, role: residue.resname === "HOH" ? "water" : "deposited_ligand", actions: ["retain", "remove"]})),
    conformer_choices: [{residue: protein, altlocs: ["A", "B"]}, {residue: other, altlocs: ["A", "B"]}],
    residues: [residue(protein, "protein", [atom(1, "A"), atom(2, "B")]), residue(ligand, "deposited_ligand", [atom(3)]), residue(water, "water", [atom(4)]), residue(other, "protein", [atom(5, "A"), atom(6, "B")])],
    conformer_constraints: [{id: "group1", residues: [protein], allowed_assignments: [["A"]]}],
    connections: [{record_index: 1, record_type: "CONECT", atom_serials: [3, 4], kind: "coordination", symmetry: []}],
    limitations: ["A", "B"].map((chain, i) => ({id: "limit" + chain, kind: "missing_residues", residues: [identity(chain, "GLY", 90 + i)], source_record_id: "record" + chain})),
  };
  // The reference and removed water are both excluded in the default selection.
  return data;
}
function eventFor(data) {
  return {type: "docking_event", schema: "DockingView@1", task_id: "task-1", preparation_id: "prep-1", request_id: "root-1", revision: 3,
    phase: "awaiting_input", view_status: "AWAITING_INPUT", reason_code: "scientific_selection_required", terminal: false, segment_complete: true,
    preview: {source: {pdb_id: "1ABC", source_sha256: digest, revision_version: "2026-01", target: {uniprot_id: "P12345", taxonomy_id: 9606},
      choice_counts: Object.fromEntries(sections.map(s => [s, data[s].length])), input_ready: false, execution_authorized: false}}};
}
class Element {
  constructor(tag, document) { this.tagName = tag; this.ownerDocument = document; this.children = []; this.attributes = {}; this.listeners = {}; this.disabled = false; this.checked = false; this.value = ""; this._text = ""; }
  set innerHTML(_) { throw new Error("HTML injection sink used"); }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(n => n.textContent).join(""); }
  appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
  removeChild(child) { this.children = this.children.filter(c => c !== child); child.parentNode = null; }
  replaceChildren(...children) { this.children.forEach(c => { c.parentNode = null; }); this.children = []; this._text = ""; children.forEach(c => this.appendChild(c)); }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  getAttribute(key) { return this.attributes[key]; }
  addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); }
  removeEventListener(type, handler) { this.listeners[type] = (this.listeners[type] || []).filter(h => h !== handler); }
  async fire(type) { for (const fn of this.listeners[type] || []) await fn({target: this, preventDefault() {}}); }
  async click() { if (!this.disabled) await this.fire("click"); }
}
function all(root) { return [root, ...root.children.flatMap(all)]; }
function harness({data = fixture(), event = eventFor(data), page, request, current = () => true} = {}) {
  const document = {createElement: tag => new Element(tag, document)};
  const host = document.createElement("div"), calls = [], errors = [], submitted = [];
  const context = {window: {}, document, console};
  assert.ok(fs.existsSync(filename), "source-selection sidecar must exist");
  vm.runInNewContext(fs.readFileSync(filename, "utf8"), context);
  assert.equal(typeof context.window.HomeDockingSourceSelection?.mount, "function");
  const options = {event, isCurrent: current, onError: e => errors.push(e), onSubmitted: value => submitted.push(value), request: async (url, body) => {
    calls.push({url, body: clone(body)});
    if (request) return request(url, body);
    if (url.endsWith("/selection")) return {task_id: "task-1", preparation_id: "prep-1", phase: "PREPARING", input_ready: false, execution_authorized: false};
    if (page) return page(body);
    return pageFor(data, body);
  }};
  const mounted = context.window.HomeDockingSourceSelection.mount(host, options);
  const controls = role => all(host).filter(n => n.getAttribute("data-role") === role);
  const button = text => all(host).find(n => n.tagName === "button" && n.textContent === text);
  return {host, calls, errors, submitted, mounted, event, data, controls, button, options};
}
function pageFor(data, b, size = b.limit) {
  const items = data[b.section].slice(b.offset, b.offset + size);
  const end = b.offset + items.length;
  return {ok: true, source_sha256: digest, section: b.section, offset: b.offset, next_offset: end < data[b.section].length ? end : null, items, input_ready: false, execution_authorized: false};
}

// Test the private capture routine itself without adding a production test API.
// All integration tests below still execute the unchanged public mount API.
function captureRig() {
  const source = fs.readFileSync(filename, "utf8"), anchor = "global.HomeDockingSourceSelection = Object.freeze({mount});";
  assert.ok(source.includes(anchor));
  const audit = []; let sawCodePoint = false;
  const context = vm.createContext({window: {}, audit: (method, value) => {
    // One witness suffices; do not retain millions of per-character test records.
    if (method === "codePointAt") { if (sawCodePoint) return; sawCodePoint = true; }
    audit.push({method, value});
  }});
  vm.runInContext(source.replace(anchor, anchor + " global.captureForTest = detached;"), context);
  vm.runInContext(`
    for (const name of ["keys", "entries", "values", "getOwnPropertyNames", "getOwnPropertyDescriptors"]) {
      const original = Object[name];
      Object[name] = function (value) { audit(name, value); return original(value); };
    }
    const ownKeys = Reflect.ownKeys;
    Reflect.ownKeys = function (value) { audit("ownKeys", value); return ownKeys(value); };
    const descriptor = Object.getOwnPropertyDescriptor;
    Object.getOwnPropertyDescriptor = function (value, key) { audit("descriptor", value); return descriptor(value, key); };
    const stringify = JSON.stringify;
    JSON.stringify = function (value, ...args) { audit("stringify", value); return stringify(value, ...args); };
    const codePointAt = String.prototype.codePointAt;
    String.prototype.codePointAt = function (index) { audit("codePointAt", this.length); return codePointAt.call(this, index); };
  `, context);
  return {capture: context.window.captureForTest, audit};
}

function proteinInventory(chainCount, count = 500) {
  const data = Object.fromEntries(sections.map(s => [s, []]));
  const names = ["N", "CA", "C", "O", "CB", "CG", "CD", "CE"];
  const heavy = names.map(name => ({name, element: name === "N" ? "N" : name === "O" ? "O" : "C"}));
  let serial = 0;
  for (let c = 0; c < chainCount; c++) {
    const chain = String.fromCharCode(65 + c);
    data.protein_selection.push({model: 1, chain, entity_id: String(c + 1), uniprot_id: "P12345", taxonomy_id: 9606, eligible: true});
    for (let i = 1; i <= count; i++) data.residues.push({residue: identity(chain, "LYS", i), role: "protein", required_heavy_atoms: clone(heavy),
      atoms: heavy.map(a => ({serial: ++serial, name: a.name, element: a.element, altloc: "", occupancy_hundredths: 100}))});
  }
  const reference = identity("A", "LIG", 9000);
  data.reference_residue.push(reference);
  data.hetero_choices.push({residue: reference, role: "deposited_ligand", actions: ["retain", "remove"]});
  data.residues.push({residue: reference, role: "deposited_ligand", required_heavy_atoms: [{name: "C", element: "C"}],
    atoms: [{serial: ++serial, name: "C", element: "C", altloc: "", occupancy_hundredths: 100}]});
  return data;
}
const load = h => h.button("查看结构并选择").click();
const submit = h => h.button("确认结构选择并生成输入");
async function change(n, value) { assert.ok(n, "expected selection control"); if (n.tagName === "input") n.checked = value; else n.value = value; await n.fire("change"); }
async function choose(h) {
  await change(h.controls("protein")[0], true);
  await change(h.controls("reference")[0], "0");
  await change(h.controls("hetero")[0], "remove");
  await change(h.controls("conformer")[0], "A");
  await change(h.controls("limitation")[0], true);
}
const unavailable = h => { assert.ok(/不可用/.test(h.host.textContent), "must explicitly show unavailable"); assert.ok(!submit(h) || submit(h).disabled); };

test("mount is inert; eight complete sections load only on explicit click with exact paginated requests", async () => {
  const data = fixture();
  data.connections = Array.from({length: 65}, (_, i) => ({record_index: i + 1, record_type: "CONECT", atom_serials: [3, 4], kind: "coordination", symmetry: []}));
  const h = harness({data}); assert.equal(h.calls.length, 0); await load(h);
  assert.equal(h.errors.length, 0); assert.equal(h.calls.length, 10);
  assert.deepEqual(h.calls.filter(c => c.body.section === "connections").map(c => c.body.offset), [0, 32, 64]);
  for (const {url, body} of h.calls) { assert.equal(url, "/api/agent/docking/tasks/task-1/source-page"); assert.deepEqual(body, {revision: 3, source_sha256: digest, section: body.section, offset: body.offset, limit: 32}); }
  assert.equal(new Set(h.calls.map(c => c.body.section)).size, 8);
  assert.ok(submit(h).disabled); assert.equal(h.controls("protein").some(n => n.checked), false);
  assert.equal(h.controls("reference")[0].value, ""); assert.match(h.host.textContent, /不.*执行/);
  await load(h); assert.equal(h.calls.length, 10);
});
test("explicit relevant choices produce the exact selection payload and only one submission", async () => {
  const h = harness(); await load(h); await choose(h);
  assert.equal(h.controls("hetero").length, 1); assert.equal(h.controls("conformer").length, 1); assert.equal(h.controls("limitation").length, 1);
  assert.equal(submit(h).disabled, false); await submit(h).click(); await submit(h).fire("click");
  assert.equal(h.calls.filter(c => c.url.endsWith("/selection")).length, 1); assert.equal(h.submitted.length, 1);
  assert.deepEqual(h.calls.at(-1), {url: "/api/agent/docking/tasks/task-1/selection", body: {revision: 3, source_sha256: digest, selection: {
    protein_selection: {model: 1, chains: [{chain: "A", entity_id: "A"}]}, reference_residue: ligand,
    hetero_choices: [{residue: water, action: "remove"}], conformer_choices: [{residue: protein, altloc: "A"}], reviewed_limitations: ["limitA"]}}});
});
test("every explicit choice and applicable acknowledgment is required; changing chains resets dependent choices", async () => {
  const h = harness(); await load(h);
  await change(h.controls("protein")[0], true); assert.ok(submit(h).disabled);
  await change(h.controls("reference")[0], "0"); assert.ok(submit(h).disabled);
  await change(h.controls("hetero")[0], "remove"); assert.ok(submit(h).disabled);
  await change(h.controls("conformer")[0], "A"); assert.ok(submit(h).disabled);
  await change(h.controls("limitation")[0], true); assert.equal(submit(h).disabled, false);
  await change(h.controls("protein")[1], true); assert.ok(submit(h).disabled); assert.equal(h.controls("reference")[0].value, "");
  await submit(h).fire("click"); assert.equal(h.calls.length, 8);
});
test("constraint assignments and incomplete atom inventories disable submission", async () => {
  const h = harness(); await load(h); await choose(h);
  await change(h.controls("conformer")[0], "B"); assert.ok(submit(h).disabled);
  const data = fixture(); data.residues[0].required_heavy_atoms.push({name: "N", element: "N"});
  const bad = harness({data}); await load(bad); await choose(bad); assert.ok(submit(bad).disabled);
});
test("short byte-limited pages continue monotonically; empty sections are requested too", async () => {
  const data = fixture(); data.connections = []; data.conformer_constraints = [];
  const h = harness({data, page: b => pageFor(data, b, 1)}); await load(h);
  assert.equal(h.errors.length, 0); assert.equal(h.calls.filter(c => c.body.section === "residues").length, 4);
  assert.ok(h.calls.some(c => c.body.section === "connections"));
});
for (const [name, mutate] of [
  ["digest", r => r.source_sha256 = "b".repeat(64)], ["section", r => r.section = "residues"], ["offset", r => r.offset++],
  ["nonmonotone", r => r.next_offset = 0], ["gap", r => r.next_offset = 3], ["truncation", r => { r.items.pop(); r.next_offset = null; }],
  ["empty continuation", r => { r.items = []; r.next_offset = 1; }], ["readiness", r => r.input_ready = true], ["authorization", r => r.execution_authorized = true],
  ["wrong type", r => r.offset = "0"], ["too many rows", r => r.items = Array(33).fill(r.items[0])], ["duplicate", r => r.items[1] = r.items[0]],
]) test("rejects invalid page: " + name, async () => {
  const data = fixture(); const h = harness({data, page: b => { const r = clone(pageFor(data, b)); mutate(r); return r; }});
  await load(h); unavailable(h); assert.equal(h.submitted.length, 0);
});
for (const [name, mutate] of [
  ["missing count", e => delete e.preview.source.choice_counts.residues], ["negative count", e => e.preview.source.choice_counts.residues = -1],
  ["row budget", e => e.preview.source.choice_counts.residues = 4097], ["revision", e => e.revision = "3"],
  ["schema", e => e.schema = "DockingView@2"], ["task path", e => e.task_id = "../approve"], ["terminal", e => e.terminal = true],
]) test("rejects invalid event without requests: " + name, async () => {
  const data = fixture(), event = eventFor(data); mutate(event); const h = harness({data, event});
  if (h.button("查看结构并选择")) await load(h); unavailable(h); assert.equal(h.calls.length, 0);
});
test("data and nesting limits fail visibly, never truncate", async () => {
  for (const huge of ["x".repeat(2100000), Array(4097).fill("x")]) {
    const data = fixture(); const h = harness({data, page: b => ({...pageFor(data, b), extra: huge})}); await load(h); unavailable(h);
  }
});
test("render facts only via textContent and detach event and returned pages", async () => {
  const data = fixture(), event = eventFor(data); event.preview.source.revision_version = "<img src=x onerror=alert(1)>";
  const h = harness({data, event}); event.revision = 999; event.preview.source.source_sha256 = "b".repeat(64);
  await load(h); assert.match(h.host.textContent, /<img src=x onerror=alert\(1\)>/); assert.match(h.host.textContent, /occupancy_hundredths/);
  data.protein_selection[0].entity_id = "changed"; data.conformer_choices[0].altlocs[0] = "Z"; data.residues[0].atoms[0].occupancy_hundredths = 0;
  await choose(h); await submit(h).click(); assert.equal(h.calls.at(-1).body.revision, 3); assert.equal(h.calls.at(-1).body.selection.protein_selection.chains[0].entity_id, "A");
});
for (const mode of ["stale", "dispose", "reject-stale"]) test("late source completion is ignored: " + mode, async () => {
  let resolve, reject, current = true; const h = harness({current: () => current, request: () => new Promise((a, b) => { resolve = a; reject = b; })});
  const pending = load(h); if (mode === "dispose") h.mounted.dispose(); else current = false;
  const before = h.host.textContent; if (mode === "reject-stale") reject(new Error("secret")); else resolve(pageFor(h.data, h.calls[0].body));
  await pending; assert.equal(h.host.textContent, before); assert.equal(h.calls.length, 1); assert.equal(h.errors.length, 0); assert.equal(h.submitted.length, 0);
});
test("late submission cannot trigger parent watch after current identity changes", async () => {
  let resolve, current = true; const data = fixture(); const h = harness({data, current: () => current, request: (url, b) => url.endsWith("/selection") ? new Promise(r => { resolve = r; }) : pageFor(data, b)});
  await load(h); await choose(h); const pending = submit(h).click(); current = false; resolve({task_id: "task-1", preparation_id: "prep-1", phase: "PREPARING", input_ready: false, execution_authorized: false}); await pending;
  assert.equal(h.submitted.length, 0); assert.equal(h.errors.length, 0);
});
test("current request rejection is generic and disables retries; dispose removes listeners and only owned DOM", async () => {
  const h = harness({request: () => Promise.reject(new Error("private detail"))}); const open = h.button("查看结构并选择");
  await load(h); unavailable(h); assert.equal(h.errors.length, 1); assert.doesNotMatch(h.host.textContent, /private detail/);
  const sibling = h.host.ownerDocument.createElement("aside"); h.host.appendChild(sibling); h.mounted.dispose(); h.mounted.dispose(); await open.fire("click");
  assert.deepEqual(h.host.children, [sibling]); assert.equal(h.calls.length, 1);
});

test("known invalid connection cuts cannot be submitted; explicit retention may satisfy the recorded edge", async () => {
  const data = fixture(); data.connections[0].atom_serials = [1, 4];
  const h = harness({data}); await load(h); await choose(h); assert.ok(submit(h).disabled);
  await change(h.controls("hetero")[0], "retain"); assert.equal(submit(h).disabled, false);
});
test("alternative-conformer connection needs an explicitly recorded counterpart", async () => {
  const data = fixture(); data.connections[0].atom_serials = [2, 4];
  const h = harness({data}); await load(h); await choose(h); await change(h.controls("hetero")[0], "retain"); assert.ok(submit(h).disabled);
  data.connections.push({...data.connections[0], record_index: 2, atom_serials: [1, 4]});
  const good = harness({data}); await load(good); await choose(good); await change(good.controls("hetero")[0], "retain"); assert.equal(submit(good).disabled, false);
});
test("complete reference options cannot silently omit an eligible deposited ligand", async () => {
  const data = fixture(), second = identity("A", "DRG", 7);
  data.residues.push({...clone(data.residues[1]), residue: second, atoms: [{...data.residues[1].atoms[0], serial: 7}]});
  data.hetero_choices.push({residue: second, role: "deposited_ligand", actions: ["retain", "remove"]});
  const h = harness({data}); await load(h); unavailable(h);
});
test("oversized complete selection is explicitly unavailable rather than silently refusing an enabled button", async () => {
  const data = fixture();
  for (let i = 0; i < 700; i++) {
    const r = identity("C", "HOH", 100 + i);
    data.residues.push({...clone(data.residues[2]), residue: r, atoms: [{...data.residues[2].atoms[0], serial: 100 + i}]});
    data.hetero_choices.push({residue: r, role: "water", actions: ["retain", "remove"]});
  }
  const h = harness({data}); await load(h); assert.equal(h.errors.length, 0);
  await change(h.controls("protein")[0], true); await change(h.controls("reference")[0], "0");
  for (let i = 0; i < data.hetero_choices.length - 1; i++) await change(h.controls("hetero")[i], "remove");
  await change(h.controls("conformer")[0], "A"); await change(h.controls("limitation")[0], true);
  unavailable(h); assert.equal(h.calls.filter(c => c.url.endsWith("/selection")).length, 0);
});
test("retained hetero altloc is explicit, removed hetero altloc is irrelevant, and coupled participants cannot be dropped", async () => {
  const data = fixture();
  data.connections = [];
  data.residues[2].atoms = [{...data.residues[2].atoms[0], altloc: "X"}];
  data.conformer_choices.push({residue: water, altlocs: ["X"]});
  data.conformer_constraints[0] = {id: "coupled", residues: [protein, water], allowed_assignments: [["A", "X"]]};
  const h = harness({data}); await load(h); await choose(h); assert.ok(submit(h).disabled);
  await change(h.controls("hetero")[0], "retain"); assert.equal(h.controls("conformer").length, 2);
  assert.equal(h.controls("conformer")[1].value, ""); assert.ok(submit(h).disabled);
  await change(h.controls("conformer")[1], "X"); assert.equal(submit(h).disabled, false);
});
test("selection responses are task/preparation scoped and failures cannot trigger watch or duplicate submission", async () => {
  for (const wrong of [{task_id: "foreign"}, {preparation_id: "foreign"}, {execution_authorized: true}, {phase: "EXECUTING"}]) {
    const data = fixture(); const h = harness({data, request: (url, body) => url.endsWith("/selection")
      ? {task_id: "task-1", preparation_id: "prep-1", phase: "PREPARING", input_ready: false, execution_authorized: false, ...wrong} : pageFor(data, body)});
    await load(h); await choose(h); await submit(h).click(); unavailable(h); assert.equal(h.submitted.length, 0);
    await submit(h).fire("click"); assert.equal(h.calls.filter(c => c.url.endsWith("/selection")).length, 1);
  }
});

test("CONECT and LINK counterparts preserve different recorded symmetry contracts", async () => {
  const data = fixture(); data.connections[0].atom_serials = [2, 4];
  data.connections.push({...data.connections[0], record_index: 2, record_type: "LINK", atom_serials: [1, 4], symmetry: ["identity", "identity"]});
  const h = harness({data}); await load(h); assert.equal(h.errors.length, 0); await choose(h);
  await change(h.controls("hetero")[0], "retain"); assert.ok(submit(h).disabled);
});
test("producer unknown connection kind stays visible but cannot be submitted", async () => {
  const data = fixture(); data.connections[0].kind = "unknown";
  const h = harness({data}); await load(h); assert.equal(h.errors.length, 0); assert.match(h.host.textContent, /unknown/);
  await choose(h); assert.ok(submit(h).disabled);
});

test("oversized array capacity is checked before any whole-container key materialization", () => {
  const {capture, audit} = captureRig(), oversized = Array(100000).fill(null);
  assert.throws(() => capture(oversized));
  assert.equal(audit.filter(a => a.value === oversized && a.method !== "descriptor").length, 0,
    "must not enumerate/materialize 100000 array keys before rejecting length");
  assert.ok(audit.filter(a => a.value === oversized && a.method === "descriptor").length <= 1);
});
test("oversized object uses bounded enumeration instead of a complete key/descriptor array", () => {
  const {capture, audit} = captureRig(), oversized = {};
  for (let i = 0; i < 100000; i++) oversized["field" + i] = null;
  assert.throws(() => capture(oversized));
  assert.equal(audit.filter(a => a.value === oversized && a.method !== "descriptor").length, 0,
    "Object.keys/entries/values/ownKeys must not materialize an unbounded input");
  assert.ok(audit.filter(a => a.value === oversized && a.method === "descriptor").length <= 32);
});
test("capture charges exact compact JSON UTF-8 bytes, including native code points, escaping and lone surrogates", () => {
  const {capture, audit} = captureRig();
  const samples = [null, true, false, 0, -0, -123.5, 1e30, "", "ASCII", "é中😺", "\b\t\n\f\r\u0000\u001f\\\"",
    "\ud800", "\udc00", "\ud800x\udc00", "\ud83d\ude3a", [], {}, [null, true, 3, "中"], {"é😺\"": ["\n", {field: "value"}]}];
  for (const value of samples) {
    const budget = {bytes: 0, nodes: 0}, copied = capture(value, budget);
    assert.equal(budget.bytes, Buffer.byteLength(JSON.stringify(value), "utf8"), "exact UTF-8 charge for " + JSON.stringify(value));
    assert.deepEqual(clone(copied), clone(value));
  }
  assert.ok(audit.some(a => a.method === "codePointAt"), "UTF-8 traversal uses native code points");
  assert.ok(audit.filter(a => a.method === "stringify").every(a => typeof a.value === "number"), "never stringify a whole container or escaped string to count it");
});
test("exact cumulative 2 MiB boundary accepts the last byte and rejects one more without constructing aggregate JSON", () => {
  const {capture, audit} = captureRig(), page = {items: ["é中😺", "\n", "a".repeat(4096)]};
  const size = Buffer.byteLength(JSON.stringify(page), "utf8"), limit = 2 * 1024 * 1024;
  const budget = {bytes: limit - size, nodes: 0}; capture(page, budget); assert.equal(budget.bytes, limit);
  assert.throws(() => capture(0, budget));
  const aggregate = {bytes: 0, nodes: 0}; let accepted = 0;
  while ((accepted + 1) * size <= limit) { capture(page, aggregate); accepted++; }
  assert.equal(aggregate.bytes, accepted * size); assert.throws(() => capture(page, aggregate));
  assert.ok(!audit.some(a => a.method === "stringify" && a.value && typeof a.value === "object"));
});
test("JSON node ceiling remains independent of byte/row ceilings and does not count synthetic array keys", () => {
  const {capture} = captureRig(), budget = {bytes: 0, nodes: 0};
  capture({a: [1, 2], b: null}, budget); assert.equal(budget.nodes, 5);
  const manyValues = Array.from({length: 38}, () => Array(4096).fill(null));
  const largeBudget = {bytes: 0, nodes: 0}; assert.throws(() => capture(manyValues, largeBudget));
  assert.ok(largeBudget.nodes <= 150001 && largeBudget.bytes < 2 * 1024 * 1024);
});
test("oversized scalar is rejected before encoding/stringifying; sparse arrays and accessors fail closed without invocation", () => {
  const {capture, audit} = captureRig(), enormous = "😺".repeat(1000000);
  assert.throws(() => capture(enormous));
  assert.equal(audit.length, 0, "length cap precedes encoding, escape allocation and codepoint scanning");
  assert.throws(() => capture(Array(4)));
  let called = 0; const accessor = {};
  Object.defineProperty(accessor, "value", {enumerable: true, get() { called++; return "hidden"; }});
  assert.throws(() => capture(accessor)); assert.equal(called, 0);
  const extra = [1]; extra.unexpected = true; assert.throws(() => capture(extra));
});
for (const chains of [1, 2]) test(`ordinary protein inventory ${chains} x 500 residues loads all ${chains * 4000 + 1} heavy atoms and permits explicit selection`, async () => {
  const data = proteinInventory(chains); let bytes = 0;
  const h = harness({data, page: b => { const page = pageFor(data, b); const size = Buffer.byteLength(JSON.stringify(page), "utf8"); assert.ok(size < 48 * 1024); bytes += size; return page; }});
  await load(h); assert.equal(h.errors.length, 0, "normal-size inventory must fit the documented UI budget");
  assert.ok(bytes < 2 * 1024 * 1024);
  assert.equal(h.calls.filter(c => c.body.section === "residues").length, Math.ceil(data.residues.length / 32));
  assert.equal(all(h.host).filter(n => n.tagName === "pre").length, 1 + sections.reduce((sum, s) => sum + data[s].length, 0), "no source row omitted");
  assert.ok(submit(h).disabled); assert.ok(h.controls("protein").every(n => !n.checked));
  for (let i = 0; i < chains; i++) await change(h.controls("protein")[i], true);
  await change(h.controls("reference")[0], "0"); assert.equal(submit(h).disabled, false);
  await submit(h).click(); assert.equal(h.calls.at(-1).body.selection.protein_selection.chains.length, chains); assert.equal(h.submitted.length, 1);
  console.log(`  inventory ${chains}x500: ${bytes} compact page-JSON UTF-8 bytes`);
});
test("oversized source inventory becomes explicitly unavailable with no omissions or submission", async () => {
  const data = proteinInventory(5); let bytes = 0;
  const h = harness({data, page: b => { const page = pageFor(data, b); bytes += Buffer.byteLength(JSON.stringify(page), "utf8"); return page; }});
  await load(h); unavailable(h); assert.equal(h.errors.length, 1);
  assert.equal(h.calls.filter(c => c.url.endsWith("/selection")).length, 0);
  assert.equal(all(h.host).filter(n => n.tagName === "pre").length, 1, "never present a partially loaded inventory as selectable");
  assert.ok(bytes > 0);
});

(async () => { let failed = 0; for (const [name, fn] of tests) { try { await fn(); console.log("PASS " + name); } catch (e) { failed++; console.error("FAIL " + name + ": " + e.message); } } console.log(`${tests.length - failed}/${tests.length} passed`); if (failed) process.exitCode = 1; })();
