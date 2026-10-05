const fs = require("fs");
const assert = require("assert");

const template = fs.readFileSync("src/web/templates/molecular_design.html", "utf8");
const css = fs.readFileSync("src/web/static/css/molecular_design.css", "utf8");
const main = fs.readFileSync("src/web/static/js/design/main.js", "utf8");
const editor = fs.readFileSync("src/web/static/js/design/molecule_editor.js", "utf8");
const properties = fs.readFileSync("src/web/static/js/design/properties_panel.js", "utf8");
const history = fs.readFileSync("src/web/static/js/design/history_manager.js", "utf8");

assert.ok(template.includes('id="uploadInput"'), "design input must expose a file upload control");
assert.ok(template.includes('id="candidateCompare"'), "candidate result area must remain available");
assert.ok(!template.includes('class="props-panel"'), "the old persistent third property column must be removed");
assert.ok(!template.includes("<footer>"), "the design workspace must not reserve a giant unrelated footer");
assert.ok(!template.includes("综合评分") && !template.includes("Top 10"), "fixed candidate scoring must be removed");
assert.ok(css.includes("grid-template-columns: minmax(300px, 360px) minmax(0, 1fr)"), "workspace must use a compact two-column layout");

assert.ok(main.includes("propsRequestSeq"), "property requests must be versioned");
assert.ok(main.includes("pendingPropsSmiles"), "property requests must converge on the latest molecule");
assert.ok(properties.includes("—"), "unavailable properties must render as an em dash");
assert.ok(!properties.includes("p.logp ?? 0"), "missing LogP must not be rendered as zero");
assert.ok(!properties.includes("p.qed ?? 0"), "missing QED must not be rendered as zero");
assert.ok(!editor.includes("candidateScore"), "candidate board must not use a fixed weighted score");
assert.ok(!editor.includes("candidates.sort"), "candidate order must remain generation order");
assert.ok(history.includes("props: props || {}"), "history must preserve unavailable properties without coercing them to zero");

console.log("molecular design frontend contract passed");
