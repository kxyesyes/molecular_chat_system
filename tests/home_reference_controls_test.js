const assert = require("assert");
const fs = require("fs");

const main = fs.readFileSync("src/web/static/js/home/main.js", "utf8");
const references = fs.readFileSync("src/web/static/js/home/scientific_references.js", "utf8");

assert(!main.includes("scientific-reference-controls"), "reference status controls must not mount");
assert(!main.includes("未选择科研引用"), "reference status copy must not render on the home page");
assert(!main.includes("清除科研选择"), "reference clear button must not render on the home page");
assert(main.includes("scientificReferences?.outgoing()"), "reference workflow wiring must remain active");
assert(references.includes("function clear()"), "reference controller must retain programmatic cleanup");

console.log("Homepage reference-control removal checks passed");
