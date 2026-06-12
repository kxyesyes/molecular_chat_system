// ==================== 分子相互作用检测与显示 ====================

// 聚焦显示：配体 + 参与相互作用残基
function focusLigandAndResidues(residueKeySet, clearStyles) {
  try {
    residueKeySet.forEach((rk) => {
      const [resn, resi, chain] = rk.split(":");
      viewer.setStyle(
        { resn, resi: parseInt(resi), chain },
        { stick: { radius: 0.3, colorscheme: "blueCarbon" } },
      );
    });
    viewer.render();
  } catch (e) {
    console.warn("focusLigandAndResidues 渲染失败:", e);
  }
}

// 切换氢键显示
function toggleHydrogenBonds() {
  if (!viewer) {
    alert("请先加载分子结构");
    return;
  }
  interactionsState.hydrogenBonds = !interactionsState.hydrogenBonds;
  const btn = document.getElementById("show-hbonds-btn");
  if (interactionsState.hydrogenBonds) {
    btn.classList.add("active");
    displayHydrogenBonds();
  } else {
    btn.classList.remove("active");
    removeInteractions("hbonds");
  }
}

// 切换π-π相互作用显示
function togglePiPiInteractions() {
  if (!viewer) {
    alert("请先加载分子结构");
    return;
  }
  interactionsState.piPiInteractions = !interactionsState.piPiInteractions;
  const btn = document.getElementById("show-pi-btn");
  if (interactionsState.piPiInteractions) {
    btn.classList.add("active");
    displayPiPiInteractions();
  } else {
    btn.classList.remove("active");
    removeInteractions("pipi");
  }
}

// 切换疏水相互作用显示
function toggleHydrophobicInteractions() {
  if (!viewer) {
    alert("请先加载分子结构");
    return;
  }
  interactionsState.hydrophobicInteractions =
    !interactionsState.hydrophobicInteractions;
  const btn = document.getElementById("show-hydrophobic-btn");
  if (interactionsState.hydrophobicInteractions) {
    btn.classList.add("active");
    displayHydrophobicInteractions();
  } else {
    btn.classList.remove("active");
    removeInteractions("hydrophobic");
  }
}

// ============ 显示氢键相互作用 (青色虚线) ============
function displayHydrogenBonds() {
  try {
    if (
      !interactionsState.piPiInteractions &&
      !interactionsState.hydrophobicInteractions
    ) {
      viewer.removeAllShapes();
    }
    const atoms = viewer.selectedAtoms({});
    const ligandKeys = identifyLigandResidueKeys(atoms);
    const proteinAtoms = atoms.filter((a) => {
      const rn = (a.resn || "").toUpperCase();
      return (
        CONFIG.PROTEIN_RESIDUES.includes(rn) &&
        !CONFIG.WATER_RESIDUES.includes(rn)
      );
    });
    const ligandAtoms = atomsForResidueKeys(atoms, ligandKeys).filter(
      (a) => !CONFIG.WATER_RESIDUES.includes((a.resn || "").toUpperCase()),
    );

    const proteinNO = proteinAtoms.filter((a) => ["N", "O"].includes(a.elem));
    const ligandNO = ligandAtoms.filter((a) => ["N", "O"].includes(a.elem));

    let count = 0;
    const resSet = new Set();
    proteinNO.forEach((p) => {
      ligandNO.forEach((l) => {
        const dx = p.x - l.x,
          dy = p.y - l.y,
          dz = p.z - l.z;
        const d = Math.sqrt(dx * dx + dy * dy + dz * dz);
        if (d >= 2.4 && d <= 3.6) {
          viewer.addCylinder({
            start: { x: p.x, y: p.y, z: p.z },
            end: { x: l.x, y: l.y, z: l.z },
            radius: 0.1,
            color: "cyan",
            dashed: true,
            fromCap: 1,
            toCap: 1,
          });
          resSet.add(`${p.resn}:${p.resi}:${p.chain || ""}`);
          count++;
        }
      });
    });
    focusLigandAndResidues(resSet, false);
    viewer.render();
    Utils.showToast(
      count > 0 ? `检测到 ${count} 个氢键 (青色虚线)` : "未检测到氢键",
      count > 0 ? "success" : "warning",
    );
  } catch (e) {
    console.error("氢键检测错误:", e);
  }
}

// ============ 显示 π–π 堆积相互作用 (洋红虚线) ============
function displayPiPiInteractions() {
  try {
    if (
      !interactionsState.hydrogenBonds &&
      !interactionsState.hydrophobicInteractions
    ) {
      viewer.removeAllShapes();
    }
    const atoms = viewer.selectedAtoms({});
    const ligandKeys = identifyLigandResidueKeys(atoms);
    const proteinAtoms = atoms.filter((a) =>
      CONFIG.PROTEIN_RESIDUES.includes((a.resn || "").toUpperCase()),
    );
    const ligandAtoms = atomsForResidueKeys(atoms, ligandKeys).filter(
      (a) => !CONFIG.WATER_RESIDUES.includes((a.resn || "").toUpperCase()),
    );

    const pRings = findAromaticRingsImproved(proteinAtoms);
    const lRings = findAromaticRingsImproved(ligandAtoms);

    let count = 0;
    const resSet = new Set();
    pRings.forEach((pR) => {
      lRings.forEach((lR) => {
        const dx = pR.center.x - lR.center.x;
        const dy = pR.center.y - lR.center.y;
        const dz = pR.center.z - lR.center.z;
        const d = Math.sqrt(dx * dx + dy * dy + dz * dz);
        if (d >= 3.3 && d <= 6.0) {
          viewer.addCylinder({
            start: pR.center,
            end: lR.center,
            radius: 0.15,
            color: "magenta",
            dashed: true,
          });
          const a0 = pR.atoms[0];
          resSet.add(`${a0.resn}:${a0.resi}:${a0.chain || ""}`);
          count++;
        }
      });
    });
    focusLigandAndResidues(resSet, false);
    viewer.render();
    Utils.showToast(
      count > 0
        ? `检测到 ${count} 个 π–π 相互作用 (洋红虚线)`
        : "未检测到 π–π 相互作用",
      count > 0 ? "success" : "warning",
    );
  } catch (e) {
    console.error("π–π 检测错误:", e);
  }
}

// ============ 显示疏水相互作用 (橙色虚线) ============
function displayHydrophobicInteractions() {
  try {
    if (
      !interactionsState.hydrogenBonds &&
      !interactionsState.piPiInteractions
    ) {
      viewer.removeAllShapes();
    }
    const atoms = viewer.selectedAtoms({});
    const ligandKeys = identifyLigandResidueKeys(atoms);

    const proteinC = atoms
      .filter(
        (a) =>
          a.elem === "C" &&
          CONFIG.PROTEIN_RESIDUES.includes((a.resn || "").toUpperCase()) &&
          !CONFIG.WATER_RESIDUES.includes((a.resn || "").toUpperCase()),
      )
      .filter((a) => !isPartOfPolarGroup(a));
    const ligandC = atomsForResidueKeys(atoms, ligandKeys).filter(
      (a) => a.elem === "C" && !isPartOfPolarGroup(a),
    );

    const byResidue = new Map();
    for (const p of proteinC) {
      for (const l of ligandC) {
        const dx = p.x - l.x,
          dy = p.y - l.y,
          dz = p.z - l.z;
        const d = Math.sqrt(dx * dx + dy * dy + dz * dz);
        if (d < 3.0 || d > 5.0) continue;
        const rk = `${p.resn}:${p.resi}:${p.chain || ""}`;
        const cur = byResidue.get(rk);
        if (!cur || d < cur.distance) byResidue.set(rk, { p, l, distance: d });
      }
    }

    let count = 0;
    const resSet = new Set();
    const shorten = 0.25;
    byResidue.forEach(({ p, l }) => {
      const vx = l.x - p.x,
        vy = l.y - p.y,
        vz = l.z - p.z;
      const len = Math.sqrt(vx * vx + vy * vy + vz * vz) || 1.0;
      const ux = vx / len,
        uy = vy / len,
        uz = vz / len;
      const start = {
        x: p.x + ux * shorten,
        y: p.y + uy * shorten,
        z: p.z + uz * shorten,
      };
      const end = {
        x: l.x - ux * shorten,
        y: l.y - uy * shorten,
        z: l.z - uz * shorten,
      };
      viewer.addCylinder({
        start,
        end,
        radius: 0.085,
        color: "#FF7A00",
        dashed: true,
        fromCap: 2,
        toCap: 2,
        alpha: 0.95,
      });
      resSet.add(`${p.resn}:${p.resi}:${p.chain || ""}`);
      count++;
    });

    focusLigandAndResidues(resSet, false);
    viewer.render();
    Utils.showToast(
      count > 0
        ? `检测到 ${count} 个疏水相互作用 (琥珀色虚线，每残基一条代表线)`
        : "未检测到疏水相互作用",
      count > 0 ? "success" : "warning",
    );
  } catch (e) {
    console.error("疏水检测错误:", e);
  }
}

// 移除特定类型的相互作用
function removeInteractions(type) {
  viewer.removeAllShapes();
  // 不要在这里调用 StyleManager.apply：
  // 它会执行全局 setStyle({},{}) 并依赖配体识别，某些场景会导致配体样式丢失/看起来消失。
  // 这里仅清理 shape，然后按当前开关重新绘制其余相互作用。

  const hasOtherInteractions =
    (type !== "hbonds" && interactionsState.hydrogenBonds) ||
    (type !== "pipi" && interactionsState.piPiInteractions) ||
    (type !== "hydrophobic" && interactionsState.hydrophobicInteractions);

  // 若所有相互作用都已关闭，恢复一次复合物基础视图，确保蛋白/配体样式稳定存在。
  if (!hasOtherInteractions) {
    try {
      const jobId =
        typeof getCurrentJobId === "function" ? getCurrentJobId() : null;
      const poseSelect = document.getElementById("pose-select");
      const currentPose = poseSelect
        ? parseInt(poseSelect.value || "1", 10)
        : 1;

      // 保持当前 pose，不要默认回到 pose 1
      if (
        jobId &&
        typeof viewPose === "function" &&
        Number.isFinite(currentPose)
      ) {
        viewPose(Math.max(1, currentPose));
      } else if (jobId && typeof loadComplexStructure === "function") {
        loadComplexStructure(jobId);
      } else {
        viewer.render();
      }
    } catch (e) {
      console.warn("恢复复合物视图失败:", e);
      viewer.render();
    }
    return;
  }

  if (type !== "hbonds" && interactionsState.hydrogenBonds)
    displayHydrogenBonds();
  if (type !== "pipi" && interactionsState.piPiInteractions)
    displayPiPiInteractions();
  if (type !== "hydrophobic" && interactionsState.hydrophobicInteractions)
    displayHydrophobicInteractions();
}

// 改进的芳香环检测函数（供本模块以及 ligand_recognizer.js 兼容调用）
function findAromaticRingsImproved(atoms) {
  const rings = [];
  const residueGroups = {};
  const otherAtoms = [];

  atoms.forEach((atom) => {
    if (atom.resn && atom.resi) {
      const key = `${atom.resn}_${atom.resi}`;
      if (!residueGroups[key]) residueGroups[key] = [];
      residueGroups[key].push(atom);
    } else {
      otherAtoms.push(atom);
    }
  });

  const aromaticResidues = ["PHE", "TYR", "TRP", "HIS"];
  for (const key in residueGroups) {
    const residueAtoms = residueGroups[key];
    const resName = residueAtoms[0].resn;
    if (aromaticResidues.includes(resName)) {
      const ringAtomNames = {
        PHE: ["CG", "CD1", "CD2", "CE1", "CE2", "CZ"],
        TYR: ["CG", "CD1", "CD2", "CE1", "CE2", "CZ"],
        TRP: ["CG", "CD1", "CD2", "CE2", "CE3", "CZ2", "CZ3", "CH2"],
        HIS: ["CG", "CD2", "CE1"],
      };
      if (ringAtomNames[resName]) {
        const ringAtoms = residueAtoms.filter((a) =>
          ringAtomNames[resName].some(
            (name) => a.atom && a.atom.includes(name),
          ),
        );
        if (ringAtoms.length >= 5) {
          const centerX =
            ringAtoms.reduce((sum, a) => sum + a.x, 0) / ringAtoms.length;
          const centerY =
            ringAtoms.reduce((sum, a) => sum + a.y, 0) / ringAtoms.length;
          const centerZ =
            ringAtoms.reduce((sum, a) => sum + a.z, 0) / ringAtoms.length;
          rings.push({
            center: { x: centerX, y: centerY, z: centerZ },
            atoms: ringAtoms,
            type: resName,
          });
        }
      }
    }
  }

  const ligandCarbons = otherAtoms.filter((a) => a.elem === "C");
  if (ligandCarbons.length >= 5) {
    const visited = new Set();
    ligandCarbons.forEach((carbon, idx) => {
      if (visited.has(idx)) return;
      const cluster = [carbon];
      visited.add(idx);
      for (let i = 0; i < ligandCarbons.length; i++) {
        if (visited.has(i)) continue;
        const other = ligandCarbons[i];
        const dist = Math.sqrt(
          Math.pow(carbon.x - other.x, 2) +
            Math.pow(carbon.y - other.y, 2) +
            Math.pow(carbon.z - other.z, 2),
        );
        if (dist < 1.6) {
          cluster.push(other);
          visited.add(i);
        }
      }
      if (cluster.length >= 5 && cluster.length <= 7) {
        const centerX =
          cluster.reduce((sum, a) => sum + a.x, 0) / cluster.length;
        const centerY =
          cluster.reduce((sum, a) => sum + a.y, 0) / cluster.length;
        const centerZ =
          cluster.reduce((sum, a) => sum + a.z, 0) / cluster.length;
        const avgDist =
          cluster.reduce((sum, a) => {
            return (
              sum +
              Math.sqrt(
                Math.pow(a.x - centerX, 2) +
                  Math.pow(a.y - centerY, 2) +
                  Math.pow(a.z - centerZ, 2),
              )
            );
          }, 0) / cluster.length;
        if (avgDist >= 1.5 && avgDist <= 3.5) {
          rings.push({
            center: { x: centerX, y: centerY, z: centerZ },
            atoms: cluster,
            type: "ligand",
          });
        }
      }
    });
  }

  console.log(`检测到 ${rings.length} 个芳香环`);
  return rings;
}

// 兼容旧版本
function findAromaticRings(atoms) {
  return findAromaticRingsImproved(atoms);
}

// 判断是否为芳香原子
function isAromatic(atom) {
  if (!atom.bonds) return false;
  const doubleBonds = atom.bonds.filter((bond) => bond.bondOrder === 2);
  return doubleBonds.length >= 1;
}

// 判断是否属于极性基团
function isPartOfPolarGroup(atom) {
  if (!atom.bonds) return false;
  return atom.bonds.some(
    (bond) => bond.elem === "O" || bond.elem === "N" || bond.elem === "S",
  );
}

// 显示相互作用信息提示
function showInteractionInfo(message) {
  const info = document.createElement("div");
  info.style.cssText = `
    position: fixed;
    top: 100px;
    right: 30px;
    background: rgba(0, 0, 0, 0.8);
    color: white;
    padding: 15px 20px;
    border-radius: 8px;
    z-index: 1000;
    font-size: 14px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.3);
    animation: fadeIn 0.3s ease-in;
  `;
  info.textContent = message;
  document.body.appendChild(info);
  setTimeout(() => {
    info.style.animation = "fadeOut 0.3s ease-out";
    setTimeout(() => info.remove(), 300);
  }, 3000);
}
