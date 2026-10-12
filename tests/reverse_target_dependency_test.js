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
  /<script\s+src="\/static\/js\/reverse_target\/main\.js\?v=20261013"><\/script>/,
  'the local reverse-target entry point must execute during parsing',
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
assert.match(main, /querySelectorAll\("\.preset-btn"\)/, 'preset buttons must have a direct event binding');
assert.match(main, /querySelectorAll\("\.tab"\)/, 'tabs must have a direct event binding');
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
