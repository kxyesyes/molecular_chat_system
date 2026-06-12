// ==================== 分子相互作用可视化核心函数（改进版） ====================

// 常见金属离子，用于排除误识别为配体的金属原子
const METAL_ELEMENTS = new Set([
  "NA",
  "K",
  "CA",
  "MG",
  "ZN",
  "FE",
  "MN",
  "CU",
  "CO",
  "NI",
  "SR",
  "PT",
  "AU",
  "HG",
  "CD",
  "PB",
  "AL",
]);

// 计算残基的空间体积
function computeBBoxVolume(atoms) {
  if (!atoms || atoms.length === 0) return 0;
  let minX = Infinity,
    minY = Infinity,
    minZ = Infinity;
  let maxX = -Infinity,
    maxY = -Infinity,
    maxZ = -Infinity;
  for (const a of atoms) {
    minX = Math.min(minX, a.x);
    minY = Math.min(minY, a.y);
    minZ = Math.min(minZ, a.z);
    maxX = Math.max(maxX, a.x);
    maxY = Math.max(maxY, a.y);
    maxZ = Math.max(maxZ, a.z);
  }
  return (maxX - minX) * (maxY - minY) * (maxZ - minZ);
}

// 统一规范化原子的残基键（兼容 SDF/MOL 中缺失 resn/resi 的情况）
function normalizeAtomResidueKey(atom) {
  const rawResn = (atom.resn || "").toUpperCase().trim();
  const resn = rawResn || "LIG";

  const parsedResi = parseInt(atom.resi, 10);
  const resi = Number.isFinite(parsedResi) ? parsedResi : 1;

  const chain = atom.chain || "";
  return {
    resn,
    resi,
    chain,
    key: `${resn}:${resi}:${chain}`,
  };
}

// 智能识别配体残基键集合（改进版）
function identifyLigandResidueKeys(atoms) {
  const waterSet = new Set(
    (CONFIG.WATER_RESIDUES || []).map((r) => r.toUpperCase()),
  );
  const proteinSet = new Set(
    (CONFIG.PROTEIN_RESIDUES || []).map((r) => r.toUpperCase()),
  );
  const minHeavy = CONFIG.LIGAND_MIN_HEAVY_ATOMS || 5;

  const ligandInfo = getCurrentLigandInfo();
  console.log("当前配体信息:", ligandInfo);

  const residueMap = new Map();
  const excludeSet = new Set(
    (CONFIG.NON_LIGAND_RESIDUES || []).map((r) => r.toUpperCase()),
  );
  const allowedNames = new Set(
    ligandInfo
      ? (ligandInfo.residueNames || [ligandInfo.residueName])
          .filter(Boolean)
          .map((s) => String(s).toUpperCase())
      : [],
  );

  for (const a of atoms) {
    const { resn, key } = normalizeAtomResidueKey(a);
    if (waterSet.has(resn)) continue;
    if (proteinSet.has(resn)) continue;
    if (excludeSet.has(resn) && !allowedNames.has(resn)) continue;
    if (!residueMap.has(key)) residueMap.set(key, []);
    residueMap.get(key).push(a);
  }

  if (residueMap.size === 0) {
    console.log("未找到非蛋白质、非水分子残基");
    return new Set();
  }

  console.log("发现残基:", Array.from(residueMap.keys()));

  if (ligandInfo && ligandInfo.type === "file") {
    console.log("检测到配体文件，使用HETATM优先识别");
    return identifyLigandFromHETATM(atoms, residueMap);
  }

  const candidates = [];
  for (const [key, group] of residueMap.entries()) {
    const heavy = group.filter((x) => x.elem && x.elem !== "H");
    const hasC = heavy.some((x) => x.elem === "C");
    const elems = new Set(heavy.map((x) => x.elem));
    const allMetal = [...elems].every((e) => METAL_ELEMENTS.has(e));
    const ligandScore = calculateLigandMatchScore(key, group, ligandInfo);

    candidates.push({
      key,
      count: heavy.length,
      hasC,
      allMetal,
      vol: computeBBoxVolume(group),
      ligandScore,
      resn: key.split(":")[0],
    });
  }

  candidates.sort((a, b) => {
    if (a.ligandScore !== b.ligandScore) return b.ligandScore - a.ligandScore;
    return b.count - a.count;
  });

  console.log(
    "配体候选排序:",
    candidates.map((c) => ({
      key: c.key,
      score: c.ligandScore,
      count: c.count,
      resn: c.resn,
    })),
  );

  if (ligandInfo && (ligandInfo.residueName || ligandInfo.residueNames)) {
    const targetNames = new Set(
      (ligandInfo.residueNames || [ligandInfo.residueName])
        .filter(Boolean)
        .map((s) => String(s).toUpperCase()),
    );
    const byName = candidates.filter((c) => targetNames.has(c.resn));
    if (byName.length > 0) {
      const result = new Set(byName.map((s) => s.key));
      console.log("根据缓存/指定配体名选择的配体:", Array.from(result));
      return result;
    }
  }

  const selected = candidates.filter(
    (c) => c.ligandScore > 0 || (c.count >= minHeavy && c.hasC && !c.allMetal),
  );

  if (selected.length === 0) {
    const fallback = candidates.filter((c) => !c.allMetal);
    fallback.sort((a, b) => b.count - a.count);
    if (fallback.length > 0) {
      console.log("使用备用配体识别:", fallback[0].key);
      return new Set([fallback[0].key]);
    }
    return new Set();
  }

  const result = new Set(selected.slice(0, 3).map((s) => s.key));
  console.log("选择的配体残基:", Array.from(result));
  return result;
}

// 计算配体匹配分数
function calculateLigandMatchScore(key, atoms, ligandInfo) {
  if (!ligandInfo) return 0;

  const [resn, resi, chain] = key.split(":");
  let score = 0;

  if (ligandInfo.type === "cached" && ligandInfo.residueName) {
    if (resn === ligandInfo.residueName.toUpperCase()) {
      score += 2000;
      console.log(`找到缓存的配体残基: ${resn}`);
    }
  }

  if (ligandInfo.type === "specified" && ligandInfo.residueNames) {
    if (ligandInfo.residueNames.includes(resn)) {
      score += 1000;
      console.log(`找到用户指定的配体残基: ${resn}`);
    }
  }

  if (ligandInfo.residueName && resn === ligandInfo.residueName.toUpperCase()) {
    score += 100;
  }

  if (ligandInfo.chainId && chain === ligandInfo.chainId) {
    score += 50;
  }

  if (
    ligandInfo.residueNumber &&
    parseInt(resi) === parseInt(ligandInfo.residueNumber)
  ) {
    score += 30;
  }

  const heavyAtoms = atoms.filter((x) => x.elem && x.elem !== "H").length;
  if (ligandInfo.expectedAtoms) {
    const diff = Math.abs(heavyAtoms - ligandInfo.expectedAtoms);
    score += Math.max(0, 20 - diff);
  }

  if (ligandInfo.expectedElements) {
    const atomElements = new Set(atoms.map((a) => a.elem).filter((e) => e));
    const expectedElements = new Set(ligandInfo.expectedElements);
    const commonElements = [...atomElements].filter((e) =>
      expectedElements.has(e),
    );
    score += commonElements.length * 5;
  }

  if (ligandInfo.type === "smiles" && ligandInfo.smiles) {
    const inferredNames = inferResidueNamesFromSMILES(ligandInfo.smiles);
    if (inferredNames.includes(resn)) {
      score += 200;
    }
  }

  return score;
}

// 获取当前配体信息
function getCurrentLigandInfo() {
  if (CONFIG.CACHED_LIGAND_INFO) {
    console.log("使用缓存的配体信息:", CONFIG.CACHED_LIGAND_INFO);
    return CONFIG.CACHED_LIGAND_INFO;
  }

  if (CONFIG.USER_SPECIFIED_LIGAND_RESIDUES.length > 0) {
    return {
      type: "specified",
      residueNames: CONFIG.USER_SPECIFIED_LIGAND_RESIDUES,
      priority: 1000,
    };
  }

  const smilesInput = document.getElementById("smiles-input");
  if (smilesInput && smilesInput.value.trim()) {
    return {
      type: "smiles",
      smiles: smilesInput.value.trim(),
      expectedAtoms: estimateAtomCount(smilesInput.value.trim()),
    };
  }

  const ligandFile = document.getElementById("ligand-file");
  if (ligandFile && ligandFile.files.length > 0) {
    const fileName = ligandFile.files[0].name.toLowerCase();
    return {
      type: "file",
      fileName: fileName,
      expectedAtoms: estimateAtomCountFromFileName(fileName),
    };
  }

  if (AppState.currentLigandData) {
    return {
      type: "loaded",
      data: AppState.currentLigandData,
      expectedAtoms: estimateAtomCountFromPDB(AppState.currentLigandData),
    };
  }

  return null;
}

// 缓存配体信息（从用户输入获取）
function cacheLigandInfo(ligandInfo) {
  CONFIG.CACHED_LIGAND_INFO = ligandInfo;
  console.log("已缓存配体信息:", ligandInfo);
}

// 清除配体信息缓存
function clearLigandCache() {
  CONFIG.CACHED_LIGAND_INFO = null;
  console.log("已清除配体信息缓存");
}

// 自动聚焦到配体（在加载对接结果后调用）
function autoFocusToLigand() {
  if (!viewer) {
    console.log("请先加载分子结构");
    return false;
  }

  const atoms = viewer.selectedAtoms({});
  const ligandKeys = identifyLigandResidueKeys(atoms);

  if (ligandKeys.size === 0) {
    console.log("未找到配体，请检查配体残基名设置");
    return false;
  }

  console.log("自动聚焦到配体:", Array.from(ligandKeys));
  focusLigandAndResidues(ligandKeys, true);
  Utils.showToast(
    `已自动聚焦到配体: ${Array.from(ligandKeys).join(", ")}`,
    "success",
  );

  return true;
}

// 确保配体可见性
function ensureLigandVisibility() {
  if (!viewer) return;

  try {
    const atoms = viewer.selectedAtoms({});
    const ligandKeys = identifyLigandResidueKeys(atoms);

    if (ligandKeys.size === 0) {
      console.log("未找到配体，尝试强制显示非蛋白质残基");

      const waterSet = new Set(
        (CONFIG.WATER_RESIDUES || []).map((r) => r.toUpperCase()),
      );
      const proteinSet = new Set(
        (CONFIG.PROTEIN_RESIDUES || []).map((r) => r.toUpperCase()),
      );

      const nonProteinAtoms = atoms.filter((a) => {
        const resn = (a.resn || "").toUpperCase();
        return !waterSet.has(resn) && !proteinSet.has(resn);
      });

      if (nonProteinAtoms.length > 0) {
        console.log("找到非蛋白质原子，强制显示为配体");
        const EXCLUDE = [
          "HOH",
          "WAT",
          "SOL",
          "H2O",
          "DOD",
          "TIP3",
          "NA",
          "CL",
          "MG",
          "CA",
          "ZN",
          "FE",
          "MN",
          "K",
          "BR",
          "I",
        ];
        viewer.setStyle(
          { hetflag: true, resn: EXCLUDE, invert: true },
          { stick: { radius: 0.25, colorscheme: "greenCarbon" } },
        );
      }
      return;
    }

    console.log("确保配体可见性:", Array.from(ligandKeys));

    ligandKeys.forEach((key) => {
      const [resn, resi, chain] = key.split(":");
      viewer.setStyle(
        { resn, resi: parseInt(resi), chain },
        { stick: { radius: 0.25, colorscheme: "greenCarbon" } },
      );
    });

    viewer.render();
  } catch (e) {
    console.error("确保配体可见性失败:", e);
  }
}

// 手动设置配体残基名
function setLigandResidueNames(residueNames) {
  if (Array.isArray(residueNames)) {
    CONFIG.USER_SPECIFIED_LIGAND_RESIDUES = residueNames.map((name) =>
      name.toUpperCase(),
    );
    console.log("已设置配体残基名:", CONFIG.USER_SPECIFIED_LIGAND_RESIDUES);
  } else if (typeof residueNames === "string") {
    CONFIG.USER_SPECIFIED_LIGAND_RESIDUES = [residueNames.toUpperCase()];
    console.log("已设置配体残基名:", CONFIG.USER_SPECIFIED_LIGAND_RESIDUES);
  }
}

// 清除配体残基名设置
function clearLigandResidueNames() {
  CONFIG.USER_SPECIFIED_LIGAND_RESIDUES = [];
  console.log("已清除配体残基名设置");
}

// 自动检测并设置配体残基名（基于用户输入）
function autoDetectLigandResidueNames() {
  const ligandInfo = getCurrentLigandInfo();
  if (!ligandInfo) return;

  if (ligandInfo.type === "specified") {
    return CONFIG.USER_SPECIFIED_LIGAND_RESIDUES;
  }

  if (ligandInfo.type === "smiles") {
    const possibleNames = inferResidueNamesFromSMILES(ligandInfo.smiles);
    if (possibleNames.length > 0) {
      setLigandResidueNames(possibleNames);
      return possibleNames;
    }
  }

  return [];
}

// 从SMILES推断可能的残基名
function inferResidueNamesFromSMILES(smiles) {
  const possibleNames = [];

  if (smiles.includes("N") && smiles.includes("O")) {
    possibleNames.push("LIG");
  }
  if (smiles.includes("c1ccccc1")) {
    possibleNames.push("BEN");
  }
  if (smiles.includes("N1CCNCC1")) {
    possibleNames.push("PIP");
  }

  return possibleNames;
}

// 估算SMILES中的原子数量
function estimateAtomCount(smiles) {
  if (!smiles) return 0;
  return smiles.replace(/[\[\](){}]/g, "").replace(/\d+/g, "").length;
}

// 从文件名估算原子数量
function estimateAtomCountFromFileName(fileName) {
  if (fileName.includes("small") || fileName.includes("frag")) return 10;
  if (fileName.includes("medium")) return 25;
  if (fileName.includes("large")) return 50;
  return 20;
}

// 从PDB数据估算原子数量
function estimateAtomCountFromPDB(pdbData) {
  if (!pdbData) return 0;
  const lines = pdbData.split(/\r?\n/);
  const atomLines = lines.filter(
    (line) => line.startsWith("ATOM") || line.startsWith("HETATM"),
  );
  if (atomLines.length > 0) return atomLines.length;

  // 兼容 MOL/SDF：counts line (V2000) 的前两列通常是原子数与键数
  if (lines.length >= 4) {
    const countsLine = lines[3] || "";
    const m = countsLine.match(/^\s*(\d+)\s+(\d+)\s+/);
    if (m) {
      const n = parseInt(m[1], 10);
      if (Number.isFinite(n) && n > 0) return n;
    }
  }

  // 再兜底：统计可能的坐标行（x y z element）
  const coordLike = lines.filter((line) => {
    const s = line.trim();
    return /^[-+]?\d*\.?\d+\s+[-+]?\d*\.?\d+\s+[-+]?\d*\.?\d+\s+[A-Za-z]{1,3}(\s|$)/.test(
      s,
    );
  }).length;
  return coordLike;
}

// 从PDB字符串中提取配体残基名（第一个非水残基）
function extractLigandResidueNameFromPDB(pdbData) {
  if (!pdbData) return "LIG";
  const lines = pdbData.split(/\r?\n/);
  const waterSet = new Set(
    (CONFIG.WATER_RESIDUES || []).map((r) => r.toUpperCase()),
  );
  for (const line of lines) {
    if (line.startsWith("ATOM") || line.startsWith("HETATM")) {
      const resn = line.substring(17, 20).trim().toUpperCase();
      if (!waterSet.has(resn)) return resn || "LIG";
    }
  }
  return "LIG";
}

// 从PDBQT文件中提取配体残基名
function extractLigandResidueNameFromPDBQT(pdbqtData) {
  if (!pdbqtData) return "LIG";

  const lines = pdbqtData.split("\n");
  const hetatmLines = lines.filter((line) => line.startsWith("HETATM"));

  if (hetatmLines.length === 0) return "LIG";

  const firstHetatm = hetatmLines[0];
  const residueName = firstHetatm.substring(17, 20).trim();

  return residueName || "LIG";
}

// 按残基键提取原子
function atomsForResidueKeys(atoms, residueKeySet) {
  return atoms.filter((a) => {
    const { key } = normalizeAtomResidueKey(a);
    return residueKeySet.has(key);
  });
}

// 判断碳是否属于极性基团
function isPartOfPolarGroup(atom) {
  if (!atom || atom.elem !== "C" || !atom.bonds) return false;
  const modelAtoms = atom.model && atom.model.atoms ? atom.model.atoms : null;
  if (!modelAtoms) return false;
  for (const bi of atom.bonds) {
    const bAtom = modelAtoms[bi];
    if (!bAtom) continue;
    if (["O", "N", "S", "P"].includes((bAtom.elem || "").toUpperCase()))
      return true;
  }
  return false;
}

// 芳香环识别（启发式，简化版）
function findAromaticRingsImproved(atoms) {
  const rings = [];
  const grouped = {};
  atoms.forEach((a) => {
    const k = `${(a.resn || "").toUpperCase()}:${a.resi}:${a.chain || ""}`;
    if (!grouped[k]) grouped[k] = [];
    grouped[k].push(a);
  });
  for (const k in grouped) {
    const list = grouped[k];
    const resn = (list[0].resn || "").toUpperCase();
    const heavy = list.filter((x) => x.elem && x.elem !== "H");
    if (CONFIG.AROMATIC_RESIDUES && CONFIG.AROMATIC_RESIDUES.includes(resn)) {
      if (heavy.length >= 4)
        rings.push({
          center: Utils.calculateCenter(heavy),
          atoms: heavy,
        });
    } else if (heavy.length >= 5 && heavy.length <= 12) {
      const avgDist = (() => {
        let s = 0,
          c = 0;
        for (let i = 0; i < heavy.length; i++)
          for (let j = i + 1; j < heavy.length; j++) {
            const dx = heavy[i].x - heavy[j].x;
            const dy = heavy[i].y - heavy[j].y;
            const dz = heavy[i].z - heavy[j].z;
            s += Math.sqrt(dx * dx + dy * dy + dz * dz);
            c++;
          }
        return c ? s / c : 0;
      })();
      if (avgDist > 1.2 && avgDist < 1.8)
        rings.push({
          center: Utils.calculateCenter(heavy),
          atoms: heavy,
        });
    }
  }
  return rings;
}

// 从HETATM记录识别配体（专门处理配体文件）
function identifyLigandFromHETATM(atoms, residueMap) {
  console.log("使用HETATM优先识别配体");

  const hetatmResidues = new Map();
  for (const a of atoms) {
    if (
      a.hetflag ||
      a.hetatm ||
      a.record === "HETATM" ||
      (a.record && a.record.includes("HETATM"))
    ) {
      const resn = (a.resn || "").toUpperCase();
      const resi = a.resi;
      const chain = a.chain || "";
      const key = `${resn}:${resi}:${chain}`;

      if (!hetatmResidues.has(key)) {
        hetatmResidues.set(key, []);
      }
      hetatmResidues.get(key).push(a);
    }
  }

  console.log("HETATM残基:", Array.from(hetatmResidues.keys()));

  if (hetatmResidues.size === 0) {
    console.log("未找到HETATM记录，使用常规识别");
    return identifyLigandFromResidueMap(residueMap);
  }

  const candidates = [];
  for (const [key, group] of hetatmResidues.entries()) {
    const heavy = group.filter((x) => x.elem && x.elem !== "H");
    const hasC = heavy.some((x) => x.elem === "C");
    const elems = new Set(heavy.map((x) => x.elem));
    const allMetal = [...elems].every((e) => METAL_ELEMENTS.has(e));

    candidates.push({
      key,
      count: heavy.length,
      hasC,
      allMetal,
      vol: computeBBoxVolume(group),
      resn: key.split(":")[0],
      isHetatm: true,
    });
  }

  candidates.sort((a, b) => {
    if (a.count !== b.count) return b.count - a.count;
    return b.vol - a.vol;
  });

  console.log(
    "HETATM候选排序:",
    candidates.map((c) => ({
      key: c.key,
      count: c.count,
      resn: c.resn,
      isHetatm: c.isHetatm,
    })),
  );

  const selected = candidates.filter((c) => c.hasC && !c.allMetal);
  if (selected.length > 0) {
    const result = new Set([selected[0].key]);
    console.log("从HETATM选择的配体:", Array.from(result));
    return result;
  }

  return identifyLigandFromResidueMap(residueMap);
}

// 从残基映射中识别配体（常规方法）
function identifyLigandFromResidueMap(residueMap) {
  const candidates = [];
  for (const [key, group] of residueMap.entries()) {
    const heavy = group.filter((x) => x.elem && x.elem !== "H");
    const hasC = heavy.some((x) => x.elem === "C");
    const elems = new Set(heavy.map((x) => x.elem));
    const allMetal = [...elems].every((e) => METAL_ELEMENTS.has(e));

    candidates.push({
      key,
      count: heavy.length,
      hasC,
      allMetal,
      vol: computeBBoxVolume(group),
      resn: key.split(":")[0],
    });
  }

  candidates.sort((a, b) => b.count - a.count);

  const selected = candidates.filter((c) => c.hasC && !c.allMetal);
  if (selected.length > 0) {
    const result = new Set([selected[0].key]);
    console.log("常规识别选择的配体:", Array.from(result));
    return result;
  }

  return new Set();
}

// 调试函数：显示所有残基信息
function debugAllResidues() {
  if (!viewer) {
    console.log("请先加载分子结构");
    return;
  }

  const atoms = viewer.selectedAtoms({});
  console.log("=== 所有残基调试信息 ===");
  console.log("总原子数:", atoms.length);

  const residueMap = new Map();
  const waterSet = new Set(
    (CONFIG.WATER_RESIDUES || []).map((r) => r.toUpperCase()),
  );
  const proteinSet = new Set(
    (CONFIG.PROTEIN_RESIDUES || []).map((r) => r.toUpperCase()),
  );

  for (const a of atoms) {
    const resn = (a.resn || "").toUpperCase();
    const resi = a.resi;
    const chain = a.chain || "";
    const key = `${resn}:${resi}:${chain}`;

    if (!residueMap.has(key)) {
      residueMap.set(key, {
        resn,
        resi,
        chain,
        atoms: [],
        heavyAtoms: 0,
        elements: new Set(),
        isWater: waterSet.has(resn),
        isProtein: proteinSet.has(resn),
        isHetatm: a.hetflag || a.hetatm || a.record === "HETATM",
      });
    }

    residueMap.get(key).atoms.push(a);
    if (a.elem && a.elem !== "H") {
      residueMap.get(key).heavyAtoms++;
    }
    if (a.elem) {
      residueMap.get(key).elements.add(a.elem);
    }
  }

  const allResidues = Array.from(residueMap.entries()).map(([key, info]) => ({
    key,
    ...info,
    elements: Array.from(info.elements),
  }));

  allResidues.forEach((res) => {
    console.log(
      `${res.key} | 重原子: ${res.heavyAtoms} | 元素: [${res.elements.join(",")}] | 水: ${res.isWater} | 蛋白: ${res.isProtein} | HETATM: ${res.isHetatm}`,
    );
  });

  const ligandCandidates = allResidues.filter(
    (res) => !res.isWater && !res.isProtein,
  );
  const hetatmCandidates = allResidues.filter((res) => res.isHetatm);
  const currentLigands = identifyLigandResidueKeys(atoms);

  console.log(
    "非蛋白质、非水分子残基:",
    ligandCandidates.map((r) => r.key),
  );
  console.log(
    "HETATM残基:",
    hetatmCandidates.map((r) => r.key),
  );
  console.log("当前识别的配体:", Array.from(currentLigands));

  return {
    allResidues,
    ligandCandidates,
    hetatmCandidates,
    currentLigands: Array.from(currentLigands),
  };
}

// 手动选择配体残基
function selectLigandManually(
  residueName,
  residueNumber = null,
  chainId = null,
) {
  if (!viewer) {
    console.log("请先加载分子结构");
    return false;
  }

  const atoms = viewer.selectedAtoms({});
  const residueMap = new Map();

  for (const a of atoms) {
    const resn = (a.resn || "").toUpperCase();
    const resi = a.resi;
    const chain = a.chain || "";
    const key = `${resn}:${resi}:${chain}`;

    if (!residueMap.has(key)) residueMap.set(key, []);
    residueMap.get(key).push(a);
  }

  const matches = [];
  for (const [key, group] of residueMap.entries()) {
    const [resn, resi, chain] = key.split(":");

    let isMatch = resn === residueName.toUpperCase();
    if (residueNumber !== null) {
      isMatch = isMatch && parseInt(resi) === parseInt(residueNumber);
    }
    if (chainId !== null) {
      isMatch = isMatch && chain === chainId;
    }

    if (isMatch) {
      matches.push({ key, atoms: group });
    }
  }

  if (matches.length === 0) {
    console.log(
      `未找到匹配的残基: ${residueName}${residueNumber ? ":" + residueNumber : ""}${chainId ? ":" + chainId : ""}`,
    );
    return false;
  }

  const ligandKeys = matches.map((m) => m.key);
  setLigandResidueNames(ligandKeys.map((k) => k.split(":")[0]));

  console.log(`已手动选择配体: ${ligandKeys.join(", ")}`);
  return true;
}

// 暴露配体识别函数到全局，方便调试和使用
window.setLigandResidueNames = setLigandResidueNames;
window.clearLigandResidueNames = clearLigandResidueNames;
window.autoDetectLigandResidueNames = autoDetectLigandResidueNames;
window.identifyLigandResidueKeys = identifyLigandResidueKeys;
window.identifyLigandFromHETATM = identifyLigandFromHETATM;
window.debugAllResidues = debugAllResidues;
window.selectLigandManually = selectLigandManually;
window.cacheLigandInfo = cacheLigandInfo;
window.clearLigandCache = clearLigandCache;
window.autoFocusToLigand = autoFocusToLigand;
