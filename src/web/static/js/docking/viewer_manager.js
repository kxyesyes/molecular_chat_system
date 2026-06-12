// ==================== 3D查看器管理 ====================
const ViewerManager = {
  // 初始化3D查看器
  init() {
    const container = document.getElementById("molecule-viewer");
    const placeholder = document.getElementById("viewer-placeholder");

    if (container && !AppState.viewer) {
      if (typeof window.$3Dmol === "undefined") {
        console.error("3Dmol.js 未加载，无法初始化分子查看器");
        if (placeholder) {
          placeholder.style.display = "flex";
          const text = placeholder.querySelector(".viewer-text");
          if (text) {
            text.innerHTML = `
              <div>3D 查看器加载失败</div>
              <div style="font-size: 14px; margin-top: 5px">
                请确认 /static/vendor/3dmol/3Dmol-min.js 可正常访问
              </div>
            `;
          }
        }
        return;
      }

      AppState.viewer = $3Dmol.createViewer(container, {
        backgroundColor: "white",
        defaultcolors: $3Dmol.rasmolElementColors,
      });

      AppState.viewer.setStyle(
        {},
        {
          stick: { radius: 0.1 },
          sphere: { scale: 0.3 },
        },
      );

      if (placeholder) placeholder.style.display = "none";
      console.log("3D查看器初始化成功");
    }
  },

  // 加载分子结构
  loadMolecule(pdbData, type = "protein", sourceName = "") {
    if (!AppState.viewer) this.init();
    if (!AppState.viewer) {
      console.error("查看器初始化失败");
      return;
    }

    try {
      AppState.viewer.clear();

      let format = "pdb";
      const sourceNameLower = (sourceName || "").toLowerCase();

      const looksLikePdbqt =
        /(^|\n)ROOT\b/i.test(pdbData) ||
        /(^|\n)BRANCH\b/i.test(pdbData) ||
        /(^|\n)TORSDOF\b/i.test(pdbData) ||
        pdbData.includes("REMARK VINA RESULT");

      if (type === "protein" && sourceNameLower.endsWith(".pdbqt")) {
        format = "pdbqt";
      } else if (type === "protein" && sourceNameLower.endsWith(".pdb")) {
        format = "pdb";
      } else if (type === "protein") {
        format = looksLikePdbqt
          ? "pdbqt"
          : AppState.currentProteinFormat || "pdb";
      } else if (
        type === "ligand" &&
        (sourceNameLower.endsWith(".pdbqt") || looksLikePdbqt)
      ) {
        const normalizedLigandPdb = extractPosePdbFromPdbqt(pdbData, 1);
        if (normalizedLigandPdb) {
          pdbData = normalizedLigandPdb;
          format = "pdb";
        } else {
          format = "pdbqt";
        }
      } else if (type === "ligand" && pdbData.includes("$$$$")) {
        format = "sdf";
      } else if (type === "ligand" && pdbData.includes("M  END")) {
        format = "mol";
      }

      if (type === "protein") {
        AppState.currentProteinFormat = format;
      }

      // 专门处理复合物：组合蛋白+配体（从PDBQT提取）
      if (type === "complex") {
        if (AppState.currentProteinData) {
          const protModel = AppState.viewer.addModel(
            AppState.currentProteinData,
            AppState.currentProteinFormat || "pdb",
          );
          if (protModel && protModel.setStyle) {
            protModel.setStyle({}, { cartoon: { color: "spectrum" } });
            const waterAndOtherResns = [
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
            waterAndOtherResns.forEach((resn) =>
              protModel.setStyle({ resn }, {}),
            );
          } else {
            AppState.viewer.setStyle(
              { model: 0 },
              { cartoon: { color: "spectrum" } },
            );
            const waterAndOtherResns = [
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
            waterAndOtherResns.forEach((resn) =>
              AppState.viewer.setStyle({ resn }, {}),
            );
          }
        }

        let ligandPdbText = "";
        try {
          if (
            pdbData &&
            (pdbData.includes("REMARK VINA") || /(^|\n)MODEL\s+/i.test(pdbData))
          ) {
            ligandPdbText = extractPosePdbFromPdbqt(pdbData, 1) || "";
          }
        } catch (e) {
          ligandPdbText = "";
        }

        let ligandDataToUse =
          ligandPdbText && ligandPdbText.length > 0
            ? ligandPdbText
            : AppState.originalLigandData || AppState.currentLigandData || "";

        let ligandFormat = "pdb";
        if (ligandPdbText && ligandPdbText.length > 0) {
          ligandFormat = "pdb";
        } else if (ligandDataToUse.includes("$$$$")) {
          ligandFormat = "sdf";
        } else if (ligandDataToUse.includes("M  END")) {
          ligandFormat = "mol";
        }

        if (!ligandDataToUse) {
          console.warn(
            "未能提取配体PDB，且无缓存配体数据，复合物中可能不显示配体。",
          );
        }

        const ligModel = ligandDataToUse
          ? AppState.viewer.addModel(ligandDataToUse, ligandFormat)
          : null;
        if (ligModel && ligModel.setStyle) {
          ligModel.setStyle(
            {},
            { stick: { radius: 0.25, colorscheme: "greenCarbon" } },
          );
        }

        AppState.viewer.zoomTo();
        AppState.viewer.render();
        syncViewerModeState("complex");
        console.log("complex 结构加载成功 (蛋白+配体)");

        setTimeout(() => {
          ensureLigandVisibility();
        }, 100);

        return;
      }

      // 普通蛋白/配体加载
      AppState.viewer.addModel(pdbData, format);

      if (type === "protein") {
        AppState.viewer.setStyle(
          {},
          {
            cartoon: { color: "spectrum" },
            stick: { radius: 0.1 },
          },
        );
      } else if (type === "ligand") {
        AppState.viewer.setStyle(
          {},
          {
            stick: { radius: 0.2, colorscheme: "greenCarbon" },
            sphere: { scale: 0.3, colorscheme: "greenCarbon" },
          },
        );
      }

      AppState.viewer.zoomTo();
      AppState.viewer.render();
      syncViewerModeState(type);
      console.log(`${type} 结构加载成功 (格式: ${format})`);
    } catch (error) {
      console.error(`加载 ${type} 结构失败:`, error);
      try {
        AppState.viewer.clear();
        AppState.viewer.addModel(pdbData, "pdb");
        AppState.viewer.zoomTo();
        AppState.viewer.render();
        syncViewerModeState(type);
        console.log(`${type} 结构使用默认格式加载成功`);
      } catch (fallbackError) {
        console.error("默认格式加载也失败:", fallbackError);
      }
    }
  },

  // 重置视角
  reset() {
    if (AppState.viewer) {
      AppState.viewer.zoomTo();
      AppState.viewer.render();
    }
  },

  // 导出图片
  exportImage() {
    if (AppState.viewer) {
      const png = AppState.viewer.pngURI();
      const link = document.createElement("a");
      link.download = "molecule_structure.png";
      link.href = png;
      link.click();
    }
  },
};

function syncViewerModeState(mode) {
  if (!["protein", "ligand", "complex"].includes(mode)) return;
  AppState.currentViewerMode = mode;
  const buttonMap = {
    protein: "show-protein-btn",
    ligand: "show-ligand-btn",
    complex: "show-complex-btn",
  };
  Object.values(buttonMap).forEach((buttonId) => {
    const button = document.getElementById(buttonId);
    if (button) button.classList.remove("active");
  });
  const activeButton = document.getElementById(buttonMap[mode]);
  if (activeButton) {
    activeButton.classList.add("active");
  }
}

// 兼容旧函数名（向后兼容）
function initViewer() {
  ViewerManager.init();
}
function loadMolecule(pdbData, type, sourceName) {
  ViewerManager.loadMolecule(pdbData, type, sourceName);
}
function resetView() {
  if (viewer) {
    viewer.zoomTo();
    viewer.render();
  }
}
function exportImage() {
  ViewerManager.exportImage();
}

// ==================== 图片样式与导出（带放大框） ====================
const StyleManager = {
  apply(styleName, showInteractions = true) {
    if (!viewer) return;
    AppState.currentStyle = styleName || AppState.currentStyle || "default";
    try {
      viewer.setBackgroundColor("white");
      viewer.setStyle({}, {});
      viewer.removeAllLabels();

      const proteinSel = { resn: CONFIG.PROTEIN_RESIDUES };

      const atoms = viewer.selectedAtoms({});
      const ligandKeys = identifyLigandResidueKeys(atoms);
      const ligandSelArr = Array.from(ligandKeys).map((k) => {
        const [resn, resi, chain] = k.split(":");
        return { resn, resi: parseInt(resi), chain };
      });

      const setLigandStyle = (style) => {
        if (ligandSelArr.length === 0) {
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
            style,
          );
        } else {
          ligandSelArr.forEach((sel) => viewer.setStyle(sel, style));
        }
      };

      switch (AppState.currentStyle) {
        case "gray": {
          viewer.setBackgroundColor("white");
          viewer.setStyle(proteinSel, { cartoon: { color: "#bfbfbf" } });
          setLigandStyle({
            stick: { radius: 0.25, colorscheme: "magentaCarbon" },
          });
          break;
        }
        case "dark": {
          viewer.setBackgroundColor("#0b1020");
          viewer.setStyle(proteinSel, { cartoon: { color: "#e6eef8" } });
          setLigandStyle({
            stick: { radius: 0.25, colorscheme: "yellowCarbon" },
          });
          break;
        }
        default: {
          viewer.setBackgroundColor("white");
          viewer.removeAllShapes();
          viewer.removeAllLabels();
          viewer.setStyle({}, {});

          viewer.setStyle(proteinSel, { cartoon: { color: "spectrum" } });
          setLigandStyle({
            stick: { radius: 0.25, colorscheme: "greenCarbon" },
          });
        }
      }

      viewer.zoomTo();
      viewer.render();

      if (showInteractions) {
        setTimeout(() => {
          if (interactionsState.hydrogenBonds) displayHydrogenBonds();
          if (interactionsState.piPiInteractions) displayPiPiInteractions();
          if (interactionsState.hydrophobicInteractions)
            displayHydrophobicInteractions();
        }, 100);
      }
    } catch (e) {
      console.warn("应用样式失败:", e);
    }
  },

  // 添加氨基酸残基标签
  addResidueLabels(atoms, ligandKeys) {
    try {
      const ligandAtoms = atomsForResidueKeys(atoms, ligandKeys);
      const proteinAtoms = atoms.filter((a) => {
        const rn = (a.resn || "").toUpperCase();
        return CONFIG.PROTEIN_RESIDUES.includes(rn);
      });

      const ligandCenter =
        ligandAtoms.length > 0
          ? Utils.calculateCenter(ligandAtoms)
          : { x: 0, y: 0, z: 0 };

      const nearbyResidues = new Map();
      const DISTANCE_CUTOFF = 5.0;

      proteinAtoms.forEach((pAtom) => {
        ligandAtoms.forEach((lAtom) => {
          const dist = Utils.calculateDistance(pAtom, lAtom);
          if (dist <= DISTANCE_CUTOFF) {
            const key = `${pAtom.resn}:${pAtom.resi}:${pAtom.chain || ""}`;
            if (!nearbyResidues.has(key)) {
              nearbyResidues.set(key, {
                resn: pAtom.resn,
                resi: pAtom.resi,
                chain: pAtom.chain,
                atoms: [],
                minDist: Infinity,
              });
            }
            nearbyResidues.get(key).atoms.push(pAtom);
            if (dist < nearbyResidues.get(key).minDist) {
              nearbyResidues.get(key).minDist = dist;
            }
          }
        });
      });

      nearbyResidues.forEach((resInfo) => {
        if (resInfo.atoms.length > 0) {
          const resCenter = Utils.calculateCenter(resInfo.atoms);

          const dx = resCenter.x - ligandCenter.x;
          const dy = resCenter.y - ligandCenter.y;
          const dz = resCenter.z - ligandCenter.z;
          const len = Math.sqrt(dx * dx + dy * dy + dz * dz) || 1.0;

          const offset = 1.5;
          const labelPos = {
            x: resCenter.x + (dx / len) * offset,
            y: resCenter.y + (dy / len) * offset,
            z: resCenter.z + (dz / len) * offset,
          };

          const label = `${resInfo.resn}${resInfo.resi}`;
          viewer.addLabel(label, {
            position: labelPos,
            backgroundColor: "rgba(255, 255, 255, 0.85)",
            backgroundOpacity: 0.85,
            fontColor: "#2d3748",
            fontSize: 11,
            showBackground: true,
            borderThickness: 0.5,
            borderColor: "#cbd5e0",
            alignment: "center",
          });
        }
      });

      viewer.render();
    } catch (e) {
      console.warn("添加残基标签失败:", e);
    }
  },
};

// 样式选择变化时自动应用
function onStyleChange(selectElement) {
  const val = selectElement.value;
  StyleManager.apply(val, true);
  Utils.showToast(
    `已应用${selectElement.options[selectElement.selectedIndex].text}`,
    "success",
  );
}

function exportStyledImage() {
  if (!viewer) return;
  const sel = document.getElementById("image-style");
  const styleName = sel ? sel.value : AppState.currentStyle;
  const prev = AppState.currentStyle;
  StyleManager.apply(styleName, true);

  setTimeout(() => {
    const mainUri = viewer.pngURI();
    continueExport(mainUri, styleName, prev);
  }, 200);
}

function continueExport(mainUri, styleName, prev) {
  const off = document.createElement("div");
  off.style.cssText =
    "position:fixed;left:-9999px;top:-9999px;width:420px;height:320px;background:#fff;";
  document.body.appendChild(off);
  const inset = $3Dmol.createViewer(off, { backgroundColor: "white" });
  try {
    if (currentProteinData) {
      const m = inset.addModel(
        currentProteinData,
        AppState.currentProteinFormat || "pdb",
      );
      m && m.setStyle({}, { cartoon: { color: "#c6ccd4", opacity: 0.96 } });
    }
    const dockedSdf = AppState.dockedLigandData || null;
    let ligText = dockedSdf || currentLigandData || "";
    const ligFormat = dockedSdf ? "sdf" : "pdb";
    if (!ligText && currentComplexData) {
      ligText = extractPosePdbFromPdbqt(currentComplexData, 1) || "";
    }
    if (ligText) {
      const lm = inset.addModel(ligText, ligFormat);
      lm &&
        lm.setStyle({}, { stick: { radius: 0.25, colorscheme: "default" } });
    }
    inset.zoomTo();
    inset.render();
  } catch (_) {}

  setTimeout(() => {
    let insetUri = "";
    try {
      insetUri = inset.pngURI();
    } catch (_) {}

    const imgMain = new Image();
    imgMain.onload = () => {
      const canvas = document.createElement("canvas");
      const ctx = canvas.getContext("2d");
      canvas.width = imgMain.width;
      canvas.height = imgMain.height;
      ctx.drawImage(imgMain, 0, 0);

      if (insetUri) {
        const imgInset = new Image();
        imgInset.onload = () => {
          const W = canvas.width,
            H = canvas.height;
          const insetW = Math.round(W * 0.38);
          const insetH = Math.round(H * 0.38);
          const insetX = W - insetW - 18;
          const insetY = 18;
          ctx.fillStyle = "#ffffff";
          ctx.fillRect(insetX - 4, insetY - 4, insetW + 8, insetH + 8);
          ctx.strokeStyle = "#000";
          ctx.lineWidth = 2;
          ctx.strokeRect(insetX - 4, insetY - 4, insetW + 8, insetH + 8);
          ctx.drawImage(imgInset, insetX, insetY, insetW, insetH);

          ctx.setLineDash([10, 7]);
          const roiW = Math.round(W * 0.32),
            roiH = Math.round(H * 0.24);
          const roiX = Math.round(W * 0.32),
            roiY = Math.round(H * 0.42);
          ctx.lineWidth = 2;
          ctx.strokeStyle = "#000";
          ctx.strokeRect(roiX, roiY, roiW, roiH);
          ctx.setLineDash([]);
          ctx.beginPath();
          ctx.moveTo(roiX + roiW, roiY + roiH / 2);
          ctx.lineTo(insetX, insetY + insetH / 2);
          ctx.stroke();
        };
        imgInset.src = insetUri;
      }

      const out = canvas.toDataURL("image/png");
      const a = document.createElement("a");
      a.download = "styled_snapshot.png";
      a.href = out;
      a.click();

      setTimeout(() => {
        document.body.removeChild(off);
        StyleManager.apply(prev, true);
      }, 100);
    };
    imgMain.src = mainUri;
  }, 60);
}

// 简易冒烟测试：检查按钮与关键函数
function runDockingPageSmokeTest() {
  const results = [];
  const mustHaveEls = [
    "start-btn",
    "show-protein-btn",
    "show-ligand-btn",
    "show-complex-btn",
    "show-hbonds-btn",
    "show-pi-btn",
    "show-hydrophobic-btn",
    "pose-select",
    "image-style",
  ];
  mustHaveEls.forEach((id) => {
    const ok = !!document.getElementById(id);
    results.push({ item: `元素#${id}`, pass: ok });
  });
  const mustHaveFns = [
    "showProtein",
    "showLigand",
    "showComplex",
    "toggleHydrogenBonds",
    "togglePiPiInteractions",
    "toggleHydrophobicInteractions",
    "overlayTopN",
    "viewPose",
    "exportStyledImage",
    "applyStyleFromUI",
  ];
  mustHaveFns.forEach((fn) => {
    const ok = typeof window[fn] === "function";
    results.push({ item: `函数 ${fn}()`, pass: ok });
  });
  try {
    UIManager && UIManager.updateButtonStates && UIManager.updateButtonStates();
    results.push({ item: "updateButtonStates()", pass: true });
  } catch (e) {
    results.push({ item: "updateButtonStates()", pass: false });
  }
  try {
    resetView();
    results.push({ item: "resetView()", pass: true });
  } catch (e) {
    results.push({ item: "resetView()", pass: false });
  }
  try {
    StyleManager.apply(AppState.currentStyle || "default");
    results.push({ item: "StyleManager.apply()", pass: true });
  } catch (e) {
    results.push({ item: "StyleManager.apply()", pass: false });
  }

  const passCnt = results.filter((r) => r.pass).length;
  const failCnt = results.length - passCnt;
  console.group("分子对接页面自检");
  results.forEach((r) =>
    console[r.pass ? "log" : "error"](`${r.pass ? "✔" : "✘"} ${r.item}`),
  );
  console.log(`总计：通过 ${passCnt}，失败 ${failCnt}`);
  console.groupEnd();
  Utils.showToast(
    failCnt === 0
      ? `自检通过（${passCnt}/${results.length}）`
      : `自检完成：${passCnt} 通过，${failCnt} 失败`,
    failCnt === 0 ? "success" : "warning",
  );
}

// 全屏切换
function toggleFullscreen() {
  const container = document.getElementById("molecule-viewer");
  if (container.requestFullscreen) {
    container.requestFullscreen();
  }
}

// AutoDock PDBQT 原子类型 → 标准 PDB 元素符号映射表
const PDBQT_TO_ELEMENT = {
  A: " C",
  C: " C",
  CA: "CA",
  CB: " C",
  N: " N",
  NA: " N",
  NS: " N",
  NR: " N",
  O: " O",
  OA: " O",
  OS: " O",
  S: " S",
  SA: " S",
  H: " H",
  HD: " H",
  HS: " H",
  P: " P",
  F: " F",
  Cl: "CL",
  CL: "CL",
  Br: "BR",
  BR: "BR",
  I: " I",
  Zn: "ZN",
  ZN: "ZN",
  Fe: "FE",
  FE: "FE",
  Mg: "MG",
  MG: "MG",
  Mn: "MN",
  MN: "MN",
  Cu: "CU",
  CU: "CU",
};

/**
 * 将 PDBQT ATOM 行转换为标准 PDB ATOM 行
 * PDBQT 第77-78列放的是 AutoDock 原子类型（如 A, OA, HD），
 * 而 PDB 第77-78列应该是元素符号（如 C, O, H）。
 * 如果不做映射，3Dmol.js 就会把芳香碳 "A" 识别为未知元素导致结构变形。
 */
function pdbqtLineToPdb(line) {
  // 确保行至少有 78 字符（标准 PDB 宽度）
  const padded = line.padEnd(80);

  // 提取 PDBQT 最后一列：AutoDock 原子类型（通常在第 77-78 列）
  const adType = padded.substring(77).trim().split(/\s+/)[0] || "";
  const elem = PDBQT_TO_ELEMENT[adType];

  if (elem) {
    // 替换第 77-78 列为正确的元素符号，清空 79-80 列避免残留（如 OA 的 A）
    return padded.substring(0, 76) + elem.padStart(2) + "  ";
  }

  // 回退：从原子名称推断
  const atomName = padded.substring(12, 16).trim();
  let guessElem = "";
  if (atomName.length > 0) {
    const first = atomName.charAt(0);
    if (/[CNOSHP]/.test(first)) {
      guessElem = " " + first;
    } else if (atomName.length >= 2) {
      const two = atomName.substring(0, 2).toUpperCase();
      if (["CL", "BR", "ZN", "FE", "MG", "MN", "CU", "CA"].includes(two)) {
        guessElem = two;
      }
    }
    if (!guessElem) guessElem = " " + first;
  }

  return padded.substring(0, 76) + (guessElem || " C").padStart(2) + "  ";
}

// 从PDBQT提取指定POSE为标准PDB字符串（默认提取第1个）
// 注意：不手动生成 CONECT 记录，由 3Dmol.js 使用其内置的标准共价半径自动推断化学键。
// 手动生成 CONECT 的做法容易因半径参数不准确产生多余的跨键（如苯环间位碳），
// 而 3Dmol.js 内置算法经过充分验证，元素符号正确后就能正确显示键连接。
function extractPosePdbFromPdbqt(pdbqtText, poseIndex = 1) {
  try {
    const lines = pdbqtText.split(/\r?\n/);
    let current = 0;
    let collecting = false;
    const rawAtoms = [];

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      if (/^MODEL\s+/i.test(line)) {
        current += 1;
        collecting = current === poseIndex;
        continue;
      }
      if (/^ENDMDL/i.test(line)) {
        if (collecting) break;
        collecting = false;
        continue;
      }
      if (collecting) {
        if (line.startsWith("ATOM") || line.startsWith("HETATM")) {
          rawAtoms.push(line);
        }
      }
    }

    // 如果没有 MODEL 记录，则取全部 ATOM 行（单姿势文件）
    if (rawAtoms.length === 0) {
      for (const line of lines) {
        if (line.startsWith("ATOM") || line.startsWith("HETATM")) {
          rawAtoms.push(line);
        }
      }
    }

    if (rawAtoms.length > 0) {
      // 将 PDBQT ATOM 行转换为标准 PDB（修正第77-78列元素符号）
      // 然后交给 3Dmol.js 用其内置算法自动推断键连接
      const pdbAtoms = rawAtoms.map(pdbqtLineToPdb);
      return [...pdbAtoms, "END"].join("\n");
    }
  } catch (e) {
    console.warn("提取PDBQT姿势失败:", e);
  }
  return "";
}

// 为了兼容性，保留viewer变量的访问
Object.defineProperty(window, "viewer", {
  get: () => AppState.viewer,
  set: (val) => {
    AppState.viewer = val;
  },
});

// 为关键全局状态创建兼容访问器
Object.defineProperty(window, "currentProteinData", {
  get: () => AppState.currentProteinData,
  set: (val) => {
    AppState.currentProteinData = val;
  },
});

Object.defineProperty(window, "currentLigandData", {
  get: () => AppState.currentLigandData,
  set: (val) => {
    AppState.currentLigandData = val;
  },
});

Object.defineProperty(window, "originalLigandData", {
  get: () => AppState.originalLigandData,
  set: (val) => {
    AppState.originalLigandData = val;
  },
});

Object.defineProperty(window, "dockedLigandData", {
  get: () => AppState.dockedLigandData,
  set: (val) => {
    AppState.dockedLigandData = val;
  },
});

Object.defineProperty(window, "currentComplexData", {
  get: () => AppState.currentComplexData,
  set: (val) => {
    AppState.currentComplexData = val;
  },
});

Object.defineProperty(window, "stepManager", {
  get: () => AppState.stepManager,
  set: (val) => {
    AppState.stepManager = val;
  },
});

// 统一交互状态引用，修复 interactionsState 未定义的问题
const interactionsState = AppState.interactions;

// 获取当前任务ID
function getCurrentJobId() {
  return AppState.currentJobId;
}

// 设置当前任务ID
function setCurrentJobId(jobId) {
  AppState.currentJobId = jobId;
}
