const assert = require("node:assert/strict");
const fs = require("node:fs");

const css = fs.readFileSync(
  "src/web/static/css/molecular_docking.css",
  "utf8",
);

function rule(selector) {
  const match = css.match(new RegExp(`${selector}\\s*\\{([\\s\\S]*?)\\}`, "m"));
  assert.ok(match, `Missing CSS rule: ${selector}`);
  return match[1];
}

const fileList = rule("\\.file-list");
const fileItem = rule("\\.file-item");
const fileName = rule("\\.file-name");
const fileRemove = rule("\\.file-remove");

assert.match(fileList, /overflow-x:\s*hidden/);
assert.match(fileItem, /min-width:\s*0/);
assert.match(fileName, /min-width:\s*0/);
assert.match(fileName, /overflow-wrap:\s*anywhere/);
assert.match(fileName, /overflow:\s*hidden/);
assert.match(fileRemove, /(?:flex:\s*0\s+0\s+auto|flex-shrink:\s*0)/);
assert.match(fileRemove, /white-space:\s*nowrap/);

console.log("docking ligand upload layout checks passed");
