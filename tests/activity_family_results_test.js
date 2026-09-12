"use strict";

// Offline contract fixtures only: no scientific inference, services or dependencies.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const root = path.join(__dirname, "..");
const read = file => fs.readFileSync(path.join(root, file), "utf8");
const tests = [];
const test = (name, run) => tests.push({name, run});

// Capture the DOM built by the production modules, not source-string render checks.
// HTML and executable-attribute sinks fail even when safe text is also present.
class Element {
  constructor(tag = "div") {
    this.tagName = tag;
    this.children = [];
    this.style = {};
    this._text = "";
    this.value = "";
    this.files = [];
    this.listeners = {};
    this.classes = new Set();
    this.classList = {
      add: name => this.classes.add(name), remove: name => this.classes.delete(name),
      contains: name => this.classes.has(name),
    };
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(""); }
  set innerHTML(value) { throw new Error("Forbidden HTML sink: " + value); }
  append(...nodes) { this.children.push(...nodes); }
  appendChild(node) { this.append(node); return node; }
  replaceChildren(...nodes) { this._text = ""; this.children = nodes; }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  setAttribute(name, value) {
    assert.ok(!/^(on|srcdoc|href|src|formaction)/i.test(name), "Executable attribute sink");
    this[name] = String(value);
  }
}

function setup() {
  const ids = Object.fromEntries([
    "resultsBody", "resultsHeader", "predictionStatus", "results", "singleSummary",
    "batchHistogram", "statusStat", "submitBtn", "batchSubmitBtn", "loading",
    "activityTarget", "batchActivityTarget", "predictForm", "batchForm",
    "singleConfidenceValue", "singleClassValue", "singleTaskTypeValue", "singleModelMeta",
    "activityBandSubtitle", "activityBandRange", "activityBandMarkerLabel", "bandTickStart",
    "bandTickLow", "bandTickHigh", "bandTickEnd", "singleScoreValue", "singleClassTag",
    "activityBandMarker",
  ].map(id => [id, new Element()]));
  const calls = {histogram: [], model: 0, alerts: [], chartClears: 0, requests: []};
  let frame = 0;
  const context = {
    document: {createElement: tag => new Element(tag), getElementById: id => ids[id] || null},
    echarts: {getInstanceByDom: () => ({clear: () => calls.chartClears++})},
    requestAnimationFrame: callback => callback(++frame * 2000),
    ActivityModels: {
      getTaskType: () => { calls.model++; return "regression"; },
      getSelectedMeta: () => { calls.model++; return {target: "WRONG-FORM-TARGET", name: "WRONG-FORM-MODEL"}; },
      getRangeConfig: () => { calls.model++; return {min: 0, max: 10, lowUpper: 5, highUpper: 7}; },
      initPopover() {}, loadModels() {},
    },
    ActivityCharts: {renderHistogram: rows => calls.histogram.push(rows), initMetricsChart() {}},
    ActivityPreflight: {init() {}}, ActivityTraining: {initRecovery() {}},
    alert: message => calls.alerts.push(message),
    FormData: class extends Map { constructor(form) { super(form.fields); } },
  };
  context.window = context;
  vm.createContext(context);
  for (const file of ["utils.js", "results_renderer.js", "main.js"]) {
    vm.runInContext(read("src/web/static/js/activity_prediction/" + file), context, {filename: file});
  }
  return {ids, calls, context, render: (rows, options = {}, extra = {}) =>
    context.ActivityResults.renderPredictionResults({success: true, status: "passed", results: rows, ...extra}, options)};
}

function family(overrides = {}) {
  return {smiles: "CCO", requested_target: "PDE5A", family_id: "pde-family",
    bundle_id: "synthetic-bundle", status: "passed", success: true,
    activity_class: "无活性", activity_probability: 0, predicted_pIC50: 4.321,
    units: "pIC50", warnings: [], errors: {}, provenance: {
      bundle_id: "synthetic-bundle", scope: "fixture-only", source_sha256: "fixture-source-hash",
      models: {classification: {model_id: "fixture-classifier", weights_sha256: "fixture-class-hash"},
        regression: {model_id: "fixture-regressor", model_card_sha256: "fixture-card-hash"}},
    }, ...overrides};
}
const cells = row => row.children.map(cell => cell.textContent);
function assertNoExecutableNodes(node) {
  assert.ok(!["img", "svg", "script", "iframe", "a"].includes(node.tagName));
  for (const name of Object.keys(node)) {
    assert.ok(!/^(on\w+|srcdoc|href|src|formaction)$/i.test(name), "Executable property: " + name);
  }
  node.children.forEach(assertNoExecutableNodes);
}

test("family passed retains probability zero, class and returned source without global gauge", () => {
  const h = setup();
  h.render([family()], {isSingleRequest: true});
  assert.match(h.ids.resultsBody.textContent, /4\.3210/);
  assert.match(h.ids.resultsBody.textContent, /0\.0%/);
  assert.match(h.ids.resultsBody.textContent, /无活性/);
  assert.match(h.ids.resultsHeader.textContent, /活性概率/);
  for (const text of ["PDE5A", "pde-family", "synthetic-bundle", "fixture-classifier",
    "fixture-regressor", "fixture-source-hash", "fixture-class-hash", "fixture-card-hash", "fixture-only"]) {
    assert.ok(h.ids.resultsBody.textContent.includes(text), "Missing returned provenance: " + text);
  }
  assert.equal(h.ids.singleSummary.style.display, "none");
  assert.equal(h.ids.batchHistogram.style.display, "none");
  assert.equal(h.calls.model, 0);
  assert.equal(h.calls.histogram.length, 0);
  const rendered = h.ids.resultsBody.textContent;
  h.ids.activityTarget.value = "BuChE";
  h.ids.batchActivityTarget.value = "BuChE";
  assert.equal(h.ids.resultsBody.textContent, rendered);
  h.render([family()]);
  assert.equal(h.ids.resultsBody.textContent, rendered, "Rerender also uses returned source");
});

test("partial regression unavailable retains independent classification and stage error", () => {
  const h = setup();
  h.render([family({status: "partial", success: false, predicted_pIC50: null,
    warnings: ["fixture warning"], errors: {regression: "regression_execution_failed"}})], {},
  {success: false, status: "partial", warnings: ["aggregate warning"]});
  const row = cells(h.ids.resultsBody.children[0]);
  assert.equal(row[1], "不可用");
  assert.equal(row[2], "无活性");
  assert.equal(row[3], "0.0%");
  assert.match(row.join(" "), /部分完成.*fixture warning.*regression_execution_failed/);
  assert.match(h.ids.predictionStatus.textContent, /部分完成.*aggregate warning/);
  assert.ok(h.ids.results.classes.has("show"));
});

test("partial returned regression zero is not discarded or replaced", () => {
  const h = setup();
  h.render([family({success: false, status: "partial", predicted_pIC50: 0})]);
  assert.equal(cells(h.ids.resultsBody.children[0])[1], "0.0000");
});

test("classification label is preserved independently of unavailable probability", () => {
  const h = setup();
  h.render([family({success: false, status: "partial", activity_probability: null, predicted_pIC50: null})]);
  const row = cells(h.ids.resultsBody.children[0]);
  assert.equal(row[2], "无活性");
  assert.equal(row[3], "不可用");
});

test("family detection does not require a regression field", () => {
  const h = setup();
  const row = family({success: false, status: "partial"});
  delete row.predicted_pIC50;
  h.render([row], {isSingleRequest: true});
  assert.match(h.ids.resultsHeader.textContent, /活性概率/);
  assert.equal(cells(h.ids.resultsBody.children[0])[1], "不可用");
  assert.equal(h.calls.model, 0);
});

test("failed rows remain visible without invented numbers", () => {
  const h = setup();
  h.render([family({success: false, status: "failed", activity_class: null,
    activity_probability: null, predicted_pIC50: null, errors: {input: "invalid_smiles"}})],
  {isSingleRequest: true}, {status: "failed", success: false});
  assert.match(h.ids.resultsBody.textContent, /失败.*invalid_smiles/);
  assert.ok(!h.ids.resultsBody.textContent.includes("0.000"));
  assert.ok(!h.ids.resultsBody.textContent.includes("0.0%"));
  assert.ok(h.ids.results.classes.has("show"));
  assert.equal(h.ids.singleSummary.style.display, "none");
});

test("empty response clears earlier rows and shows explicit failure, not a zero row", () => {
  const h = setup();
  h.render([family()]);
  h.render([], {}, {status: "failed", success: false});
  assert.equal(h.ids.resultsBody.children.length, 0);
  assert.match(h.ids.predictionStatus.textContent, /失败.*无.*结果/);
  assert.equal(h.ids.singleSummary.style.display, "none");
  assert.ok(h.ids.results.classes.has("show"));
});

test("mixed batch preserves order, duplicates and each row's own status and source", () => {
  const h = setup();
  const rows = [family(), family({smiles: "invalid", requested_target: "BuChE", success: false,
    status: "failed", predicted_pIC50: null, activity_probability: null, activity_class: null}),
    family({success: false, status: "partial", predicted_pIC50: null})];
  h.render(rows, {}, {success: false, status: "partial"});
  assert.equal(h.ids.resultsBody.children.length, 3);
  assert.deepEqual(h.ids.resultsBody.children.map(row => cells(row)[0]), ["CCO", "invalid", "CCO"]);
  assert.match(h.ids.resultsBody.children[1].textContent, /失败.*BuChE/);
  assert.match(h.ids.resultsBody.children[2].textContent, /部分完成.*PDE5A/);
  assert.equal(h.calls.histogram.length, 0);
});

test("hostile fields reach text only, including all nested provenance and aggregate warnings", () => {
  const h = setup();
  const hostile = '<img src=x onerror="alert(1)"><svg onload=alert(1)>';
  const row = family({smiles: hostile, requested_target: hostile, family_id: hostile,
    bundle_id: hostile, activity_class: hostile, warnings: [hostile], errors: {[hostile]: hostile},
    provenance: {models: {classification: {model_id: hostile}}, extra: hostile}});
  h.render([row], {}, {warnings: [hostile]});
  assert.ok(cells(h.ids.resultsBody.children[0])[0].includes(hostile));
  assert.ok(cells(h.ids.resultsBody.children[0])[2].includes(hostile));
  assert.ok(h.ids.resultsBody.textContent.includes('"extra"'));
  assert.ok(h.ids.predictionStatus.textContent.includes(hostile));
  assertNoExecutableNodes(h.ids.resultsBody);
  assertNoExecutableNodes(h.ids.predictionStatus);
});

test("malformed optional warnings never erase returned observations or become character lists", () => {
  const h = setup();
  for (const warnings of [null, "not-a-list", [null, 1, {bad: true}, "valid warning"]]) {
    h.render([family({warnings})], {}, {warnings});
    assert.match(h.ids.resultsBody.textContent, /4\.3210/);
    assert.ok(!h.ids.resultsBody.textContent.includes("[object Object]"));
    assert.ok(!h.ids.resultsBody.textContent.includes("n；o；t"));
  }
});

test("null, strings, booleans and nonfinite numbers do not become scientific zero", () => {
  const h = setup();
  for (const value of [null, undefined, "", "0", false, NaN, Infinity]) {
    h.render([family({activity_probability: value, predicted_pIC50: value})]);
    const row = cells(h.ids.resultsBody.children[0]);
    assert.equal(row[1], "不可用");
    assert.equal(row[3], "不可用");
  }
  for (const value of [-0.1, 1.1]) {
    h.render([family({activity_probability: value})]);
    assert.equal(cells(h.ids.resultsBody.children[0])[3], "不可用");
  }
});

test("consistency warning preserves both original outputs", () => {
  const h = setup();
  h.render([family({classification_regression_consistent: false})]);
  assert.match(h.ids.resultsBody.textContent, /不一致/);
  assert.match(h.ids.resultsBody.textContent, /4\.3210/);
  assert.match(h.ids.resultsBody.textContent, /0\.0%/);
});

test("task-aware legacy endpoint values retain units, zero and errors without pIC50 inference", () => {
  const h = setup();
  h.render([{smiles: "CCO", success: true, task_type: "regression", endpoint: "Ki", units: "nM", value: 0},
    {smiles: "CCC", success: true, task_type: "classification", endpoint: "activity", units: "probability", probability: 0},
    {smiles: "invalid", success: false, error: "fixture error"}]);
  assert.match(h.ids.resultsHeader.textContent, /Endpoint/);
  assert.match(h.ids.resultsBody.textContent, /Ki0\.0000nM/);
  assert.match(h.ids.resultsBody.textContent, /activity0\.0000probability/);
  assert.match(h.ids.resultsBody.textContent, /fixture error/);
  assert.ok(!h.ids.resultsBody.textContent.includes("pIC50"));
  assert.equal(h.calls.histogram.length, 0);
  assert.equal(h.calls.model, 0);
  h.render([{smiles: "CCO", success: true, task_type: "regression", value: null}], {isSingleRequest: true});
  assert.match(h.ids.resultsBody.textContent, /不可用/);
  assert.ok(!h.ids.resultsBody.textContent.includes("0.000"));
});

test("legacy missing score is unavailable while real zero and summary behaviors survive", () => {
  const h = setup();
  h.render([{smiles: "CCO", success: true, activity_score: null}], {isSingleRequest: true});
  assert.match(h.ids.resultsBody.textContent, /不可用/);
  assert.ok(!h.ids.resultsBody.textContent.includes("0.000"));
  assert.equal(h.ids.singleSummary.style.display, "none");
  h.render([{smiles: "CCO", success: true, activity_score: 0, confidence: 0, class: "Low"}], {isSingleRequest: true});
  assert.match(h.ids.resultsBody.textContent, /0\.000/);
  assert.match(h.ids.resultsBody.textContent, /0\.0%/);
  assert.equal(h.ids.singleSummary.style.display, "block");
  assert.ok(!h.ids.singleModelMeta.textContent.includes("WRONG-FORM"), "Do not invent returned model identity");
  h.render([family()]);
  assert.equal(h.ids.singleSummary.style.display, "none");
  h.render([{smiles: "CCO", success: true, activity_score: 1}]);
  assert.equal(h.ids.resultsHeader.children.length, 4);
  assert.equal(h.calls.histogram.length, 1);
  assert.equal(h.ids.resultsBody.children.length, 1);
  assert.ok(!h.ids.resultsBody.textContent.includes("synthetic-bundle"));
});

test("template exposes separate PDE/BuChE/legacy Form fields and accessible result status", () => {
  const template = read("src/web/templates/activity_prediction.html");
  for (const id of ["predictForm", "batchForm"]) {
    const form = template.match(new RegExp('<form id="' + id + '">([\\s\\S]*?)</form>'))[1];
    const select = form.match(/<select[^>]*name="target"[^>]*>([\s\S]*?)<\/select>/);
    assert.ok(select, id + " must submit optional target via FormData");
    for (const target of ["PDE", "BuChE", ""]) assert.ok(select[1].includes('value="' + target + '"'));
  }
  assert.match(template, /id="resultsHeader"/);
  assert.match(template, /id="predictionStatus"[^>]*role="status"/);
  assert.ok(!template.includes(">Ready</div>"));
});

test("single and batch requests display partial/failed/empty payloads and release loading state", async () => {
  for (const endpoint of ["predict", "batch_predict"]) {
    for (const status of ["passed", "partial", "failed", "empty"]) {
      const h = setup();
      const payload = {success: status === "passed", status: status === "empty" ? "failed" : status,
        results: status === "empty" ? [] : [family({status, success: status === "passed",
          predicted_pIC50: status === "passed" ? 4.321 : null})]};
      const form = new Map([["target", "BuChE"]]);
      h.context.fetch = async (url, options) => {
        assert.equal(options.body.get("target"), "BuChE");
        assert.equal(options.method, "POST");
        assert.ok(h.ids.loading.classes.has("show"));
        return {ok: true, json: async () => payload};
      };
      const button = endpoint === "predict" ? "submitBtn" : "batchSubmitBtn";
      await h.context.ActivityMain.handlePredict("/api/activity/" + endpoint, form, button);
      assert.equal(h.calls.alerts.length, 0, "Scientific failure must remain an inspectable report");
      assert.equal(h.ids.resultsBody.children.length, payload.results.length);
      assert.equal(h.ids.statusStat.textContent, {passed: "完成", partial: "部分完成", failed: "失败", empty: "失败"}[status]);
      assert.ok(h.ids.results.classes.has("show"));
      assert.equal(h.ids[button].disabled, false);
      assert.ok(!h.ids.loading.classes.has("show"));
    }
  }
});

test("legacy optional target is omitted and HTTP/shape/network errors retain safety behavior", async () => {
  for (const response of [
    {ok: false, json: async () => ({detail: "fixture HTTP error", results: [family()]})},
    {ok: true, json: async () => ({success: true})},
    null,
  ]) {
    const h = setup();
    const form = new Map([["target", ""], ["smiles", "CCO"]]);
    h.context.fetch = async (url, options) => {
      assert.equal(options.body.has("target"), false, "Empty legacy option must be omitted");
      assert.equal(options.body.get("smiles"), "CCO");
      if (!response) throw new Error("fixture network error");
      return response;
    };
    await h.context.ActivityMain.handlePredict("/api/activity/predict", form, "submitBtn");
    assert.equal(form.has("target"), false);
    assert.equal(h.ids.statusStat.textContent, "Error");
    assert.equal(h.calls.alerts.length, 1);
    assert.equal(h.ids.submitBtn.disabled, false);
    assert.ok(!h.ids.loading.classes.has("show"));
    assert.ok(!h.ids.results.classes.has("show"));
  }
});

test("registered submit handlers pass each form's own target and file/smiles", async () => {
  const h = setup();
  h.context.fetch = async (url, options) => {
    h.calls.requests.push({url, fields: Array.from(options.body)});
    return {ok: true, json: async () => ({success: false, status: "failed", results: []})};
  };
  h.context.ActivityMain.init();
  const file = {name: "fixture.smi"};
  h.ids.predictForm.fields = [["target", "PDE"], ["smiles", "CCO"]];
  h.ids.batchForm.fields = [["target", "BuChE"], ["file", file]];
  for (const id of ["predictForm", "batchForm"]) {
    let prevented = false;
    h.ids[id].listeners.submit({target: h.ids[id], preventDefault: () => { prevented = true; }});
    assert.ok(prevented);
    await new Promise(resolve => setImmediate(resolve));
  }
  assert.deepEqual(h.calls.requests, [
    {url: "/api/activity/predict", fields: h.ids.predictForm.fields},
    {url: "/api/activity/batch_predict", fields: h.ids.batchForm.fields},
  ]);
});

(async () => {
  let failed = 0;
  for (const {name, run} of tests) {
    try { await run(); console.log("PASS " + name); }
    catch (error) { failed++; console.error("FAIL " + name + "\n" + error.stack); }
  }
  console.log(`Activity family DOM/request contracts: ${tests.length - failed}/${tests.length} passed`);
  if (failed) process.exitCode = 1;
})();
