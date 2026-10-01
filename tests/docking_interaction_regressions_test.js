"use strict";

// Regression probes for the current viewer-side interaction implementation.
// These tests intentionally describe scientific safety invariants, not a new
// chemistry implementation. They are expected to fail on PR #117 until the
// backend/mature-analysis replacement is wired in.

const assert = require("node:assert/strict");
const geometry = require("../src/web/static/js/docking/interaction_geometry.js");

function atom(elem, x, y, z, extra = {}) {
  return { elem, x, y, z, ...extra };
}

function test(name, fn) {
  try {
    fn();
    process.stdout.write(`ok - ${name}\n`);
  } catch (error) {
    process.stderr.write(`not ok - ${name}\n${error.stack}\n`);
    process.exitCode = 1;
  }
}

test("does not report a protein-internal hydrogen bond as a ligand interaction", () => {
  const donor = atom("N", 0, 0, 0, {
    hydrogens: [atom("H", 1, 0, 0)],
    resn: "ALA",
    resi: "1",
  });
  const acceptor = atom("O", 3, 0, 0, { resn: "ALA", resi: "2" });
  const result = geometry.detectHydrogenBonds([donor, acceptor], []);
  assert.equal(result.interactions.length, 0);
});

test("does not infer an untyped amide nitrogen to be an acceptor", () => {
  const donor = atom("N", 0, 0, 0, {
    hydrogens: [atom("H", 1, 0, 0)],
    resn: "LIG",
    resi: "1",
  });
  const amideNitrogen = atom("N", 3, 0, 0, { resn: "PRO", resi: "10", atom: "N" });
  const result = geometry.detectHydrogenBonds([], [donor, amideNitrogen]);
  assert.equal(result.interactions.length, 0);
});

test("rejects non-finite coordinates instead of emitting NaN hydrophobic distances", () => {
  const protein = atom("C", Number.NaN, 0, 0, { resn: "LEU", resi: "1" });
  const ligand = atom("C", 4, 0, 0, { resn: "LIG", resi: "1" });
  const result = geometry.detectHydrophobicContacts([protein], [ligand]);
  assert.equal(result.interactions.length, 0);
});

test("does not classify a laterally separated pair of coplanar rings as pi stacking", () => {
  const result = geometry.classifyPiPiInteraction(
    { center: atom("C", 0, 0, 0), normal: { x: 0, y: 0, z: 1 } },
    { center: atom("C", 5, 0, 0), normal: { x: 0, y: 0, z: 1 } },
  );
  assert.equal(result, null);
});

test("does not claim hydrophobic evidence when protein bond topology is unavailable", () => {
  const protein = atom("C", 0, 0, 0, { resn: "LEU", resi: "1", bonds: [0, 1] });
  const ligand = atom("C", 4, 0, 0, { resn: "LIG", resi: "1", bonds: [0, 1] });
  const result = geometry.detectHydrophobicContacts([protein], [ligand]);
  assert.equal(result.interactions.length, 0);
});

