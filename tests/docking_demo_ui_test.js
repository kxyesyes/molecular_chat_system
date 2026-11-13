"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const {File} = require("node:buffer");

const template = fs.readFileSync("src/web/templates/molecular_docking.html", "utf8");
const source = fs.readFileSync("src/web/static/js/docking/main.js", "utf8");
const uiSource = fs.readFileSync("src/web/static/js/docking/ui_manager.js", "utf8");

assert.match(template, /载入示例[：:]\s*曼氏血吸虫 PDE4A apo（6FG5）\+ 罗氟司特/);
assert.match(source, /已载入曼氏血吸虫 PDE4A apo（6FG5）和罗氟司特/);
assert.match(template, /docking\/ui_manager\.js\?v=20261112-docking-regions-v4/);
assert.match(template, /docking\/main\.js\?v=20261112-demo-regions-v3/);
assert.match(source, /async function loadDockingDemo\(\)/);
assert.match(source, /\/api\/docking\/demo\/pde4a-apo/);
assert.match(source, /PDE4A_apo_6FG5\.pdb/);
assert.doesNotMatch(source, /loadDockingDemo[\s\S]{0,240}startDocking\(/);

assert.match(uiSource, /function renderSelectedFiles\(input, display, files\)/);
assert.match(uiSource, /function loadProteinFile\(file\)[\s\S]{0,500}return new Promise/);
assert.match(source, /await loadProteinFile\(file\)/);
assert.doesNotMatch(source, /proteinInput\.dispatchEvent\(new Event\("change"/);

async function checkDemoInputs(ligandAvailable) {
  const events = [];
  const nodes = new Map();
  const document = {
    addEventListener() {},
    getElementById(id) {
      if (!nodes.has(id)) nodes.set(id, {
        files: [], value: "", click() {}, dispatchEvent(event) { events.push([id, event.type]); },
      });
      return nodes.get(id);
    },
  };
  const ligandBytes = fs.readFileSync("data/samples/roflumilast.pdbqt");
  const messages = [];
  const ctx = {
    document, File, Event,
    DataTransfer: class { files = []; items = {add: (file) => this.files.push(file)}; },
    fetch: async (url) => {
      if (url === "/api/docking/demo/pde4a-apo") return new Response("HEADER fixture");
      assert.equal(url, "/api/docking/demo/roflumilast.pdbqt");
      return ligandAvailable ? new Response(ligandBytes) : new Response("{}", {status: 404});
    },
    loadProteinFile: async () => {},
    Utils: {showToast: (message, type) => messages.push(type)},
  };
  vm.runInNewContext(source, ctx);
  const smiles = document.getElementById("smiles-input");
  smiles.value = "CCO";
  const searchFields = ["exhaustiveness", "num_modes", "energy_range"].map(id => document.getElementById(id));
  const originalValues = ["8", "10", "5"];
  searchFields.forEach((field, i) => { field.value = originalValues[i]; });
  await ctx.loadDockingDemo();
  if (!ligandAvailable) {
    assert.deepEqual(searchFields.map(field => field.value), originalValues);
    assert.deepEqual(messages, ["warning"]);
    assert.equal(document.getElementById("protein-file").files.length, 0);
    assert.equal(smiles.value, "CCO");
    return;
  }
  const [file] = document.getElementById("ligand-file").files;
  assert.deepEqual(searchFields.map(field => field.value), ["24", "20", "3"]);
  assert.equal(file.name, "roflumilast.pdbqt");
  assert.deepEqual(Buffer.from(await file.arrayBuffer()), ligandBytes);
  assert.equal(smiles.value, "");
  assert.deepEqual(events, [["smiles-input", "input"], ["ligand-file", "change"]]);
  assert.deepEqual(messages, ["success"]);
}

Promise.all([checkDemoInputs(true), checkDemoInputs(false)]).catch(error => {
  console.error(error);
  process.exitCode = 1;
});
