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
  "task_state.js",
);
const sandbox = {window: {}, Object};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(sourcePath, "utf8"), sandbox, {
  filename: sourcePath,
});

const stateApi = sandbox.window.HomeTaskState;
assert(stateApi, "HomeTaskState must be exposed");

let state = stateApi.create();
assert.deepStrictEqual(JSON.parse(JSON.stringify(state)), {
  active: false,
  lastEventType: null,
  progressText: "准备中",
});

state = stateApi.reset(state);
assert.deepStrictEqual(JSON.parse(JSON.stringify(state)), {
  active: true,
  lastEventType: null,
  progressText: "执行中",
});

state = stateApi.record(state, {
  eventType: "tool_started",
  progressText: "42%",
  terminal: false,
});
assert.deepStrictEqual(JSON.parse(JSON.stringify(state)), {
  active: true,
  lastEventType: "tool_started",
  progressText: "42%",
});

state = stateApi.record(state, {
  eventType: "task_partial",
  progressText: "部分完成",
  terminal: true,
});
assert.deepStrictEqual(JSON.parse(JSON.stringify(state)), {
  active: false,
  lastEventType: "task_partial",
  progressText: "部分完成",
});

// A new event after a terminal state starts a fresh run, matching the old
// main.js behavior without letting DOM code own the runtime state.
state = stateApi.record(state, {
  eventType: "tool_started",
  progressText: "执行中",
  terminal: false,
});
assert.strictEqual(state.active, true);
assert.strictEqual(state.lastEventType, "tool_started");
assert.strictEqual(state.progressText, "执行中");

assert.throws(
  () => stateApi.record(state, null),
  /presentation is required/,
);
console.log("Home task state checks passed");
