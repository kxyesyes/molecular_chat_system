const assert = require("assert");
const fs = require("fs");
const path = require("path");

const mainSource = fs.readFileSync(
  path.join(__dirname, "..", "src", "web", "static", "js", "home", "main.js"),
  "utf8",
);

assert(!mainSource.includes("未选择科研引用"), "首页不得渲染科研引用状态控件");
assert(!mainSource.includes("清除科研选择"), "首页不得渲染清除科研选择控件");
assert(!mainSource.includes("scientific-reference-controls"), "首页不得创建科研引用控件容器");

// Removing the visual controls must not remove the underlying reference workflow.
assert(mainSource.includes("scientificReferences?.outgoing()"), "科研引用仍应随请求传递");
assert(mainSource.includes("scientificReferences.select"), "候选分子仍应支持科研引用选择");

console.log("home_reference_controls_test: passed");
