"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("src/web/static/js/docking/docking_box.js", "utf8");

function node(value = "0") {
  return { value, style: {}, textContent: "" };
}

const fields = new Map([
  ["center_x", node()],
  ["center_y", node()],
  ["center_z", node()],
  ["size_x", node("20")],
  ["size_y", node("20")],
  ["size_z", node("20")],
  ["toggle-box-btn", node()],
]);

const context = {
  console,
  setTimeout,
  CONFIG: {
    WATER_RESIDUES: [],
    PROTEIN_RESIDUES: ["CA"],
    NON_LIGAND_RESIDUES: [],
    LIGAND_MIN_HEAVY_ATOMS: 5,
  },
  Utils: {
    calculateCenter(atoms) {
      return {
        x: atoms.reduce((sum, atom) => sum + atom.x, 0) / atoms.length,
        y: atoms.reduce((sum, atom) => sum + atom.y, 0) / atoms.length,
        z: atoms.reduce((sum, atom) => sum + atom.z, 0) / atoms.length,
      };
    },
    showToast() {},
  },
  document: { getElementById: (id) => fields.get(id) },
  viewer: {
    selectedAtoms: () => [{ resn: "CA", x: 4, y: 5, z: 6 }],
    addCylinder() {},
    addSphere() {},
    addLabel() {},
    removeAllShapes() {},
    removeAllLabels() {},
    render() {},
  },
  currentProteinData: null,
  currentLigandData: null,
  currentComplexData: null,
  interactionsState: {
    hydrogenBonds: false,
    piPiInteractions: false,
    hydrophobicInteractions: false,
  },
  AppState: { dockingBoxCenterTouched: false },
};

vm.createContext(context);
vm.runInContext(source, context);
context.autoSetBoxToLigand(true);

assert.equal(context.AppState.dockingBoxCenterTouched, false);
assert.deepEqual(
  [fields.get("center_x").value, fields.get("center_y").value, fields.get("center_z").value],
  ["4.00", "5.00", "6.00"],
);

context.autoSetBoxToLigand();
assert.equal(context.AppState.dockingBoxCenterTouched, true);

console.log("docking box confirmation state checks passed");
