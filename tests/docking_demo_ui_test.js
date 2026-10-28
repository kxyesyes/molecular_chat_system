"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");

const template = fs.readFileSync("src/web/templates/molecular_docking.html", "utf8");
const source = fs.readFileSync("src/web/static/js/docking/main.js", "utf8");

assert.match(template, /载入示例[：:]\s*PDE4A apo（6FG5）\+ 罗氟司特/);
assert.match(source, /async function loadDockingDemo\(\)/);
assert.match(source, /\/api\/docking\/demo\/pde4a-apo/);
assert.match(source, /PDE4A_apo_6FG5\.pdb/);
assert.match(
  source,
  /C1CC1COC2=C\(C=CC\(=C2\)C\(=O\)NC3=C\(C=NC=C3Cl\)Cl\)OC\(F\)F/,
);
assert.doesNotMatch(source, /loadDockingDemo[\s\S]{0,240}startDocking\(/);

console.log("docking demo UI contract should be implemented");
