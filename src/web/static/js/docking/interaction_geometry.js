/*
 * Small, dependency-free geometry layer for the docking viewer.
 *
 * This is deliberately conservative: without explicit hydrogens, bond
 * orders, or protonation metadata it reports no confirmed hydrogen bond and
 * returns a warning instead of guessing from N/O distance alone.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.InteractionGeometry = factory();
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const PROTEIN_HYDROPHOBIC_RESIDUES = new Set([
    "ALA",
    "VAL",
    "LEU",
    "ILE",
    "MET",
    "PHE",
    "TRP",
    "TYR",
    "PRO",
  ]);

  function finitePoint(atom) {
    return (
      atom &&
      [atom.x, atom.y, atom.z].every(
        (value) => typeof value === "number" && Number.isFinite(value),
      )
    );
  }

  function distance(left, right) {
    return Math.hypot(left.x - right.x, left.y - right.y, left.z - right.z);
  }

  function dot(left, right) {
    return left.x * right.x + left.y * right.y + left.z * right.z;
  }

  function norm(vector) {
    return Math.hypot(vector.x, vector.y, vector.z);
  }

  function angleAt(donor, hydrogen, acceptor) {
    const first = {
      x: donor.x - hydrogen.x,
      y: donor.y - hydrogen.y,
      z: donor.z - hydrogen.z,
    };
    const second = {
      x: acceptor.x - hydrogen.x,
      y: acceptor.y - hydrogen.y,
      z: acceptor.z - hydrogen.z,
    };
    const denominator = norm(first) * norm(second);
    if (!denominator) return null;
    const cosine = Math.max(-1, Math.min(1, dot(first, second) / denominator));
    return (Math.acos(cosine) * 180) / Math.PI;
  }

  function attachedHydrogens(atom) {
    const result = [];
    if (Array.isArray(atom && atom.hydrogens)) result.push(...atom.hydrogens);
    if (Array.isArray(atom && atom.bonds)) {
      atom.bonds.forEach((bond) => {
        const neighbor = bond && bond.atom && typeof bond.atom === "object"
          ? bond.atom
          : bond;
        if (neighbor && String(neighbor.elem || "").toUpperCase() === "H") {
          result.push(neighbor);
        }
      });
    }
    return result.filter(finitePoint);
  }

  function isHydrogenBondDonor(atom) {
    return (
      atom &&
      ["N", "O", "S"].includes(String(atom.elem || "").toUpperCase()) &&
      attachedHydrogens(atom).length > 0
    );
  }

  function isHydrogenBondAcceptor(atom) {
    if (!atom || !finitePoint(atom)) return false;
    if (atom.acceptor === false || Number(atom.charge) > 0) return false;
    if (atom.acceptor === true) return true;
    return ["N", "O", "S"].includes(String(atom.elem || "").toUpperCase());
  }

  function residueKey(atom) {
    return `${atom.resn || "?"}:${atom.resi || "?"}:${atom.chain || ""}`;
  }

  function detectHydrogenBonds(proteinAtoms, ligandAtoms) {
    const interactions = [];
    const warnings = [];
    const donors = proteinAtoms.filter(isHydrogenBondDonor).concat(
      ligandAtoms.filter(isHydrogenBondDonor),
    );
    const acceptors = proteinAtoms.filter(isHydrogenBondAcceptor).concat(
      ligandAtoms.filter(isHydrogenBondAcceptor),
    );
    const possibleDonors = proteinAtoms.concat(ligandAtoms).filter((atom) =>
      atom && ["N", "O", "S"].includes(String(atom.elem || "").toUpperCase()),
    );
    if (possibleDonors.some((atom) => !isHydrogenBondDonor(atom))) {
      warnings.push("donor_hydrogen_coordinates_missing");
    }
    donors.forEach((donor) => {
      attachedHydrogens(donor).forEach((hydrogen) => {
        acceptors.forEach((acceptor) => {
          if (donor === acceptor || !finitePoint(acceptor)) return;
          const donorAcceptorDistance = distance(donor, acceptor);
          const angle = angleAt(donor, hydrogen, acceptor);
          if (
            donorAcceptorDistance >= 2.4 &&
            donorAcceptorDistance <= 3.6 &&
            angle !== null &&
            angle >= 120
          ) {
            interactions.push({
              type: "hydrogen_bond",
              donor,
              acceptor,
              donor_residue: residueKey(donor),
              acceptor_residue: residueKey(acceptor),
              distance: donorAcceptorDistance,
              angle,
              quality: "geometry_supported",
              method: "donor_hydrogen_distance_angle",
            });
          }
        });
      });
    });
    return { interactions, warnings: [...new Set(warnings)] };
  }

  function classifyPiPiInteraction(proteinRing, ligandRing) {
    if (!proteinRing || !ligandRing || !finitePoint(proteinRing.center) || !finitePoint(ligandRing.center)) {
      return null;
    }
    const firstNormal = proteinRing.normal;
    const secondNormal = ligandRing.normal;
    if (!firstNormal || !secondNormal || !norm(firstNormal) || !norm(secondNormal)) return null;
    const separation = distance(proteinRing.center, ligandRing.center);
    if (separation < 3.3 || separation > 5.5) return null;
    const cosine = Math.max(
      -1,
      Math.min(1, Math.abs(dot(firstNormal, secondNormal) / (norm(firstNormal) * norm(secondNormal)))),
    );
    const angle = (Math.acos(cosine) * 180) / Math.PI;
    let type = null;
    if (angle <= 30) type = "parallel_displaced";
    if (angle >= 60 && angle <= 90) type = "t_shaped";
    if (!type) return null;
    return {
      type,
      distance: separation,
      plane_angle: angle,
      method: "aromatic_ring_centroid_and_plane_geometry",
      quality: "geometry_supported",
    };
  }

  function isNonPolarLigandCarbon(atom) {
    if (!atom || String(atom.elem || "").toUpperCase() !== "C") return false;
    return !Array.isArray(atom.bonds) || !atom.bonds.some((bond) =>
      ["N", "O", "S"].includes(String((bond && bond.elem) || "").toUpperCase()),
    );
  }

  function detectHydrophobicContacts(proteinAtoms, ligandAtoms) {
    const interactions = [];
    const byResidue = new Map();
    proteinAtoms
      .filter((atom) =>
        PROTEIN_HYDROPHOBIC_RESIDUES.has(String(atom.resn || "").toUpperCase()) &&
        isNonPolarLigandCarbon(atom),
      )
      .forEach((proteinAtom) => {
        ligandAtoms.filter(isNonPolarLigandCarbon).forEach((ligandAtom) => {
          const separation = distance(proteinAtom, ligandAtom);
          if (separation < 3.4 || separation > 4.8) return;
          const key = residueKey(proteinAtom);
          const current = byResidue.get(key);
          if (!current || separation < current.distance) {
            byResidue.set(key, { proteinAtom, ligandAtom, distance: separation });
          }
        });
      });
    byResidue.forEach(({ proteinAtom, ligandAtom, distance: separation }) => {
      interactions.push({
        type: "hydrophobic_contact",
        protein_atom: proteinAtom,
        ligand_atom: ligandAtom,
        residue: residueKey(proteinAtom),
        distance: separation,
        quality: "heuristic",
        method: "hydrophobic_residue_carbon_contact",
      });
    });
    return {
      interactions,
      warnings: interactions.length ? ["solvent_exposure_not_assessed"] : [],
    };
  }

  return {
    detectHydrogenBonds,
    classifyPiPiInteraction,
    detectHydrophobicContacts,
    attachedHydrogens,
  };
});
