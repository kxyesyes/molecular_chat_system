"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");

const source = fs.readFileSync("src/web/static/js/docking/ui_manager.js", "utf8");
const historyStart = source.indexOf("function loadHistoryJob");
const historyEnd = source.length;
assert.notEqual(historyStart, -1, "history loader should exist");
const historyBlock = source.slice(historyStart, historyEnd);

assert.match(
  source,
  /loadHistoryJob\(\s*'\$\{safeJobIdJs\}'\s*,\s*\$\{safeBestPoseIndex\}\s*\)/,
  "history action should pass the indexed best pose to the loader",
);
assert.match(
  historyBlock,
  /function loadHistoryJob\(jobId,\s*poseIndex\)/,
  "history loader should accept the recorded best pose index",
);
assert.match(
  historyBlock,
  /fetchDockedPoseSdf\(jobId,\s*selectedPoseNumber\)/,
  "history loader should request the selected best pose",
);
assert.doesNotMatch(
  historyBlock,
  /fetchDockedPoseSdf\(jobId,\s*1\)/,
  "history loader must not silently fall back to Pose 1",
);
assert.match(
  historyBlock,
  /binding_energy[\s\S]*selectedPoseNumber/,
  "older records without metadata should select the lowest finite binding energy",
);

console.log("docking_history_best_pose_ui_test: all assertions passed");
