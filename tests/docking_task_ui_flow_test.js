const assert = require("node:assert/strict");
const fs = require("node:fs");

const source = fs.readFileSync("src/web/static/js/docking/ui_manager.js", "utf8");
const template = fs.readFileSync("src/web/templates/molecular_docking.html", "utf8");

assert.match(source, /\/api\/docking\/tasks/);
assert.match(source, /\/api\/tasks\//);
assert.match(source, /\/cancel/);
assert.match(source, /sessionStorage/);
assert.match(source, /record\.phase/);
assert.match(source, /record\.progress/);
assert.match(source, /dockingProgressPercent/);
assert.match(
  source,
  /if \(ligandFile && smilesInput\)[\s\S]{0,220}单配体模式只能选择配体文件或 SMILES/,
);
assert.match(
  source,
  /if \(ligandFiles\.length > 0 && batchSmiles\)[\s\S]{0,220}批量模式只能选择配体文件或多行 SMILES/,
);
assert.match(source, /numeric >= 0 && numeric <= 1/);
assert.doesNotMatch(source, /function startProgressSimulation/);
assert.doesNotMatch(source, /setInterval\(/);
assert.doesNotMatch(source, /elapsed >= 1\.5/);
assert.doesNotMatch(source, /elapsed >= 4\.0/);
assert.doesNotMatch(source, /elapsed >= 6\.5/);
assert.doesNotMatch(source, /elapsed >= 15\.0/);
assert.match(template, /id="cancel-docking-btn"/);
assert.match(template, /docking_box\.js\?v=20261012-box-confirmation-v1/);
assert.match(template, /ui_manager\.js\?v=20261027-durable-history-v8/);
const startDockingSource = source.slice(source.indexOf("function startDocking()"));
const centerGuard = startDockingSource.indexOf('if (!AppState.dockingBoxCenterTouched)');
const startButtonSetup = startDockingSource.indexOf('const startBtn = document.getElementById("start-btn")');
assert.ok(centerGuard >= 0 && centerGuard < startButtonSetup);
assert.match(source, /Utils\.showToast\("请先手动确认对接盒中心后再提交持久化任务", "warning"\)/);
const complexLoaderStart = source.indexOf("function loadComplexStructure(");
const complexLoaderEnd = source.indexOf("// 查看特定结构", complexLoaderStart);
const complexLoaderSource = source.slice(complexLoaderStart, complexLoaderEnd);
assert.doesNotMatch(complexLoaderSource, /loadMolecule\(currentProteinData, "protein"\)/);
assert.match(complexLoaderSource, /复合物结构加载失败，请重试/);

console.log("docking durable task UI checks passed");
