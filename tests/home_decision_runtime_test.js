"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const crypto = require("crypto");
const root = path.join(__dirname, "..");
const home = path.join(root, "src/web/static/js/home");

// Small DOM transport, not a second implementation of the home event handler.
class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase(); this.children = []; this.attributes = {};
    this.style = {}; this.listeners = {}; this.className = ""; this.value = "";
    this.disabled = false; this.hidden = false; this._text = "";
    this.classList = {
      contains: c => this.className.split(/\s+/).includes(c),
      add: (...cs) => {this.className = [...new Set([...this.className.split(/\s+/).filter(Boolean), ...cs])].join(" ");},
      remove: c => {this.className = this.className.split(/\s+/).filter(x => x !== c).join(" ");},
    };
  }
  appendChild(child) {child.remove(); this.children.push(child); child.parentNode = this; return child;}
  removeChild(child) {this.children = this.children.filter(c => c !== child); child.parentNode = null;}
  remove() {this.parentNode?.removeChild(this);}
  replaceChildren(...children) {this.children.slice().forEach(c => c.remove()); children.forEach(c => this.appendChild(c));}
  setAttribute(k, v) {this.attributes[k] = String(v); if (k === "class") this.className = String(v);}
  getAttribute(k) {return k === "class" ? this.className : this.attributes[k] ?? null;}
  addEventListener(k, fn) {(this.listeners[k] ||= []).push(fn);}
  click() {if (!this.disabled) (this.listeners.click || []).forEach(fn => fn({target: this, preventDefault() {}}));}
  focus() {}
  get isConnected() {return this.root === true || Boolean(this.parentNode?.isConnected);}
  get lastChild() {return this.children.at(-1);}
  contains(c) {return c === this || this.children.some(x => x.contains(c));}
  get textContent() {return this._text + this.children.map(c => c.textContent).join("");}
  set textContent(s) {this.replaceChildren(); this._text = String(s);}
  set innerHTML(s) {this.replaceChildren(); this._text = String(s);}
  get innerHTML() {return this._text;}
  matches(selector) {
    const last = selector.endsWith(":last-child");
    if (last && this.parentNode?.lastChild !== this) return false;
    selector = selector.replace(":last-child", "");
    if (selector.startsWith(".")) return selector.slice(1).split(".").every(c => this.classList.contains(c));
    if (selector.startsWith("#")) return this.id === selector.slice(1);
    const attr = selector.match(/^\[([^=]+)="([^"]+)"\]$/);
    if (attr) return this.getAttribute(attr[1]) === attr[2];
    return this.tagName.toLowerCase() === selector;
  }
  querySelectorAll(selector) {
    const parts = selector.trim().split(/\s+/);
    return this.children.flatMap(c => [c, ...c.descendants()]).filter(c => {
      if (!c.matches(parts.at(-1))) return false;
      let parent = c.parentNode;
      for (let i = parts.length - 2; i >= 0; i--) {
        while (parent && !parent.matches(parts[i])) parent = parent.parentNode;
        if (!parent) return false;
        parent = parent.parentNode;
      }
      return true;
    });
  }
  descendants() {return this.children.flatMap(c => [c, ...c.descendants()]);}
  querySelector(selector) {return this.querySelectorAll(selector)[0] || null;}
  closest(selector) {return this.matches(selector) ? this : this.parentNode?.closest(selector) || null;}
}

function loadHome({request, storedPointer = null} = {}) {
  const body = new Element("body"); body.root = true;
  const input = body.appendChild(new Element("input")), send = body.appendChild(new Element("button"));
  const chat = body.appendChild(new Element("div"));
  const timers = new Map(), sockets = [], logs = [], requests = [];
  let timer = 0, stored = storedPointer;
  class Socket {
    static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
    constructor(url) {this.url = url; this.readyState = 0; this.sent = []; sockets.push(this);}
    send(raw) {assert.equal(this.readyState, 1); this.sent.push(JSON.parse(raw));}
    open() {this.readyState = 1; this.onopen();}
    emit(message) {this.onmessage({data: typeof message === "string" ? message : JSON.stringify(message)});}
    close(code = 1000, reason = "") {this.readyState = 3; this.onclose?.({code, reason, wasClean: true});}
  }
  const document = {readyState: "loading", addEventListener() {}, createElement: tag => new Element(tag),
    querySelector: s => body.querySelector(s), querySelectorAll: s => body.querySelectorAll(s),
    getElementById: id => body.querySelector("#" + id)};
  const renderer = {scrollToBottom() {}, getCurrentTime: () => "12:00", updateConnectionStatus() {}, showNotification() {}};
  const window = {location: {host: "example.invalid", protocol: "http:"}, addEventListener() {},
    sessionStorage: {getItem: () => stored, setItem: (_, s) => {stored = s;}, removeItem: () => {stored = null;}},
    HomeChatRenderer: renderer};
  const context = vm.createContext({window, document, WebSocket: Socket, console: Object.fromEntries(
    ["log", "warn", "error"].map(k => [k, (...args) => logs.push([k, ...args])])),
    HomeChatRenderer: renderer, HomeState: {}, HomeFormatters: {formatContent: s => String(s)},
    HomeAdvancedOptions: {getConfig: () => ({ragCount: 3, temperature: 0.7, molCount: 1})},
    setTimeout: (fn, ms) => {timers.set(++timer, {fn, ms}); return timer;}, clearTimeout: id => timers.delete(id),
    fetch: async (url, options) => {requests.push([url, JSON.parse(options.body)]);
      return {ok: true, json: async () => vm.runInContext("JSON.parse", context)(JSON.stringify({success: true,
        data: request ? await request(url, JSON.parse(options.body)) : {confirmed: true}}))};},
  });
  for (const file of ["molecule_candidates.js", "scientific_references.js", "evidence_report.js"]) {
    vm.runInContext(fs.readFileSync(path.join(home, file), "utf8"), context, {filename: file});
  }
  let source = fs.readFileSync(path.join(home, "main.js"), "utf8");
  // Expose lexical entry points only in this VM; run the complete real script.
  source = source.replace(/\}\)\(\);\s*$/, `window.testHome = {connectWebSocket, sendMessage, elements, scientificReferences,
    prepare() {chatMode = true; elements.input = window.testInput; elements.sendBtn = window.testSend;
      elements.chatContainer = window.testChat;}};})();`);
  Object.assign(window, {testInput: input, testSend: send, testChat: chat});
  vm.runInContext(source, context, {filename: "main.js"});
  window.testHome.prepare(); window.testHome.connectWebSocket(); sockets[0].open();
  return {body, chat, input, logs, requests, timers, sockets, socket: sockets[0], refs: window.testHome.scientificReferences,
    send: () => window.testHome.sendMessage(), reconnect: () => window.testHome.connectWebSocket(),
    button: name => body.querySelectorAll("button").find(b => b.textContent === name),
    ready: mode => sockets.at(-1).emit({type: "connection_ready", normal_chat_mode: mode,
      capabilities: {cancel: true, rag_retrieval: false, scientific_tools: ["property_calculator"]}}),
    flushTimers: () => {const pending = [...timers.values()]; timers.clear(); pending.forEach(t => t.fn());},
  };
}
const turn = "1".repeat(32), trace = "2".repeat(32), nonce = "3".repeat(32);
const accepted = (t = turn, tr = trace) => ({type: "request_accepted", turn_id: t, trace_id: tr});
const result = (status, t = turn, tr = trace) => ({type: "agent_result", turn_id: t, trace_id: tr,
  status, final_answer: "Measured value 0; unavailable is not zero.", message: "server result", warnings: [],
  metadata: {rag_requested: true, retrieval_performed: false,
    ...(status === "waiting_for_input" ? {waiting_for_input: true, continuation_id: nonce} : {})}});
const complete = (status, t = turn, tr = trace) => ({type: "complete", turn_id: t, trace_id: tr,
  status, content: "Measured value 0; unavailable is not zero.",
  continuation_id: status === "waiting_for_input" ? nonce : null});
function start(h, t = turn, tr = trace) {h.input.value = "Explain logP"; h.send(); h.socket.emit(accepted(t, tr));}
function candidateEvent(tr = trace) {
  const candidates = ["CCO", "CCN"].map((smiles, i) => ({
    candidate_id: `cand-${String(i + 1).padStart(3, "0")}-${crypto.createHash("sha256").update(smiles).digest("hex").slice(0, 8)}`,
    source_index: i + 1, smiles, original_smiles: smiles, canonical_smiles: smiles,
    validation: {valid: true, method: "RDKit"}, generation_provenance: {}, metadata: {}}));
  return {type: "molecule_candidates", trace_id: tr,
    source: {tool_name: "llm_molecular_generator", model_name: "synthetic historical fixture only", status: "partial"},
    candidate_set: {version: "1", requested_count: 3, valid_count: 2, unique_count: 2,
      invalid_count: 0, duplicate_count: 0, candidates, rejected: [], status: "partial"},
    warnings: ["synthetic historical candidates; no live generation"],
    reference: {trace_id: tr, presentation_id: "11111111-1111-4111-8111-111111111111", revision: "a".repeat(64),
      ordered_keys: candidates.map(c => ["observation", c.candidate_id])}};
}
const tick = () => new Promise(resolve => setImmediate(resolve));
let passed = 0, failed = 0;
async function test(name, fn) {
  try {await fn(); passed++; console.log("PASS " + name);}
  catch (error) {failed++; console.error("FAIL " + name, error.stack);}
}
async function run() {
  await test("legacy announcement has no decision controls and keeps legacy payload", () => {
    const h = loadHome(); h.ready("legacy");
    assert(!h.button("停止") && !h.button("继续") && !h.button("开始新请求"));
    h.input.value = "legacy input"; h.send();
    assert.equal(h.socket.sent.at(-1).client_id, "web_client");
  });
  for (const [previous, mode] of [[null, "decision_a2"], [null, "legacy"], ["legacy", "decision_a2"]]) {
    await test(`${previous || "first connection"} to ${mode} waits for server ready without replay`, () => {
      const h = loadHome();
      if (previous) {
        h.ready(previous); h.socket.close(); h.reconnect(); h.sockets.at(-1).open();
      }
      const socket = h.sockets.at(-1);
      const sendButton = h.body.children.find(c => c.tagName === "BUTTON");
      h.input.value = "Explain logP"; h.send();
      assert(socket.sent.every(f => f.type === "ping"), "unknown connection mode must not dispatch legacy input");
      assert.equal(h.input.value, "Explain logP", "unsent input remains available");
      assert(sendButton.disabled, "native send is disabled until the announcement");
      assert(!h.chat.querySelector(".typing-indicator-wrapper"));
      if (previous) {
        h.socket.emit({type: "connection_ready", normal_chat_mode: "legacy"});
        assert(sendButton.disabled, "old socket ready cannot enable the new connection");
      }
      h.ready(mode);
      assert(socket.sent.every(f => f.type === "ping"), "ready does not automatically replay input");
      assert.equal(h.input.value, "Explain logP"); assert(!sendButton.disabled);
      h.send();
      assert.equal(socket.sent.filter(f => f.type !== "ping").length, 1);
      if (mode === "legacy") {
        assert.equal(socket.sent.at(-1).client_id, "web_client");
        assert(!h.button("停止"), "legacy ready retains its existing UI");
      } else {
        socket.emit(accepted()); assert(!h.button("停止").disabled);
        socket.emit({...result("completed"), final_answer: "READY_RESPONSE_MUST_BE_VISIBLE"});
        socket.emit(complete("completed"));
        assert(h.chat.textContent.includes("READY_RESPONSE_MUST_BE_VISIBLE"));
        assert.equal(h.chat.querySelectorAll(".decision-runtime-status").length, 1);
        assert(!h.chat.querySelector(".typing-indicator-wrapper"));
      }
    });
  }
  await test("only server announcement enables controls; accepted turn owns Stop", () => {
    const h = loadHome(); h.ready("decision_a2");
    const stop = h.button("停止"); assert(stop, "missing accessible Stop button");
    assert(stop.disabled);
    h.input.value = "Explain logP"; h.send();
    assert(stop.disabled, "pending acceptance has no fabricated turn id");
    const sent = h.socket.sent.length; h.input.value = "second concurrent request";
    h.send(); assert.equal(h.socket.sent.length, sent);
    h.socket.emit(accepted()); assert(!stop.disabled); stop.click();
    assert.deepEqual(h.socket.sent.at(-1), {type: "cancel", turn_id: turn});
    stop.click(); assert.equal(h.socket.sent.filter(f => f.type === "cancel").length, 1);
  });
  for (const [status, label] of Object.entries({completed: "已完成", waiting_for_input: "等待补充输入",
    partial: "部分完成", failed: "失败", rejected: "已拒绝", cancelled: "已取消"})) {
    await test(`persistent ${status} survives complete, timers and stale frames`, () => {
      const h = loadHome(); h.ready("decision_a2"); start(h);
      h.socket.emit(result(status)); h.socket.emit(complete(status));
      const state = h.chat.querySelector('.decision-runtime-status');
      assert(state, "missing per-message persistent status");
      assert.equal(state.getAttribute("role"), "status");
      assert(state.textContent.includes(label) && state.textContent.includes("未执行检索"));
      const before = state.textContent;
      h.flushTimers(); h.socket.emit(result("completed")); h.socket.emit(complete("completed"));
      assert.equal(state.textContent, before);
      assert(state.isConnected && !h.chat.querySelector(".typing-indicator-wrapper"));
      if (status !== "waiting_for_input") {
        start(h, "4".repeat(32), "5".repeat(32));
        h.socket.emit(result("failed", turn, trace));
        assert.equal(state.textContent, before, "next turn cannot overwrite the previous status");
      }
    });
  }
  await test("Continue has exact socket-local nonce and full input; Start new abandons", () => {
    const h = loadHome(); h.ready("decision_a2"); start(h);
    h.socket.emit(result("waiting_for_input")); h.socket.emit(complete("waiting_for_input"));
    const next = h.button("继续"), fresh = h.button("开始新请求");
    assert(next && fresh && !next.disabled && !fresh.disabled);
    h.input.value = "计算 logP；SMILES: CCO\n完整澄清文本"; next.click();
    assert.deepEqual(h.socket.sent.at(-1), {type: "resume", trace_id: trace, continuation_id: nonce,
      message: "计算 logP；SMILES: CCO\n完整澄清文本"});
    const t2 = "4".repeat(32); h.socket.emit(accepted(t2));
    h.socket.emit(result("waiting_for_input", t2)); h.socket.emit(complete("waiting_for_input", t2));
    fresh.click(); assert.deepEqual(h.socket.sent.at(-1), {type: "abandon", trace_id: trace, continuation_id: nonce});
    h.socket.emit({type: "continuation_abandoned", trace_id: trace});
    h.input.value = "Explain logP"; h.send(); assert.equal(h.socket.sent.at(-1).type, "chat");
  });
  for (const response of ["success", "expired"]) await test(`abandon awaits ${response} before an explicit new chat`, () => {
    const h = loadHome(); h.ready("decision_a2"); start(h);
    h.socket.emit(result("waiting_for_input")); h.socket.emit(complete("waiting_for_input"));
    h.button("开始新请求").click();
    assert.deepEqual(h.socket.sent.at(-1), {type: "abandon", trace_id: trace, continuation_id: nonce});
    const sent = h.socket.sent.length;
    h.input.value = "Explain logP"; h.send();
    assert.equal(h.socket.sent.length, sent, "nonempty new input must not race abandon response");
    assert(h.button("继续").disabled && h.button("开始新请求").disabled);
    assert(h.body.children.find(c => c.tagName === "BUTTON").disabled);
    h.button("继续").click(); h.button("开始新请求").click();
    assert.equal(h.socket.sent.length, sent, "no repeat abandon or queued resume");
    h.socket.emit({type: "continuation_abandoned", trace_id: "9".repeat(32)});
    h.send(); assert.equal(h.socket.sent.length, sent, "wrong trace cannot resolve abandon");
    h.socket.emit(response === "expired" ? {type: "error", code: "continuation_unavailable"}
      : {type: "continuation_abandoned", trace_id: trace});
    assert.equal(h.socket.sent.length, sent, "response must not automatically send stored input");
    assert.equal(h.input.value, "Explain logP");
    h.send(); assert.equal(h.socket.sent.at(-1).type, "chat");
    const t2 = "4".repeat(32), tr2 = "5".repeat(32);
    h.socket.emit(accepted(t2, tr2)); assert(!h.button("停止").disabled);
    h.socket.emit(result("completed", t2, tr2)); h.socket.emit(complete("completed", t2, tr2));
    assert(h.chat.querySelectorAll(".decision-runtime-status").at(-1).textContent.includes("已完成"));
  });
  for (const code of ["invalid_frame", "turn_in_progress"]) await test(`abandon ${code} retains waiting authority for an explicit retry`, () => {
    const h = loadHome(); h.ready("decision_a2"); start(h);
    h.socket.emit(result("waiting_for_input")); h.socket.emit(complete("waiting_for_input"));
    h.button("开始新请求").click();
    h.socket.emit({type: "error", code});
    assert(!h.button("开始新请求").disabled && !h.button("继续").disabled);
    h.input.value = "must not become fresh chat"; const count = h.socket.sent.length;
    h.send(); assert.equal(h.socket.sent.length, count);
    h.button("开始新请求").click();
    assert.deepEqual(h.socket.sent.at(-1), {type: "abandon", trace_id: trace, continuation_id: nonce});
    assert.equal(h.socket.sent.length, count + 1);
  });
  for (const action of ["duplicate-abandon", "resume-before-reply"]) await test(`late cancel error cannot permit ${action}`, () => {
    const h = loadHome(); h.ready("decision_a2"); start(h);
    h.button("停止").click(); assert.equal(h.socket.sent.at(-1).type, "cancel");
    h.socket.emit(result("waiting_for_input")); h.socket.emit(complete("waiting_for_input"));
    h.button("开始新请求").click(); assert.equal(h.socket.sent.at(-1).type, "abandon");
    const sent = h.socket.sent.length;
    // The previous cancel's error is not a reply to this pending abandon.
    h.socket.emit({type: "error", code: "invalid_control"});
    h.input.value = "计算 logP；SMILES: CCO";
    h.button(action === "duplicate-abandon" ? "开始新请求" : "继续").click();
    assert.equal(h.socket.sent.length, sent, "late cancel must not release outstanding abandon");
    assert(h.button("开始新请求").disabled && h.button("继续").disabled);
    h.send(); assert.equal(h.socket.sent.length, sent, "fresh nonempty input remains blocked");
    h.socket.emit(action === "duplicate-abandon"
      ? {type: "continuation_abandoned", trace_id: trace}
      : {type: "error", code: "continuation_unavailable"});
    assert.equal(h.socket.sent.length, sent, "real abandon reply never automatically resends");
    h.send(); assert.equal(h.socket.sent.at(-1).type, "chat");
    const t2 = "4".repeat(32), tr2 = "5".repeat(32);
    h.socket.emit(accepted(t2, tr2)); assert(!h.button("停止").disabled);
    h.socket.emit(result("completed", t2, tr2)); h.socket.emit(complete("completed", t2, tr2));
    assert(h.chat.querySelectorAll(".decision-runtime-status").at(-1).textContent.includes("已完成"));
  });
  for (const resumed of [false, true]) await test(`late cancel invalid_control preserves pending ${resumed ? "resume" : "chat"}`, () => {
    const h = loadHome(); h.ready("decision_a2"); start(h);
    h.button("停止").click(); assert.equal(h.socket.sent.at(-1).type, "cancel");
    const priorStatus = resumed ? "waiting_for_input" : "completed";
    h.socket.emit(result(priorStatus)); h.socket.emit(complete(priorStatus));
    const prior = h.chat.querySelector(".decision-runtime-status").textContent;
    h.input.value = resumed ? "计算 logP；SMILES: CCO" : "解释分子生成的概念";
    if (resumed) h.button("继续").click(); else h.send();
    assert.equal(h.socket.sent.at(-1).type, resumed ? "resume" : "chat");
    const sent = h.socket.sent.length;
    // The old turn completed before the server received its cancel. Its
    // uncorrelated error can arrive before acceptance of the next request.
    h.socket.emit({type: "error", code: "invalid_control"});
    assert.equal(h.chat.querySelector(".decision-runtime-status").textContent, prior);
    const t2 = "4".repeat(32), tr2 = resumed ? trace : "5".repeat(32);
    h.socket.emit(accepted(t2, tr2));
    assert(!h.button("停止").disabled, "late prior cancel error must not discard new acceptance");
    h.socket.emit({...result("completed", t2, tr2), final_answer: "SECOND_TURN_DISPLAY_MARKER"});
    h.socket.emit(complete("completed", t2, tr2));
    assert(h.chat.textContent.includes("SECOND_TURN_DISPLAY_MARKER"));
    assert.equal(h.socket.sent.length, sent, "no synthetic cancel ACK, queue or replay");
    assert.equal(h.chat.querySelectorAll(".decision-runtime-status").length, 2);
    assert.equal(h.socket.readyState, 1);
  });
  for (const code of ["invalid_frame", "turn_in_progress", "continuation_unavailable"]) {
    await test(`current pending rejection ${code} releases UI without scientific success`, () => {
      const h = loadHome(); h.ready("decision_a2");
      const resumed = code === "continuation_unavailable";
      if (resumed) {
        start(h); h.socket.emit(result("waiting_for_input")); h.socket.emit(complete("waiting_for_input"));
      }
      h.input.value = "计算 logP；SMILES: CCO";
      if (resumed) h.button("继续").click(); else h.send();
      const sent = h.socket.sent.length;
      h.socket.emit({type: "error", code});
      assert(h.chat.querySelectorAll(".decision-runtime-status").at(-1).textContent.includes("请求未被接受"));
      assert(!h.chat.querySelector(".typing-indicator-wrapper"));
      assert(h.button("停止").disabled && h.button("继续").disabled);
      assert.equal(h.socket.sent.length, sent);
      h.input.value = "Explain logP"; h.send(); assert.equal(h.socket.sent.length, sent + 1);
      assert.equal(h.socket.sent.at(-1).type, "chat");
      const t2 = "4".repeat(32), tr2 = "5".repeat(32);
      h.socket.emit(accepted(t2, tr2)); assert(!h.button("停止").disabled);
      h.socket.emit(result("completed", t2, tr2)); h.socket.emit(complete("completed", t2, tr2));
      assert(h.chat.querySelectorAll(".decision-runtime-status").at(-1).textContent.includes("已完成"));
    });
  }
  await test("disconnect clears pending abandon; old socket responses cannot release a new handle", () => {
    const h = loadHome(); h.ready("decision_a2"); start(h);
    h.socket.emit(result("waiting_for_input")); h.socket.emit(complete("waiting_for_input"));
    h.button("开始新请求").click(); assert(h.button("开始新请求").disabled);
    h.socket.close(); h.reconnect(); const newer = h.sockets.at(-1); newer.open(); h.ready("decision_a2");
    assert(h.button("继续").disabled && h.button("开始新请求").disabled);
    assert(newer.sent.every(f => f.type === "ping"));
    h.input.value = "Explain logP"; h.send();
    const t2 = "4".repeat(32), tr2 = "5".repeat(32);
    newer.emit(accepted(t2, tr2)); newer.emit(result("waiting_for_input", t2, tr2));
    newer.emit(complete("waiting_for_input", t2, tr2)); h.button("开始新请求").click();
    const sent = newer.sent.length;
    h.socket.emit({type: "continuation_abandoned", trace_id: tr2});
    h.socket.emit({type: "error", code: "continuation_unavailable"});
    h.input.value = "new socket explicit input"; h.send();
    assert.equal(newer.sent.length, sent); assert(h.button("开始新请求").disabled);
    newer.emit({type: "continuation_abandoned", trace_id: tr2});
    assert.equal(newer.sent.length, sent); h.send(); assert.equal(newer.sent.at(-1).type, "chat");
  });
  await test("disconnect clears continuation and stale socket cannot restore it; no replay", () => {
    const h = loadHome(); h.ready("decision_a2"); start(h);
    h.socket.emit(result("waiting_for_input")); h.socket.emit(complete("waiting_for_input"));
    h.socket.close(); h.reconnect(); const newer = h.sockets.at(-1); newer.open(); h.ready("decision_a2");
    h.socket.emit(accepted()); h.socket.emit(result("waiting_for_input"));
    assert(h.button("继续").disabled && h.button("停止").disabled);
    assert(newer.sent.every(f => f.type === "ping"));
  });
  await test("transport logging never captures user text or inbound raw frames", () => {
    const h = loadHome(); h.ready("decision_a2");
    h.input.value = "PRIVATE_OUTBOUND_MARKER"; h.send(); h.socket.emit(accepted());
    h.socket.emit({...result("completed"), final_answer: "PRIVATE_INBOUND_MARKER"});
    h.socket.emit('{"PRIVATE_MALFORMED_MARKER":');
    assert(!JSON.stringify(h.logs).match(/PRIVATE_(OUTBOUND|INBOUND|MALFORMED)_MARKER/));
  });
  await test("known decision socket cannot fall back to legacy during reconnect before ready", () => {
    const h = loadHome(); h.ready("decision_a2"); h.socket.close(); h.reconnect();
    const newer = h.sockets.at(-1); newer.open();
    h.input.value = "pending server mode"; h.send();
    assert(newer.sent.every(f => f.type === "ping"));
    h.ready("decision_a2"); h.send(); assert.equal(newer.sent.at(-1).type, "chat");
  });
  await test("active main script cache version points to Task8 implementation", () => {
    const template = fs.readFileSync(path.join(root, "src/web/templates/index.html"), "utf8");
    const scripts = [...template.matchAll(/<script\s+src="([^\"]*\/home\/main\.js[^\"]*)"/g)].map(m => m[1]);
    assert.deepEqual(scripts, ["/static/js/home/main.js?v=20260925-decision-runtime-v1"]);
  });
  await test("strict candidate trace is socket-bound and receive is not mounted ACK", async () => {
    const h = loadHome(); h.ready("decision_a2"); start(h);
    h.socket.emit(result("partial"));
    h.socket.emit(candidateEvent("9".repeat(32)));
    const event = candidateEvent(); h.socket.emit(event);
    assert.equal(h.requests.length, 0, "receiving candidates must not confirm");
    assert.equal(h.chat.querySelectorAll(".molecule-card").length, 0);
    h.socket.emit(complete("partial")); await tick();
    assert.deepEqual(h.requests.map(r => r[1]), [event.reference]);
    assert(h.chat.textContent.includes("synthetic historical candidates"));
    assert(h.chat.textContent.includes("CCO") && h.chat.textContent.includes("CCN"));
    h.button("选择此分子").click();
    h.input.value = "计算 logP"; h.send();
    assert.equal(h.socket.sent.at(-1).selection.candidate_id, event.reference.ordered_keys[0][1]);
    assert(!("turn_id" in event));
  });
  for (const fault of ["detached", "partial"]) await test(`${fault} candidate mount cannot ACK`, async () => {
    const h = loadHome(); h.ready("decision_a2"); start(h); h.socket.emit(result("partial"));
    const event = candidateEvent(); h.socket.emit(event);
    const append = Element.prototype.appendChild;
    try {
      if (fault === "detached") h.chat.remove();
      else Element.prototype.appendChild = function(child) {
        if (child.getAttribute("data-candidate-id") === event.candidate_set.candidates[1].candidate_id) return child;
        return append.call(this, child);
      };
      h.socket.emit(complete("partial")); await tick();
      assert.equal(h.requests.length, 0);
    } finally {Element.prototype.appendChild = append;}
  });
  for (const boundary of ["next-turn", "disconnect"]) await test(`slow ACK cannot authorize after ${boundary}`, async () => {
    let resolveAck;
    const h = loadHome({request: () => new Promise(resolve => {resolveAck = resolve;})});
    h.ready("decision_a2"); start(h); h.socket.emit(result("partial")); h.socket.emit(candidateEvent());
    h.socket.emit(complete("partial")); await tick();
    assert.equal(typeof resolveAck, "function");
    if (boundary === "next-turn") {h.input.value = "Explain logP"; h.send();}
    else h.socket.close();
    resolveAck({confirmed: true}); await tick();
    h.button("选择此分子").click();
    assert.equal(JSON.stringify(h.refs.outgoing()), "{}");
  });
  await test("wrong turn/trace and repeated acceptance cannot seize a new turn", () => {
    const h = loadHome(); h.ready("decision_a2"); start(h);
    h.socket.emit(result("completed")); h.socket.emit(complete("completed"));
    h.input.value = "next concept"; h.send(); h.socket.emit(accepted());
    assert(h.button("停止").disabled);
    const freshTurn = "4".repeat(32), freshTrace = "5".repeat(32);
    h.socket.emit(accepted(freshTurn, freshTrace));
    h.socket.emit(result("failed", turn, freshTrace)); h.socket.emit(result("failed", freshTurn, trace));
    h.button("停止").click(); assert.deepEqual(h.socket.sent.at(-1), {type: "cancel", turn_id: freshTurn});
    h.socket.emit(result("partial", freshTurn, freshTrace));
    h.socket.emit(complete("completed", freshTurn, freshTrace));
    assert(h.chat.querySelectorAll(".decision-runtime-status").at(-1).textContent.includes("部分完成"));
  });
  await test("1011 after result preserves science status but never claims delivery complete", () => {
    const h = loadHome(); h.ready("decision_a2"); start(h); h.socket.emit(result("partial"));
    h.socket.close(1011); const state = h.chat.querySelector(".decision-runtime-status");
    assert(state.textContent.includes("部分完成") && state.textContent.includes("传输未确认结束"));
    assert(!state.textContent.includes("已取消"));
    assert(h.button("继续").disabled && h.button("停止").disabled);
  });
  await test("errored decision socket cannot reinterpret late frames as legacy traffic", () => {
    const h = loadHome(); h.ready("decision_a2"); start(h); h.socket.emit(result("partial"));
    h.socket.onerror({type: "error"});
    h.socket.emit({type: "stream", content: "LATE_FRAME_MUST_NOT_RENDER"});
    assert(!h.chat.textContent.includes("LATE_FRAME_MUST_NOT_RENDER"));
    h.input.value = "cannot send before new ready";
    const count = h.socket.sent.length; h.send(); assert.equal(h.socket.sent.length, count);
  });
  console.log(`${passed} passed, ${failed} failed`);
  if (failed) process.exitCode = 1;
}
async function runFrameContracts(frames) {
  // Supplied by the approved isolated Python runner: real RDKit/SQLite report
  // from explicitly synthetic historical generation, NOT A2 generation admission.
  const event = frames.find(f => f.type === "molecule_candidates");
  const report = frames.find(f => f.type === "scientific_report");
  assert(event && report && /^[a-f0-9]{32}$/.test(event.trace_id));
  const original = JSON.stringify(frames);
  for (const mode of ["positive", "absent", "foreign-trace"]) await test(`full main script same-frame report ${mode}`, async () => {
    const h = loadHome(); h.ready("decision_a2"); start(h, turn, event.trace_id);
    h.socket.emit(result("partial", turn, event.trace_id)); h.socket.emit(event);
    if (mode !== "absent") h.socket.emit(mode === "positive" ? report : {...report, trace_id: "9".repeat(32)});
    assert.equal(h.requests.length, 0);
    h.socket.emit(complete("partial", turn, event.trace_id)); await tick();
    assert.deepEqual(h.requests.map(r => r[1]), [event.reference]);
    assert.equal(Boolean(h.chat.querySelector(".scientific-evidence-report")), mode === "positive");
    if (mode === "positive") assert(h.chat.textContent.includes("46.07"), "actual independent RDKit row must render");
    else assert(h.chat.textContent.includes("当前候选事件未携带经独立性质工具验证的属性"));
    assert(!("turn_id" in report) && !("turn_id" in event));
  });
  await test("protected candidate-only restore cannot resurrect live report", async () => {
    const pointer = {trace_id: event.trace_id, presentation_id: event.reference.presentation_id, revision: event.reference.revision};
    const h = loadHome({storedPointer: JSON.stringify(pointer), request: async url => {
      assert(url.endsWith("/restore"));
      return {reference: event.reference, events: [event], source_status: "partial", warnings: [], expires_at: Date.now()/1000 + 60};
    }});
    h.ready("decision_a2"); await tick();
    assert(h.chat.textContent.includes("已恢复此前确认的候选集合"));
    assert(!h.chat.querySelector(".scientific-evidence-report"));
    assert(h.chat.textContent.includes("当前候选事件未携带经独立性质工具验证的属性"));
    assert(!h.chat.textContent.includes("46.07"));
  });
  assert.equal(JSON.stringify(frames), original);
  console.log(`${passed} same-frame passed, ${failed} failed`);
  if (failed) process.exitCode = 1;
}
(process.argv.includes("--frames-stdin") ? runFrameContracts(JSON.parse(fs.readFileSync(0, "utf8"))) : run())
  .catch(error => {console.error(error); process.exitCode = 1;});
