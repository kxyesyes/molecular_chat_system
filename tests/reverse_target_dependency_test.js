const fs = require('fs');
const path = require('path');
const assert = require('assert');

const template = fs.readFileSync(
  path.join(__dirname, '..', 'src', 'web', 'templates', 'reverse_target.html'),
  'utf8',
);

assert.match(
  template,
  /<script\s+async\s+src="https:\/\/code\.jquery\.com\/jquery-3\.6\.0\.min\.js"/,
  'jQuery must load asynchronously so it cannot block the local reverse-target scripts',
);
assert.match(
  template,
  /<script\s+async\s+src="https:\/\/3dmol\.org\/build\/3Dmol-min\.js"/,
  '3Dmol must load asynchronously so it cannot block the local reverse-target scripts',
);
assert.match(
  template,
  /<script\s+src="\/static\/js\/reverse_target\/main\.js\?v=20261015-numeric-validation-v3"><\/script>/,
  'the local reverse-target entry point must invalidate validation changes',
);
assert.match(
  template,
  /<script\s+src="\/static\/js\/reverse_target\/ui_manager\.js\?v=20261014-batch-input-state-v1"><\/script>/,
  'the reverse-target UI manager cache must invalidate batch file-state changes',
);

const main = fs.readFileSync(
  path.join(__dirname, '..', 'src', 'web', 'static', 'js', 'reverse_target', 'main.js'),
  'utf8',
);
const uiManager = fs.readFileSync(
  path.join(__dirname, '..', 'src', 'web', 'static', 'js', 'reverse_target', 'ui_manager.js'),
  'utf8',
);
const viewer = fs.readFileSync(
  path.join(__dirname, '..', 'src', 'web', 'static', 'js', 'reverse_target', 'viewer_3d.js'),
  'utf8',
);
assert.match(
  main,
  /window\.applyReverseTargetPreset\s*=\s*applyReverseTargetPreset/,
  'preset handlers must be explicitly exposed for inline HTML event attributes',
);
assert.match(
  main,
  /window\.switchTab\s*=\s*switchTab/,
  'tab handlers must be explicitly exposed for inline HTML event attributes',
);
assert.match(template, /class="preset-btn active" data-mode="broad"/, 'preset buttons must expose a data mode for delegated binding');
assert.match(template, /class="tab" data-tab="batch"/, 'tabs must expose a data tab for delegated binding');
assert.match(template, /<form id="predictForm" novalidate>/, 'single prediction must use explicit UI validation instead of a stale native required bubble');
assert.match(template, /<form id="batchForm" novalidate>/, 'batch prediction must use explicit file validation');
assert.doesNotMatch(template, /id="batchFile"[^>]*required/, 'hidden batch file input must not block the submit handler before explicit validation');
assert.match(template, /id="batchFile"[^>]*accept="\.csv,\.txt"/, 'batch upload choices must match the backend parser contract');
assert.match(main, /querySelectorAll\("\.preset-btn"\)/, 'preset buttons must have a direct event binding');
assert.match(main, /querySelectorAll\("\.tab"\)/, 'tabs must have a direct event binding');
assert.match(main, /请输入 SMILES/, 'empty single prediction must show an explicit validation message');
assert.match(main, /请先上传批量分子文件/, 'empty batch prediction must show an explicit validation message');
assert.match(uiManager, /clearError\(\)/, 'selecting a batch file must clear an earlier validation error');
assert.match(
  main,
  /classList\.toggle\([\s\S]*?button\.dataset\.mode\s*===\s*mode/,
  'preset active state must use the data-mode attribute',
);
assert.doesNotMatch(
  uiManager,
  /event\.currentTarget/,
  'tab switching must not depend on the legacy global event object',
);
assert.match(
  template,
  /onload="markReverseTarget3D\(true\)"/,
  'the asynchronous 3Dmol dependency must expose a readiness callback',
);
assert.match(
  viewer,
  /window\.reverseTargetDependencyReady/,
  '3D viewer opening must wait for asynchronous 3Dmol readiness',
);
assert.match(viewer, /await _ensureViewer\(\)/, '3D viewer opening must await dependency initialization');

console.log('reverse target dependency contract passed');
