"use strict";

// The browser must not perform chemistry classification from 3Dmol's partial
// atom objects. The backend adapter is the only source of interaction claims.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("src/web/static/js/docking/interactions.js", "utf8");
const template = fs.readFileSync("src/web/templates/molecular_docking.html", "utf8");

assert.match(source, /\/api\/docking\/interactions\//);
assert.match(source, /analysis\.status !== "success"/);
assert.match(source, /analysis\.warnings\?\.\[0\]/);
assert.match(source, /未显示推测结果/);
assert.match(source, /display_geometry/);
assert.match(source, /addCylinder/);
assert.doesNotMatch(source, /InteractionGeometry/);
assert.doesNotMatch(source, /selectedAtoms\(\{\}\)/);
assert.doesNotMatch(source, /selectedAtoms\(/);
assert.doesNotMatch(template, /docking\/interaction_geometry\.js/);
assert.match(template, /docking\/interactions\.js\?v=20261112-pose-contacts-v3/);
assert.match(source, /interactionAnalysisCache/);

function makeViewer() {
  return {shapes: [], addCylinder(spec) {
    const shape = {...spec, shapePosition: this.shapes.length};
    this.shapes.push(shape);
    return shape;
  }, removeShape(shape) { delete this.shapes[shape.shapePosition]; }, render() {}};
}
const context = vm.createContext({
  viewer: makeViewer(),
  console,
  setTimeout,
});
vm.runInContext(source, context);
assert.equal(
  vm.runInContext(
    `renderInteractionEvidence("hydrogen_bond", [{
      display_geometry: {
        protein: {x: 1, y: 2, z: 3},
        ligand: {x: 4, y: 5, z: 6},
      },
    }])`,
    context,
  ),
  1,
);
assert.equal(JSON.stringify(context.viewer.shapes[0].start), JSON.stringify({x: 1, y: 2, z: 3}));
assert.equal(
  vm.runInContext(
    `renderInteractionEvidence("hydrogen_bond", [{
      parent_atom_indices: {protein: [0], ligand: [0]},
    }])`,
    context,
  ),
  0,
);

const requests = [];
const requestContext = vm.createContext({
  viewer: makeViewer(),
  interactionsState: {hydrogenBonds: true},
  console,
  setTimeout,
  getCurrentJobId: () => "job-1",
  document: { getElementById: () => ({ value: "1" }) },
  Utils: { showToast() {} },
  fetch: async () => {
    requests.push("interaction");
    return {
      ok: true,
      json: async () => ({
        status: "success",
        interactions: [{
          type: "hydrogen_bond",
          display_geometry: {
            protein: {x: 1, y: 2, z: 3},
            ligand: {x: 4, y: 5, z: 6},
          },
        }],
      }),
    };
  },
});
vm.runInContext(source, requestContext);

async function main() {
  await requestContext.requestInteractionAnalysis("hydrogen_bond");
  await requestContext.requestInteractionAnalysis("hydrogen_bond");
  assert.equal(requests.length, 1);
  assert.equal(requestContext.viewer.shapes.filter(Boolean).length, 1);
  const contact = [{display_geometry: {protein: {x: 0,y: 1,z: 2}, ligand: {x: 3,y: 4,z: 5}}}];
  requestContext.renderInteractionEvidence("hydrophobic", contact);
  requestContext.renderInteractionEvidence("pi_pi", contact);
  const box = requestContext.viewer.addCylinder({color: "box"});
  requestContext.removeInteractions("hydrophobic");
  assert.deepEqual(requestContext.viewer.shapes.filter(Boolean).map(s => s.color), ["#16a3ff", "#a855f7", "box"]);
  requestContext.clearInteractionOverlays();
  assert.deepEqual(requestContext.viewer.shapes.filter(Boolean), [box]);
  const status = {textContent: ""};
  const unavailable = vm.createContext({
    interactionsState: {hydrogenBonds: true},
    viewer: {addCylinder() {}, render() {}}, console, setTimeout,
    getCurrentJobId: () => "job-2",
    document: {getElementById: (id) => id === "interaction-status" ? status : {value: "1"}},
    Utils: {showToast() {}},
    fetch: async () => ({ok: true, json: async () => ({status: "unavailable",
      warnings: ["protein_explicit_hydrogens_are_required_by_prolif"], interactions: []})}),
  });
  vm.runInContext(source, unavailable);
  await unavailable.requestInteractionAnalysis("hydrogen_bond");
  assert.match(status.textContent, /受体.*显式氢/);
  assert.match(status.textContent, /无法.*分析/);
  assert.doesNotMatch(status.textContent, /未报告|protein_explicit/);
  let finish;
  let viewSequence = 1;
  let drawn = 0;
  const late = vm.createContext({
    interactionsState: {hydrogenBonds: true, hydrophobicInteractions: true},
    viewer: {addCylinder() { drawn++; }, render() {}}, console, setTimeout,
    getCurrentJobId: () => "job-A",
    getDockingViewSequence: () => viewSequence,
    document: {getElementById: () => ({value: "1"})},
    Utils: {showToast() {}},
    fetch: () => new Promise((resolve) => { finish = resolve; }),
  });
  vm.runInContext(source, late);
  const pending = late.requestInteractionAnalysis("hydrogen_bond");
  viewSequence++;
  finish({ok: true, json: async () => ({status: "success", interactions: [{type: "hydrogen_bond", display_geometry: {protein: {x: 1,y: 2,z: 3}, ligand: {x: 4,y: 5,z: 6}}}]})});
  await pending;
  assert.equal(drawn, 0, "stale interaction cannot be drawn on a new view");
  late.document.getElementById = () => ({value: "2"});
  late.interactionsState.hydrophobicInteractions = true;
  const delayed = late.requestInteractionAnalysis("hydrophobic");
  late.interactionsState.hydrophobicInteractions = false;
  finish({ok: true, json: async () => ({status: "success", interactions: [{type: "hydrophobic",
    display_geometry: {protein: {x:1,y:2,z:3}, ligand: {x:4,y:5,z:6}}}]})});
  await delayed;
  assert.equal(drawn, 0, "a disabled overlay must not reappear after its analysis arrives");
  console.log("docking interaction fail-closed checks passed");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

