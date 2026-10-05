const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.join(__dirname, "..");
const html = fs.readFileSync(path.join(root, "src", "web", "templates", "index.html"), "utf8");
const main = fs.readFileSync(path.join(root, "src", "web", "static", "js", "home", "main.js"), "utf8");

const modelSelect = html.match(/<select id="modelSelect">([\s\S]*?)<\/select>/)?.[1] || "";
assert.match(modelSelect, /<option value="deepseek" selected>DeepSeek<\/option>/);
assert.match(modelSelect, /<option value="glm4">GLM-5\.1<\/option>/);
assert.match(modelSelect, /<option value="qwen3">Qwen3-235B<\/option>/);
assert.doesNotMatch(modelSelect, /官方|魔搭|社区/);
assert.doesNotMatch(modelSelect, /<option value="glm4" selected>/);

assert.match(main, /function syncModelSelector\(/);
assert.match(main, /fetch\("\/api\/llm\/config"\)/);
assert.match(main, /deepseek/);
assert.match(main, /elements\.modelSelect\.value/);
assert.match(html, /\/static\/js\/home\/main\.js\?v=20261006-model-header-v2/);

console.log("home_model_header_test: passed");
