const assert = require("assert");
const fs = require("fs");
const path = require("path");

const projectRoot = path.resolve(__dirname, "..");
const template = fs.readFileSync(
  path.join(projectRoot, "src", "web", "templates", "activity_prediction.html"),
  "utf8",
);
const source = fs.readFileSync(
  path.join(projectRoot, "src", "web", "static", "js", "activity_prediction", "main.js"),
  "utf8",
);

assert.match(template, /id="batchValidationMessage"[^>]*role="alert"/);
assert.doesNotMatch(template, /id="batchFile"[^>]*required/);
assert.match(source, /batchFile/);
assert.match(source, /batchValidationMessage/);
assert.match(source, /请先上传批量分子文件/);

console.log("activity batch validation checks passed");
