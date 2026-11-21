// ═══════════════════════════════════════════════════════════
//  反向寻靶模块 – 页面入口
//  负责初始化所有子模块、绑定表单提交事件
// ═══════════════════════════════════════════════════════════

document.addEventListener("DOMContentLoaded", function () {
  // 1. 加载数据库统计卡片
  RtUI.loadStats();

  // 2. 初始化文件上传交互
  RtUI.initFileUpload();

  bindQueryForm("predictForm", false);
  bindQueryForm("batchForm", true);

  // 5. 绑定模态框点击关闭
  _bindModalDismiss();

  document.querySelectorAll(".preset-btn").forEach((button) => {
    button.addEventListener("click", () => {
      applyReverseTargetPreset(button.dataset.mode || "broad");
    });
  });
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      switchTab(tab.dataset.tab || "single");
    });
  });
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
      button.dataset.mode === mode,
    );
  });
}

// Switching tabs retires the request even if its server response is still pending.
let pendingQuery = null;

function finishQuery() {
  if (!pendingQuery) return;
  RtUI.setSubmitEnabled(pendingQuery.mode === "batch" ? "batchSubmitBtn" : "submitBtn", true);
  RtUI.hideLoading();
  pendingQuery = null;
}

function bindQueryForm(formId, batch) {
  const form = document.getElementById(formId);
  if (!form) return;
  const button = batch ? "batchSubmitBtn" : "submitBtn";
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (pendingQuery) return;
    const data = new FormData(form);
    const querySmiles = String(data.get("smiles") || "").trim();
    const input = document.getElementById(batch ? "batchFile" : "smiles");
    if (batch ? !input?.files.length : !querySmiles) {
      RtUI.showError(batch ? "请先上传批量分子文件" : "请输入 SMILES");
      input?.focus();
      return;
    }
    if (!form.reportValidity()) return;

    const mode = batch ? "batch" : (RT_CONFIG.IS_PHARM_3D_MODE ? "3d" : "2d");
    const organismFilter = document.getElementById(batch ? "batchHumanOnly" : "humanOnly")?.checked ? "Human" : "";
    if (organismFilter) data.set("organism_filter", organismFilter);
    if (!batch) data.set("smiles", querySmiles);
    if (mode === "3d") {
      const alpha = document.getElementById("alpha3d").value || "0.6";
      data.set("max_refine", String(Number(document.getElementById("maxRefine").value || "50")));
      data.set("alpha_3d", alpha);
      data.set("alpha_2d", String(1 - Number(alpha)));
    }

    const query = {mode, querySmiles, organismFilter, threshold: Number(data.get("threshold") || (batch ? 0.6 : 0))};
    pendingQuery = query;
    RtResults.clear();
    RtUI.setSubmitEnabled(button, false);
    RtUI.showLoading();
    try {
      const predict = {batch: RtApi.batchPredict, "3d": RtApi.predict3D, "2d": RtApi.predict2D}[mode];
      const response = await predict(data);
      if (pendingQuery === query) RtResults.display(query, response);
    } catch (error) {
      if (pendingQuery === query) RtUI.showError(error.message);
    } finally {
      if (pendingQuery === query) finishQuery();
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
  if (document.querySelector(".tab.active")?.dataset.tab === tabName) return;
  finishQuery();
  RtResults.clear();
  RtUI.switchTab(tabName);
}
function exportData() {
  RtUI.exportData(RtResults.current);
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

// Expose the legacy HTML onclick bridge explicitly.  Some deployments wrap
// classic scripts in a scoped loader, so top-level function declarations are
// not guaranteed to become window properties.
window.applyReverseTargetPreset = applyReverseTargetPreset;
window.switchTab = switchTab;
window.exportData = exportData;
window.closeSimilarModal = closeSimilarModal;
window.resetCamera = resetCamera;
window.toggleSpin = toggleSpin;
window.closePharm3DModal = closePharm3DModal;
function openPharmacophore3D(s, id) {
  RtViewer.openPharmacophore3D(s, id);
}
window.openPharmacophore3D = openPharmacophore3D;
