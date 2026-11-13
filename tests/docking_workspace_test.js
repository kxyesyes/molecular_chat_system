"use strict";
const assert = require("node:assert/strict");
const {readFileSync} = require("node:fs");
const vm = require("node:vm");
const test = require("node:test");

test("selecting a detected cavity supplies its exact Vina box; stale detections are ignored", async () => {
  const protein = new File(["ATOM"], "sample.pdb");
  const fields = new Map();
  const events = [];
  const document = {
    addEventListener() {},
    getElementById(id) {
      if (!fields.has(id)) fields.set(id, { value: "", files: [], options: [],
        replaceChildren(...options) { this.options = options; this.value = ""; },
        add(option) { this.options.push(option); }
      });
      return fields.get(id);
    },
  };
  const ctx = {document, FormData, Option: class {constructor(text, value) { Object.assign(this, {text, value}); }},
    protein, AppState: {}, dockingBoxVisible: false,
    toggleDockingBox: () => events.push("box"),
    window: {}, onStyleChange() {}, fetch: null};
  vm.createContext(ctx);
  vm.runInContext(readFileSync("src/web/static/js/docking/workspace.js", "utf8"), ctx);
  vm.runInContext(readFileSync("src/web/static/js/docking/ui_manager.js", "utf8"), ctx);
  vm.runInContext("loadedDockingProteinFile = protein", ctx);
  ctx.showProtein = () => events.push("protein");
  const input = document.getElementById("protein-file");
  const select = document.getElementById("cavity-choice");
  input.files = [protein];
  const response = {ok: true, json: async () => ({cavities: [
    {volume: 100, center: [2, -4, 6], size: [15, 18, 21]},
  ]})};
  ctx.fetch = async (url, init) => {
    assert.equal(url, "/api/docking/cavities");
    assert.equal(init.body.get("protein_file").name, protein.name);
    return response;
  };
  const button = {};
  await ctx.detectDockingCavities(button);
  select.value = "0";
  ctx.applyDockingCavity(select);
  const params = ctx.getDockingParams();
  assert.deepEqual([params.center_x, params.center_y, params.center_z], [2, -4, 6]);
  assert.deepEqual([params.size_x, params.size_y, params.size_z], [15, 18, 21]);
  assert.equal(params.manual_center, "true");
  assert.deepEqual(events, ["protein", "box"]);
  let resolve;
  ctx.fetch = () => new Promise(r => { resolve = r; });
  const pending = ctx.detectDockingCavities(button);
  input.files = [];
  ctx.clearDockingCavities();
  resolve(response);
  await pending;
  assert.deepEqual(select.options.map(option => option.value), [""]);
  assert.equal(button.disabled, false);
});
