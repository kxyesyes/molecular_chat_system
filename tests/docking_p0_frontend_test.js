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
assert.doesNotMatch(source, /item\.best_energy !== null \? item\.best_energy\.toFixed/);
assert.match(interactionSource, /geometry\.detectHydrogenBonds/);
assert.match(interactionSource, /geometry\.classifyPiPiInteraction/);
assert.match(interactionSource, /geometry\.detectHydrophobicContacts/);
assert.match(template, /docking\/interaction_geometry\.js/);

console.log("docking P0 frontend checks passed");
