"use strict";

const assert = require("node:assert/strict");
const { test } = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../src/web/static/js/reverse_target/results_renderer.js"), "utf8");
const viewerSource = fs.readFileSync(path.join(__dirname, "../src/web/static/js/reverse_target/viewer_3d.js"), "utf8");

test("target links distinguish database records from searches and omit legacy guessed links", () => {
  const renderer = vm.runInNewContext(source + "\nRtResults;", { window: {} });
  const render = fields => renderer._test.singleResultRow({target_name: "Target", ...fields}, 0, false);
  const direct = render({chembl_search_url: "https://www.ebi.ac.uk/chembl/web_components/explore/target/CHEMBL203",
    uniprot_search_url: "https://www.uniprot.org/uniprotkb/P00533/entry"});
  assert.match(direct, />ChEMBL 条目</);
  assert.match(direct, />UniProt 条目</);
  const search = render({chembl_search_url: "https://www.ebi.ac.uk/chembl/web_components/search_results/Target",
    uniprot_search_url: "https://www.uniprot.org/uniprotkb?query=%22Target%22"});
  assert.match(search, />搜索 ChEMBL</);
  assert.match(search, />搜索 UniProt</);
  const legacy = render({chembl_search_url: "https://www.ebi.ac.uk/chembl/target_report_card/Target/",
    uniprot_search_url: "https://www.uniprot.org/uniprotkb?query=Target+AND+organism_id:9606"});
  assert.doesNotMatch(legacy, /target_report_card|organism_id:9606/);
});

test("ranked neighbor rows preserve assay precision, relation and reported units", () => {
  const renderer = vm.runInNewContext(source + "\nRtResults;", { window: {} });
  const render = fields => renderer._test.singleResultRow({ standard_type: "IC50", final_similarity: 0.98, ...fields }, 0, false);
  assert.match(render({ standard_value: 0.00001, standard_relation: "<", standard_units: "μM" }), /&lt; 0.00001 μM/);
  assert.match(render({ standard_value: 0 }), />0[ （(]+单位未提供/);
  assert.match(render({ measurement_status: "unit_verification_required", standard_value: null }), /rt-activity-value">单位待核验</);
  for (const value of [null, "", undefined]) {
    const html = render({ standard_value: value });
    assert.match(html, /rt-activity-value">未提供</);
    assert.doesNotMatch(html, /[0-9] nM|高可信|中可信/);
  }
});

test("similar molecule modal uses the source assay units, including missing metadata", async () => {
  const fields = new Map();
  function element(id) {
    if (!fields.has(id)) fields.set(id, {
      classList: { add() {} }, removeAttribute() {}, addEventListener() {}, replaceChildren() {},
      querySelector: selector => element(id + selector),
    });
    return fields.get(id);
  }
  const molecules = [
    { standard_value: 2, standard_units: "μM", standard_relation: ">" },
    { standard_value: null, standard_units: "nM" },
    { standard_value: null, measurement_status: "unit_verification_required" },
  ];
  const requested = [];
  const context = { window: { location: new URL("http://test/"), currentQuerySmiles: "OLD" }, URL, AbortController,
    document: { createElement: () => element("panel"), getElementById: element },
    RtApi: { fetchSimilarMolecules: async query => { requested.push(query); return {results: molecules}; },
      smilesImageUrl: query => "/image?query=" + query },
  };
  const renderer = vm.runInNewContext(source + "\nRtResults;", context);
  await renderer.showSimilarMolecules("target", "CCO");
  assert.deepEqual(requested, ["CCO"]);
  assert.equal(element('panel[data-field="query-smiles"]').textContent, "CCO");
  assert.match(element('panel[data-field="query-image"]').src, /query=CCO$/);
  assert.equal(element('panel[data-molecule-index="0"][data-field="standard-value"]').textContent, "> 2 μM");
  assert.equal(element('panel[data-molecule-index="1"][data-field="standard-value"]').textContent, "未提供");
  assert.equal(element('panel[data-molecule-index="2"][data-field="standard-value"]').textContent, "单位待核验");
});

test("closing or replacing a detail modal retires pending errors and cancels its request", async () => {
  const requests = [], modal = {classList: {add() {}, remove() {}}}, body = {};
  const context = { window: {}, AbortController,
    document: {getElementById: id => id === "similarModal" ? modal : id === "modalBody" ? body : {}},
    RtApi: {fetchSimilarMolecules: (query, target, threshold, limit, signal) =>
      new Promise((resolve, reject) => requests.push({signal, resolve, reject}))},
  };
  const renderer = vm.runInNewContext(source + "\nRtResults;", context);
  const first = renderer.showSimilarMolecules("1", "C");
  const second = renderer.showSimilarMolecules("2", "CC");
  requests[0].reject(new Error("first closed"));
  await first;
  assert.equal(body.textContent, undefined);
  renderer.closeSimilarModal();
  assert.equal(requests[1].signal.aborted, true);
  requests[1].reject(new Error("second closed"));
  await second;
  assert.equal(body.textContent, undefined);
});

test("batch row details fetch with their own query, threshold and species", async () => {
  let click, url;
  const row = {querySelector: () => ({addEventListener(name, fn) { click = fn; }})};
  const element = {querySelector: () => row, classList: {add() {}}, style: {}};
  const api = fs.readFileSync(path.join(__dirname, "../src/web/static/js/reverse_target/api_client.js"), "utf8");
  const context = { URLSearchParams, AbortController, RT_CONFIG: {API: {SIMILAR_MOLECULES: "/similar"}},
    window: {lastThreshold: 0.8, lastOrganismFilter: "Human", lastReverseTargetSummary: {prefilter_threshold: 0.4}},
    document: {getElementById: () => element, querySelector: () => element},
    fetch: async value => { url = value; return {ok: true, json: async () => ({results: []})}; },
  };
  const renderer = vm.runInNewContext(api + "\n" + source + "\nRtResults;", context);
  renderer.displayBatchResults([{success: true, query_smiles: "CCN", targets: [{target_name: "B"}]}],
    {threshold: 0, organismFilter: ""});
  click();
  const params = new URL(url, "http://test").searchParams;
  assert.equal(params.get("smiles"), "CCN");
  assert.equal(params.get("threshold"), "0");
  assert.equal(params.has("organism_filter"), false);
});

test("CSV export carries source relation and unit beside the unchanged raw assay value", async () => {
  let blob;
  const ui = fs.readFileSync(path.join(__dirname, "../src/web/static/js/reverse_target/ui_manager.js"), "utf8");
  const context = { Blob, URL: { createObjectURL(value) { blob = value; }, revokeObjectURL() {} },
    document: { createElement: () => ({ click() {}, remove() {} }), body: { appendChild() {} } },
    window: { currentSingleResults: [
      { target_name: "T", standard_relation: "<", standard_units: "μM", standard_value: 0 },
      { target_name: "U", standard_value: null, measurement_status: "unit_verification_required" },
    ] },
  };
  vm.runInNewContext(ui + "\nRtUI.exportData();", context);
  const [header, record, unresolved] = (await blob.text()).split("\n").map(row => row.split(","));
  for (const [key, value] of [["standard_value", "0"], ["standard_relation", "<"], ["standard_units", "μM"]]) {
    assert.equal(record[header.indexOf(key)], JSON.stringify(value));
  }
  assert.equal(unresolved[header.indexOf("measurement_status")], '"unit_verification_required"');
  assert.equal(unresolved[header.indexOf("standard_value")], '""');
});

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
