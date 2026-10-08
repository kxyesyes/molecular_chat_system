const assert = require("assert");
const fs = require("fs");

const template = fs.readFileSync("src/web/templates/index.html", "utf8");
const main = fs.readFileSync("src/web/static/js/home/main.js", "utf8");

assert(
  template.includes("main.js?v=20261007-legacy-payload-v1"),
  "the homepage script cache version must change with the protocol fix"
);
assert(!template.includes("智能工具"), "the smart-tool toggle must not render");
assert(!main.includes("toolsToggle"), "the removed toggle must not be queried or bound");
assert(!main.includes("function toggleTools"), "the removed toggle handler must not remain");
assert(main.includes("let toolsEnabled = true"), "agent tools must remain enabled by default");
assert(main.includes("enable_tools: toolsEnabled"), "chat requests must keep agent-tool capability");

console.log("Homepage smart-tool toggle removal checks passed");
