"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const sourcePath = path.join(
  __dirname,
  "..",
  "src",
  "web",
  "static",
  "js",
  "home",
  "task_status.js",
);
const sandbox = { window: {}, Object, Math };
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(sourcePath, "utf8"), sandbox, {
  filename: sourcePath,
});

const status = sandbox.window.HomeTaskStatus;
assert(status, "HomeTaskStatus must be exposed");

assert.deepStrictEqual(JSON.parse(JSON.stringify(status.resolve({event: "task_partial", progress: 2}))), {
  eventType: "task_partial",
  progressText: "部分完成",
  terminal: true,
});
for (const outcome of ["timeout", "unavailable", "not_calculated"]) {
  const resolved = status.resolve({
    event: "task_failed",
    progress: 1,
    payload: {status: outcome},
  });
  assert.strictEqual(resolved.progressText, {
    timeout: "计算超时",
    unavailable: "工具不可用",
    not_calculated: "尚未计算",
  }[outcome]);
  assert.strictEqual(resolved.terminal, true);
}
assert.deepStrictEqual(JSON.parse(JSON.stringify(status.resolve({event: "tool_started", progress: 0.42}))), {
  eventType: "tool_started",
  progressText: "42%",
  terminal: false,
});
assert.deepStrictEqual(JSON.parse(JSON.stringify(status.resolve({event: "unknown"}))), {
  eventType: "unknown",
  progressText: "执行中",
  terminal: false,
});
assert.strictEqual(status.label("activity_predictor"), "Agent 事件");
assert.strictEqual(status.label("tool_started", "activity_predictor"), "调用 活性预测");
assert.strictEqual(status.label('tool_started', 'candidate_ranker'), '调用 候选排序');
assert.strictEqual(status.className("task_partial"), "is-warning");
assert.strictEqual(status.className("task_completed"), "is-complete");
assert.strictEqual(status.className("tool_started"), "is-running");
assert.strictEqual(status.message({event:'tool_failed', tool:'admet_predictor', message:'Observation preparation failed'}),
  '结果准备或校验失败，无法采用该结果。');
assert.strictEqual(status.message({event:'task_failed', message:'Decision publication invalidated'}),
  '结果校验未通过，无法交付本次任务结果。');
assert.strictEqual(status.message({event:'validation_warning', tool:'candidate_ranker',
  message:'Tool result has validation warnings', payload:{warnings:[
    'ADMET evidence was absent or unusable for 2 ranked candidate(s)',
    'Activity evidence was absent, demo, fallback, or unusable for 2 ranked candidate(s)']}}),
  '2 个候选缺少可用的 ADMET 证据。；2 个候选缺少可用的活性证据。');
console.log("Home task status checks passed");
