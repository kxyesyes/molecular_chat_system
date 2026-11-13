"use strict";
const assert = require("node:assert/strict");
const {readFileSync} = require("node:fs");
const vm = require("node:vm");
const test = require("node:test");

test("selecting a detected cavity supplies its exact Vina box; stale detections are ignored", async () => {
  const protein = new File(["ATOM"], "sample.pdb");
  const fields = new Map();
  const events = [];
  let ready;
  const document = {
    addEventListener(name, fn) { if (name === "DOMContentLoaded") ready = fn; },
    getElementById(id) {
      if (!fields.has(id)) fields.set(id, { value: "", files: [], options: [],
        replaceChildren(...options) { this.options = options; this.value = ""; },
        add(option) { this.options.push(option); },
        addEventListener(name, fn) { this[name] = fn; }
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
  ready();
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
  assert.equal(select.value, "0", "a loaded receptor defaults to its largest candidate");
  const params = ctx.getDockingParams();
  assert.deepEqual([params.center_x, params.center_y, params.center_z], [2, -4, 6]);
  assert.deepEqual([params.size_x, params.size_y, params.size_z], [15, 18, 21]);
  assert.equal(params.manual_center, "true");
  assert.deepEqual(events, ["protein", "box"]);
  let resolve;
  ctx.fetch = () => new Promise(r => { resolve = r; });
  const editing = ctx.detectDockingCavities(button);
  const size = document.getElementById("size_x");
  size.value = "25";
  size.input();
  resolve(response);
  await editing;
  assert.equal(select.value, "");
  assert.equal(ctx.getDockingParams().size_x, 25);
  const pending = ctx.detectDockingCavities(button);
  input.files = [];
  ctx.clearDockingCavities();
  resolve(response);
  await pending;
  assert.deepEqual(select.options.map(option => option.value), [""]);
  assert.equal(button.disabled, false);
});

test("restoring the viewer theme preserves the separate colors of overlaid Vina poses", async () => {
  const atoms = [];
  const viewer = {
    clear() {}, zoomTo() {}, render() {},
    addModel() {
      const model = {setStyle(_selection, style) { this.style = style; }};
      atoms.push(model);
      return model;
    },
  };
  const through = new Promise(resolve => {
    const ctx = {window: {}, viewer, onStyleChange() {}, currentProteinData: null, console,
      AppState: {currentStyle: "gray"}, setTimeout: fn => { fn(); resolve(); },
      getCurrentJobId: () => "job", document: {getElementById: id => id === "overlay-count" ? {value: "2"} : null},
      StyleManager: {apply() { atoms.forEach(model => model.setStyle({}, {})); }},
      fetch: async () => ({ok: true, text: async () => "pose"}),
    };
    vm.runInNewContext(readFileSync("src/web/static/js/docking/ui_manager.js", "utf8"), ctx);
    ctx.overlayTopN();
  });
  await through;
  assert.deepEqual(atoms.map(model => model.style.stick?.colorscheme), ["greenCarbon", "blueCarbon"]);
});
