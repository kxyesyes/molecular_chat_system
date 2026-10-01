"use strict";

// The browser must not perform chemistry classification from 3Dmol's partial
// atom objects. The backend adapter is the only source of interaction claims.
const assert = require("node:assert/strict");
const fs = require("node:fs");

const source = fs.readFileSync("src/web/static/js/docking/interactions.js", "utf8");
const template = fs.readFileSync("src/web/templates/molecular_docking.html", "utf8");

assert.match(source, /\/api\/docking\/interactions\//);
assert.match(source, /analysis\.status !== "success"/);
assert.match(source, /未显示推测结果/);
assert.doesNotMatch(source, /InteractionGeometry/);
assert.doesNotMatch(source, /selectedAtoms\(\{\}\)/);
assert.doesNotMatch(template, /docking\/interaction_geometry\.js/);

console.log("docking interaction fail-closed checks passed");

