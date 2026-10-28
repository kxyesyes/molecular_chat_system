"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync("src/web/static/js/docking/ui_manager.js", "utf8");

function harness() {
  const nodes = new Map();
  const node = () => ({
    style: {}, disabled: false, textContent: "", innerHTML: "", files: [], value: "",
    children: [], classList: {add() {}, remove() {}, contains() { return false; }}, setAttribute() {}, removeAttribute() {}, getAttribute() {},
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
    AppState: { dockingMode: "single", dockingBoxCenterTouched: true, currentJobId: null },
    setCurrentJobId: (jobId) => { context.AppState.currentJobId = jobId; },
    DOCKING_STEPS: ["prepare_protein", "prepare_ligand", "setup_docking", "run_docking", "parse_results"].map((id) => ({ id, title: id })),
    stepManager: { reset() {}, show() {}, hide() {}, startStep() {}, completeStep: (id) => completedSteps.push(id), updateStepProgress() {}, errorStep() {} },
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
test("durable result uses explicit legacy identity and best pose mapping", () => {
  const { context: c, get } = harness();
  const r = success();
  r.result.data.result_job_id = "result-job-7";
  r.result.data.best_pose.pose = 2;
  const summary = c.durableDockingResult(r);
  assert.equal(summary.result_job_id, "result-job-7");
  assert.equal(summary.best_pose_index, 2);
  c.renderDurableTaskSuccess(r, summary);
  assert.match(get("results-content").innerHTML, /loadComplexStructure\('result-job-7', 2\)/);
  assert.doesNotMatch(get("results-content").innerHTML, /映射为 Pose 1/);
  assert.equal(c.AppState.currentJobId, "result-job-7");
  assert.equal(get("docking-progress").style.width, "100%");
  assert.equal(get("progress-log").children[1].textContent, "完成 · 100%");
  assert.deepEqual(
    get("pose-select").children.map((option) => option.value),
    ["1", "2", "3"],
  );
  assert.equal(get("show-complex-btn").disabled, false);
});
test("durable success renders the verified Pose detail table", async () => {
  const { context: c, get } = harness();
  const r = success();
  r.result.data.result_job_id = "task-1";
  r.result.data.best_pose.pose = 2;
  c.fetch = async (url) => {
    assert.equal(url, "/api/docking/result/task-1/summary");
    return {
      ok: true,
      json: async () => ({
        success: true,
        job_id: "task-1",
        total_poses: 2,
        results: [
          { pose: 1, binding_energy: -8.4, rmsd_lb: 0, rmsd_ub: 0, ligand_efficiency: null },
          { pose: 2, binding_energy: -6.2, rmsd_lb: 0.9, rmsd_ub: 2.7, ligand_efficiency: null },
        ],
      }),
    };
  };
  await c.renderDurableTaskSuccess(r, c.durableDockingResult(r));
  assert.match(get("results-content").innerHTML, /结构编号/);
  assert.match(get("results-content").innerHTML, /2\.7/);
  assert.match(get("results-content").innerHTML, /viewPose\(2\)/);
});
test("durable success loads Pose details with the task id when no result job id is present", async () => {
  const { context: c, get } = harness();
  const r = success();
  r.result.data.best_pose.pose = 1;
  c.fetch = async (url) => {
    assert.equal(url, "/api/docking/result/task-1/summary");
    return {
      ok: true,
      json: async () => ({
        success: true,
        job_id: "task-1",
        total_poses: 1,
        results: [
          { pose: 1, binding_energy: -5.9, rmsd_lb: 0, rmsd_ub: 0, ligand_efficiency: 0.227 },
        ],
      }),
    };
  };
  await c.renderDurableTaskSuccess(r, c.durableDockingResult(r));
  assert.match(get("results-content").innerHTML, /结构编号/);
  assert.match(get("results-content").innerHTML, /0\.227/);
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
test("generic docking failure is actionable without claiming a scientific result", () => {
  const { context: c, get } = harness();
  c.renderDurableTaskFailure({task_id: "task-1", status: "failed", error: "Docking process failed"});
  assert.match(get("results-content").innerHTML, /未生成可验证结果/);
  assert.match(get("results-content").innerHTML, /受体结构准备工具/);
  assert.doesNotMatch(get("results-content").innerHTML, /-\d+\.\d+\s*kcal\/mol/);
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
test("old in-flight read cannot overwrite a newer task", async () => {
  const { context: c, get } = harness();
  let resolveGet;
  c.rememberDockingTask("task-1");
  c.fetch = async () => new Promise((resolve) => { resolveGet = resolve; });
  const pending = c.pollDockingTask("task-1");
  c.rememberDockingTask("task-2");
  c.setDockingTaskButtonState(true);
  get("results-content").innerHTML = "task-2 state";
  resolveGet(response(success()));
  await pending;
  assert.equal(c.readRememberedDockingTask(), "task-2");
  assert.equal(get("results-content").innerHTML, "task-2 state");
  assert.equal(get("start-btn").disabled, true);
});
test("old in-flight cancellation cannot overwrite a newer task", async () => {
  const { context: c, get } = harness();
  let resolveCancel;
  c.rememberDockingTask("task-1");
  c.fetch = async (url) => url.endsWith("/cancel")
    ? new Promise((resolve) => { resolveCancel = resolve; })
    : response(record());
  const pending = c.cancelDockingTask();
  c.rememberDockingTask("task-2");
  c.setDockingTaskButtonState(true);
  get("results-content").innerHTML = "task-2 state";
  resolveCancel(response(record("canceled")));
  await pending;
  assert.equal(c.readRememberedDockingTask(), "task-2");
  assert.equal(get("results-content").innerHTML, "task-2 state");
  assert.equal(get("start-btn").disabled, true);
});
test("old cancellation HTTP errors cannot overwrite a newer task", async () => {
  const { context: c, get } = harness();
  let resolveCancel;
  c.rememberDockingTask("task-1");
  c.fetch = async () => new Promise((resolve) => { resolveCancel = resolve; });
  const pending = c.cancelDockingTask();
  c.rememberDockingTask("task-2");
  c.setDockingTaskButtonState(true);
  get("results-content").innerHTML = "task-2 state";
  resolveCancel({ ok: false, json: async () => ({ success: false, message: "old failure" }) });
  await pending;
  assert.equal(c.readRememberedDockingTask(), "task-2");
  assert.equal(get("results-content").innerHTML, "task-2 state");
  assert.equal(get("start-btn").disabled, true);
});
test("old cancellation network errors cannot overwrite a newer task", async () => {
  const { context: c, get } = harness();
  let rejectCancel;
  c.rememberDockingTask("task-1");
  c.fetch = async () => new Promise((_, reject) => { rejectCancel = reject; });
  const pending = c.cancelDockingTask();
  c.rememberDockingTask("task-2");
  c.setDockingTaskButtonState(true);
  get("results-content").innerHTML = "task-2 state";
  rejectCancel(new Error("old network failure"));
  await pending;
  assert.equal(c.readRememberedDockingTask(), "task-2");
  assert.equal(get("results-content").innerHTML, "task-2 state");
  assert.equal(get("start-btn").disabled, true);
});
test("single mode refuses multiple files without posting", () => {
  const { context: c, get } = harness();
  get("protein-file").files = [{}]; get("ligand-file").files = [{}, {}];
  c.startDocking();
  assert.match(c.lastAlert, /单配体|一个|单个/);
});
test("rerun preserves the existing input and submits a fresh task", () => {
  const { context: c, get } = harness();
  get("protein-file").files = [{}];
  get("smiles-input").value = "CCO";
  c.AppState.currentProteinData = "ATOM      1  C   MOL A   1       0.000   0.000   0.000\n";
  c.AppState.dockingBoxCenterTouched = true;
  c.resetDocking({ preserveInputs: true });
  assert.equal(get("protein-file").files.length, 1);
  assert.equal(get("smiles-input").value, "CCO");
  assert.equal(c.AppState.currentProteinData.startsWith("ATOM"), true);
  assert.equal(typeof c.rerunDocking, "function");
});

test("durable history loads independently of slow legacy history", async () => {
  const { context: c, get } = harness();
  let resolveLegacy;
  c.fetch = async (url) => url === "/api/tasks?task_type=docking&limit=50"
    ? response([{...success(), task_type: "docking"}])
    : new Promise((resolve) => { resolveLegacy = resolve; });
  c.loadDockingHistory();
  await new Promise((resolve) => setImmediate(resolve));
  assert.match(get("durable-history-list").innerHTML, /task-1/);
  assert.match(get("durable-history-list").innerHTML, /恢复结果/);
  resolveLegacy({ok: true, json: async () => ({success: true, history: []})});
});
test("history errors are not presented as an empty history", async () => {
  const { context: c, get } = harness();
  c.fetch = async () => ({ok: false, json: async () => ({success: false, message: "unavailable"})});
  c.loadDockingHistory();
  await new Promise((resolve) => setImmediate(resolve));
  assert.match(get("durable-history-list").innerHTML, /加载失败/);
  assert.match(get("legacy-history-list").innerHTML, /加载失败/);
});
test("history restoration rechecks the owned record and avoids another receptor", async () => {
  const { context: c, get } = harness();
  const r = success(); r.task_type = "docking"; r.result.data.result_job_id = "task-1"; r.result.data.best_pose.pose = 1;
  c.fetch = async (url) => { assert.equal(url, "/api/tasks/task-1"); return response(r); };
  await c.restoreDurableDockingTask("task-1");
  assert.match(get("results-content").innerHTML, /-8\.400/);
  assert.doesNotMatch(get("results-content").innerHTML, /loadComplexStructure/);
  assert.match(get("results-content").innerHTML, /蛋白/);
  assert.doesNotMatch(get("results-content").innerHTML, /重新对接/);
  assert.equal(get("pose-select").disabled, true);
  c.renderDurableTaskSuccess(r, c.durableDockingResult(r));
  assert.equal(get("pose-select").disabled, false);
  assert.equal(get("show-hbonds-btn").disabled, false);
});
test("history restoration cannot overwrite a running task", async () => {
  const { context: c, get } = harness();
  c.rememberDockingTask("task-2");
  get("results-content").innerHTML = "active task";
  c.fetch = async () => { throw new Error("must not fetch"); };
  await c.restoreDurableDockingTask("task-1");
  assert.equal(get("results-content").innerHTML, "active task");
  assert.equal(c.readRememberedDockingTask(), "task-2");
});
test("a history read cannot overwrite a task started and completed meanwhile", async () => {
  const { context: c, get } = harness();
  let finish;
  c.fetch = () => new Promise((resolve) => { finish = resolve; });
  const pending = c.restoreDurableDockingTask("task-1");
  c.startDocking(); // Even an invalid new attempt invalidates the older read.
  get("results-content").innerHTML = "newer state";
  const r = success(); r.task_type = "docking";
  finish(response(r));
  await pending;
  assert.equal(get("results-content").innerHTML, "newer state");
});
test("a resumed running historical task stays isolated from the current receptor on completion", () => {
  const { context: c, get } = harness();
  const r = success(); r.task_type = "docking"; r.result.data.result_job_id = "task-1"; r.result.data.best_pose.pose = 1;
  c.rememberDockingTask("task-1");
  c.markHistoricalDockingTask("task-1");
  c.applyDockingTaskRecord(r);
  assert.doesNotMatch(get("results-content").innerHTML, /loadComplexStructure/);
  assert.match(get("results-content").innerHTML, /蛋白/);
});
test("a resumed historical task with a failed poll unlocks and reports the failure", async () => {
  const { context: c, get } = harness();
  const running = {...record("running"), task_type: "docking"};
  let calls = 0;
  c.fetch = async () => ++calls === 1 ? response(running) : ({ok: false, json: async () => ({success: false, message: "read failed"})});
  await c.restoreDurableDockingTask("task-1");
  assert.equal(get("start-btn").disabled, false);
  assert.match(get("results-content").innerHTML, /read failed/);
});
test("older complex requests cannot paint over a restored history result", async () => {
  const { context: c } = harness();
  let resolvePose;
  let paints = 0;
  c.viewer = {clear() {}, render() {}, addModel() { paints++; return {setStyle() {}}; }, zoomTo() {}};
  c.currentProteinData = "protein A";
  c.fetchDockedPoseSdf = () => new Promise((resolve) => { resolvePose = resolve; });
  c.fetch = async (url) => url === "/api/tasks/task-1" ? response({...success(), task_type: "docking"}) : {ok: true, text: async () => "pdbqt"};
  c.loadComplexStructure("task-A", 1);
  await c.restoreDurableDockingTask("task-1");
  resolvePose("ligand A");
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(paints, 0);
});
test("replacing the submitted protein prevents its task from displaying a mismatched complex", () => {
  const { context: c, get } = harness();
  c.AppState.currentProteinData = "protein A";
  vm.runInContext('submittedDockingProtein = "protein A"', c);
  c.AppState.currentProteinData = "protein B";
  const r = success(); r.result.data.result_job_id = "task-1"; r.result.data.best_pose.pose = 1;
  c.applyDockingTaskRecord(r);
  assert.doesNotMatch(get("results-content").innerHTML, /loadComplexStructure/);
  assert.match(get("results-content").innerHTML, /蛋白/);
});
test("completing a new task invalidates a complex request started during the run", async () => {
  const { context: c } = harness();
  let finishPose; let paints = 0;
  c.viewer = {clear() {}, render() {}, addModel() { paints++; return {setStyle() {}}; }, zoomTo() {}};
  c.currentProteinData = "protein A";
  c.fetch = async () => ({ok: true, text: async () => "pdbqt"});
  c.fetchDockedPoseSdf = () => new Promise((resolve) => {finishPose = resolve;});
  c.loadComplexStructure("task-A", 1);
  c.applyDockingTaskRecord(success());
  finishPose("ligand A");
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(paints, 0);
});
test("a new successful task clears the previously displayed complex", () => {
  const { context: c } = harness();
  let cleared = 0;
  c.viewer = {clear() { cleared++; }, render() {}};
  c.currentProteinData = "protein A";
  const r = success(); r.result.data.result_job_id = "task-B"; r.result.data.best_pose.pose = 1;
  c.applyDockingTaskRecord(r);
  assert.equal(cleared, 1);
});
test("batch view refuses a different or unverified receptor", () => {
  const { context: c } = harness();
  c.viewer = {clear() {}, render() {}};
  vm.runInContext('batchDockingProteins.set("batch-job", "protein A")', c);
  c.AppState.currentProteinData = "protein B";
  c.loadComplexStructure = () => { throw new Error("wrong receptor"); };
  c.viewBatchLigand("batch-job");
  assert.equal(c.AppState.currentJobId, null);
  vm.runInContext('setCurrentJobId("batch-job")', c);
  c.loadComplexStructure = () => { throw new Error("wrong receptor bypass"); };
  c.viewPose(1);
  assert.equal(c.AppState.currentJobId, "batch-job");
});
test("switching to a protein-only view invalidates pending analysis", () => {
  const { context: c } = harness();
  c.AppState.currentProteinData = "protein";
  c.loadMolecule = () => {};
  c.setActiveButton = () => {};
  const before = c.getDockingViewSequence();
  c.showProtein();
  assert.ok(c.getDockingViewSequence() > before);
});
test("legacy history does not load over an active task", () => {
  const { context: c } = harness();
  c.rememberDockingTask("task-2");
  c.fetch = () => { throw new Error("legacy load should not request"); };
  c.loadHistoryJob("old-job", 1);
});
test("late legacy history cannot replace a newer durable history result", async () => {
  const { context: c, get } = harness();
  c.viewer = {clear() {}, render() {}};
  let finishLegacy;
  c.fetch = async (url) => url === "/api/tasks/task-1" ? response({...success(), task_type: "docking"}) : new Promise((resolve) => { finishLegacy = resolve; });
  c.loadHistoryJob("old-job", 1);
  await c.restoreDurableDockingTask("task-1");
  finishLegacy({ok: true, text: async () => "REMARK VINA RESULT: -8.0 0.0 0.0"});
  await new Promise((resolve) => setImmediate(resolve));
  assert.match(get("results-content").innerHTML, /task-1/);
  assert.doesNotMatch(get("results-content").innerHTML, /old-job/);
});
test("legacy history does not pair an unverified current protein with its poses", async () => {
  const { context: c, get } = harness();
  c.currentProteinData = "unrelated protein";
  c.viewer = {clear() {}, render() {}};
  c.adjustDockingResultsHeight = () => {};
  c.fetch = async () => ({ok: true, text: async () => "REMARK VINA RESULT: -8.0 0.0 0.0"});
  c.loadComplexStructure = () => { throw new Error("unverified complex"); };
  c.loadHistoryJob("old-job", 1);
  await new Promise((resolve) => setImmediate(resolve));
  assert.match(get("results-content").innerHTML, /蛋白/);
  assert.doesNotMatch(get("results-content").innerHTML, /viewPose\(/);
  assert.equal(c.AppState.currentJobId, null);
});
test("a poll failure allows retrying the same remembered task without creating a new one", async () => {
  const { context: c, get } = harness();
  c.rememberDockingTask("task-1");
  get("start-btn").disabled = false;
  c.fetch = async () => response({...success(), task_type: "docking"});
  await c.restoreDurableDockingTask("task-1");
  assert.match(get("results-content").innerHTML, /-8\.400/);
  assert.equal(c.readRememberedDockingTask(), null);
});
test("restoring a running task initializes the progress manager on a fresh page", async () => {
  const { context: c } = harness();
  c.stepManager = null;
  c.DockingStepManager = class { reset() {} show() {} startStep() {} completeStep() {} updateStepProgress() {} errorStep() {} };
  let calls = 0;
  c.fetch = async () => response(++calls === 1 ? {...record(), task_type: "docking"} : {...success(), task_type: "docking"});
  await c.restoreDurableDockingTask("task-1");
  assert.equal(c.readRememberedDockingTask(), null);
});

(async () => {
  let failures = 0;
  for (const [name, fn] of tests) {
    try { await fn(); console.log(`PASS ${name}`); }
    catch (error) { failures++; console.error(`FAIL ${name}: ${error.message}`); }
  }
  if (failures) process.exitCode = 1;
})();
