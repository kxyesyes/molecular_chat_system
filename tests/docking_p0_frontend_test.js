const assert = require("assert");
const fs = require("fs");

const source = fs.readFileSync(
  "src/web/static/js/docking/ui_manager.js",
  "utf8",
);

assert.match(source, /Number\.isFinite\(item\.best_energy\)/);
assert.match(source, /"未计算"/);
assert.match(source, /ligand_efficiency: null/);
assert.doesNotMatch(source, /item\.best_energy !== null \? item\.best_energy\.toFixed/);

console.log("docking P0 frontend checks passed");
