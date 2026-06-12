const assert = require("assert");
const fs = require("fs");
const path = require("path");

const projectRoot = path.resolve(__dirname, "..");
const configPath = path.join(
  projectRoot,
  "src",
  "web",
  "static",
  "js",
  "reverse_target",
  "config.js",
);
const mainPath = path.join(
  projectRoot,
  "src",
  "web",
  "static",
  "js",
  "reverse_target",
  "main.js",
);
const templatePath = path.join(projectRoot, "src", "web", "templates", "reverse_target.html");

const configSource = fs.readFileSync(configPath, "utf8");
const mainSource = fs.readFileSync(mainPath, "utf8");
const templateSource = fs.readFileSync(templatePath, "utf8");

assert.match(configSource, /THRESHOLD:\s*0\.5\b/);
assert.match(configSource, /TOP_K:\s*100\b/);
assert.match(configSource, /MAX_REFINE:\s*50\b/);
assert.match(templateSource, /id="threshold"[\s\S]*value="0\.5"/);
assert.match(templateSource, /id="topK"[\s\S]*value="100"/);
assert.match(mainSource, /function\s+applyReverseTargetPreset\s*\(/);
assert.match(mainSource, /threshold\.value\s*=\s*"0\.5"/);
assert.match(mainSource, /topK\.value\s*=\s*"100"/);
assert.match(mainSource, /window\.lastReverseTargetSummary\s*=\s*data/);
assert.match(fs.readFileSync(path.join(projectRoot, "src", "web", "static", "js", "reverse_target", "results_renderer.js"), "utf8"), /unique_target_count_before_top_k/);

console.log("reverse target broad-recall defaults checks passed");
