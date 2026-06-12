// ==================== 页面初始化入口 ====================
document.addEventListener("DOMContentLoaded", function () {
  // 初始化3D查看器
  initViewer();

  // 初始化文件上传处理
  handleFileUpload("protein-file", "protein-files", "protein");
  handleFileUpload("ligand-file", "ligand-files", "ligand");

  // 为SMILES输入添加事件监听器
  const smilesInput = document.getElementById("smiles-input");
  const smilesHint = document.getElementById("smiles-hint");
  initDockingModeToggle();
  if (smilesInput) {
    smilesInput.addEventListener("input", function () {
      const value = this.value.trim();

      updateButtonStates();

      if (smilesHint) {
        if (value.length > 3) {
          smilesHint.style.display = "block";
          smilesHint.style.animation = "fadeIn 0.3s ease-out";
        } else {
          smilesHint.style.display = "none";
        }
      }

      if (
        !value &&
        currentLigandData &&
        !document.getElementById("ligand-file").files[0]
      ) {
        currentLigandData = null;
        originalLigandData = null;
        dockedLigandData = null;
        updateButtonStates();
      }
    });

    smilesInput.addEventListener("blur", function () {
      const value = this.value.trim();
      if (value.length > 3) {
        setTimeout(() => {
          if (smilesHint) {
            smilesHint.style.animation = "pulse 1.5s ease-in-out 2";
          }
        }, 500);
      }
    });
  }

  // 初始化按钮状态
  updateButtonStates();

  ["center_x", "center_y", "center_z"].forEach((id) => {
    const input = document.getElementById(id);
    if (!input) return;
    input.addEventListener("input", () => {
      AppState.dockingBoxCenterTouched = true;
    });
  });

  // 调整对接结果卡片高度
  adjustDockingResultsHeight();

  // 页面加载后先对齐一次高度
  setResultsMinHeightToLeft();

  // 环境自检：加载页面即检测并在结果面板提示
  fetch("/api/docking/env_check")
    .then((r) => r.json())
    .then((res) => {
      if (!res || !res.diagnostics) return;
      const d = res.diagnostics;
      const py = d.python_env || {};
      const missing = [];
      if (!d.vina || !d.vina.exists) missing.push("AutoDock Vina 未就绪");
      if (!d.adfr_prepare_receptor || !d.adfr_prepare_receptor.exists)
        missing.push("ADFRsuite prepare_receptor.bat 缺失");
      if (!d.mk_prepare_ligand || !d.mk_prepare_ligand.exists)
        missing.push("mk_prepare_ligand.exe 缺失（Meeko CLI）");
      if (!py.has_rdkit) missing.push("当前 Python 环境缺少 RDKit");
      if (!res.success) {
        const resultsContent = document.getElementById("results-content");
        const tips = (d.suggestions || []).map((s) => `<li>${s}</li>`).join("");
        resultsContent.innerHTML = `
          <div style="background:#fff7ed;border:1px solid #fed7aa;color:#9a3412;border-radius:8px;padding:16px;">
            <div style="font-weight:700;margin-bottom:8px;">环境自检未通过</div>
            <div style="font-size:13px;margin-bottom:8px;">缺失项：${missing.join("、") || "未知"}</div>
            <ul style="padding-left:18px;line-height:1.7;">${tips}</ul>
          </div>`;
      }
    })
    .catch(() => {});

  // 拖拽上传功能
  const uploadAreas = document.querySelectorAll(".upload-area");
  uploadAreas.forEach((area) => {
    area.addEventListener("dragover", (e) => {
      e.preventDefault();
      area.classList.add("active");
    });

    area.addEventListener("dragleave", () => {
      area.classList.remove("active");
    });

    area.addEventListener("drop", (e) => {
      e.preventDefault();
      area.classList.remove("active");
      try {
        const section = area.closest(".input-section");
        if (!section) return;
        const input = section.querySelector('input[type="file"]');
        if (!input) return;

        const dt = new DataTransfer();
        for (const f of e.dataTransfer.files || []) {
          dt.items.add(f);
        }
        if (dt.files.length > 0) {
          input.files = dt.files;
          input.dispatchEvent(new Event("change"));
        }
      } catch (_) {}
    });
  });
});

function initDockingModeToggle() {
  const singleBtn = document.getElementById("single-mode-btn");
  const batchBtn = document.getElementById("batch-mode-btn");
  const smilesInput = document.getElementById("smiles-input");
  const batchSmilesInput = document.getElementById("batch-smiles-input");
  const batchSmilesFileWrap = document.getElementById("batch-smiles-file-wrap");
  const batchSmilesFile = document.getElementById("batch-smiles-file");
  const smilesHint = document.getElementById("smiles-hint");
  const batchHint = document.getElementById("batch-hint");
  const uploadText = document.getElementById("ligand-upload-text");
  const startBtn = document.getElementById("start-btn");

  const setMode = (mode) => {
    AppState.dockingMode = mode;
    const isBatch = mode === "batch";
    if (singleBtn) singleBtn.classList.toggle("active", !isBatch);
    if (batchBtn) batchBtn.classList.toggle("active", isBatch);
    if (smilesInput) smilesInput.style.display = isBatch ? "none" : "";
    if (batchSmilesInput) batchSmilesInput.style.display = isBatch ? "" : "none";
    if (batchSmilesFileWrap) batchSmilesFileWrap.style.display = isBatch ? "block" : "none";
    if (smilesHint) smilesHint.style.display = "none";
    if (batchHint) batchHint.style.display = isBatch ? "block" : "none";
    if (uploadText) {
      uploadText.textContent = isBatch
        ? "批量上传配体文件 (可多选 SDF/MOL/PDBQT)"
        : "上传配体文件 (SDF/MOL/PDBQT)";
    }
    if (startBtn) {
      startBtn.textContent = isBatch ? "开始批量分子对接" : "开始分子对接分析";
    }
    updateButtonStates();
  };

  if (singleBtn) singleBtn.addEventListener("click", () => setMode("single"));
  if (batchBtn) batchBtn.addEventListener("click", () => setMode("batch"));
  if (batchSmilesInput) {
    batchSmilesInput.addEventListener("input", updateButtonStates);
  }
  if (batchSmilesFile && batchSmilesInput) {
    batchSmilesFile.addEventListener("change", (event) => {
      const file = event.target.files && event.target.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = (e) => {
        batchSmilesInput.value = e.target.result || "";
        updateButtonStates();
        Utils.showToast(`已导入 ${file.name}`, "success");
      };
      reader.readAsText(file);
    });
  }
  setMode(AppState.dockingMode || "single");
}
