"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("src/web/static/js/docking/ui_manager.js", "utf8");
const tableRenderStart = source.indexOf("function renderDockingResultsTable");
assert.notEqual(tableRenderStart, -1);
assert.match(source.slice(tableRenderStart, tableRenderStart + 7000), /scrollLeft\s*=\s*0/);

const viewPoseStart = source.indexOf("function viewPose(poseNumber)");
assert.notEqual(viewPoseStart, -1);
assert.match(
  source.slice(viewPoseStart, viewPoseStart + 900),
  /poseSelect\.value\s*=\s*String\(poseNumber\)/,
);

function harness() {
  const nodes = new Map();
  const node = () => ({
    style: {},
    disabled: false,
    innerHTML: "",
    textContent: "",
    children: [],
    classList: { add() {}, remove() {}, contains() { return false; } },
    setAttribute() {},
    removeAttribute() {},
    appendChild(child) { this.children.push(child); },
    replaceChildren(...children) { this.children = children; },
    querySelectorAll() { return []; },
  });
  const get = (id) => {
    if (!nodes.has(id)) nodes.set(id, node());
    return nodes.get(id);
  };
  const context = {
    window: { MedChatSafeRender: { escapeHtml: String, escapeInlineJsString: String } },
    document: {
      getElementById: get,
      querySelector: () => node(),
      querySelectorAll: () => [],
      createElement: node,
    },
    setTimeout: () => 1,
    clearTimeout() {},
    console,
    AppState: { dockingMode: "single", currentJobId: null },
    Utils: { showToast() {} },
    onStyleChange() {},
    alert() {},
  };
  vm.createContext(context);
  vm.runInContext(source, context);
  return { context, get };
}

const rows = [
  { pose: 1, binding_energy: -8.4, ligand_efficiency: null, rmsd_lb: 0, rmsd_ub: 0 },
  { pose: 2, binding_energy: -6.2, ligand_efficiency: null, rmsd_lb: 0.9, rmsd_ub: 2.7 },
];
const { context, get } = harness();

assert.equal(typeof context.renderDockingResultsTable, "function");
context.renderDockingResultsTable(
  { job_id: "job-1", total_poses: 2, best_pose: { pose: 1 }, results: rows },
  { restored: false },
);
assert.match(get("results-content").innerHTML, /结构编号/);
assert.match(get("results-content").innerHTML, /-8\.4/);
assert.match(get("results-content").innerHTML, /2\.7/);
assert.match(get("results-content").innerHTML, /viewPose\(2\)/);

context.renderDockingResultsTable(
  { job_id: "job-1", total_poses: 1, results: [{ pose: 1, binding_energy: null, rmsd_lb: null, rmsd_ub: null, ligand_efficiency: null }] },
  { restored: true },
);
assert.match(get("results-content").innerHTML, /未计算/);
assert.doesNotMatch(get("results-content").innerHTML, /0\.0/);
assert.doesNotMatch(get("results-content").innerHTML, /viewPose\(/);

console.log("docking result table UI contract should be implemented");
