// ==================== 对接盒子可视化 ====================
let dockingBoxVisible = false;
let dockingBoxShapeSpecs = [];

// 切换对接盒子显示/隐藏
function toggleDockingBox() {
  if (!viewer) {
    Utils.showToast("请先加载蛋白质结构", "warning");
    return;
  }

  dockingBoxVisible = !dockingBoxVisible;
  const btn = document.getElementById("toggle-box-btn");

  if (dockingBoxVisible) {
    drawDockingBox();
    btn.textContent = "✅ 隐藏对接盒子";
    btn.style.background = "linear-gradient(135deg, #48bb78 0%, #38a169 100%)";
    btn.style.color = "white";
    Utils.showToast("对接盒子已显示", "success");
  } else {
    removeDockingBox();
    btn.textContent = "📦 显示对接盒子";
    btn.style.background = "";
    btn.style.color = "";
    Utils.showToast("对接盒子已隐藏", "info");
  }
}

// 绘制对接盒子
function drawDockingBox() {
  if (!viewer) return;

  removeDockingBox();

  const centerX = parseFloat(document.getElementById("center_x").value) || 0;
  const centerY = parseFloat(document.getElementById("center_y").value) || 0;
  const centerZ = parseFloat(document.getElementById("center_z").value) || 0;
  const sizeX = parseFloat(document.getElementById("size_x").value) || 20;
  const sizeY = parseFloat(document.getElementById("size_y").value) || 20;
  const sizeZ = parseFloat(document.getElementById("size_z").value) || 20;

  console.log(
    `绘制对接盒子: 中心(${centerX}, ${centerY}, ${centerZ}), 大小(${sizeX}, ${sizeY}, ${sizeZ})`,
  );

  const halfX = sizeX / 2;
  const halfY = sizeY / 2;
  const halfZ = sizeZ / 2;

  const vertices = [
    { x: centerX - halfX, y: centerY - halfY, z: centerZ - halfZ },
    { x: centerX + halfX, y: centerY - halfY, z: centerZ - halfZ },
    { x: centerX + halfX, y: centerY + halfY, z: centerZ - halfZ },
    { x: centerX - halfX, y: centerY + halfY, z: centerZ - halfZ },
    { x: centerX - halfX, y: centerY - halfY, z: centerZ + halfZ },
    { x: centerX + halfX, y: centerY - halfY, z: centerZ + halfZ },
    { x: centerX + halfX, y: centerY + halfY, z: centerZ + halfZ },
    { x: centerX - halfX, y: centerY + halfY, z: centerZ + halfZ },
  ];

  const edges = [
    [0, 1],
    [1, 2],
    [2, 3],
    [3, 0], // 底面
    [4, 5],
    [5, 6],
    [6, 7],
    [7, 4], // 顶面
    [0, 4],
    [1, 5],
    [2, 6],
    [3, 7], // 竖边
  ];

  try {
    dockingBoxShapeSpecs = [];

    edges.forEach(([i, j]) => {
      const spec = {
        start: vertices[i],
        end: vertices[j],
        radius: 0.15,
        color: "red",
        opacity: 0.8,
        fromCap: 1,
        toCap: 1,
      };
      dockingBoxShapeSpecs.push({ type: "cylinder", spec });
      viewer.addCylinder(spec);
    });

    const sphereSpec = {
      center: { x: centerX, y: centerY, z: centerZ },
      radius: 0.6,
      color: "red",
      opacity: 0.9,
    };
    dockingBoxShapeSpecs.push({ type: "sphere", spec: sphereSpec });
    viewer.addSphere(sphereSpec);

    viewer.addLabel(
      `盒子: ${sizeX.toFixed(1)} × ${sizeY.toFixed(1)} × ${sizeZ.toFixed(1)} Å`,
      {
        position: { x: centerX, y: centerY, z: centerZ + halfZ + 2 },
        backgroundColor: "rgba(255, 50, 50, 0.85)",
        fontColor: "white",
        fontSize: 12,
        showBackground: true,
      },
    );

    viewer.render();
    console.log(`盒子绘制完成，共 ${dockingBoxShapeSpecs.length} 个形状`);
  } catch (e) {
    console.error("绘制盒子失败:", e);
    Utils.showToast("绘制盒子失败", "error");
  }
}

// 移除对接盒子
function removeDockingBox() {
  if (!viewer) return;

  dockingBoxShapeSpecs = [];

  if (currentProteinData || currentLigandData || currentComplexData) {
    const savedInteractions = {
      hydrogenBonds: interactionsState.hydrogenBonds,
      piPiInteractions: interactionsState.piPiInteractions,
      hydrophobicInteractions: interactionsState.hydrophobicInteractions,
    };

    viewer.removeAllShapes();
    viewer.removeAllLabels();

    setTimeout(() => {
      if (savedInteractions.hydrogenBonds) displayHydrogenBonds();
      if (savedInteractions.piPiInteractions) displayPiPiInteractions();
      if (savedInteractions.hydrophobicInteractions)
        displayHydrophobicInteractions();
    }, 50);
  }

  viewer.render();
  console.log("盒子已移除");
}

// 更新对接盒子（当参数改变时）
function updateDockingBox() {
  if (dockingBoxVisible) {
    drawDockingBox();
  }
}

function findEmbeddedLigandAtoms(atoms) {
  const waterSet = new Set(
    (CONFIG.WATER_RESIDUES || []).map((r) => r.toUpperCase()),
  );
  const proteinSet = new Set(
    (CONFIG.PROTEIN_RESIDUES || []).map((r) => r.toUpperCase()),
  );
  const excludeSet = new Set(
    (CONFIG.NON_LIGAND_RESIDUES || []).map((r) => r.toUpperCase()),
  );

  const residueMap = new Map();
  for (const atom of atoms || []) {
    const resn = (atom.resn || "").toUpperCase();
    const resi = atom.resi;
    const chain = atom.chain || "";
    if (!resn || waterSet.has(resn) || proteinSet.has(resn) || excludeSet.has(resn)) {
      continue;
    }
    const key = `${resn}:${resi}:${chain}`;
    if (!residueMap.has(key)) residueMap.set(key, []);
    residueMap.get(key).push(atom);
  }

  const candidates = [];
  for (const [, group] of residueMap.entries()) {
    const heavyAtoms = group.filter((a) => (a.elem || "").toUpperCase() !== "H");
    const hasCarbon = heavyAtoms.some((a) => (a.elem || "").toUpperCase() === "C");
    const hasHetflag = group.some((a) => !!a.hetflag);
    if (heavyAtoms.length >= (CONFIG.LIGAND_MIN_HEAVY_ATOMS || 5) && hasCarbon) {
      candidates.push({
        atoms: group,
        score: (hasHetflag ? 1000 : 0) + heavyAtoms.length,
      });
    }
  }

  candidates.sort((a, b) => b.score - a.score);
  return candidates.length > 0 ? candidates[0].atoms : [];
}

function getProteinCenterAtoms(atoms) {
  const proteinAtoms = (atoms || []).filter((a) =>
    CONFIG.PROTEIN_RESIDUES.includes((a.resn || "").toUpperCase()),
  );
  return proteinAtoms.length > 0 ? proteinAtoms : atoms || [];
}

// 自动将盒子中心设置为配体中心
function autoSetBoxToLigand() {
  let silent = false;
  if (arguments.length > 0) {
    silent = !!arguments[0];
  }

  if (!viewer) {
    if (!silent) Utils.showToast("请先加载结构", "warning");
    return;
  }

  try {
    if (
      typeof currentProteinData !== "undefined" &&
      currentProteinData &&
      typeof loadMolecule === "function"
    ) {
      loadMolecule(currentProteinData, "protein");
      if (typeof setActiveButton === "function") {
        setActiveButton("show-protein-btn");
      }
    }

    const atoms = viewer.selectedAtoms({});
    if (!atoms || atoms.length === 0) {
      if (!silent) Utils.showToast("未找到分子结构", "warning");
      return;
    }

    const embeddedLigandAtoms = findEmbeddedLigandAtoms(atoms);
    const useEmbeddedLigand = embeddedLigandAtoms.length > 0;
    const centerAtoms = useEmbeddedLigand
      ? embeddedLigandAtoms
      : getProteinCenterAtoms(atoms);
    const center = Utils.calculateCenter(centerAtoms);

    let sizeX;
    let sizeY;
    let sizeZ;
    if (useEmbeddedLigand) {
      const bbox = Utils.calculateBoundingBox(embeddedLigandAtoms);
      const padding = 8.0;
      const clamp = (v, min, max) => Math.max(min, Math.min(max, v));
      sizeX = clamp(bbox.maxX - bbox.minX + padding, 10, 50);
      sizeY = clamp(bbox.maxY - bbox.minY + padding, 10, 50);
      sizeZ = clamp(bbox.maxZ - bbox.minZ + padding, 10, 50);
    } else {
      const sxEl = document.getElementById("size_x");
      const syEl = document.getElementById("size_y");
      const szEl = document.getElementById("size_z");
      sizeX = parseFloat((sxEl && sxEl.value) || "20") || 20;
      sizeY = parseFloat((syEl && syEl.value) || "20") || 20;
      sizeZ = parseFloat((szEl && szEl.value) || "20") || 20;
    }

    document.getElementById("center_x").value = center.x.toFixed(2);
    document.getElementById("center_y").value = center.y.toFixed(2);
    document.getElementById("center_z").value = center.z.toFixed(2);

    const sxEl = document.getElementById("size_x");
    const syEl = document.getElementById("size_y");
    const szEl = document.getElementById("size_z");
    if (sxEl) sxEl.value = sizeX.toFixed(2);
    if (syEl) syEl.value = sizeY.toFixed(2);
    if (szEl) szEl.value = sizeZ.toFixed(2);

    dockingBoxVisible = true;
    const btn = document.getElementById("toggle-box-btn");
    if (btn) {
      btn.textContent = "✅ 隐藏对接盒子";
      btn.style.background = "linear-gradient(135deg, #48bb78 0%, #38a169 100%)";
      btn.style.color = "white";
    }
    drawDockingBox();

    if (!silent) {
      Utils.showToast(
        useEmbeddedLigand
          ? `已切换到蛋白视图，并按蛋白内配体自动定位盒子: center=(${center.x.toFixed(2)}, ${center.y.toFixed(2)}, ${center.z.toFixed(2)}), size=(${sizeX.toFixed(2)}, ${sizeY.toFixed(2)}, ${sizeZ.toFixed(2)})`
          : `已切换到蛋白视图；蛋白中未识别到配体，已按蛋白中心定位盒子: center=(${center.x.toFixed(2)}, ${center.y.toFixed(2)}, ${center.z.toFixed(2)}), size=(${sizeX.toFixed(2)}, ${sizeY.toFixed(2)}, ${sizeZ.toFixed(2)})`,
        "success",
      );
    }
  } catch (e) {
    console.error("自动定位盒子失败:", e);
    if (!silent) Utils.showToast("自动定位失败", "error");
  }
}

// 返回主页
function goBack() {
  window.location.href = "/";
}
