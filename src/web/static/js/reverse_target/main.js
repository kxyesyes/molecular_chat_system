// ═══════════════════════════════════════════════════════════
//  反向寻靶模块 – 页面入口
//  负责初始化所有子模块、绑定表单提交事件
// ═══════════════════════════════════════════════════════════

document.addEventListener("DOMContentLoaded", function () {
  // 1. 加载数据库统计卡片
  RtUI.loadStats();

  // 2. 初始化文件上传交互
  RtUI.initFileUpload();

  // 3. 绑定单分子预测表单
  _bindPredictForm();

  // 4. 绑定批量预测表单
  _bindBatchForm();

  // 5. 绑定模态框点击关闭
  _bindModalDismiss();
});

function applyReverseTargetPreset(mode) {
  const threshold = document.getElementById("threshold");
  const topK = document.getElementById("topK");
  const maxRefine = document.getElementById("maxRefine");
  const alpha3d = document.getElementById("alpha3d");

  if (mode === "precise") {
    if (threshold) threshold.value = "0.6";
    if (topK) topK.value = "20";
    if (maxRefine) maxRefine.value = "50";
    if (alpha3d) alpha3d.value = "0.6";
  } else {
    if (threshold) threshold.value = "0.5";
    if (topK) topK.value = "100";
    if (maxRefine) maxRefine.value = "50";
    if (alpha3d) alpha3d.value = "0.6";
  }

  document.querySelectorAll(".preset-btn").forEach((button) => {
    button.classList.toggle(
      "active",
      button.getAttribute("onclick")?.includes(`'${mode}'`) || false,
    );
  });
}

// ══════════════════════════════════════
//  单分子预测表单提交
// ══════════════════════════════════════
function _bindPredictForm() {
  const form = document.getElementById("predictForm");
  if (!form) return;

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    RtUI.setSubmitEnabled("submitBtn", false);
    RtUI.showLoading();

    const formData = new FormData(e.target);
    window.lastQuerySmiles = formData.get("smiles");
    window.lastThreshold = formData.get("threshold");
    window.lastOrganismFilter = document.getElementById("humanOnly")?.checked ? "Human" : "";
    if (window.lastOrganismFilter) {
      formData.append("organism_filter", window.lastOrganismFilter);
    }

    try {
      if (RT_CONFIG.IS_PHARM_3D_MODE) {
        // ── 3D 药效团精修模式 ──
        const fd = new FormData();
        fd.append("smiles", formData.get("smiles"));
        fd.append("threshold", formData.get("threshold"));
        fd.append("top_k", formData.get("top_k"));
        const requestedMaxRefine = parseInt(document.getElementById("maxRefine").value || "50", 10);
        fd.append("max_refine", String(requestedMaxRefine));
        fd.append(
          "alpha_3d",
          document.getElementById("alpha3d").value || "0.6",
        );
        fd.append(
          "alpha_2d",
          String(
            1.0 - parseFloat(document.getElementById("alpha3d").value || "0.6"),
          ),
        );
        if (window.lastOrganismFilter) {
          fd.append("organism_filter", window.lastOrganismFilter);
        }

        const data = await RtApi.predict3D(fd);
        window.lastReverseTargetSummary = data;
        window.lastQuery3DPharmacophore = data.query_pharmacophore;
        window.currentQuerySmiles = window.lastQuerySmiles;
        RtResults.displayResults(data.results, true);
      } else {
        // ── 2D 模式 ──
        const data = await RtApi.predict2D(formData);
        window.lastReverseTargetSummary = data;
        window.lastQuery3DPharmacophore = null;
        window.currentQuerySmiles = window.lastQuerySmiles;
        RtResults.displayResults(data.results, false);
      }
    } catch (err) {
      RtUI.showError(err.message);
    } finally {
      RtUI.setSubmitEnabled("submitBtn", true);
      RtUI.hideLoading();
    }
  });
}

// ══════════════════════════════════════
//  批量预测表单提交
// ══════════════════════════════════════
function _bindBatchForm() {
  const form = document.getElementById("batchForm");
  if (!form) return;

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    RtUI.setSubmitEnabled("batchSubmitBtn", false);
    RtUI.showLoading();

    try {
      const formData = new FormData(e.target);
      const organismFilter = document.getElementById("batchHumanOnly")?.checked ? "Human" : "";
      if (organismFilter) formData.append("organism_filter", organismFilter);
      window.lastOrganismFilter = organismFilter;
      const data = await RtApi.batchPredict(formData);
      RtResults.displayBatchResults(data.results);
    } catch (err) {
      RtUI.showError(err.message);
    } finally {
      RtUI.setSubmitEnabled("batchSubmitBtn", true);
      RtUI.hideLoading();
    }
  });
}

// ══════════════════════════════════════
//  模态框点击遮罩关闭
// ══════════════════════════════════════
function _bindModalDismiss() {
  const similarModal = document.getElementById("similarModal");
  if (similarModal) {
    similarModal.addEventListener("click", (e) => {
      if (e.target.id === "similarModal") RtResults.closeSimilarModal();
    });
  }
}

// ══════════════════════════════════════
//  全局函数桥接（供 HTML onclick 属性调用）
// ══════════════════════════════════════
function switchTab(tabName) {
  RtUI.switchTab(tabName);
}
function exportData() {
  RtUI.exportData();
}
function closeSimilarModal() {
  RtResults.closeSimilarModal();
}
function resetCamera() {
  RtViewer.resetCamera();
}
function toggleSpin() {
  RtViewer.toggleSpin();
}
function closePharm3DModal() {
  RtViewer.closePharm3DModal();
}
function openPharmacophore3D(s, id) {
  RtViewer.openPharmacophore3D(s, id);
}
