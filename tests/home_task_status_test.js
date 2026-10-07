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
assert.strictEqual(status.className("task_partial"), "is-warning");
assert.strictEqual(status.className("task_completed"), "is-complete");
assert.strictEqual(status.className("tool_started"), "is-running");
console.log("Home task status checks passed");
