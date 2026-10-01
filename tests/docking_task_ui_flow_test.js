const assert = require("node:assert/strict");
const fs = require("node:fs");

const source = fs.readFileSync("src/web/static/js/docking/ui_manager.js", "utf8");
const template = fs.readFileSync("src/web/templates/molecular_docking.html", "utf8");

assert.match(source, /\/api\/docking\/tasks/);
assert.match(source, /\/api\/tasks\//);
assert.match(source, /\/cancel/);
assert.match(source, /sessionStorage/);
assert.match(source, /record\.phase/);
assert.match(source, /record\.progress/);
assert.match(source, /dockingProgressPercent/);
assert.match(source, /numeric >= 0 && numeric <= 1/);
assert.doesNotMatch(source, /function startProgressSimulation/);
assert.doesNotMatch(source, /setInterval\(/);
assert.doesNotMatch(source, /elapsed >= 1\.5/);
assert.doesNotMatch(source, /elapsed >= 4\.0/);
assert.doesNotMatch(source, /elapsed >= 6\.5/);
assert.doesNotMatch(source, /elapsed >= 15\.0/);
assert.match(template, /id="cancel-docking-btn"/);

console.log("docking durable task UI checks passed");
