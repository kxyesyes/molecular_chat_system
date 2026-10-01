const assert = require("assert");
const Geometry = require("../src/web/static/js/docking/interaction_geometry.js");

const atom = (elem, x, y, z, extra = {}) => ({ elem, x, y, z, ...extra });

const donor = atom("N", 0, 0, 0, {
  atom: "N",
  hydrogens: [atom("H", 1, 0, 0)],
});
const acceptor = atom("O", 3, 0, 0, { atom: "O", acceptor: true });

const hbond = Geometry.detectHydrogenBonds([donor], [acceptor]);
assert.strictEqual(hbond.interactions.length, 1);
assert.strictEqual(hbond.interactions[0].distance.toFixed(2), "3.00");
assert.strictEqual(hbond.interactions[0].angle.toFixed(0), "180");
assert.strictEqual(hbond.interactions[0].quality, "geometry_supported");

const missingHydrogen = Geometry.detectHydrogenBonds(
  [atom("N", 0, 0, 0, { atom: "N" })],
  [acceptor],
);
assert.strictEqual(missingHydrogen.interactions.length, 0);
assert.ok(missingHydrogen.warnings.includes("donor_hydrogen_coordinates_missing"));

const ring = (center, normal) => ({
  center,
  normal,
  atoms: [atom("C", center.x, center.y, center.z)],
});
const parallel = Geometry.classifyPiPiInteraction(
  ring({ x: 0, y: 0, z: 0 }, { x: 0, y: 0, z: 1 }),
  ring({ x: 0, y: 0, z: 4, }, { x: 0, y: 0, z: 1 }),
);
assert.strictEqual(parallel.type, "parallel_displaced");

const tStacked = Geometry.classifyPiPiInteraction(
  ring({ x: 0, y: 0, z: 0 }, { x: 0, y: 0, z: 1 }),
  ring({ x: 0, y: 0, z: 4, }, { x: 1, y: 0, z: 0 }),
);
assert.strictEqual(tStacked.type, "t_shaped");

const tooFar = Geometry.classifyPiPiInteraction(
  ring({ x: 0, y: 0, z: 0 }, { x: 0, y: 0, z: 1 }),
  ring({ x: 0, y: 0, z: 7, }, { x: 0, y: 0, z: 1 }),
);
assert.strictEqual(tooFar, null);

const hydrophobic = Geometry.detectHydrophobicContacts(
  [atom("C", 0, 0, 0, { resn: "LEU", atom: "CD1" })],
  [atom("C", 4, 0, 0, { resn: "LIG", atom: "C1" })],
);
assert.strictEqual(hydrophobic.interactions.length, 1);
assert.strictEqual(hydrophobic.interactions[0].method, "hydrophobic_residue_carbon_contact");

const polar = Geometry.detectHydrophobicContacts(
  [atom("C", 0, 0, 0, { resn: "ASN", atom: "CG" })],
  [atom("C", 4, 0, 0, { resn: "LIG", atom: "C1" })],
);
assert.strictEqual(polar.interactions.length, 0);

console.log("docking interaction geometry checks passed");
