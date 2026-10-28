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

const wrapper = html.match(/<div\b[^>]*class="home-comparison-table-scroll"[^>]*>/);
assert(wrapper, "comparison tables need a scoped local scroll wrapper");
assert(/tabindex="0"/.test(wrapper[0]), "table scroll region must be keyboard focusable");
assert(/role="region"/.test(wrapper[0]) && /aria-label="[^"]+"/.test(wrapper[0]),
  "keyboard scroll region must have an accessible name");
const wide = render("| 分子 | MW | logP | pIC50 | ADMET |\n| --- | --- | --- | --- | --- |\n| CCO | 46.07 | 0 | 6.123 | unavailable |");
assert.equal((wide.match(/<th\b/g) || []).length, 5);
assert.equal((wide.match(/<td\b/g) || []).length, 5);
for (const value of ["CCO", "46.07", "0", "6.123", "unavailable"]) {
  assert(wide.includes(">" + value + "</td>"), "all scientific values must remain intact: " + value);
}
const template = fs.readFileSync(path.join(__dirname, "../src/web/templates/index.html"), "utf8");
assert(template.includes('/static/js/home/formatters.js?v=20261026-scientific-notation-v9'), "refresh must load the accessible formatter");

const unsafe = render("| 分子 | 来源 |\n| --- | --- |\n| <img src=x onerror=alert(1)> | [link](javascript:alert) |");
assert(!unsafe.includes("<img"), "Table cells must retain HTML escaping");
assert(!unsafe.includes('href="javascript:'), "Table cells must retain safe URL policy");
assert(unsafe.includes("&lt;img"));
const rankPrefix = '反向寻靶：前 1 个候选靶点\n\n结果是结构相似性线索，不能视为已确认靶点、结合能或活性预测；仍需实验验证。\n\n';
const rankTable = '| 排名 | 靶点 | 物种 | 综合相似度 | Morgan | MACCS | 匹配分子数 |\n| --- | --- | --- | --- | --- | --- | --- |\n| 1 | ABC&#95;XYZ &amp; Kinase | Human | 0.5 | 0.5 | 0.5 | 1 |';
const ranked = render('来源部分完成。\n\n' + rankPrefix + rankTable, {displayKind: 'reverse_target_rank_v1'});
assert(ranked.includes('ABC&#95;XYZ &amp; Kinase'), 'pre-escaped ranked names must not display literal entities');
assert(!ranked.includes('&amp;#95;') && !ranked.includes('&amp;amp;'), 'escape exactly once');
const rankedUnsafe = render(rankPrefix + rankTable.replace('ABC&#95;XYZ &amp; Kinase', '&lt;img src=x onerror=alert(1)&gt;'), {displayKind: 'reverse_target_rank_v1'});
assert(!rankedUnsafe.includes('<img') && rankedUnsafe.includes('&lt;img'), 'untrusted names remain escaped');
const ordinaryEntities = render('| 文本 | 值 |\n| --- | --- |\n| &lt; &#95; | 0.5 |');
assert(ordinaryEntities.includes('&amp;lt; &amp;#95;'), 'ordinary table literal entities stay literal');
assert(render('| 文本 | 值 |\n| --- | --- |\n| &lt; &#95; | 0.5 |', {displayKind: 'reverse_target_rank_v1'}).includes('&amp;lt; &amp;#95;'),
  'typed answer must not alter unrelated tables');
assert(render(rankPrefix + rankTable).includes('&amp;#95;'), 'matching prose alone must not grant typed rendering');
assert(!render("| A | B |\n| --- | --- |\n| 1 | 2 | 3 |").includes("<table"), "Unequal columns must remain literal, not lose data");
assert(!render("SMILES C|N and ordinary | text").includes("<table"));
assert(!render("| A | B |\n| x | y |").includes("<table"));
assert.strictEqual((render(input + "\n\n" + input).match(/<table\b/g) || []).length, 2);
const crossing = render('| A | B |\n| --- | --- |\n| [aspirin | caffeine](https://www.rcsb.org/structure/1ABC) |');
assert.strictEqual((crossing.match(/<td\b/g) || []).length, 2, 'Inline markdown must not swallow a table boundary');
assert(crossing.includes('[aspirin') && crossing.includes('caffeine]'));

const codeBlock = render('```json\n{"source_record_id":"' + 'x'.repeat(400) + '"}\n```');
assert(codeBlock.includes('<pre'), 'Scientific JSON should remain a code block');
assert(codeBlock.includes('white-space: pre-wrap'), 'Code blocks should wrap long values');
assert(codeBlock.includes('overflow-wrap: anywhere'), 'Code blocks should not widen result cards');
assert(!codeBlock.includes('```'), 'Code fences should not leak into rendered output');
console.log("home comparison table tests passed");
