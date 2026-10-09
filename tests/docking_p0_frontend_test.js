const assert = require("assert");
const fs = require("fs");

const source = fs.readFileSync(
  "src/web/static/js/docking/ui_manager.js",
  "utf8",
);
const interactionSource = fs.readFileSync(
  "src/web/static/js/docking/interactions.js",
  "utf8",
);
const template = fs.readFileSync(
  "src/web/templates/molecular_docking.html",
  "utf8",
);

assert.match(source, /Number\.isFinite\(item\.best_energy\)/);
assert.match(source, /"未计算"/);
assert.match(source, /ligand_efficiency: null/);
assert.match(source, /Number\.isFinite\(r\.binding_energy\)/);
assert.match(source, /const poseEnergy = Number\.isFinite\(r\.binding_energy\)/);
assert.doesNotMatch(source, /item\.best_energy !== null \? item\.best_energy\.toFixed/);
assert.match(interactionSource, /\/api\/docking\/interactions\//);
assert.match(interactionSource, /requestInteractionAnalysis\("hydrogen_bond"\)/);
assert.match(interactionSource, /requestInteractionAnalysis\("pi_pi"\)/);
assert.match(interactionSource, /requestInteractionAnalysis\("hydrophobic"\)/);
assert.doesNotMatch(interactionSource, /InteractionGeometry/);
assert.doesNotMatch(template, /docking\/interaction_geometry\.js/);

console.log("docking P0 frontend checks passed");
