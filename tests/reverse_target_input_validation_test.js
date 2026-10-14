"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");
const source = fs.readFileSync(path.join(__dirname, "../src/web/static/js/reverse_target/main.js"), "utf8");

function setup({valid = true, smiles = "CCO", maxRefine = "50", file = true, mode3d = true} = {}) {
  const calls = {requests: [], errors: [], loading: 0, validation: []};
  const forms = {};
  for (const id of ["predictForm", "batchForm"]) {
    forms[id] = {
      fields: [["smiles", smiles], ["threshold", "0.5"], ["top_k", "100"]],
      addEventListener(type, callback) { this[type] = callback; },
      // Browser constraint validation is an external boundary; invalid fixtures
      // represent range/step/bad-input failures without reimplementing the DOM.
      reportValidity() { calls.validation.push(id); return valid; },
    };
  }
  const ids = {...forms, smiles: {focus() {}}, maxRefine: {value: maxRefine},
    alpha3d: {value: "0.6"}, batchFile: {files: file ? [{name: "fixture.csv"}] : [], focus() {}}};
  const request = mode => async data => {
    calls.requests.push({mode, data: new Map(data)});
    return {results: []};
  };
  const context = {
    document: {addEventListener() {}, getElementById: id => ids[id] || null},
    FormData: class extends Map {
      constructor(form) { super(form?.fields); }
      append(key, value) { this.set(key, value); }
    },
    RT_CONFIG: {IS_PHARM_3D_MODE: mode3d},
    RtUI: {setSubmitEnabled() {}, showLoading() { calls.loading++; }, hideLoading() {},
      showError(message) { calls.errors.push(message); }},
    RtApi: {predict3D: request("3d"), predict2D: request("2d"), batchPredict: request("batch")},
    RtResults: {displayResults() {}, displayBatchResults() {}},
  };
  context.window = context;
  vm.createContext(context);
  vm.runInContext(source, context);
  context._bindPredictForm();
  context._bindBatchForm();
  return {calls, ids, context, submit: id => forms[id].submit({target: forms[id], preventDefault() {}})};
}

for (const value of ["1.9", "0", "101"]) {
  test(`single invalid refinement ${value} never sends a truncated or out-of-range request`, async () => {
    const h = setup({valid: false, maxRefine: value});
    await h.submit("predictForm");
    assert.deepEqual(h.calls.requests, []);
    assert.deepEqual(h.calls.validation, ["predictForm"]);
    assert.equal(h.calls.loading, 0);
    assert.equal(h.context.lastQuerySmiles, undefined);
  });
}

test("2D numeric validation blocks a request before entering loading state", async () => {
  const h = setup({valid: false, mode3d: false});
  await h.submit("predictForm");
  assert.deepEqual(h.calls.requests, []);
  assert.deepEqual(h.calls.validation, ["predictForm"]);
  assert.equal(h.calls.loading, 0);
});

test("batch numeric validation remains active after explicit file validation", async () => {
  const h = setup({valid: false});
  await h.submit("batchForm");
  assert.deepEqual(h.calls.requests, []);
  assert.deepEqual(h.calls.validation, ["batchForm"]);
  assert.equal(h.calls.loading, 0);
});

for (const [value, expected] of [["19", "19"], ["1e2", "100"], ["", "50"]]) {
  test(`valid refinement ${JSON.stringify(value)} preserves its numeric value`, async () => {
    const h = setup({maxRefine: value});
    await h.submit("predictForm");
    assert.equal(h.calls.requests.length, 1);
    assert.equal(h.calls.requests[0].data.get("max_refine"), expected);
    assert.deepEqual(h.calls.validation, ["predictForm"]);
  });
}

test("empty single input retains the custom error before native validation", async () => {
  const h = setup({smiles: "  ", valid: false});
  await h.submit("predictForm");
  assert.deepEqual(h.calls.errors, ["请输入 SMILES"]);
  assert.deepEqual(h.calls.requests, []);
  assert.deepEqual(h.calls.validation, []);
});

test("missing batch file retains the custom error before native validation", async () => {
  const h = setup({file: false, valid: false});
  await h.submit("batchForm");
  assert.deepEqual(h.calls.errors, ["请先上传批量分子文件"]);
  assert.deepEqual(h.calls.requests, []);
  assert.deepEqual(h.calls.validation, []);
});

test("valid batch and 2D forms still submit once", async () => {
  const h = setup({mode3d: false});
  await h.submit("predictForm");
  await h.submit("batchForm");
  assert.deepEqual(h.calls.requests.map(row => row.mode), ["2d", "batch"]);
  assert.deepEqual(h.calls.validation, ["predictForm", "batchForm"]);
});
