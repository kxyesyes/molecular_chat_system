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

const prose = render("pIC50 ≥ 6（即 IC50 ≤ 1 μM），pIC50预测与IC50有关。");
assert(prose.includes("pIC<sub>50</sub> ≥ 6"));
assert(prose.includes("IC<sub>50</sub> ≤ 1 μM"));
assert(prose.includes("pIC<sub>50</sub>预测与IC<sub>50</sub>有关"));
assert(render("**pIC50** 和 *IC50*").includes("pIC<sub>50</sub>"));
const table = render("| 指标 | 值 |\n| --- | --- |\n| pIC50 | 6.0 |");
assert(table.includes("pIC<sub>50</sub>") && table.includes(">6.0</td>"));

const protectedContent = render("`pIC50` 和 predicted_pIC50；\n```json\n{\"IC50\": 3}\n```\n[IC50](https://www.rcsb.org/structure/IC50)");
assert(protectedContent.includes(">pIC50</code>"));
assert(protectedContent.includes("predicted_pIC50"));
assert(protectedContent.includes("&quot;IC50&quot;: 3"));
assert(protectedContent.includes('href="https://www.rcsb.org/structure/IC50"'));
assert(protectedContent.includes(">IC50</a>"));
assert(!protectedContent.includes("<sub>"), "raw code, identifier and URL must not be rewritten");
assert(render("https://example.com/IC50").includes("https://example.com/IC50"));
assert(!render("https://example.com/IC50").includes("<sub>"), "plain URLs stay byte-for-byte intact");
assert(!render("```json\n{\"IC50\": 3").includes("<sub>"), "unfinished streamed code fences remain literal");
assert(!render("`predicted_pIC50 and IC50").includes("<sub>"), "unfinished streamed inline code remains literal");
assert(!render('{"IC50": 3, "pIC50": 6}').includes("<sub>"), "unfenced JSON keys remain literal");
assert(!render('assay-IC50 IC50-value').includes("<sub>"), "identifier fragments remain literal");
const fencedTable = render('```text\n| IC50 | value |\n| --- | --- |\n| IC50 | 5 |\n```');
assert(fencedTable.includes('<pre') && !fencedTable.includes('<table'), "fenced tables stay inside code");
assert(!fencedTable.includes('<sub>'), "fenced table contents stay literal");
const codeLiterals = render('```text\nC*CC* [IC50](https://www.rcsb.org/structure/IC50)\n``` `C*CC*`');
assert(codeLiterals.includes('C*CC*') && !codeLiterals.includes('<em>'), 'code asterisks remain literal');
assert(!codeLiterals.includes('<a '), 'links inside code remain literal');
assert(render('```text\n**IC50**\n```').includes('**IC50**'), 'code must retain paired asterisks');
assert(render('\uE0000\uE000 `IC50`').includes('\uE0000\uE000'), 'literal marker-like text remains intact');
assert(render('Use `x` #tag').includes('</code> #tag'), 'mid-line hashes after code are not headings');
assert(!render('Use `x`\n# Heading').includes('# Heading'), 'real line-start heading cleanup remains');
const codeInUrl = render('[link](/path/`IC50`)');
assert(!codeInUrl.includes('<a '), 'code embedded in a link destination must not create malformed href markup');
const codeInLabel = render('[pIC50 `value`](https://www.rcsb.org/structure/1ABC)');
assert(codeInLabel.includes('href="https://www.rcsb.org/structure/1ABC"') && codeInLabel.includes('<code'),
  'safe links with inline-code labels remain usable');
const unfinishedBackticks = render('```js\nconst a = `IC50`;\n');
assert(!unfinishedBackticks.includes('<code') && !unfinishedBackticks.includes('<sub>'),
  'an unfinished fence must stay literal, including embedded backticks');
const contractions = render("It's pIC50 and it's IC50.");
assert(contractions.includes('pIC<sub>50</sub>') && contractions.includes('IC<sub>50</sub>'),
  'apostrophes inside words must not suppress prose formatting');
assert(!render("<img src=x onerror=alert(1)> pIC50").includes("<img"));
console.log("home scientific notation tests passed");
