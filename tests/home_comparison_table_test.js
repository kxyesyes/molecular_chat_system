"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const sandbox = { window: {}, URL };
vm.createContext(sandbox);
for (const file of ["shared/safe_render.js", "home/formatters.js"]) {
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../src/web/static/js", file), "utf8"), sandbox);
}
const render = sandbox.window.HomeFormatters.formatContent;
const input = [
  "本轮比较（不重跑 ADMET）：",
  "| 分子 | MW (Da) | logP |",
  "| --- | ---: | ---: |",
  "| 阿司匹林 | 180.16 | 1.310 |",
  "| 咖啡因 | 194.19 | -1.029 |",
  "来源：已验证工具结果。",
].join("\n");
const html = render(input);
assert(html.includes("<table"), "Browser comparison must render a semantic table");
assert.strictEqual((html.match(/<th\b/g) || []).length, 3);
assert.strictEqual((html.match(/<td\b/g) || []).length, 6);
assert(html.includes("180.16") && html.includes("-1.029") && html.includes("来源：已验证工具结果。"));
assert(render(input.replace(/\n/g, "\r\n")).includes("<table"));

const unsafe = render("| 分子 | 来源 |\n| --- | --- |\n| <img src=x onerror=alert(1)> | [link](javascript:alert) |");
assert(!unsafe.includes("<img"), "Table cells must retain HTML escaping");
assert(!unsafe.includes('href="javascript:'), "Table cells must retain safe URL policy");
assert(unsafe.includes("&lt;img"));
assert(!render("| A | B |\n| --- | --- |\n| 1 | 2 | 3 |").includes("<table"), "Unequal columns must remain literal, not lose data");
assert(!render("SMILES C|N and ordinary | text").includes("<table"));
assert(!render("| A | B |\n| x | y |").includes("<table"));
assert.strictEqual((render(input + "\n\n" + input).match(/<table\b/g) || []).length, 2);
const crossing = render('| A | B |\n| --- | --- |\n| [aspirin | caffeine](https://www.rcsb.org/structure/1ABC) |');
assert.strictEqual((crossing.match(/<td\b/g) || []).length, 2, 'Inline markdown must not swallow a table boundary');
assert(crossing.includes('[aspirin') && crossing.includes('caffeine]'));
console.log("home comparison table tests passed");
