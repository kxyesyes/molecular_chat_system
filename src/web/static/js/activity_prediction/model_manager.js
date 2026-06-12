"use strict";

window.ActivityModels = (function () {
  let allModels = [];
  let popoverBound = false;

  function getAll() {
    return allModels;
  }

  function setAll(models) {
    allModels = models || [];
  }

  function getSelectedMeta() {
    const selector = document.getElementById("modelSelector");
    if (!selector) return null;
    return (
      allModels.find(function (model) {
        return model.weights_file === selector.value;
      }) || null
    );
  }

  function getTaskType() {
    return (getSelectedMeta() && getSelectedMeta().task_type) || "regression";
  }

  function getRangeConfig() {
    return (
      ActivityUtils.DEFAULT_ACTIVITY_RANGE[getTaskType()] ||
      ActivityUtils.DEFAULT_ACTIVITY_RANGE.regression
    );
  }

  function updateTaskTypeStat() {
    const el = document.getElementById("taskTypeStat");
    if (!el) return;
    el.textContent = getTaskType() === "classification" ? "分类" : "回归";
  }

  function updatePopoverContent() {
    const selector = document.getElementById("modelSelector");
    const content = document.getElementById("popoverContent");
    if (!selector || !content) return;

    const selectedFile = selector.value;
    const model = allModels.find(function (item) {
      return item.weights_file === selectedFile;
    });

    if (!model) {
      content.innerHTML = '<div class="popover-title">未发现模型详情</div>';
      return;
    }

    const dateStr = new Date(model.created_at * 1000).toLocaleString();
    let metricsHtml = "";

    if (model.best_metrics) {
      Object.entries(model.best_metrics).forEach(function (entry) {
        const key = entry[0];
        const value = entry[1];
        metricsHtml += `
          <div class="popover-metric-row">
            <span class="metric-label">${String(key).toUpperCase()}</span>
            <span class="metric-highlight">${Number(value).toFixed(4)}</span>
          </div>
        `;
      });
    }

    content.innerHTML = `
      <div class="popover-title">${model.name}</div>
      <div class="popover-metric-row">
        <span class="metric-label">任务类型</span>
        <span class="metric-value">${model.task_type === "regression" ? "回归" : "分类"}</span>
      </div>
      <div class="popover-metric-row">
        <span class="metric-label">目标列</span>
        <span class="metric-value">${model.target}</span>
      </div>
      <div class="popover-metric-row">
        <span class="metric-label">样本数量</span>
        <span class="metric-value">${model.samples}</span>
      </div>
      <div style="margin: 12px 0 8px; border-top: 1px solid #f1f5f9; padding-top: 8px; font-weight:700; font-size:12px; color:#64748b;">
        核心指标 · Metrics
      </div>
      ${
        metricsHtml ||
        '<div style="font-size:12px; color:var(--muted)">暂无评估数据</div>'
      }
      <div style="margin-top: 12px; font-size: 11px; color: #94a3b8; text-align: right;">训练于: ${dateStr}</div>
    `;
  }

  async function switchCurrentModel(file) {
    if (!file) return;

    try {
      const res = await fetch("/api/activity/models/switch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model_file: file }),
      });
      const data = await res.json();

      if (data.success) {
        return true;
      }

      alert("❌ 切换失败: " + (data.detail || "未知错误"));
      await loadModels();
      return false;
    } catch (err) {
      console.error(err);
      alert("❌ 切换失败，请检查网络");
      await loadModels();
      return false;
    }
  }

  async function loadModels() {
    try {
      const res = await fetch("/api/activity/models");
      const data = await res.json();
      if (!data.success) return [];

      allModels = data.models || [];
      const selector = document.getElementById("modelSelector");
      const deleteBtn = document.getElementById("deleteModelBtn");
      const infoBtn = document.getElementById("modelInfoBtn");

      if (!selector) return allModels;

      selector.innerHTML = "";

      if (!allModels.length) {
        selector.innerHTML = '<option value="">(尚未训练自定义模型)</option>';
        if (deleteBtn) deleteBtn.style.display = "none";
        if (infoBtn) infoBtn.style.display = "none";
        updateTaskTypeStat();
        updatePopoverContent();
        return allModels;
      }

      if (deleteBtn) deleteBtn.style.display = "flex";
      if (infoBtn) infoBtn.style.display = "block";

      allModels.forEach(function (model) {
        const opt = document.createElement("option");
        opt.value = model.weights_file;
        opt.setAttribute("data-id", model.model_id);
        opt.textContent = model.name;
        if (data.current_model === model.weights_file) {
          opt.selected = true;
        }
        selector.appendChild(opt);
      });

      updateTaskTypeStat();
      updatePopoverContent();
      return allModels;
    } catch (e) {
      console.error("加载模型列表失败", e);
      return [];
    }
  }

  async function confirmDeleteModel() {
    const selector = document.getElementById("modelSelector");
    if (!selector) return;

    const selectedOpt = selector.options[selector.selectedIndex];
    if (!selectedOpt || !selectedOpt.value) return;

    const modelId = selectedOpt.getAttribute("data-id");
    const modelName = selectedOpt.textContent;

    if (
      !confirm(
        `⚠️ 确定要物理删除模型 "${modelName}" 吗？\n删除后该权重文件将无法找回。`,
      )
    ) {
      return;
    }

    try {
      const res = await fetch(`/api/activity/models/${modelId}`, {
        method: "DELETE",
      });
      const data = await res.json();
      if (data.success) {
        alert("✅ 模型已成功删除");
        await loadModels();
      } else {
        alert("❌ 删除失败: " + (data.detail || "未知错误"));
      }
    } catch (err) {
      alert("❌ 网络请求失败");
      console.error(err);
    }
  }

  function initPopover() {
    if (popoverBound) return;

    const infoBtn = document.getElementById("modelInfoBtn");
    const popover = document.getElementById("modelInfoPopover");
    const selector = document.getElementById("modelSelector");

    infoBtn &&
      infoBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        const isShow = popover && popover.style.display === "block";
        if (popover) popover.style.display = isShow ? "none" : "block";
      });

    document.addEventListener("click", function () {
      if (popover) popover.style.display = "none";
    });

    popover &&
      popover.addEventListener("click", function (e) {
        e.stopPropagation();
      });

    selector &&
      selector.addEventListener("change", async function (e) {
        updateTaskTypeStat();
        updatePopoverContent();
        await switchCurrentModel(e.target.value);
      });

    popoverBound = true;
  }

  return {
    getAll: getAll,
    setAll: setAll,
    getSelectedMeta: getSelectedMeta,
    getTaskType: getTaskType,
    getRangeConfig: getRangeConfig,
    updateTaskTypeStat: updateTaskTypeStat,
    updatePopoverContent: updatePopoverContent,
    loadModels: loadModels,
    confirmDeleteModel: confirmDeleteModel,
    initPopover: initPopover,
  };
})();
