const assert = require("assert");
const fs = require("fs");
const path = require("path");

const projectRoot = path.resolve(__dirname, "..");
const source = fs.readFileSync(
  path.join(projectRoot, "src", "web", "static", "js", "target_search", "main.js"),
  "utf8",
);
const styles = fs.readFileSync(
  path.join(projectRoot, "src", "web", "static", "css", "target_search.css"),
  "utf8",
);

assert.match(source, /STRUCTURES_PER_PAGE\s*=\s*8/);
assert.match(source, /structurePage/);
assert.doesNotMatch(source, /const visibleStructures = structures\.slice\(0, 8\);/);
assert.match(source, /data-structure-page/);
assert.match(source, /structure-page-status/);
assert.match(source, /structure-page-btn/);
assert.match(styles, /\.structure-pagination\s*\{/);

console.log("target search structure pagination checks passed");
