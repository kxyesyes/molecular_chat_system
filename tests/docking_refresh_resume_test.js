"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");

const source = fs.readFileSync(
  "src/web/static/js/docking/ui_manager.js",
  "utf8",
);

assert.match(
  source,
  /new URL\(window\.location\.href\)[\s\S]*DOCKING_TASK_QUERY_PARAM/,
  "refresh recovery must read the task id from the URL fallback",
);
assert.match(
  source,
  /history\.replaceState\(/,
  "task persistence must update the URL without reloading the page",
);

console.log("docking_refresh_resume_test: all assertions passed");
