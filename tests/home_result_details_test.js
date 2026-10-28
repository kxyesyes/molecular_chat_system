"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const {spawnSync} = require("child_process");

const file = path.join(__dirname, "../src/web/static/js/home/result_details.js");
assert(fs.existsSync(file), "lazy result-details component is missing");

class Element {
  constructor(tag, doc) { this.tagName = tag; this.ownerDocument = doc; this.children = [];
    this.listeners = {}; this.attributes = {}; this.open = false; this._text = ""; }
  appendChild(child) { child.remove(); this.children.push(child); child.parentNode = this; return child; }
  remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(c => c !== this);
    this.parentNode = null; }
  replaceChildren(...children) { this.children.slice().forEach(c => c.remove()); this._text = "";
    children.forEach(c => this.appendChild(c)); }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
  removeEventListener(name, fn) { this.listeners[name] = (this.listeners[name] || []).filter(f => f !== fn); }
  dispatch(name) { return Promise.all((this.listeners[name] || []).map(fn => fn({target: this}))); }
  get isConnected() { return this.root === true || Boolean(this.parentNode?.isConnected); }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(" "); }
  set textContent(value) { this.replaceChildren(); this._text = String(value); }
  set innerHTML(_) { throw new Error("details must never use HTML injection"); }
  all() { return this.children.flatMap(child => [child, ...child.all()]); }
}

const observers = new Set();
class MutationObserver {
  constructor(callback) {this.callback = callback;}
  observe() {observers.add(this);}
  disconnect() {observers.delete(this);}
}
function mutations() {for (const observer of [...observers]) observer.callback();}
const document = {documentElement: {}, createElement(tag) {return new Element(tag, document);}};
const window = {document, MutationObserver, HomeConfig: {resultDetails: {baseUrl: "/api/agent/runs"}}};
const context = vm.createContext({window, document, console, TextEncoder, TextDecoder,
  AbortController, Uint8Array, URL, JSON, setTimeout, clearTimeout});
vm.runInContext(fs.readFileSync(file, "utf8"), context, {filename: file});
const api = window.HomeResultDetails;
assert(api && api.mount && api.dispose && api.read && api.abortAll, "explicit details API is required");
const traceId = "a".repeat(32);
const reference = {view_id: "00000000000040008000000000000001", revision: "b".repeat(64), byte_length: 400};
const payload = {format: "summary_details_v1", trace_id: traceId, status: "partial",
  tool_result_sequence: [{tool_name: "property_calculator", status: "succeeded",
    data: [{smiles: "CCO", molecular_weight: 46.07, hostile: "<img src=x onerror=alert(1)>"}]}],
  error: null, warnings: ["specific fixture warning"], display_redacted: false};
const details = {...reference, payload};
function host() {const node = document.createElement("div"); node.root = true; return node;}
const tick = () => new Promise(resolve => setImmediate(resolve));
async function expandAll(container) {
  for (let round = 0; round < 8; round++) {
    for (const node of container.all().filter(n => n.tagName === "details" && !n.open)) {
      node.open = true; await node.dispatch("toggle");
    }
  }
}
let passed = 0;
async function test(name, fn) {await fn(); passed++; console.log("PASS " + name);}
const copy = value => JSON.parse(JSON.stringify(value));
const readData = value => api.read({traceId, reference, fetcher: async () => response(value)});
const code = expected => error => error.code === expected && !/private|secret/.test(error.message);
async function open(container) {
  const root = container.children.find(n => n.tagName === "details");
  root.open = true; await root.dispatch("toggle"); await tick(); return root;
}
async function showEverything(container) {
  for (let i = 0; i < 80; i++) {
    const disclosures = container.all().filter(n => n.tagName === "details" && !n.open);
    const buttons = container.all().filter(n => n.tagName === "button" && n.textContent.startsWith("显示更多"));
    if (!disclosures.length && !buttons.length) return;
    for (const node of disclosures) {node.open = true; await node.dispatch("toggle");}
    for (const node of buttons) await node.dispatch("click");
  }
  assert.fail("all bounded fixture fields must be reachable");
}

// Raw numeric tokens must never pass through JS Number in the test fixture.
function numericWire(token) {
  const rawPayload = JSON.stringify({...payload, tool_result_sequence: [
    {tool_name: "property_calculator", data: {value: "NUMBER_TOKEN"}},
  ]}).replace('"NUMBER_TOKEN"', token);
  const ref = {...reference, byte_length: Buffer.byteLength(rawPayload)};
  const raw = JSON.stringify({success: true, data: {...ref, payload: "PAYLOAD_TOKEN"}})
    .replace('"PAYLOAD_TOKEN"', rawPayload);
  return {ref, raw};
}
const readWire = ({ref, raw}) => api.read({traceId, reference: ref,
  fetcher: async () => byteResponse(new TextEncoder().encode(raw))});

function pythonNumericWire() {
  const script = `
import hashlib, json
values = [2**53+1, -(2**53+1), 2**4096-1, -(2**4096-1),
          1e-7, 1e20, 1.0, -0.0, 1.0000000000000002, 5e-324, 1.7976931348623157e308]
def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':'))
expected = [encode(value) for value in values]
payload = dict(format='summary_details_v1', trace_id='a'*32, status='completed',
    tool_result_sequence=[dict(tool_name='property_calculator', status='succeeded',
        data=[dict(endpoint='synthetic-'+str(i), value=value, text=expected[i]) for i, value in enumerate(values)])],
    error=None, warnings=[], display_redacted=False)
raw = encode(payload).encode('utf-8')
ref = dict(view_id='00000000000040008000000000000001', revision=hashlib.sha256(raw).hexdigest(), byte_length=len(raw))
print(encode(dict(ref=ref, raw=encode(dict(success=True, data=dict(**ref, payload=payload))), expected=expected)))
`;
  const result = spawnSync(process.env.MEDCHAT_TEST_PYTHON || "python", ["-B", "-c", script],
    {encoding: "utf8", timeout: 10000, maxBuffer: 128 * 1024, windowsHide: true});
  assert.equal(result.status, 0, "Python numeric fixture generation failed; set MEDCHAT_TEST_PYTHON to a Python 3 executable");
  return JSON.parse(result.stdout);
}

async function run() {
  await test("401 is an authentication error, never reads diagnostics, and retries only its own GET", async () => {
    const container = host(), calls = [];
    let bodyReads = 0;
    const fetcher = async (url, options) => {
      calls.push({url, options});
      if (calls.length === 1) return {ok: false, status: 401,
        get body() {bodyReads++; throw new Error("C:/private/secret");},
        text() {bodyReads++; return "C:/private/secret";},
        json() {bodyReads++; return {message: "C:/private/secret"};}};
      return response({success: true, data: details});
    };
    await assert.rejects(api.read({traceId, reference, fetcher}), code("auth"));
    calls.length = 0;
    api.mount(container, {traceId, reference, load: options => api.read({...options, fetcher})});
    await open(container);
    assert(container.textContent.includes("会话验证失败，请重新登录后重试。"));
    assert(!/网络异常|private|secret/.test(container.textContent));
    assert.equal(bodyReads, 0);
    const retry = container.all().find(n => n.tagName === "button" && n.textContent === "重试");
    assert(retry); await retry.dispatch("click"); await showEverything(container);
    assert.equal(calls.length, 2);
    assert(calls.every(c => c.options.method === "GET" && c.options.credentials === "same-origin"));
    assert.equal(calls[0].url, calls[1].url);
    assert(container.textContent.includes("46.07")); api.dispose(container);
    await assert.rejects(api.read({traceId, reference, fetcher: async () => {throw new TypeError("offline");}}), code("network"));
    await assert.rejects(api.read({traceId, reference, fetcher: async () => ({ok: false, status: 404})}), code("not_found"));
  });
  await test("Python JSON integers and finite floats retain exact numeric lexemes through the frozen cache and tables", async () => {
    const wire = pythonNumericWire(), data = await readWire(wire);
    const rows = data.payload.tool_result_sequence[0].data;
    for (let i = 0; i < wire.expected.length; i++) {
      const lexeme = wire.expected[i], value = rows[i].value;
      assert.equal(rows[i].text, lexeme, "ordinary strings must remain strings");
      if (JSON.stringify(Number(lexeme)) !== lexeme) {
        assert.equal(typeof value, "object", `must preserve ${lexeme.slice(0, 48)} as a numeric scalar`);
        assert(Object.isFrozen(value));
        assert.throws(() => JSON.stringify(value), "lossless numbers must not silently serialize as {} or strings");
      }
    }
    assert(Object.isFrozen(data) && Object.isFrozen(rows));
    const container = host(); let calls = 0;
    const root = api.mount(container, {traceId, reference: wire.ref, load: async () => {calls++; return data;}});
    await open(container); await showEverything(container);
    const tableRows = container.all().filter(n => n.tagName === "tr" && n.children[0]?.tagName === "td");
    assert.equal(tableRows.length, wire.expected.length, "numeric scalars must stay scalar table cells");
    tableRows.forEach((row, i) => assert.equal(row.children[2].textContent, wire.expected[i]));
    const before = container.textContent;
    root.open = false; await root.dispatch("toggle"); root.open = true; await root.dispatch("toggle");
    assert.equal(calls, 1); assert.equal(container.textContent, before); api.dispose(container);
  });
  await test("precision-sensitive decimal literals and negative zero remain numeric scalars, never rounded text", async () => {
    for (const token of ["0.100000000000000005", "1.23456789012345678901234567890", "-0", "1e-400"]) {
      const wire = numericWire(token), data = await readWire(wire), container = host();
      const scalar = data.payload.tool_result_sequence[0].data.value;
      assert.equal(typeof scalar, "object"); assert(Object.isFrozen(scalar));
      api.mount(container, {traceId, reference: wire.ref, load: async () => data});
      await open(container); await showEverything(container);
      assert(container.all().some(n => n.tagName === "pre" && n.textContent === token), token);
      assert(!container.all().some(n => n.tagName === "button" && n.textContent === "重试")); api.dispose(container);
    }
  });
  await test("malformed or nonfinite numeric tokens and integers beyond the server bit budget fail closed", async () => {
    for (const token of ["01", "+1", ".1", "1.", "1e", "NaN", "Infinity", "-Infinity",
      "1e999", "1e309", "-1e309", (2n ** 4096n).toString(), "-" + (2n ** 4096n).toString()]) {
      await assert.rejects(readWire(numericWire(token)), code("invalid"), token.slice(0, 40));
    }
  });
  await test("lossless scientific scalars cannot replace strict integer envelope fields or schema records", async () => {
    const wire = numericWire("9007199254740993");
    for (const token of [`${wire.ref.byte_length}.00000000000000001`, "9007199254740993", "1e999", '"400"', "true"]) {
      const raw = wire.raw.replace(`"byte_length":${wire.ref.byte_length}`, `"byte_length":${token}`);
      await assert.rejects(readWire({...wire, raw}), code("invalid"));
    }
    for (const [field, value] of [["error", "null"], ["status", '"partial"'], ["warnings", JSON.stringify(payload.warnings)]]) {
      const raw = wire.raw.replace(`"${field}":${value}`, `"${field}":9007199254740993`);
      await assert.rejects(readWire({...wire, raw}), code("invalid"));
    }
    await assert.rejects(readWire({...wire, raw: wire.raw.replace(
      /"tool_result_sequence":\[.*\],"error"/, '"tool_result_sequence":[9007199254740993],"error"')}), code("invalid"));
    const scalar = (await readWire(wire)).payload.tool_result_sequence[0].data.value;
    await assert.rejects(api.read({traceId, reference: {...wire.ref, byte_length: scalar}, fetcher: async () => {
      assert.fail("numeric scalar references must fail before fetching");
    }}), code("invalid"));
  });
  await test("starts closed, loads on expansion, and reuses successful data", async () => {
    let calls = 0;
    const container = host();
    const root = api.mount(container, {traceId, reference, load: async () => {calls++; return details;}});
    assert.equal(root.tagName, "details"); assert.equal(root.open, false); assert.equal(calls, 0);
    assert(container.textContent.includes("查看明细"));
    root.open = true; await root.dispatch("toggle"); await tick();
    await expandAll(container);
    assert.equal(calls, 1); assert(container.textContent.includes("46.07"));
    assert(container.textContent.includes("<img src=x onerror=alert(1)>"));
    assert(!container.all().some(node => node.tagName === "img"));
    root.open = false; await root.dispatch("toggle"); root.open = true; await root.dispatch("toggle");
    assert.equal(calls, 1);
  });
  await test("concurrent expansion shares a read and disposal prevents stale mutation", async () => {
    let finish, calls = 0, signal;
    const container = host();
    const root = api.mount(container, {traceId, reference, load: options => {
      signal = options.signal; calls++; return new Promise(resolve => {finish = resolve;});
    }});
    root.open = true; const first = root.dispatch("toggle"); root.dispatch("toggle");
    await tick(); assert.equal(calls, 1);
    api.dispose(container); assert(signal.aborted);
    const before = container.textContent; finish(details); await first; await tick();
    assert.equal(container.textContent, before);
  });
  await test("failed reads have a read-only retry and do not silently change status", async () => {
    let calls = 0; const container = host();
    const root = api.mount(container, {traceId, reference, load: async () => {
      if (++calls === 1) throw new Error("untrusted diagnostic C:/private/secret"); return details;
    }});
    root.open = true; await root.dispatch("toggle"); await tick();
    assert(!container.textContent.includes("C:/private"));
    const retry = container.all().find(n => n.tagName === "button" && n.textContent === "重试");
    assert(retry); await retry.dispatch("click"); await tick(); await expandAll(container);
    assert.equal(calls, 2); assert(container.textContent.includes("46.07"));
  });
  await test("read is same-origin and strictly binds the response reference", async () => {
    const requests = [];
    const fetcher = async (url, options) => {
      requests.push({url, options});
      return response({success: true, data: details});
    };
    const got = await api.read({traceId, reference, fetcher});
    assert.deepEqual(JSON.parse(JSON.stringify(got)), details);
    assert.equal(requests[0].url, `/api/agent/runs/${traceId}/result-details/${reference.view_id}?revision=${reference.revision}`);
    assert.equal(requests[0].options.credentials, "same-origin");
    assert.equal(requests[0].options.method, "GET");
    assert.equal(requests[0].options.cache, "no-store");
    assert.equal(requests[0].options.redirect, "error");
    await assert.rejects(api.read({traceId, reference, fetcher: async () => response({success: true,
      data: {...details, revision: "c".repeat(64)}})}));
    await assert.rejects(api.read({traceId: "../foreign", reference, fetcher}));
    assert.equal(requests.length, 1);
  });
  await test("streamed reads enforce aggregate bytes even without Content-Length", async () => {
    let cancelled = false, reads = 0;
    const fetcher = async () => ({ok: true, headers: {get: () => null}, body: {getReader: () => ({
      async read() {reads++; return {done: false, value: new Uint8Array(1024 * 1024)};},
      async cancel() {cancelled = true;}, releaseLock() {},
    })}});
    await assert.rejects(api.read({traceId, reference, fetcher}));
    assert(cancelled); assert(reads <= 5);
  });
  await test("invalid reference and trace types fail before any request", async () => {
    let calls = 0; const fetcher = async () => {calls++; return response({success: true, data: details});};
    for (const bad of [null, [], {...reference, extra: 1}, {...reference, view_id: "1".repeat(32)},
      {...reference, view_id: reference.view_id.toUpperCase().replace("4", "F")},
      {...reference, revision: "B".repeat(64)}, {...reference, revision: new String(reference.revision)},
      ...[0, -1, true, "400", 1.5, Infinity, 4 * 1024 * 1024 + 1].map(byte_length => ({...reference, byte_length}))]) {
      await assert.rejects(api.read({traceId, reference: bad, fetcher}), code("invalid"));
    }
    for (const bad of [null, 1, "A".repeat(32), "a".repeat(31), "../foreign", traceId + "\n"]) {
      await assert.rejects(api.read({traceId: bad, reference, fetcher}), code("invalid"));
    }
    assert.equal(calls, 0);
  });
  await test("remote or ambiguous configured URLs never reach fetch", async () => {
    let calls = 0;
    for (const baseUrl of ["https://evil.test/api/agent/runs", "//evil.test", "/\\evil.test", "/api/../runs",
      "/api/agent/runs?next=https://evil.test", "javascript:alert(1)", undefined]) {
      window.HomeConfig.resultDetails.baseUrl = baseUrl;
      await assert.rejects(api.read({traceId, reference, fetcher: async () => {calls++;}}), code("invalid"));
    }
    window.HomeConfig.resultDetails.baseUrl = "/api/agent/runs";
    assert.equal(calls, 0);
  });
  await test("redirects and opaque responses are rejected without reading", async () => {
    for (const metadata of [{redirected: true}, {type: "opaqueredirect"}, {type: "opaque"},
      {url: "https://evil.test/api/agent/runs"}]) {
      let reads = 0;
      await assert.rejects(api.read({traceId, reference, fetcher: async () => ({ok: true, ...metadata,
        body: {getReader() {reads++; throw new Error("private");}}})}), code("invalid"));
      assert.equal(reads, 0);
    }
  });
  await test("standard envelope metadata is accepted but untrusted messages are not rendered", async () => {
    const got = await readData({success: true, code: "OK", message: "C:/private/secret",
      request_id: "opaque-id", data: details});
    assert.deepEqual(copy(got), details);
    for (const bad of [{success: "true", data: details}, {success: false, data: details},
      {success: true, data: null}, {success: true, data: {...details, extra: true}},
      {success: true, data: {...details, view_id: "00000000000040008000000000000002"}},
      {success: true, data: {...details, byte_length: 401}}]) {
      await assert.rejects(readData(bad), code("invalid"));
    }
  });
  await test("payload has exact fields and strict status, trace, format and value types", async () => {
    for (const patch of [{format: "legacy"}, {trace_id: "c".repeat(32)}, {status: "succeeded"},
      {status: {}}, {tool_result_sequence: {}}, {tool_result_sequence: [null]},
      {tool_result_sequence: ["bad"]}, {error: "C:/private/secret"}, {error: []},
      {warnings: "warning"}, {warnings: [null]}, {display_redacted: 0}, {unknown: 1}]) {
      await assert.rejects(readData({success: true, data: {...details, payload: {...payload, ...patch}}}), code("invalid"));
    }
    const missing = copy(details); delete missing.payload.error;
    await assert.rejects(readData({success: true, data: missing}), code("invalid"));
    for (const status of ["completed", "partial", "failed", "rejected", "cancelled", "timeout",
      "unavailable", "not_calculated", "waiting_for_input"]) {
      assert.equal((await readData({success: true, data: {...details, payload: {...payload, status}}})).payload.status, status);
    }
  });
  await test("malformed JSON, duplicate keys, invalid UTF8 and nonfinite JSON fail closed", async () => {
    const valid = JSON.stringify({success: true, data: details});
    for (const raw of ["{", "null", valid + "{}", valid.replace('"success":true', '"success":false,"success":true'),
      valid.replace("46.07", "1e999"), valid.replace('"success":true', '"su\\u0063cess":false,"success":true')]) {
      await assert.rejects(api.read({traceId, reference, fetcher: async () => byteResponse(new TextEncoder().encode(raw))}), code("invalid"));
    }
    await assert.rejects(api.read({traceId, reference, fetcher: async () => byteResponse(new Uint8Array([0xff, 0xff]))}), code("invalid"));
  });
  await test("UTF8 split across chunks decodes safely and Content-Length is not trusted", async () => {
    const value = {success: true, data: {...details, payload: {...payload, warnings: ["中文 🧪"]}}};
    const bytes = new TextEncoder().encode(JSON.stringify(value));
    let offset = 0;
    const got = await api.read({traceId, reference, fetcher: async () => ({ok: true,
      headers: {get: () => "1"}, body: {getReader: () => ({
        async read() {return offset < bytes.length ? {done: false, value: bytes.slice(offset, ++offset)} : {done: true};},
        async cancel() {}, releaseLock() {},
      })}})});
    assert.equal(got.payload.warnings[0], "中文 🧪");
  });
  await test("both declared and hard streamed byte limits cancel at their boundary", async () => {
    for (const ref of [reference, {...reference, byte_length: 4 * 1024 * 1024}]) {
      const limit = ref.byte_length + 64 * 1024;
      const base = new TextEncoder().encode(JSON.stringify({success: true, data: {...details, byte_length: ref.byte_length}}));
      const padded = new Uint8Array(limit); padded.fill(32); padded.set(base);
      await api.read({traceId, reference: ref, fetcher: async () => byteResponse(padded)});
      let reads = 0, cancelled = 0;
      await assert.rejects(api.read({traceId, reference: ref, fetcher: async () => ({ok: true,
        headers: {get: () => "0"}, body: {getReader: () => ({
          async read() {reads++; return {done: false, value: reads === 1 ? padded : new Uint8Array(1)};},
          async cancel() {cancelled++;}, releaseLock() {},
        })}})}), code("too_large"));
      assert.equal(reads, 2); assert.equal(cancelled, 1);
    }
  });
  await test("no unbounded json/text fallback and aborted readers cancel promptly", async () => {
    let fallback = 0;
    await assert.rejects(api.read({traceId, reference, fetcher: async () => ({ok: true,
      json() {fallback++;}, text() {fallback++;}})}), code("invalid"));
    assert.equal(fallback, 0);
    const controller = new AbortController(); let cancelled = 0, reads = 0;
    const promise = api.read({traceId, reference, signal: controller.signal, fetcher: async () => ({ok: true,
      body: {getReader: () => ({read() {reads++; return new Promise(() => {});},
        async cancel() {cancelled++;}, releaseLock() {},})}})});
    await tick(); controller.abort();
    await assert.rejects(promise, code("aborted")); assert.equal(reads, 1); assert.equal(cancelled, 1);
    let fetched = false;
    await assert.rejects(api.read({traceId, reference, signal: controller.signal, fetcher: async () => {fetched = true;}}), code("aborted"));
    assert.equal(fetched, false);
  });
  await test("nesting and node budgets reject before recursive rendering", async () => {
    const broad = copy(details); broad.payload.tool_result_sequence[0].data = Array(65536).fill(null);
    await assert.rejects(readData({success: true, data: broad}), code("too_large"));
    const deep = copy(details); let tail = deep.payload.tool_result_sequence[0];
    for (let i = 0; i < 40; i++) {tail.data = {}; tail = tail.data;}
    await assert.rejects(readData({success: true, data: deep}), code("too_large"));
    const nested = '{"success":true,"data":' + '['.repeat(2000) + '0' + ']'.repeat(2000) + '}';
    await assert.rejects(api.read({traceId, reference, fetcher: async () => byteResponse(new TextEncoder().encode(nested))}), code("too_large"));
    const allowed = copy(details); tail = allowed.payload.tool_result_sequence[0];
    for (let i = 0; i < 30; i++) {tail.data = {}; tail = tail.data;} tail.value = 1;
    await readData({success: true, data: allowed});
  });
  await test("custom loaders cannot bypass schema, cycle, getter or size guards", async () => {
    const cyclic = copy(details); cyclic.payload.error = cyclic;
    const getter = copy(details); Object.defineProperty(getter.payload, "status", {get() {throw new Error("private");}});
    const oversized = copy(details); oversized.payload.warnings = ["界".repeat(1500000)];
    const nan = copy(details); nan.payload.error = {value: NaN};
    for (const value of [cyclic, getter, oversized, nan, {...details, revision: "c".repeat(64)}]) {
      const container = host(); api.mount(container, {traceId, reference, load: async () => value});
      await open(container);
      assert(container.all().some(n => n.tagName === "button" && n.textContent === "重试"));
      assert(!container.textContent.includes("private")); api.dispose(container);
    }
  });
  await test("error categories are localized and never expose diagnostics or response bodies", async () => {
    const cases = [
      [async () => {throw new Error("C:/private/secret");}, "网络"],
      [async () => ({ok: false, status: 404, text() {throw new Error("private");}}), "不存在"],
      [async () => response({success: true, data: {...details, revision: "c".repeat(64)}}), "格式"],
      [async () => byteResponse(new Uint8Array(reference.byte_length + 65537)), "过大"],
    ];
    for (const [fetcher, text] of cases) {
      const container = host(); api.mount(container, {traceId, reference, load: options => api.read({...options, fetcher})});
      await open(container); assert(container.textContent.includes(text));
      assert(!/private|secret/.test(container.textContent)); api.dispose(container);
    }
  });
  await test("business labels and all scientific/error/quality/provenance fields stay inside details", async () => {
    const value = copy(details);
    value.payload.error = {code: "partial_fixture", message: "缺少模型"};
    value.payload.tool_result_sequence = ["property_calculator", "admet_predictor", "activity_predictor",
      "target_database_search", "reverse_target_predictor", "molecular_docking"].map(tool_name => ({
        tool_name, status: "failed", data: [{smiles: "CCO", molecular_weight: 46.07, units: "g/mol"}],
        quality: {disagreement: "预测分歧"}, provenance: {model_version: "fixture-model"}, step_id: "fixture-step"}));
    const container = host(); const sentinel = document.createElement("p"); sentinel.textContent = "summary";
    container.appendChild(sentinel); const root = api.mount(container, {traceId, reference, load: async () => value});
    root.open = true; await root.dispatch("toggle"); await showEverything(container);
    for (const text of ["分子性质", "ADMET", "活性预测", "靶点检索", "反向寻靶", "分子对接", "46.07", "g/mol",
      "缺少模型", "预测分歧", "fixture-model", "fixture-step", reference.revision, traceId]) assert(root.textContent.includes(text), text);
    assert(root.all().some(n => n.tagName === "dl" || n.tagName === "table"));
    assert.equal(container.children.length, 2); api.dispose(container);
    assert.equal(container.textContent, "summary");
  });
  await test("large/wide collections and long strings paginate without lost fields or eager DOM", async () => {
    const value = copy(details), long = "起" + "中".repeat(12000) + "终";
    value.payload.tool_result_sequence[0].data = Array.from({length: 140}, (_, i) => ({endpoint: `fixture-${i}`, value: i}));
    value.payload.tool_result_sequence[0].quality = Object.fromEntries(Array.from({length: 160}, (_, i) => [`wide-${i}`, i]));
    value.payload.error = {message: long};
    const container = host(); api.mount(container, {traceId, reference, load: async () => value});
    await open(container); assert(container.all().length < 100);
    await expandAll(container); assert(container.all().length < 500);
    assert(container.all().some(n => n.tagName === "button" && n.textContent.startsWith("显示更多")));
    await showEverything(container);
    for (let i = 0; i < 140; i++) assert(container.textContent.includes(`fixture-${i}`));
    for (let i = 0; i < 160; i++) assert(container.textContent.includes(`wide-${i}`));
    const longNode = container.all().find(n => n.tagName === "pre" && n.textContent.startsWith("起"));
    assert(longNode); assert.equal(longNode.textContent, long); api.dispose(container);
  });
  await test("prototype-like keys and remote strings remain inert literal text", async () => {
    const value = copy(details);
    value.payload.error = JSON.parse('{"__proto__":"literal-proto","constructor":"literal-constructor","url":"https://evil.test","html":"<script>alert(1)</script>"}');
    const container = host(); api.mount(container, {traceId, reference, load: async () => value});
    await open(container); await showEverything(container);
    for (const text of ["literal-proto", "literal-constructor", "https://evil.test", "<script>alert(1)</script>"]) assert(container.textContent.includes(text));
    assert(!container.all().some(n => ["a", "script", "img", "iframe"].includes(n.tagName))); api.dispose(container);
  });
  await test("each mount freezes its reference and cached payload independently", async () => {
    const ref = {...reference}, value = copy(details), seen = [], first = host(), second = host();
    api.mount(first, {traceId, reference: ref, load: async options => {seen.push(options); return value;}});
    ref.revision = "c".repeat(64); ref.byte_length = 1;
    await open(first); value.payload.tool_result_sequence[0].data[0].molecular_weight = 999;
    await showEverything(first); assert(first.textContent.includes("46.07")); assert(!first.textContent.includes("999"));
    api.mount(second, {traceId, reference, load: async () => details}); await open(second);
    assert.equal(seen[0].reference.revision, reference.revision); assert(Object.isFrozen(seen[0].reference));
    api.dispose(first); assert(second.children.length); api.dispose(second);
  });
  await test("remount cancels the old instance and ignores its late completion", async () => {
    const container = host(); let finish, signal;
    const old = api.mount(container, {traceId, reference, load: options => {signal = options.signal; return new Promise(resolve => {finish = resolve;});}});
    old.open = true; const pending = old.dispatch("toggle"); await tick();
    const replacement = api.mount(container, {traceId, reference, load: async () => details});
    assert(signal.aborted); assert.equal(container.children.length, 1); assert.equal(replacement.open, false);
    finish({...details, revision: "c".repeat(64)}); await pending;
    assert.equal(container.textContent.trim(), "查看明细"); api.dispose(container); api.dispose(container);
  });
  await test("detachment aborts immediately and late responses cannot rebuild detached UI", async () => {
    const container = host(); let finish, signal;
    const root = api.mount(container, {traceId, reference, load: options => {signal = options.signal; return new Promise(resolve => {finish = resolve;});}});
    root.open = true; const pending = root.dispatch("toggle"); await tick();
    container.root = false; mutations(); assert(signal.aborted);
    const before = container.textContent; finish(details); await pending;
    assert.equal(container.textContent, before);
    container.root = true; root.open = true; await root.dispatch("toggle"); assert.equal(container.textContent, before);
  });
  await test("detached containers never start reads and connection is rechecked after await", async () => {
    const container = document.createElement("div"); let calls = 0, finish;
    const root = api.mount(container, {traceId, reference, load: () => {calls++; return new Promise(resolve => {finish = resolve;});}});
    root.open = true; await root.dispatch("toggle"); assert.equal(calls, 0);
    container.root = true; const pending = root.dispatch("toggle"); await tick();
    container.root = false; finish(details); await pending;
    assert(!container.textContent.includes("46.07")); api.dispose(container);
  });
  await test("abortAll only cancels inflight reads, preserves history, ignores late data and permits retry", async () => {
    const historical = host(), loading = host(); let resolveOld, signal, calls = 0;
    api.mount(historical, {traceId, reference, load: async () => details}); await open(historical); await showEverything(historical);
    const before = historical.textContent;
    const root = api.mount(loading, {traceId, reference, load: options => {
      calls++; signal = options.signal;
      return calls === 1 ? new Promise(resolve => {resolveOld = resolve;}) : Promise.resolve(details);
    }});
    root.open = true; const first = root.dispatch("toggle"); await tick(); api.abortAll();
    assert(signal.aborted); assert.equal(historical.textContent, before); assert(loading.children.includes(root));
    const retry = loading.all().find(n => n.tagName === "button" && n.textContent === "重试"); assert(retry);
    await retry.dispatch("click"); await showEverything(loading); assert.equal(calls, 2); const successful = loading.textContent;
    resolveOld({...details, revision: "c".repeat(64)}); await first; await tick();
    assert.equal(loading.textContent, successful); assert(loading.textContent.includes("部分完成"));
    api.abortAll(); assert.equal(historical.textContent, before); api.dispose(historical); api.dispose(loading);
  });
  await test("abortAll permits explicit reopen of the same immutable reference", async () => {
    const container = host(); let calls = 0; const refs = [];
    const root = api.mount(container, {traceId, reference, load: options => {
      refs.push(copy(options.reference)); return ++calls === 1 ? new Promise(() => {}) : Promise.resolve(details);
    }});
    root.open = true; root.dispatch("toggle"); await tick(); api.abortAll();
    root.open = false; await root.dispatch("toggle"); root.open = true; await root.dispatch("toggle");
    assert.equal(calls, 2); assert.deepEqual(refs[0], refs[1]); api.dispose(container);
  });
  await test("CSS is scoped and supports native disclosure, keyboard focus and narrow overflow", () => {
    const css = fs.readFileSync(path.join(__dirname, "../src/web/static/css/result_details.css"), "utf8");
    assert(css.includes(".home-result-details")); assert(css.includes(":focus-visible"));
    assert(/overflow-x:\s*auto/.test(css)); assert(!/url\(|@import/.test(css));
    for (const selector of css.replace(/\/\*[\s\S]*?\*\//g, "").split("}")) {
      const head = selector.split("{")[0].trim(); if (!head) continue;
      assert(head.split(",").every(s => s.trim().startsWith(".home-result-details")), head);
    }
  });
  await test("moving a live disclosure to a different connected message cancels its original read", async () => {
    const first = host(), foreign = host(); let signal;
    const root = api.mount(first, {traceId, reference, load: options => {
      signal = options.signal; return new Promise(() => {});
    }});
    root.open = true; root.dispatch("toggle"); await tick();
    foreign.appendChild(root); mutations();
    assert(signal.aborted); assert.equal(foreign.textContent, ""); api.dispose(first);
  });
  await test("unknown structured tool names are rendered as data without key coercion", async () => {
    const value = copy(details);
    value.payload.tool_result_sequence[0].tool_name = {toString: "unknown-structure"};
    const container = host(); api.mount(container, {traceId, reference, load: async () => value});
    await open(container); await showEverything(container);
    assert(container.textContent.includes("unknown-structure")); assert(container.textContent.includes("46.07"));
    api.dispose(container);
  });
  await test("long unknown field names also paginate completely", async () => {
    const value = copy(details), key = "键".repeat(12000);
    value.payload.error = {[key]: "field-value"};
    const container = host(); api.mount(container, {traceId, reference, load: async () => value});
    await open(container); await expandAll(container);
    assert(!container.textContent.includes(key), "keys must not cause a giant synchronous text node");
    await showEverything(container); assert(container.textContent.includes(key));
    assert(container.textContent.includes("field-value")); api.dispose(container);
  });
  await test("empty stream chunks fail closed rather than consuming unbounded work", async () => {
    let reads = 0, cancelled = false;
    await assert.rejects(api.read({traceId, reference, fetcher: async () => ({ok: true,
      body: {getReader: () => ({async read() {reads++;
        return reads < 10 ? {done: false, value: new Uint8Array(0)} : {done: true};},
        async cancel() {cancelled = true;}, releaseLock() {},})}})}), code("invalid"));
    assert.equal(reads, 1); assert(cancelled);
  });
  await test("default loader accepts only the exact same-origin final URL", async () => {
    window.location = {origin: "http://127.0.0.1:6001"}; let calls = 0;
    window.fetch = async (url, options) => {
      calls++; assert.equal(options.redirect, "error");
      return {...response({success: true, data: details}), url: window.location.origin + url};
    };
    const container = host(); api.mount(container, {traceId, reference}); await open(container);
    await showEverything(container); assert.equal(calls, 1); assert(container.textContent.includes("46.07"));
    await assert.rejects(api.read({traceId, reference, fetcher: async () => ({...response({success: true, data: details}),
      url: window.location.origin + "/api/other"})}), code("invalid"));
    api.dispose(container); delete window.fetch; delete window.location;
  });
  await test("nested schema-like keys remain ordinary data and scalar tool rows retain business labels", async () => {
    const value = copy(details);
    value.payload.tool_result_sequence = [{tool_name: "activity_predictor", status: "failed"}];
    value.payload.error = {tool_result_sequence: [null, "unknown-item", {value: 3}], status: {nested: "unknown-status"}};
    const container = host(); api.mount(container, {traceId, reference, load: async () => value});
    await open(container); await showEverything(container);
    for (const text of ["活性预测", "失败", "unknown-item", "unknown-status"]) assert(container.textContent.includes(text), text);
    api.dispose(container);
  });
  console.log(`${passed} result detail contracts passed`);
}
function response(value) {
  return byteResponse(new TextEncoder().encode(JSON.stringify(value)));
}
function byteResponse(bytes) {
  let finished = false;
  return {ok: true, headers: {get: () => null}, body: {getReader: () => ({
    async read() {if (finished) return {done: true}; finished = true; return {done: false, value: bytes};},
    async cancel() {}, releaseLock() {},
  })}};
}
run().catch(error => {console.error(error.stack); process.exitCode = 1;});
