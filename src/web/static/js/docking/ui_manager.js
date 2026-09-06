// ==================== UI状态管理 ====================
const Safe = window.MedChatSafeRender;

const UIManager = {
  // 更新按钮状态
  updateButtonStates() {
    const proteinBtn = document.getElementById("show-protein-btn");
    const ligandBtn = document.getElementById("show-ligand-btn");
    const complexBtn = document.getElementById("show-complex-btn");

    // 更新蛋白质按钮状态
    const proteinEnabled = !!AppState.currentProteinData;
    proteinBtn.disabled = !proteinEnabled;
    proteinBtn.style.opacity = proteinEnabled ? "1" : "0.5";

    // 更新配体按钮状态
    const ligandInput = document.getElementById("ligand-file");
    const ligandFile = ligandInput && ligandInput.files[0];
    const smilesInput = document.getElementById("smiles-input").value.trim();
    const batchSmilesInput = document.getElementById("batch-smiles-input");
    const batchSmiles = batchSmilesInput ? batchSmilesInput.value.trim() : "";
    const isBatch = AppState.dockingMode === "batch";
    const ligandEnabled = isBatch
      ? !!((ligandInput && ligandInput.files.length > 0) || batchSmiles)
      : !!(AppState.currentLigandData || ligandFile || smilesInput);
    ligandBtn.disabled = !ligandEnabled;
    ligandBtn.style.opacity = ligandEnabled ? "1" : "0.5";

    // 更新复合物按钮状态
    const resultsContent = document.getElementById("results-content");
    const hasResults = resultsContent.innerHTML.includes("结构编号");
    const complexEnabled = !!(AppState.currentComplexData || hasResults);
    complexBtn.disabled = !complexEnabled;
    complexBtn.style.opacity = complexEnabled ? "1" : "0.5";
  },

  // 设置按钮激活状态
  setActiveButton(buttonId) {
    ["show-protein-btn", "show-ligand-btn", "show-complex-btn"].forEach(
      (viewButtonId) => {
        const button = document.getElementById(viewButtonId);
        if (button) button.classList.remove("active");
      },
    );
    const activeButton = document.getElementById(buttonId);
    if (activeButton) activeButton.classList.add("active");
  },
};

// 兼容旧函数名
function updateButtonStates() {
  UIManager.updateButtonStates();
}
function setActiveButton(buttonId) {
  UIManager.setActiveButton(buttonId);
}

// Pose 选择控件逻辑
function onPoseSelectChange(sel) {
  const val = parseInt(sel.value || "1");
  if (!isNaN(val)) {
    viewPose(val);
  }
}

// 保存当前样式和相互作用状态
function saveCurrentViewState() {
  return {
    style: AppState.currentStyle,
    interactions: {
      hydrogenBonds: interactionsState.hydrogenBonds,
      piPiInteractions: interactionsState.piPiInteractions,
      hydrophobicInteractions: interactionsState.hydrophobicInteractions,
    },
  };
}

async function fetchDockedPoseSdf(jobId, poseNumber = 1) {
  const response = await fetch(
    `/api/docking/pose_sdf/${jobId}?pose=${poseNumber}`,
  );
  if (!response.ok) {
    let detail = "无法获取对接姿势结构";
    try {
      const err = await response.json();
      detail = err.detail || detail;
    } catch (_) {}
    throw new Error(detail);
  }
  return await response.text();
}

// 恢复样式和相互作用状态
function restoreViewState(state) {
  if (!state) return;
  if (state.style) {
    StyleManager.apply(state.style, false);
  }
  setTimeout(() => {
    if (state.interactions.hydrogenBonds) displayHydrogenBonds();
    if (state.interactions.piPiInteractions) displayPiPiInteractions();
    if (state.interactions.hydrophobicInteractions)
      displayHydrophobicInteractions();
  }, 100);
}

function prevPose() {
  const sel = document.getElementById("pose-select");
  if (!sel || sel.options.length === 0) return;
  sel.selectedIndex = Math.max(0, sel.selectedIndex - 1);
  onPoseSelectChange(sel);
}

function nextPose() {
  const sel = document.getElementById("pose-select");
  if (!sel || sel.options.length === 0) return;
  sel.selectedIndex = Math.min(sel.options.length - 1, sel.selectedIndex + 1);
  onPoseSelectChange(sel);
}

function overlayTopN() {
  const nInput = document.getElementById("overlay-count");
  let n = parseInt(nInput && nInput.value ? nInput.value : "1");
  if (isNaN(n) || n < 1) n = 1;

  const jobId = getCurrentJobId && getCurrentJobId();
  if (!jobId) return;

  if (!viewer) initViewer();

  const savedState = saveCurrentViewState();

  // 为每个 pose 并发请求标准 SDF（用 null 代替失败的 pose）
  const posePromises = [];
  for (let i = 1; i <= n; i++) {
    posePromises.push(fetchDockedPoseSdf(jobId, i).catch(() => null));
  }

  Promise.all(posePromises)
    .then((poseSdfs) => {
      try {
        viewer.clear();

        if (currentProteinData) {
          const prot = viewer.addModel(
            currentProteinData,
            AppState.currentProteinFormat || "pdb",
          );
          if (prot && prot.setStyle) {
            prot.setStyle({}, { cartoon: { color: "spectrum" } });
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
            ];
            waterAndOtherResns.forEach((resn) => prot.setStyle({ resn }, {}));
          } else {
            viewer.setStyle({ model: 0 }, { cartoon: { color: "spectrum" } });
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
            ];
            waterAndOtherResns.forEach((resn) => viewer.setStyle({ resn }, {}));
          }
        }

        const colors = [
          "greenCarbon",
          "blueCarbon",
          "magentaCarbon",
          "yellowCarbon",
          "cyanCarbon",
          "orangeCarbon",
        ];
        poseSdfs.forEach((poseSdf, idx) => {
          if (!poseSdf) return;
          const lig = viewer.addModel(poseSdf, "sdf");
          const cs = colors[idx % colors.length];
          if (lig && lig.setStyle) {
            lig.setStyle({}, { stick: { radius: 0.25, colorscheme: cs } });
          }
        });

        viewer.zoomTo();
        viewer.render();
        restoreViewState(savedState);
      } catch (e) {
        console.error("叠加多个 Pose 失败:", e);
      }
    })
    .catch((err) => console.warn("加载 Pose 叠加结果失败:", err));
}

function showProtein() {
  if (AppState.currentProteinData) {
    loadMolecule(AppState.currentProteinData, "protein");
    setActiveButton("show-protein-btn");
  } else {
    alert("请先上传蛋白质文件");
  }
}

function showLigand() {
  const ligandToShow =
    AppState.originalLigandData || AppState.currentLigandData;

  if (ligandToShow) {
    loadMolecule(ligandToShow, "ligand");
    setActiveButton("show-ligand-btn");
  } else {
    const ligandFile = document.getElementById("ligand-file").files[0];
    const smilesInput = document.getElementById("smiles-input").value.trim();

    if (ligandFile) {
      loadLigandFile(ligandFile, () => setActiveButton("show-ligand-btn"));
    } else if (smilesInput) {
      loadSmilesPreview(smilesInput);
    } else {
      alert("请先上传配体文件或输入SMILES字符串");
    }
  }
}

// 加载SMILES预览
function loadSmilesPreview(smiles) {
  Utils.showToast("正在生成SMILES 3D结构预览...", "info");

  fetch("/api/docking/smiles_to_3d", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ smiles: smiles }),
  })
    .then((response) => {
      if (!response.ok) throw new Error("无法生成3D结构");
      return response.json();
    })
    .then((data) => {
      if (data.success && data.pdb_data) {
        AppState.currentLigandData = data.pdb_data;
        AppState.originalLigandData = data.pdb_data;
        AppState.dockedLigandData = null;
        cacheLigandInfo({
          type: "cached",
          residueName: "LIG",
          smiles: smiles,
          expectedAtoms: estimateAtomCount(smiles),
        });
        loadMolecule(data.pdb_data, "ligand");
        setActiveButton("show-ligand-btn");
        updateButtonStates();
        Utils.showToast("SMILES结构加载成功！", "success");
      } else {
        throw new Error(data.error || "生成3D结构失败");
      }
    })
    .catch((error) => {
      console.error("SMILES预览失败:", error);
      Utils.showToast(`加载失败: ${error.message}`, "error");
    });
}

// 右侧"对接结果"最小高度跟随左侧输入面板高度
function setResultsMinHeightToLeft() {
  try {
    const left = document.querySelector(".input-panel");
    const right = document.querySelector(".result-panel");
    if (!left || !right) return;
    if (window.innerWidth <= 768) {
      left.style.minHeight = "";
      right.style.minHeight = "";
      return;
    }

    left.style.minHeight = "";
    right.style.minHeight = "";

    const leftHeight = Math.ceil(left.getBoundingClientRect().height);
    const rightHeight = Math.ceil(right.getBoundingClientRect().height);
    const maxHeight = Math.max(leftHeight, rightHeight);

    left.style.minHeight = maxHeight + "px";
    right.style.minHeight = maxHeight + "px";
  } catch (_) {}
}

// 兼容旧函数名
function showNotification(message, type) {
  Utils.showToast(message, type);
}

function showComplex() {
  const jobId = getCurrentJobId && getCurrentJobId();

  if (jobId) {
    loadComplexStructure(jobId);
    setActiveButton("show-complex-btn");
  } else {
    const resultsContent = document.getElementById("results-content");
    const hasResults = resultsContent.innerHTML.includes("结构编号");

    if (hasResults) {
      if (jobId) {
        loadComplexStructure(jobId);
      } else {
        alert("复合物结构数据不可用，请重新进行对接分析");
      }
    } else {
      alert("请先进行分子对接分析以生成复合物结构");
    }
  }
}

// 文件上传处理
function handleFileUpload(inputId, displayId, fileType) {
  const input = document.getElementById(inputId);
  const display = document.getElementById(displayId);

  input.addEventListener("change", function (e) {
    const files = Array.from(e.target.files);
    display.innerHTML = "";

    files.forEach((file, index) => {
      const fileItem = document.createElement("div");
      fileItem.className = "file-item";
      fileItem.innerHTML = `
        <span class="file-name">${Safe.escapeHtml(file.name)} (${(file.size / 1024).toFixed(1)} KB)</span>
        <button class="file-remove" onclick="removeFile(${index}, '${Safe.escapeInlineJsString(inputId)}', '${Safe.escapeInlineJsString(displayId)}')">删除</button>
      `;
      display.appendChild(fileItem);

      if (
        fileType === "protein" &&
        (file.name.toLowerCase().endsWith(".pdb") ||
          file.name.toLowerCase().endsWith(".pdbqt"))
      ) {
        loadProteinFile(file);
      }

      if (
        fileType === "ligand" &&
        AppState.dockingMode !== "batch" &&
        (file.name.toLowerCase().endsWith(".sdf") ||
          file.name.toLowerCase().endsWith(".mol") ||
          file.name.toLowerCase().endsWith(".pdbqt"))
      ) {
        loadLigandFile(file);
      }
    });
  });
}

// 加载蛋白质文件
function loadProteinFile(file) {
  const reader = new FileReader();
  reader.onload = function (e) {
    const pdbData = e.target.result;
    currentProteinData = pdbData;
    AppState.currentProteinFormat = file.name.toLowerCase().endsWith(".pdbqt")
      ? "pdbqt"
      : "pdb";
    loadMolecule(pdbData, "protein", file.name);

    try {
      const cxEl = document.getElementById("center_x");
      const cyEl = document.getElementById("center_y");
      const czEl = document.getElementById("center_z");
      const sxEl = document.getElementById("size_x");
      const syEl = document.getElementById("size_y");
      const szEl = document.getElementById("size_z");

      const cx = cxEl ? parseFloat(cxEl.value || "0") : 0;
      const cy = cyEl ? parseFloat(cyEl.value || "0") : 0;
      const cz = czEl ? parseFloat(czEl.value || "0") : 0;
      const sx = sxEl ? parseFloat(sxEl.value || "20") : 20;
      const sy = syEl ? parseFloat(syEl.value || "20") : 20;
      const sz = szEl ? parseFloat(szEl.value || "20") : 20;

      const isDefaultCenter =
        Math.abs(cx) < 1e-6 && Math.abs(cy) < 1e-6 && Math.abs(cz) < 1e-6;
      const isDefaultSize =
        Math.abs(sx - 20) < 1e-6 &&
        Math.abs(sy - 20) < 1e-6 &&
        Math.abs(sz - 20) < 1e-6;

      if (isDefaultCenter && isDefaultSize) {
        setTimeout(() => {
          autoSetBoxToLigand(true);
        }, 80);
      }
    } catch (e) {
      console.warn("自动定位默认对接盒失败:", e);
    }

    const statusIndicators = document.querySelectorAll(".status-indicator");
    statusIndicators.forEach((indicator) => {
      if (indicator.textContent.includes("等待输入")) {
        indicator.className = "status-indicator status-completed";
        indicator.textContent = "已加载";
      }
    });

    updateButtonStates();
    setResultsMinHeightToLeft();
  };
  reader.readAsText(file);
}

// 加载配体文件
function loadLigandFile(file, callback = null) {
  const reader = new FileReader();
  reader.onload = function (e) {
    const fileData = e.target.result;
    const fileName = file.name.toLowerCase();

    if (fileName.endsWith(".sdf") || fileName.endsWith(".mol")) {
      convertLigandToPDB(fileData, fileName, callback);
    } else if (fileName.endsWith(".pdbqt")) {
      currentLigandData = fileData;
      originalLigandData = fileData;
      dockedLigandData = null;

      const ligandResidueName = extractLigandResidueNameFromPDBQT(fileData);
      cacheLigandInfo({
        type: "cached",
        residueName: ligandResidueName,
        fileName: fileName,
        expectedAtoms: estimateAtomCountFromPDB(fileData),
      });

      loadMolecule(fileData, "ligand", file.name);

      if (callback) callback();
    }

    const statusIndicators = document.querySelectorAll(".status-indicator");
    statusIndicators.forEach((indicator) => {
      if (indicator.textContent.includes("等待输入")) {
        indicator.className = "status-indicator status-completed";
        indicator.textContent = "已加载";
      }
    });

    updateButtonStates();
    setResultsMinHeightToLeft();

    if (callback) callback();
  };
  reader.readAsText(file);
}

// 转换配体文件为PDB格式
function convertLigandToPDB(fileData, fileName, callback = null) {
  try {
    const lines = fileData.split("\n");
    let pdbData = "";
    let atomCount = 0;

    for (let line of lines) {
      if (line.startsWith("ATOM") || line.startsWith("HETATM")) {
        pdbData += line + "\n";
        atomCount++;
      }
    }

    if (atomCount > 0) {
      currentLigandData = pdbData;
      originalLigandData = pdbData;
      dockedLigandData = null;
      loadMolecule(pdbData, "ligand");
      console.log(`配体文件转换成功，包含 ${atomCount} 个原子`);
    } else {
      currentLigandData = fileData;
      originalLigandData = fileData;
      dockedLigandData = null;
      loadMolecule(fileData, "ligand");
      console.log("使用原始配体数据");
    }

    if (callback) callback();
  } catch (error) {
    console.error("配体文件转换失败:", error);
    currentLigandData = fileData;
    originalLigandData = fileData;
    dockedLigandData = null;
    loadMolecule(fileData, "ligand");
    if (callback) callback();
  }
}

// 移除文件
function removeFile(index, inputId, displayId) {
  const input = document.getElementById(inputId);
  const display = document.getElementById(displayId);

  const dt = new DataTransfer();
  const files = Array.from(input.files);

  files.forEach((file, i) => {
    if (i !== index) dt.items.add(file);
  });

  input.files = dt.files;
  display.innerHTML = "";

  Array.from(input.files).forEach((file, i) => {
    const fileItem = document.createElement("div");
    fileItem.className = "file-item";
    fileItem.innerHTML = `
      <span class="file-name">${Safe.escapeHtml(file.name)} (${(file.size / 1024).toFixed(1)} KB)</span>
      <button class="file-remove" onclick="removeFile(${i}, '${Safe.escapeInlineJsString(inputId)}', '${Safe.escapeInlineJsString(displayId)}')">删除</button>
    `;
    display.appendChild(fileItem);
  });
}

function getDockingParams() {
  return {
    center_x: parseFloat(document.getElementById("center_x").value || 0),
    center_y: parseFloat(document.getElementById("center_y").value || 0),
    center_z: parseFloat(document.getElementById("center_z").value || 0),
    manual_center: AppState.dockingBoxCenterTouched ? "true" : "false",
    size_x: parseFloat(document.getElementById("size_x").value || 20),
    size_y: parseFloat(document.getElementById("size_y").value || 20),
    size_z: parseFloat(document.getElementById("size_z").value || 20),
    exhaustiveness: parseInt(
      document.getElementById("exhaustiveness").value || 8,
    ),
    num_modes: parseInt(document.getElementById("num_modes").value || 10),
    energy_range: parseFloat(
      document.getElementById("energy_range").value || 3,
    ),
  };
}

function startBatchDocking() {
  const proteinFile = document.getElementById("protein-file").files[0];
  const ligandInput = document.getElementById("ligand-file");
  const ligandFiles = ligandInput ? Array.from(ligandInput.files || []) : [];
  const batchSmilesInput = document.getElementById("batch-smiles-input");
  const batchSmiles = batchSmilesInput ? batchSmilesInput.value.trim() : "";

  if (!proteinFile || (ligandFiles.length === 0 && !batchSmiles)) {
    alert("请上传蛋白质文件，并提供多个配体文件或多行 SMILES");
    return;
  }

  const startBtn = document.getElementById("start-btn");
  if (startBtn && !startBtn.disabled) {
    startBtn.disabled = true;
    startBtn.setAttribute("data-original-text", startBtn.textContent || "开始批量分子对接");
    startBtn.innerHTML =
      '<span class="spinner" aria-hidden="true"></span> 正在批量对接...';
  }

  const statusIndicators = document.querySelectorAll(".status-indicator");
  statusIndicators.forEach((indicator) => {
    indicator.className = "status-indicator status-running";
    indicator.textContent = "批量分析中...";
  });

  const resultsContent = document.getElementById("results-content");
  resultsContent.innerHTML = `
    <div style="padding: 20px; border-radius: 12px; background: #f8fafc; color: #475569;">
      <div style="font-weight: 700; color: #1e293b; margin-bottom: 8px;">批量对接任务已提交</div>
      <div>系统将按顺序对接 ${ligandFiles.length} 个配体文件和 ${
        batchSmiles ? batchSmiles.split(/\r?\n/).filter((line) => line.trim()).length : 0
      } 行 SMILES。请稍候...</div>
    </div>
  `;

  const formData = new FormData();
  formData.append("protein_file", proteinFile);
  ligandFiles.forEach((file) => formData.append("ligand_files", file));
  if (batchSmiles) formData.append("batch_smiles", batchSmiles);
  Object.entries(getDockingParams()).forEach(([k, v]) => formData.append(k, v));

  fetch("/api/docking/batch_submit", {
    method: "POST",
    body: formData,
  })
    .then((response) => {
      if (!response.ok) {
        return response.json().then((err) => {
          throw new Error(err.detail || "批量对接失败");
        });
      }
      return response.json();
    })
    .then((data) => {
      if (!data.results || data.results.length === 0) {
        throw new Error("批量任务没有返回有效结果");
      }
      showBatchResults(data);
      const firstSuccess = data.results.find((item) => item.success && item.job_id);
      if (firstSuccess) {
        setCurrentJobId(firstSuccess.job_id);
      }
      statusIndicators.forEach((indicator) => {
        indicator.className = data.failed > 0
          ? "status-indicator status-running"
          : "status-indicator status-completed";
        indicator.textContent = `完成 ${data.completed}/${data.total}`;
      });
      Utils.showToast(
        `批量对接完成：成功 ${data.completed}，失败 ${data.failed}`,
        data.failed > 0 ? "warning" : "success",
      );
    })
    .catch((error) => {
      console.error("批量对接失败:", error);
      resultsContent.innerHTML = `
        <div style="padding: 20px; border-radius: 12px; background: #fef2f2; color: #991b1b;">
          <div style="font-weight: 700; margin-bottom: 8px;">批量对接失败</div>
          <div>${Safe.escapeHtml(error.message)}</div>
        </div>
      `;
      statusIndicators.forEach((indicator) => {
        indicator.className = "status-indicator status-error";
        indicator.textContent = "批量失败";
      });
      Utils.showToast(error.message, "error");
    })
    .finally(() => {
      if (startBtn) {
        const originalText =
          startBtn.getAttribute("data-original-text") || "开始批量分子对接";
        startBtn.disabled = false;
        startBtn.innerHTML = originalText;
        startBtn.removeAttribute("data-original-text");
      }
      setResultsMinHeightToLeft();
    });
}

// 开始对接分析
function startDocking() {
  if (AppState.dockingMode === "batch") {
    startBatchDocking();
    return;
  }

  const proteinFile = document.getElementById("protein-file").files[0];
  const ligandFile = document.getElementById("ligand-file").files[0];
  const smilesInput = document.getElementById("smiles-input").value.trim();

  if (!proteinFile || (!ligandFile && !smilesInput)) {
    alert("请上传蛋白质文件和配体文件，或输入SMILES字符串");
    return;
  }

  const startBtn = document.getElementById("start-btn");
  if (startBtn && !startBtn.disabled) {
    startBtn.disabled = true;
    startBtn.setAttribute(
      "data-original-text",
      startBtn.textContent || "开始分子对接分析",
    );
    startBtn.innerHTML =
      '<span class="spinner" aria-hidden="true"></span> 正在分析…';
  }

  if (!stepManager) stepManager = new DockingStepManager();
  stepManager.reset();
  stepManager.show();

  const statusIndicators = document.querySelectorAll(".status-indicator");
  statusIndicators.forEach((indicator) => {
    indicator.className = "status-indicator status-running";
    indicator.textContent = "分析中...";
  });

  const progressContainer = document.querySelector(".progress-container");
  const progressBar = document.getElementById("docking-progress");
  const progressLog = document.getElementById("progress-log");
  if (progressLog) {
    progressLog.textContent = "";
    progressLog.style.display = "block";
  }
  progressBar.style.width = "0%";
  progressContainer.style.display = "block";

  window.showStepLog = showStepLog;

  const formData = new FormData();
  formData.append("protein_file", proteinFile);
  if (ligandFile) formData.append("ligand_file", ligandFile);
  if (smilesInput) formData.append("smiles", smilesInput);

  const params = getDockingParams();

  Object.entries(params).forEach(([k, v]) => formData.append(k, v));

  // -------- CLEAN PROGRESS CONTROLLER --------
  let requestFinished = false;
  let simulatedProgress = 0;
  let progressTimer = null;
  const startedSteps = new Set(["prepare_protein"]);
  const requestStartAt = Date.now();

  function renderProgress(percent) {
    const p = Math.max(0, Math.min(100, percent));
    progressBar.style.width = `${p}%`;
    progressBar.setAttribute("data-progress", `${Math.round(p)}%`);
  }

  function showStepLog(stepId, status) {
    const stepInfo = DOCKING_STEPS.find((s) => s.id === stepId);
    if (!stepInfo) return;
    const log = document.getElementById("progress-log");
    if (!log) return;

    const icon = stepInfo.icon || "⚙️";
    let statusClass = "running";
    let statusText = status;

    if (
      status.includes("Success") ||
      status.includes("成功") ||
      status.includes("完成")
    ) {
      statusClass = "success";
      statusText = "✓ 完成";
    } else if (
      status.includes("Failed") ||
      status.includes("失败") ||
      status.includes("错误")
    ) {
      statusClass = "failed";
      statusText = "✗ 失败";
    } else if (
      status.includes("进行中") ||
      status.includes("等待") ||
      status.includes("计算中")
    ) {
      statusText = "⏳ " + status;
    }

    log.innerHTML = `${icon} ${stepInfo.title} - ${stepInfo.description}  <span class="step-status ${statusClass}">${statusText}</span>`;
    log.offsetHeight; // force reflow
  }

  function startProgressSimulation() {
    stepManager.startStep("prepare_protein");
    showStepLog("prepare_protein", "进行中...");
    renderProgress(0);

    progressTimer = setInterval(() => {
      if (requestFinished) return;

      const elapsed = (Date.now() - requestStartAt) / 1000;

      // Smooth curve approaching ~95% maximum until actual response
      simulatedProgress = 95 * (1 - Math.exp(-elapsed / 12));
      renderProgress(simulatedProgress);

      if (elapsed >= 1.5 && !startedSteps.has("prepare_ligand")) {
        startedSteps.add("prepare_ligand");
        stepManager.completeStep("prepare_protein", "蛋白质已解析");
        showStepLog("prepare_protein", "✓ 完成");
        stepManager.startStep("prepare_ligand");
        showStepLog("prepare_ligand", "进行中...");
      }
      if (elapsed >= 4.0 && !startedSteps.has("setup_docking")) {
        startedSteps.add("setup_docking");
        stepManager.completeStep("prepare_ligand", "配体已准备");
        showStepLog("prepare_ligand", "✓ 完成");
        stepManager.startStep("setup_docking");
        showStepLog("setup_docking", "进行中...");
      }
      if (elapsed >= 6.5 && !startedSteps.has("run_docking")) {
        startedSteps.add("run_docking");
        stepManager.completeStep("setup_docking", "生成对接网格");
        showStepLog("setup_docking", "✓ 完成");
        stepManager.startStep("run_docking");
        showStepLog("run_docking", "计算中...");
      }
      if (elapsed >= 15.0 && !startedSteps.has("parse_results")) {
        // purely to keep the UI active during long waiting times
        startedSteps.add("parse_results");
        showStepLog("run_docking", "等待服务器最后响应...");
      }
    }, 100); // Higher frequency for perfectly natural animation
  }

  function clearProgressTimers() {
    if (progressTimer) {
      clearInterval(progressTimer);
      progressTimer = null;
    }
  }

  // 开始平滑动画
  startProgressSimulation();

  fetch("/api/docking/submit", {
    method: "POST",
    body: formData,
  })
    .then((response) => {
      requestFinished = true;
      clearProgressTimers();

      renderProgress(94);
      stepManager.completeStep("run_docking", "Vina计算完成");
      showStepLog("run_docking", "✓ 完成");

      stepManager.startStep("parse_results");
      showStepLog("parse_results", "解析对接坐标...");

      if (!response.ok) {
        return response.json().then((err) => {
          throw new Error(err.detail || "对接计算失败");
        });
      }
      return response.json();
    })
    .then((data) => {
      renderProgress(100);
      stepManager.completeStep("parse_results", "生成3D构象");
      showStepLog("parse_results", "✓ 完成");

      if (data.success) {
        stepManager.completeStep(
          "parse_results",
          `共 ${data.total_poses} 个结构`,
        );
        showStepLog("parse_results", "Success！");
        showRealResults(data);
      } else {
        showStepLog("parse_results", "Failed！");
        throw new Error(data.error || "对接计算失败");
      }

      if (startBtn) {
        const t =
          startBtn.getAttribute("data-original-text") || "开始分子对接分析";
        startBtn.innerHTML = t;
        startBtn.disabled = false;
        startBtn.removeAttribute("data-original-text");
      }
    })
    .catch((error) => {
      requestFinished = true;
      clearProgressTimers();

      stepManager.errorStep(
        stepManager.steps[stepManager.currentStep]?.id || "run_docking",
        error.message || "失败",
      );
      console.error("对接错误:", error);

      statusIndicators.forEach((indicator) => {
        indicator.className = "status-indicator status-error";
        indicator.textContent = "计算失败";
      });

      progressBar.style.width = "0%";
      progressBar.removeAttribute("data-progress");
      progressContainer.style.display = "none";

      const resultsContent = document.getElementById("results-content");
      const safeErrorMessage = Safe.escapeHtml(error.message || "对接计算失败");
      resultsContent.innerHTML = `
        <div style="text-align: center; color: #ef4444; padding: 40px;">
          <div style="font-size: 48px; margin-bottom: 16px;">❌</div>
          <div style="font-size: 18px; font-weight: bold; margin-bottom: 8px;">对接计算失败</div>
          <div style="font-size: 14px;">${safeErrorMessage}</div>
        </div>
      `;

      if (startBtn) {
        const t =
          startBtn.getAttribute("data-original-text") || "开始分子对接分析";
        startBtn.innerHTML = t;
        startBtn.disabled = false;
        startBtn.removeAttribute("data-original-text");
      }
    });
}

// 显示真实的对接结果
function showRealResults(data) {
  const resultsContent = document.getElementById("results-content");
  const safeJobIdJs = Safe.escapeInlineJsString(data.job_id || "");

  if (!data.results || data.results.length === 0) {
    resultsContent.innerHTML = `
      <div style="text-align: center; color: #718096; padding: 40px;">
        <div style="font-size: 48px; margin-bottom: 16px;">🤔</div>
        <div>未找到有效的对接结果</div>
      </div>
    `;
    return;
  }

  if (data.job_id && currentProteinData) {
    stepManager.startStep("visualize");
    loadComplexStructure(data.job_id);
  }

  let tableHTML = `
    <div style="margin-bottom: 20px; padding: 16px; background: #f0f9ff; border-radius: 8px; border-left: 4px solid #0284c7;">
      <div style="font-weight: bold; color: #0c4a6e;">对接计算完成</div>
      <div style="font-size: 14px; color: #075985; margin-top: 4px;">
        任务ID: ${data.job_id} | 共找到 ${data.total_poses} 个结构
      </div>
    </div>
    <table class="results-table">
      <thead>
        <tr>
          <th>结构编号</th>
          <th>结合能 (kcal/mol)</th>
          <th>配体效率</th>
          <th>RMSD下限 (Å)</th>
          <th>RMSD上限 (Å)</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
  `;

  data.results.forEach((result) => {
    const energyClass =
      result.binding_energy <= -8
        ? "score-high"
        : result.binding_energy <= -6
          ? "score-medium"
          : "score-low";

    tableHTML += `
      <tr>
        <td>Pose ${result.pose}</td>
        <td><span class="${energyClass}">${result.binding_energy.toFixed(1)}</span></td>
        <td>${result.ligand_efficiency.toFixed(3)}</td>
        <td>${result.rmsd_lb.toFixed(1)}</td>
        <td>${result.rmsd_ub.toFixed(1)}</td>
        <td><button class="action-btn btn-primary" style="padding: 5px 10px; font-size: 12px;" onclick="viewPose(${result.pose})">查看</button></td>
      </tr>
    `;
  });

  tableHTML += `
      </tbody>
    </table>
    <div class="action-buttons">
      <button class="action-btn btn-primary" onclick="downloadResults('${safeJobIdJs}')">导出结果</button>
      <button class="action-btn btn-secondary" onclick="generateReport('${safeJobIdJs}')">生成报告</button>
      <button class="action-btn btn-secondary" onclick="resetDocking()">重新对接</button>
    </div>
  `;

  resultsContent.innerHTML = tableHTML;

  adjustDockingResultsHeight();
  const wrapper = document.querySelector(".results-content-wrapper");
  if (wrapper) wrapper.scrollTop = 0;

  // 初始化 Pose 选择器
  const poseSelect = document.getElementById("pose-select");
  if (poseSelect) {
    poseSelect.innerHTML = "";
    data.results.forEach((r) => {
      const opt = document.createElement("option");
      opt.value = r.pose;
      opt.textContent = `Pose ${r.pose} (E=${r.binding_energy.toFixed(1)})`;
      poseSelect.appendChild(opt);
    });
  }

  const statusIndicators = document.querySelectorAll(".status-indicator");
  statusIndicators.forEach((indicator) => {
    indicator.className = "status-indicator status-completed";
    indicator.textContent = "已完成";
  });

  setCurrentJobId(data.job_id);
  updateButtonStates();

  const container = document.querySelector(".progress-container");
  const bar = document.getElementById("docking-progress");
  if (bar) {
    bar.style.width = "0%";
    bar.removeAttribute("data-progress");
  }
  if (container) container.style.display = "none";
  setResultsMinHeightToLeft();
}

function showBatchResults(data) {
  AppState.currentBatchResults = data.results || [];
  const resultsContent = document.getElementById("results-content");
  const rows = AppState.currentBatchResults
    .map((item) => {
      const safeBatchJobIdJs = Safe.escapeInlineJsString(item.job_id || "");
      const safeLigandNameText = Safe.escapeHtml(item.ligand_name || "--");
      const safeLigandNameAttr = Safe.escapeAttr(item.ligand_name || "");
      const safeIndex = Safe.escapeHtml(item.index);
      const safeError = Safe.escapeHtml(item.error || "");
      const status = item.success
        ? '<span class="status-indicator status-completed">成功</span>'
        : '<span class="status-indicator status-error">失败</span>';
      const energy =
        typeof item.best_energy === "number" ? item.best_energy.toFixed(2) : "--";
      const poseCount = item.total_poses || 0;
      const viewBtn =
        item.success && item.job_id
          ? `<button class="action-btn btn-primary" style="padding: 5px 10px; font-size: 12px;" onclick="viewBatchLigand('${safeBatchJobIdJs}')">查看</button>`
          : `<span style="color:#94a3b8;font-size:12px;">不可用</span>`;
      const downloadBtn =
        item.success && item.job_id
          ? `<button class="action-btn btn-secondary" style="padding: 5px 10px; font-size: 12px;" onclick="downloadResults('${safeBatchJobIdJs}')">下载</button>`
          : "";
      return `
        <tr>
          <td>${safeIndex}</td>
          <td title="${safeLigandNameAttr}">${safeLigandNameText}</td>
          <td>${item.input_type === "smiles" ? "SMILES" : "文件"}</td>
          <td>${status}</td>
          <td><strong>${energy}</strong></td>
          <td>${poseCount}</td>
          <td>${viewBtn} ${downloadBtn}</td>
          <td style="max-width: 220px; color:#ef4444; font-size:12px;">${safeError}</td>
        </tr>
      `;
    })
    .join("");

  resultsContent.innerHTML = `
    <div style="margin-bottom: 20px; padding: 16px; background: #eef2ff; border-radius: 8px; border-left: 4px solid #6366f1;">
      <div style="font-weight: bold; color: #312e81;">批量对接完成</div>
      <div style="font-size: 14px; color: #475569; margin-top: 4px;">
        批量任务ID: ${data.batch_job_id} | 成功 ${data.completed}/${data.total} | 失败 ${data.failed}
      </div>
    </div>
    <div class="action-buttons" style="justify-content: flex-start; margin-bottom: 12px;">
      <button class="action-btn btn-secondary" onclick="exportBatchResultsCsv()">导出CSV汇总</button>
    </div>
    <table class="results-table">
      <thead>
        <tr>
          <th>#</th>
          <th>配体</th>
          <th>类型</th>
          <th>状态</th>
          <th>最佳能量</th>
          <th>Pose数</th>
          <th>操作</th>
          <th>错误信息</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;
  setResultsMinHeightToLeft();
}

function viewBatchLigand(jobId) {
  if (!jobId) return;
  setCurrentJobId(jobId);
  loadComplexStructure(jobId);
  setActiveButton("show-complex-btn");
}

function exportBatchResultsCsv() {
  const results = AppState.currentBatchResults || [];
  if (results.length === 0) {
    Utils.showToast("暂无批量结果可导出", "warning");
    return;
  }

  const header = [
    "index",
    "ligand_name",
    "input_type",
    "success",
    "job_id",
    "best_energy",
    "total_poses",
    "error",
  ];
  const escapeCsv = (value) => {
    const text = value === null || value === undefined ? "" : String(value);
    return `"${text.replace(/"/g, '""')}"`;
  };
  const lines = [
    header.join(","),
    ...results.map((item) =>
      header
        .map((key) => escapeCsv(key === "best_energy" ? item.best_energy : item[key]))
        .join(","),
    ),
  ];
  const blob = new Blob([`\ufeff${lines.join("\n")}`], {
    type: "text/csv;charset=utf-8;",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "batch_docking_results.csv";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// 加载复合物结构（叠加蛋白+配体）
function loadComplexStructure(jobId) {
  if (!viewer) initViewer();

  Promise.all([
    fetch(`/api/docking/result/${jobId}`).then((response) => {
      if (response.ok) return response.text();
      throw new Error("无法获取对接结果文件");
    }),
    fetchDockedPoseSdf(jobId, 1),
  ])
    .then(([ligandPdbqt, ligandSdf]) => {
      try {
        viewer.clear();

        if (currentProteinData) {
          const protModel = viewer.addModel(
            currentProteinData,
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
            ];
            waterAndOtherResns.forEach((resn) =>
              protModel.setStyle({ resn }, {}),
            );
          } else {
            viewer.setStyle({ model: 0 }, { cartoon: { color: "spectrum" } });
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
            ];
            waterAndOtherResns.forEach((resn) => viewer.setStyle({ resn }, {}));
          }
        }

        let ligandModel;
        if (ligandSdf && ligandSdf.length > 0) {
          ligandModel = viewer.addModel(ligandSdf, "sdf");
          dockedLigandData = ligandSdf;
          try {
            cacheLigandInfo({
              type: "cached",
              residueName: "LIG",
              expectedAtoms: estimateAtomCountFromPDB(
                AppState.originalLigandData || "",
              ),
            });
          } catch (e) {
            console.warn("缓存配体信息失败:", e);
          }
        } else {
          throw new Error("无法获取标准 pose 结构");
        }

        if (ligandModel && ligandModel.setStyle) {
          ligandModel.setStyle(
            {},
            { stick: { radius: 0.25, colorscheme: "greenCarbon" } },
          );
        }

        viewer.zoomTo();
        viewer.render();

        currentComplexData = ligandPdbqt;
        if (typeof syncViewerModeState === "function") {
          syncViewerModeState("complex");
        } else {
          setActiveButton("show-complex-btn");
        }
        console.log("复合物结构加载成功（叠加显示）");
        if (stepManager) stepManager.completeStep("visualize", "复合物已加载");

        setTimeout(() => {
          autoFocusToLigand();
        }, 500);

        setTimeout(() => {
          if (interactionsState.hydrogenBonds) displayHydrogenBonds();
          if (interactionsState.piPiInteractions) displayPiPiInteractions();
          if (interactionsState.hydrophobicInteractions)
            displayHydrophobicInteractions();
        }, 100);
      } catch (e) {
        console.error("复合物渲染失败:", e);
        if (currentProteinData) loadMolecule(currentProteinData, "protein");
      }
    })
    .catch((error) => {
      console.warn("无法加载复合物结构:", error);
      if (currentProteinData) loadMolecule(currentProteinData, "protein");
    });
}

// 查看特定结构
function viewPose(poseNumber) {
  if (!viewer) initViewer();

  const savedState = saveCurrentViewState();

  const renderPose = (poseSdf) => {
    try {
      viewer.clear();
      if (currentProteinData) {
        const prot = viewer.addModel(
          currentProteinData,
          AppState.currentProteinFormat || "pdb",
        );
        if (prot && prot.setStyle) {
          prot.setStyle({}, { cartoon: { color: "spectrum" } });
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
          ];
          waterAndOtherResns.forEach((resn) => prot.setStyle({ resn }, {}));
        } else {
          viewer.setStyle({ model: 0 }, { cartoon: { color: "spectrum" } });
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
          ];
          waterAndOtherResns.forEach((resn) => viewer.setStyle({ resn }, {}));
        }
      }

      if (poseSdf) {
        const ligModel = viewer.addModel(poseSdf, "sdf");
        if (ligModel && ligModel.setStyle) {
          ligModel.setStyle(
            {},
            { stick: { radius: 0.25, colorscheme: "greenCarbon" } },
          );
        }
        viewer.zoomTo();
        viewer.render();
        // 只恢复交互显示，不调用 StyleManager.apply：
        // restoreViewState 内部会 setStyle({},{}) 清除所有样式，
        // 而 SDF 加载的配体原子没有 hetflag 属性，导致配体样式无法重新识别，配体隐形。
        setTimeout(() => {
          if (interactionsState.hydrogenBonds) displayHydrogenBonds();
          if (interactionsState.piPiInteractions) displayPiPiInteractions();
          if (interactionsState.hydrophobicInteractions)
            displayHydrophobicInteractions();
        }, 100);
      } else {
        alert(`未能获取 Pose ${poseNumber} 的标准结构`);
      }
    } catch (e) {
      console.error("显示指定Pose失败:", e);
    }
  };

  const jobId = getCurrentJobId && getCurrentJobId();
  if (!jobId) {
    alert(`查看结构 ${poseNumber} 的详细信息\n(缺少任务ID，无法加载对接结果)`);
    return;
  }

  Promise.all([
    fetch(`/api/docking/result/${jobId}`).then((r) =>
      r.ok ? r.text() : Promise.reject("无法获取对接结果文件"),
    ),
    fetchDockedPoseSdf(jobId, poseNumber),
  ])
    .then(([pdbqt, poseSdf]) => {
      currentComplexData = pdbqt;
      dockedLigandData = poseSdf;
      renderPose(poseSdf);
    })
    .catch((err) => {
      console.warn("加载对接结果失败:", err);
      alert(`查看结构 ${poseNumber} 的详细信息\n(无法加载对接结果)`);
    });
}

// 下载结果
function downloadResults(jobId) {
  if (!jobId) {
    const id = getCurrentJobId && getCurrentJobId();
    jobId = id || jobId;
  }
  if (!jobId) {
    alert("缺少任务ID，无法导出结果");
    return;
  }

  fetch(`/api/docking/result/${jobId}`)
    .then((r) => {
      if (!r.ok) return Promise.reject("导出结果失败");
      const cd = r.headers.get("Content-Disposition") || "";
      const match = cd.match(/filename=([^;]+)$/i);
      const filename = match ? match[1] : `docking_result_${jobId}.pdbqt`;
      return r.blob().then((blob) => ({ blob, filename }));
    })
    .then(({ blob, filename }) => {
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    })
    .catch((e) => {
      alert(typeof e === "string" ? e : "导出结果失败");
    });
}

// 生成报告
function generateReport(jobId) {
  if (!jobId) {
    const id = getCurrentJobId && getCurrentJobId();
    jobId = id || jobId;
  }
  if (!jobId) {
    alert("缺少任务ID，无法生成报告");
    return;
  }

  const format = "md";

  fetch(`/api/docking/report/${jobId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      format: format,
      viewer_png_base64: null,
      smiles_images: [],
    }),
  })
    .then((r) => {
      if (!r.ok) return Promise.reject("生成报告失败");
      const cd = r.headers.get("Content-Disposition") || "";
      const match = cd.match(/filename=([^;]+)$/i);
      const filename = match ? match[1] : `docking_report_${jobId}.${format}`;
      return r.blob().then((blob) => ({ blob, filename }));
    })
    .then(({ blob, filename }) => {
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    })
    .catch((e) => {
      alert(typeof e === "string" ? e : "生成报告失败");
    });
}

// 重置对接
function resetDocking() {
  document.getElementById("protein-file").value = "";
  document.getElementById("ligand-file").value = "";
  document.getElementById("smiles-input").value = "";
  document.getElementById("protein-files").innerHTML = "";
  document.getElementById("ligand-files").innerHTML = "";

  const statusIndicators = document.querySelectorAll(".status-indicator");
  statusIndicators.forEach((indicator) => {
    indicator.className = "status-indicator status-waiting";
    indicator.textContent = "等待输入";
  });

  const resultsContent = document.getElementById("results-content");
  resultsContent.innerHTML = `
    <div style="text-align: center; color: #718096; padding: 40px;">
      暂无结果，请先上传文件并开始对接分析
    </div>
  `;

  const container = document.querySelector(".progress-container");
  const bar = document.getElementById("docking-progress");
  if (bar) bar.style.width = "0%";
  if (container) container.style.display = "none";

  currentProteinData = null;
  AppState.currentProteinFormat = "pdb";
  currentLigandData = null;
  originalLigandData = null;
  dockedLigandData = null;
  currentComplexData = null;
  AppState.currentViewerMode = null;
  AppState.currentJobId = null;
  AppState.dockingBoxCenterTouched = false;
  AppState.currentBatchResults = [];
  const batchSmilesInput = document.getElementById("batch-smiles-input");
  if (batchSmilesInput) batchSmilesInput.value = "";

  const allButtons = document.querySelectorAll(".action-btn");
  allButtons.forEach((btn) => btn.classList.remove("active"));

  updateButtonStates();
  if (stepManager) {
    stepManager.hide();
    stepManager.reset();
  }

  const startBtn = document.getElementById("start-btn");
  if (startBtn) {
    startBtn.disabled = false;
    const t = startBtn.getAttribute("data-original-text");
    if (t) {
      startBtn.innerHTML = t;
      startBtn.removeAttribute("data-original-text");
    } else {
      startBtn.textContent = "开始分子对接分析";
    }
  }

  const leftCol = document.querySelector(".input-panel");
  const rightCol = document.querySelector(".result-panel");
  if (leftCol) leftCol.style.minHeight = "";
  if (rightCol) rightCol.style.minHeight = "";
}

// 对接结果卡片高度自适应
function adjustDockingResultsHeight() {
  try {
    const card = document.querySelector(".docking-results-card");
    if (!card) return;
    card.style.height = "";
    card.style.maxHeight = "";
  } catch (_) {}
}

// applyStyleFromUI 别名（冒烟测试使用）
window.applyStyleFromUI = onStyleChange;

// ==================== 对接历史面板 ====================

function toggleHistoryPanel() {
  const panel = document.getElementById("history-panel");
  const overlay = document.getElementById("history-overlay");
  if (!panel) return;
  const isOpen = panel.classList.contains("open");
  if (isOpen) {
    closeHistoryPanel();
  } else {
    panel.classList.add("open");
    if (overlay) overlay.classList.add("open");
    loadDockingHistory();
  }
}

function closeHistoryPanel() {
  const panel = document.getElementById("history-panel");
  const overlay = document.getElementById("history-overlay");
  if (panel) panel.classList.remove("open");
  if (overlay) overlay.classList.remove("open");
}

function loadDockingHistory() {
  const listEl = document.getElementById("history-list");
  if (!listEl) return;
  listEl.innerHTML = '<div class="history-empty">加载中...</div>';
  fetch("/api/docking/history")
    .then((r) => r.json())
    .then((data) => {
      if (!data.success || !data.history || data.history.length === 0) {
        listEl.innerHTML =
          '<div class="history-empty">暂无对接历史记录</div>';
        return;
      }

      let html = "";
      data.history.forEach((item) => {
        const statusClass =
          item.status === "completed"
            ? "history-status-ok"
            : item.status === "processing"
              ? "history-status-warn"
              : "history-status-err";
        const statusText =
          item.status === "completed"
            ? "✅ 已完成"
            : item.status === "processing"
              ? "⏳ 进行中"
              : "❌ 失败";
        const energy =
          item.best_energy !== null ? item.best_energy.toFixed(1) : "--";
        const sizeKB = (item.size_bytes / 1024).toFixed(0);
        const safeJobIdText = Safe.escapeHtml(item.job_id);
        const safeJobIdAttr = Safe.escapeAttr(item.job_id);
        const safeJobIdJs = Safe.escapeInlineJsString(item.job_id);
        const safeHistoryTime = Safe.escapeHtml(item.time);

        html += `
          <div class="history-card" data-jobid="${safeJobIdAttr}">
            <div class="history-card-top">
              <span class="history-job-id" title="${safeJobIdAttr}">🔬 ${safeJobIdText}</span>
              <span class="${statusClass}">${statusText}</span>
            </div>
            <div class="history-card-info">
              <span class="history-meta history-time">🕐 ${safeHistoryTime}</span>
              <span class="history-energy">${energy} kcal/mol</span>
              <span class="history-meta">📊 ${item.pose_count} 个构象</span>
              <span class="history-meta">💾 ${sizeKB} KB</span>
            </div>
            <div class="history-card-actions">
              ${item.has_result ? `<button class="history-action-btn history-load-btn" onclick="loadHistoryJob('${safeJobIdJs}')">📂 加载结果</button>` : ""}
              <button class="history-action-btn history-delete-btn" onclick="deleteHistoryJob('${safeJobIdJs}')">🗑️ 删除</button>
            </div>
          </div>
        `;
      });

      listEl.innerHTML = html;
    })
    .catch((err) => {
      console.error("加载对接历史失败:", err);
      listEl.innerHTML = '<div class="history-empty">⚠️ 加载失败，请重试</div>';
    });
}

function clearAllHistory() {
  if (!confirm("确定要清除所有对接历史记录？\n此操作不可撤销！")) return;
  fetch("/api/docking/history", { method: "DELETE" })
    .then((r) => r.json())
    .then((data) => {
      if (data.success) {
        Utils.showToast(data.message, "success");
        loadDockingHistory();
      } else {
        Utils.showToast("清除失败", "error");
      }
    })
    .catch(() => Utils.showToast("清除失败", "error"));
}

function deleteHistoryJob(jobId) {
  if (!confirm(`确定删除任务 ${jobId} ？`)) return;
  fetch(`/api/docking/history/${jobId}`, { method: "DELETE" })
    .then((r) => r.json())
    .then((data) => {
      if (data.success) {
        // 从列表移除卡片（带动画）
        const safeJobIdSelector = Safe.escapeCssIdent(jobId);
        const card = document.querySelector(
          `.history-card[data-jobid="${safeJobIdSelector}"]`,
        );
        if (card) {
          card.style.transition = "all 0.3s ease";
          card.style.opacity = "0";
          card.style.transform = "translateX(100%)";
          setTimeout(() => {
            card.remove();
            const listEl = document.getElementById("history-list");
            if (listEl && listEl.children.length === 0) {
              listEl.innerHTML =
                '<div class="history-empty">暂无对接历史记录</div>';
            }
          }, 300);
        }
        Utils.showToast(`已删除 ${jobId}`, "success");
      }
    })
    .catch(() => Utils.showToast("删除失败", "error"));
}

function loadHistoryJob(jobId) {
  if (!viewer) initViewer();

  fetch(`/api/docking/result/${jobId}`)
    .then((r) => {
      if (!r.ok) throw new Error("无法获取对接结果");
      return r.text();
    })
    .then((pdbqtText) => {
      // 解析结果
      const lines = pdbqtText.split(/\r?\n/);
      const results = [];
      let poseNum = 0;
      lines.forEach((line) => {
        const m = line.match(
          /REMARK\s+VINA\s+RESULT:\s*([\-+]?\d*\.?\d+)\s+([\-+]?\d*\.?\d+)\s+([\-+]?\d*\.?\d+)/,
        );
        if (m) {
          poseNum++;
          results.push({
            pose: poseNum,
            binding_energy: parseFloat(m[1]),
            rmsd_lb: parseFloat(m[2]),
            rmsd_ub: parseFloat(m[3]),
            ligand_efficiency: 0,
          });
        }
      });

      // 设置全局状态
      currentComplexData = pdbqtText;
      setCurrentJobId(jobId);

      fetchDockedPoseSdf(jobId, 1)
        .then((ligandSdf) => {
          dockedLigandData = ligandSdf;

          viewer.clear();
          if (currentProteinData) {
            const prot = viewer.addModel(
              currentProteinData,
              AppState.currentProteinFormat || "pdb",
            );
            if (prot && prot.setStyle) {
              prot.setStyle({}, { cartoon: { color: "spectrum" } });
            }
          }
          if (ligandSdf) {
            const lig = viewer.addModel(ligandSdf, "sdf");
            if (lig && lig.setStyle) {
              lig.setStyle(
                {},
                { stick: { radius: 0.25, colorscheme: "greenCarbon" } },
              );
            }
          }
          viewer.zoomTo();
          viewer.render();
        })
        .catch((err) => {
          console.warn("加载标准 pose 结构失败:", err);
        });

      // 填充结果表格
      if (results.length > 0) {
        showRealResults({
          success: true,
          job_id: jobId,
          results: results,
          best_pose: results[0],
          total_poses: results.length,
        });
      }

      // 关闭面板
      closeHistoryPanel();
      Utils.showToast(`已加载任务 ${jobId} 的对接结果`, "success");
      updateButtonStates();
    })
    .catch((err) => {
      console.error("加载历史任务失败:", err);
      Utils.showToast("加载失败: " + err.message, "error");
    });
}
