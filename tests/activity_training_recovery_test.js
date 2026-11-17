"use strict";

const assert = require("node:assert/strict");
const {test} = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../src/web/static/js/activity_prediction/training_monitor.js"), "utf8");

function restore(fetch, callbacks = {}) {
  const saved = new Map([["activeTrainJobId", JSON.stringify({jobId: "active-job", totalEpochs: 4})]]);
  const labels = {};
  const dock = {style: {}};
  const timers = new Set();
  const context = {
    fetch, window: {}, console,
    document: {getElementById: id => id === "train-dock" ? dock : null},
    ActivityUtils: {setText: (id, text) => {labels[id] = text;}},
    ActivityCharts: {buildMetricSummary: () => null},
    localStorage: {getItem: key => saved.get(key), removeItem: key => saved.delete(key)},
    setInterval(fn) {timers.add(fn); return fn;},
    clearInterval(fn) {timers.delete(fn);},
  };
  vm.runInNewContext(source, context);
  context.window.ActivityTraining.initRecovery(callbacks);
  return {saved, labels, timers, dock};
}

for (const failure of ["503", "offline", "empty response"]) {
  test("refresh retains the training job after " + failure + " and reconnects", async () => {
    let calls = 0;
    const state = restore(async () => {
      if (++calls === 1) {
        if (failure === "offline") throw new Error("offline");
        if (failure === "empty response") return {ok:true, status:200, json:async () => null};
        return {ok:false, status:503, json:async () => ({detail:"unavailable"})};
      }
      return {ok:true, status:200, json:async () => ({success:true, status:{state:"running", epoch:2}})};
    });
    await new Promise(resolve => setImmediate(resolve));
    assert.ok(state.saved.has("activeTrainJobId"));
    assert.match(state.labels["dock-status-text"], /重试/);
    assert.equal(state.timers.size, 1);
    await [...state.timers][0]();
    assert.equal(state.labels["dock-epoch-text"], "Epoch 2/4");
  });
}

test("refresh discards a training job confirmed missing", async () => {
  const state = restore(async () => ({ok:false, status:404, json:async () => ({detail:"任务不存在"})}));
  await new Promise(resolve => setImmediate(resolve));
  assert.ok(!state.saved.has("activeTrainJobId"));
  assert.equal(state.timers.size, 0);
  assert.equal(state.dock.style.display, "none");
});

for (const first of ["canceled", "running"]) {
  test("cancellation releases the recovered job after initial state " + first, async () => {
    let phase = first;
    const finished = [];
    const state = restore(async () => ({
      ok: true, status: 200, json: async () => ({success:true, status:{state:phase}}),
    }), {onFinish: value => finished.push(value)});
    await new Promise(resolve => setImmediate(resolve));
    if (first === "running") {
      phase = "canceled";
      await [...state.timers][0]();
    }
    assert.deepEqual(finished, ["canceled"]);
    assert.equal(state.saved.has("activeTrainJobId"), false);
    assert.equal(state.timers.size, 0);
    assert.equal(state.labels["dock-status-text"], "训练已取消");
  });
}
