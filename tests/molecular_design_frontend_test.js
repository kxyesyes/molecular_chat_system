const fs = require("fs");
const assert = require("assert");

const template = fs.readFileSync("src/web/templates/molecular_design.html", "utf8");
const css = fs.readFileSync("src/web/static/css/molecular_design.css", "utf8");
const main = fs.readFileSync("src/web/static/js/design/main.js", "utf8");
const editor = fs.readFileSync("src/web/static/js/design/molecule_editor.js", "utf8");
const properties = fs.readFileSync("src/web/static/js/design/properties_panel.js", "utf8");
const history = fs.readFileSync("src/web/static/js/design/history_manager.js", "utf8");
const uiManager = fs.readFileSync("src/web/static/js/design/ui_manager.js", "utf8");

assert.ok(template.includes('id="uploadInput"'), "design input must expose a file upload control");
assert.ok(template.includes('id="candidateCompare"'), "candidate result area must remain available");
assert.ok(!template.includes('class="ai-bar"'), "the primary design flow must not expose a redundant AI control bar");
assert.ok(!template.includes('id="aiInput"'), "AI free-form reply input must not crowd the design workspace");
assert.ok(template.includes('id="optimizationInput"'), "explicit optimization goals must remain available without an AI reply panel");
assert.ok(!template.includes('class="props-panel"'), "the old persistent third property column must be removed");
assert.ok(!template.includes("<footer>"), "the design workspace must not reserve a giant unrelated footer");
assert.ok(!template.includes("综合评分") && !template.includes("Top 10"), "fixed candidate scoring must be removed");
assert.ok(!template.includes("prop-bar-track") && !properties.includes("prop-bar"), "decorative property progress bars must be removed");
assert.ok(css.includes("grid-template-columns: minmax(300px, 360px) minmax(0, 1fr)"), "workspace must use a compact two-column layout");

assert.ok(main.includes("propsRequestSeq"), "property requests must be versioned");
assert.ok(main.includes("pendingPropsSmiles"), "property requests must converge on the latest molecule");
assert.ok(main.includes("propsSmiles !== smi"), "saving must require properties for the same SMILES");
assert.ok(editor.includes("mutationSeq"), "substitution and clearing must invalidate obsolete mutations");
assert.ok(editor.includes("editorWriteQueue"), "editor writes must be serialized");
assert.ok(editor.includes("detectedSitesSmiles"), "site detection must be bound to the inspected SMILES");
assert.ok(main.includes("requestToken !== S.propsRequestSeq"), "same-SMILES goal changes must invalidate in-flight property work");
assert.ok(main.includes("if (S.pendingPropsSmiles)"), "obsolete property work must not clear a newer result");
assert.ok(main.includes("releaseObsoleteOperationOverlay();"), "editor polling must release an obsolete operation overlay");
assert.ok(uiManager.includes("function escText"), "HTML text must use text escaping");
assert.ok(uiManager.includes("function escInlineJs"), "onclick arguments must use inline-JS escaping");
assert.ok(properties.includes("—"), "unavailable properties must render as an em dash");
assert.ok(properties.includes('propertyStatus[key] === "unavailable"'), "backend unavailable status must override numeric placeholders");
assert.ok(properties.includes("UI.escText(item.label || \"\")"), "optimization goal labels must be escaped before HTML insertion");
assert.ok(!properties.includes("<span>\" + item.label + \"</span>"), "optimization goal labels must not be inserted raw");
assert.ok(!properties.includes("p.logp ?? 0"), "missing LogP must not be rendered as zero");
assert.ok(!properties.includes("p.qed ?? 0"), "missing QED must not be rendered as zero");
assert.ok(!editor.includes("candidateScore"), "candidate board must not use a fixed weighted score");
assert.ok(!editor.includes("candidates.sort"), "candidate order must remain generation order");
assert.ok(history.includes("props: props || {}"), "history must preserve unavailable properties without coercing them to zero");

console.log("molecular design frontend contract passed");
