"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");

const template = fs.readFileSync("src/web/templates/molecular_docking.html", "utf8");
const source = fs.readFileSync("src/web/static/js/docking/main.js", "utf8");
const uiSource = fs.readFileSync("src/web/static/js/docking/ui_manager.js", "utf8");

assert.match(template, /载入示例[：:]\s*PDE4A apo（6FG5）\+ 罗氟司特/);
assert.match(template, /docking\/ui_manager\.js\?v=20261028-docking-demo-v1/);
assert.match(template, /docking\/main\.js\?v=20261028-docking-demo-v1/);
assert.match(source, /async function loadDockingDemo\(\)/);
assert.match(source, /\/api\/docking\/demo\/pde4a-apo/);
assert.match(source, /PDE4A_apo_6FG5\.pdb/);
assert.match(
  source,
  /C1CC1COC2=C\(C=CC\(=C2\)C\(=O\)NC3=C\(C=NC=C3Cl\)Cl\)OC\(F\)F/,
);
assert.doesNotMatch(source, /loadDockingDemo[\s\S]{0,240}startDocking\(/);

assert.match(uiSource, /function renderSelectedFiles\(input, display, files\)/);
assert.match(uiSource, /function loadProteinFile\(file\)[\s\S]{0,500}return new Promise/);
assert.match(source, /await loadProteinFile\(file\)/);
assert.doesNotMatch(source, /proteinInput\.dispatchEvent\(new Event\("change"/);
assert.match(source, /const previewLoaded =[\s\S]{0,180}loadSmilesPreview\(ROFLUMILAST_DEMO_SMILES\)/);
assert.match(source, /if \(!previewLoaded\) throw new Error\(/);

console.log("docking demo UI contract should be implemented");
