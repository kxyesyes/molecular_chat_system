"use strict";

const assert = require("node:assert/strict");
const { test } = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../src/web/static/js/reverse_target/results_renderer.js"), "utf8");
const viewerSource = fs.readFileSync(path.join(__dirname, "../src/web/static/js/reverse_target/viewer_3d.js"), "utf8");

test("query legend puts six server properties only in text nodes and preserves zero", () => {
  const writes = [];
  const fields = new Map();
  const payload = '<img src=x onerror="legend-xss">';
  let inserted = false;
  const legend = { querySelector(selector) {
    if (!fields.has(selector)) fields.set(selector, { textContent: "" });
    return fields.get(selector);
  }};
  const context = vm.createContext({
    window: { lastQuery3DPharmacophore: { properties: {
      MW: payload, LogP: payload, TPSA: payload, HBD: 0, HBA: payload, RotBonds: 0,
    } } },
    $: () => inserted ? legend : null,
    document: { querySelector: () => ({ insertAdjacentHTML(_position, html) {
      writes.push(html); inserted = true;
    } }) },
    renderPharmLegend: () => {},
  });
  const start = source.indexOf("  function _attachQueryLegend(");
  const end = source.indexOf("  function displayBatchResults(", start);
  assert.ok(start >= 0 && end > start);
  vm.runInContext(source.slice(start, end) + "\n_attachQueryLegend(true);", context);
  assert.equal(writes.length, 1);
  assert.ok(!writes[0].includes(payload));
  for (const name of ["mw", "logp", "tpsa", "hba"]) {
    assert.equal(fields.get(`[data-field="query-${name}"]`).textContent, payload);
  }
  for (const name of ["hbd", "rotbonds"]) {
    assert.equal(fields.get(`[data-field="query-${name}"]`).textContent, "0");
  }
});

for (const count of ['<img src=x onerror="count-xss">', -1, null, Infinity]) {
  test(`similar molecule count rejects ${String(count)}`, () => {
    const context = vm.createContext({ window: {}, document: {}, FEAT_META: {} });
    const renderer = vm.runInContext(source + "\nRtResults;", context);
    const html = renderer._test.singleResultRow({ target_name: "target", organism: "Human", similar_count: count }, 0, false);
    assert.ok(!html.includes('<img'));
    assert.match(html, /相似分子 未提供/);
  });
}

test("3D viewer keeps server properties, labels and coordinates out of HTML sinks", () => {
  assert.ok(viewerSource.includes("function _renderInfoCard"));
  assert.ok(viewerSource.includes("textContent"));
  assert.ok(!viewerSource.includes("${p.MW"));
  assert.ok(!viewerSource.includes("${p.LogP"));
  assert.ok(!viewerSource.includes("${f.label}"));
  assert.ok(!viewerSource.includes("${f.icon}"));
  assert.ok(!viewerSource.includes("onclick=\"RtViewer.focusOnFeature"));
  assert.ok(viewerSource.includes("addEventListener(\"click\""));
});
