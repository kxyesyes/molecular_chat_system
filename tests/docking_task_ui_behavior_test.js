"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync("src/web/static/js/docking/ui_manager.js", "utf8");

function harness() {
  const nodes = new Map();
  const node = () => ({
    style: {}, disabled: false, textContent: "", innerHTML: "", files: [], value: "",
    children: [], setAttribute() {}, removeAttribute() {}, getAttribute() {},
    appendChild(child) { this.children.push(child); }, replaceChildren(...children) { this.children = children; },
  });
  const get = (id) => { if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id); };
  const timers = new Map();
  const storage = new Map();
  const completedSteps = [];
  let timerId = 0;
  const context = {
    window: { MedChatSafeRender: { escapeHtml: String, escapeInlineJsString: String }, crypto: { randomUUID: () => "request-1" } },
    document: { getElementById: get, querySelector: get, querySelectorAll: () => [], createElement: node },
    sessionStorage: { getItem: (key) => storage.get(key) || null, setItem: (key, value) => storage.set(key, value), removeItem: (key) => storage.delete(key) },
    setTimeout: (fn, ms) => { const id = ++timerId; timers.set(id, { fn, ms }); return id; },
    clearTimeout: (id) => timers.delete(id), AbortController, console,
    AppState: { dockingMode: "single", dockingBoxCenterTouched: true },
    DOCKING_STEPS: ["prepare_protein", "prepare_ligand", "setup_docking", "run_docking", "parse_results"].map((id) => ({ id, title: id })),
    stepManager: { reset() {}, startStep() {}, completeStep: (id) => completedSteps.push(id), updateStepProgress() {}, errorStep() {} },
    Utils: { showToast() {} },
    onStyleChange: () => {},
    alert: (message) => { context.lastAlert = message; },
    fetch: async () => { throw new Error("offline"); },
  };
  vm.createContext(context);
  vm.runInContext(source, context);
  return { context, get, timers, storage, completedSteps };
}
const record = (status = "running") => ({ task_id: "task-1", task_type: "molecular_docking", status, phase: "vina_running", progress: 0.5 });
const success = () => ({ ...record("succeeded"), result: { success: true, data: { total_poses: 3, best_pose: { binding_energy: -8.4 } } }, warnings: [{code: "TOOL_WARNING", message: "预处理警告"}], artifacts: [{ path: "task-1/artifacts/pose.pdbqt", artifact_type: "docking_pose" }] });
const response = (data) => ({ ok: true, status: 200, json: async () => ({ success: true, data }) });
const tests = [];
function test(name, fn) { tests.push([name, fn]); }

test("missing energies and pose counts are never coerced or invented", () => {
  const { context: c } = harness();
  for (const value of [null, "", false, "-8.4", NaN, Infinity, undefined]) {
    const r = success(); r.result.data.best_pose.binding_energy = value;
    assert.equal(c.durableDockingResult(r), null, `energy=${String(value)}`);
  }
  for (const value of [null, 0, -1, 1.5, "3", undefined]) {
    const r = success(); r.result.data.total_poses = value;
    assert.equal(c.durableDockingResult(r), null, `count=${String(value)}`);
  }
});
test("summary never invents pose 1 or a legacy job path", () => {
  const { context: c } = harness();
  const summary = c.durableDockingResult(success());
  assert.equal(summary.binding_energy, -8.4);
  assert.equal(summary.total_poses, 3);
  assert.equal(summary.results, undefined);
  assert.equal(summary.job_id, undefined);
});
test("running is not proof that any preparation step completed", () => {
  const { context: c, completedSteps, get } = harness();
  c.syncDockingStepState({ ...record(), phase: "running" });
  assert.deepEqual(completedSteps, []);
  assert.equal(get("docking-progress").style.width, "50%");
  assert.equal(c.dockingProgressPercent(2), 2);
});
test("failed task cannot show full successful progress", () => {
  const { context: c, get } = harness();
  c.rememberDockingTask("task-1");
  c.applyDockingTaskRecord({ ...record("failed"), progress: 1, error: "Vina failed" });
  assert.notEqual(get("docking-progress").style.width, "100%");
  assert.equal(c.readRememberedDockingTask(), null);
});
test("poll disconnect holds task lock and schedules reconnect", async () => {
  const { context: c, get, timers } = harness();
  c.rememberDockingTask("task-1");
  c.setDockingTaskButtonState(true);
  await c.pollDockingTask("task-1");
  assert.equal(c.readRememberedDockingTask(), "task-1");
  assert.equal(get("start-btn").disabled, true);
  assert.equal(get("cancel-docking-btn").disabled, false);
  assert.equal(timers.size, 1);
});
test("terminal result stops polling and keeps provenance visible", async () => {
  const { context: c, get, timers } = harness();
  c.rememberDockingTask("task-1");
  c.fetch = async () => response(success());
  c.showRealResults = () => { throw new Error("legacy paths must not be guessed"); };
  await c.pollDockingTask("task-1");
  assert.equal(timers.size, 0);
  assert.equal(c.readRememberedDockingTask(), null);
  assert.equal(get("start-btn").disabled, false);
  assert.equal(get("cancel-docking-btn").disabled, true);
  assert.match(get("results-content").innerHTML, /-8\.400/);
  assert.match(get("results-content").innerHTML, /预处理警告/);
  assert.match(get("results-content").innerHTML, /artifacts\/pose\.pdbqt/);
});
test("old in-flight read cannot revive a cancelled task", async () => {
  const { context: c, timers } = harness();
  let resolveGet;
  c.rememberDockingTask("task-1");
  c.fetch = async (url) => url.endsWith("/cancel") ? response(record("canceled")) : new Promise((resolve) => { resolveGet = resolve; });
  const pending = c.pollDockingTask("task-1");
  await c.cancelDockingTask();
  resolveGet(response(record()));
  await pending;
  assert.equal(timers.size, 0);
  assert.equal(c.readRememberedDockingTask(), null);
});
test("single mode refuses multiple files without posting", () => {
  const { context: c, get } = harness();
  get("protein-file").files = [{}]; get("ligand-file").files = [{}, {}];
  c.startDocking();
  assert.match(c.lastAlert, /单配体|一个|单个/);
});

(async () => {
  let failures = 0;
  for (const [name, fn] of tests) {
    try { await fn(); console.log(`PASS ${name}`); }
    catch (error) { failures++; console.error(`FAIL ${name}: ${error.message}`); }
  }
  if (failures) process.exitCode = 1;
})();
