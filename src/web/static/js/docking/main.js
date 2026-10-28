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

  // A target-search handoff prepares a verified structure and opens this
  // existing docking workflow with the receptor preloaded. It never submits a
  // docking job automatically; the user still supplies a ligand and confirms
  // the docking box before execution.
  loadTargetStructureFromQuery();

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

  // 从浏览器会话恢复仍在运行或已完成的持久化任务，不重新提交文件。
  if (typeof resumeDockingTaskIfPresent === "function") {
    resumeDockingTaskIfPresent();
  }

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
        const tips = (Array.isArray(d.suggestions) ? d.suggestions : [])
          .map((s) => `<li>${Safe.escapeHtml(String(s))}</li>`)
          .join("");
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

const ROFLUMILAST_DEMO_SMILES =
  "C1CC1COC2=C(C=CC(=C2)C(=O)NC3=C(C=NC=C3Cl)Cl)OC(F)F";

async function loadDockingDemo() {
  const button = document.getElementById("docking-demo-btn");
  const proteinInput = document.getElementById("protein-file");
  const smilesInput = document.getElementById("smiles-input");
  if (!button || !proteinInput || !smilesInput) return;

  button.disabled = true;
  const originalText = button.textContent;
  button.textContent = "正在载入示例…";
  try {
    const response = await fetch("/api/docking/demo/pde4a-apo", {
      headers: { Accept: "chemical/x-pdb" },
    });
    if (!response.ok) {
      let detail = "本地 PDE4A apo 示例不可用";
      try {
        const payload = await response.json();
        detail = payload.detail || payload.message || detail;
      } catch (_) {}
      throw new Error(detail);
    }
    if (typeof DataTransfer === "undefined" || typeof File === "undefined") {
      throw new Error("当前浏览器不支持自动载入示例，请手动上传 PDB 文件。");
    }

    const file = new File([await response.blob()], "PDE4A_apo_6FG5.pdb", {
      type: "chemical/x-pdb",
    });
    const transfer = new DataTransfer();
    transfer.items.add(file);
    proteinInput.files = transfer.files;
    proteinInput.dispatchEvent(new Event("change", { bubbles: true }));

    const singleModeButton = document.getElementById("single-mode-btn");
    if (singleModeButton && typeof singleModeButton.click === "function") {
      singleModeButton.click();
    }
    smilesInput.value = ROFLUMILAST_DEMO_SMILES;
    smilesInput.dispatchEvent(new Event("input", { bubbles: true }));
    if (typeof loadSmilesPreview === "function") {
      await loadSmilesPreview(ROFLUMILAST_DEMO_SMILES);
    }
    Utils.showToast("已载入 PDE4A apo（6FG5）和罗氟司特；请确认对接盒后再提交。", "success", 6000);
  } catch (error) {
    Utils.showToast(`示例载入失败：${error.message || "未知错误"}`, "warning", 7000);
  } finally {
    button.disabled = false;
    button.textContent = originalText;
  }
}

async function loadTargetStructureFromQuery() {
  const params = new URLSearchParams(window.location.search);
  const rawId = params.get("target_structure") || params.get("target_structure_id");
  if (!rawId || !/^\d+$/.test(rawId)) return;

  const structureId = encodeURIComponent(rawId);
  try {
    Utils.showToast("正在加载靶点结构到分子对接流程…", "info");
    const preflightResponse = await fetch(
      `/api/target-db/structures/${structureId}/preflight?format=pdb`,
    );
    const preflight = await preflightResponse.json();
    if (!preflightResponse.ok) {
      throw new Error(preflight.detail || preflight.message || "靶点结构检查失败");
    }
    if (!preflight.can_download) {
      throw new Error("该结构当前不可下载，无法作为分子对接受体输入。");
    }

    const fileResponse = await fetch(
      `/api/target-db/structures/${structureId}/download?format=pdb`,
    );
    if (!fileResponse.ok) {
      let detail = "靶点结构下载失败";
      try {
        const error = await fileResponse.json();
        detail = error.detail || error.error || error.message || detail;
      } catch (_) {}
      throw new Error(detail);
    }

    const blob = await fileResponse.blob();
    const filename = `target-${preflight.structure_id || rawId}.pdb`;
    const file = new File([blob], filename, { type: "chemical/x-pdb" });
    const input = document.getElementById("protein-file");
    if (!input || typeof DataTransfer === "undefined") {
      throw new Error("当前浏览器不支持自动载入受体结构，请手动上传 PDB 文件。");
    }
    const transfer = new DataTransfer();
    transfer.items.add(file);
    input.files = transfer.files;
    input.dispatchEvent(new Event("change", { bubbles: true }));
    Utils.showToast(
      `已加载 ${preflight.structure_id || rawId}；请提供配体并确认对接盒后再提交任务。`,
      "success",
      6000,
    );
  } catch (error) {
    Utils.showToast(
      `靶点结构未能自动载入：${error.message || "未知错误"}。请手动上传受体文件。`,
      "warning",
      7000,
    );
  } finally {
    const cleanUrl = new URL(window.location.href);
    cleanUrl.searchParams.delete("target_structure");
    cleanUrl.searchParams.delete("target_structure_id");
    window.history.replaceState({}, "", cleanUrl.pathname + cleanUrl.search + cleanUrl.hash);
  }
}

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
