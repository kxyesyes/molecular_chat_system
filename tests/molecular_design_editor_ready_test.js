"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function setup(initialEditor, initialState = {}) {
  let editor = initialEditor;
  let sequence = 0;
  const timers = new Map();
  const intervals = new Map();
  const listeners = {};
  const toasts = [];
  let reads = 0;
  const status = { textContent: "" };
  const frame = { addEventListener: (name, fn) => { listeners[name] = fn; } };
  const state = { ketcherReady: false, smiles: "", ...initialState };
  const context = vm.createContext({
    console, window: {},
    document: {
      addEventListener() {},
      getElementById(id) { return id === "ketcher-frame" ? frame : status; },
    },
    DesignState: state,
    DesignUI: { toast: (text, type) => toasts.push({ text, type }), setFeedback() {} },
    DesignApi: {},
    FragmentBrowser: { renderCommon() {}, async loadFrags() {} },
    MoleculeEditor: { getK: () => editor, async getSMILES() { reads++; return ""; } },
    PropertiesPanel: {}, HistoryManager: {},
    setTimeout(fn) { timers.set(++sequence, fn); return sequence; },
    clearTimeout(id) { timers.delete(id); },
    setInterval(fn) { intervals.set(++sequence, fn); return sequence; },
    clearInterval(id) { intervals.delete(id); },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../src/web/static/js/design/main.js"), "utf8"), context);
  return {
    state, status, toasts, intervals, timers, listeners,
    get reads() { return reads; },
    setEditor(value) { editor = value; },
    async tick() {
      const current = [...timers.values()];
      timers.clear();
      await Promise.all(current.map(fn => fn()));
    },
  };
}

function readyEditor() { return { getSmiles() {}, setMolecule() {} }; }

test("polling never starts a conversion during an owned editor write", async () => {
  const app = setup(readyEditor(), { editorWriteInFlight: true });
  await Promise.resolve();
  assert.equal(app.reads, 0);
  app.state.editorWriteInFlight = false;
  await Promise.all([...app.intervals.values()].map(fn => fn()));
  assert.equal(app.reads, 1);
});

test("blocked iframe load never reports ready and times out visibly", async () => {
  const app = setup(null);
  app.listeners.load(); // Browsers emit load even when frame policy blocks the document.
  for (let i = 0; i < 250; i++) await app.tick();
  assert.equal(app.state.ketcherReady, false);
  assert.equal(app.intervals.size, 0);
  assert.equal(app.timers.size, 0, "readiness polling must be bounded");
  assert.equal(app.toasts.some(item => item.type === "success"), false);
  assert.match(app.status.textContent, /未能加载|加载失败/);
});

test("waits for the actual editor API instead of a fixed load delay", async () => {
  const app = setup({});
  app.listeners.load();
  await app.tick();
  assert.equal(app.state.ketcherReady, false);
  app.setEditor(readyEditor());
  await app.tick();
  assert.equal(app.state.ketcherReady, true);
  assert.equal(app.intervals.size, 1);
  assert.match(app.status.textContent, /就绪/);
});

test("already initialized cached iframe is detected without another load event", () => {
  const app = setup(readyEditor());
  assert.equal(app.state.ketcherReady, true);
  assert.equal(app.intervals.size, 1);
});

test("iframe reload stops old polling and can recover after a failed load", async () => {
  const app = setup(readyEditor());
  app.listeners.load();
  await app.tick();
  app.setEditor(null);
  app.listeners.load();
  assert.equal(app.state.ketcherReady, false);
  assert.equal(app.intervals.size, 0);
  for (let i = 0; i < 250; i++) await app.tick();
  app.setEditor(readyEditor());
  app.listeners.load();
  await app.tick();
  assert.equal(app.state.ketcherReady, true);
  assert.equal(app.intervals.size, 1);
});
