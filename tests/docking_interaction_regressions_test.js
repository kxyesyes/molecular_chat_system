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
assert.match(template, /docking\/interactions\.js\?v=20261012-interaction-analysis-v2/);
assert.match(source, /interactionAnalysisCache/);

const shapes = [];
const context = vm.createContext({
  viewer: {
    addCylinder: (shape) => shapes.push(shape),
    render: () => {},
  },
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
assert.equal(JSON.stringify(shapes[0].start), JSON.stringify({x: 1, y: 2, z: 3}));
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
  viewer: { addCylinder() {}, render() {} },
  interactionsState: {},
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
  console.log("docking interaction fail-closed checks passed");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

