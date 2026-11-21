"use strict";

window.ActivityMain = (function () {
  let activeTab = "single";
  let currentPrediction = null;
  let trainingStatus = "Idle";

  function fillExample() {
    const input = document.getElementById("smilesInput");
    if (!input) return;
    input.value = "CC(=O)OC1=CC=CC=C1C(=O)O";
    input.focus();
  }

  function switchTab(mode, tabEl) {
    if (mode === activeTab) return;
    activeTab = mode;
    if (currentPrediction) currentPrediction.button.disabled = false;
    currentPrediction = null;
    document.getElementById("loading").classList.remove("show");
    displayStatus(mode === "train" ? trainingStatus : "Idle");
    document.querySelectorAll(".tab").forEach(function (tab) {
      tab.classList.remove("active");
    });
    if (tabEl) tabEl.classList.add("active");

    ["single", "batch", "train"].forEach(function (name) {
      const panel = document.getElementById(`${name}-panel`);
      if (panel) panel.style.display = name === mode ? "block" : "none";
    });

    const results = document.getElementById("results");
    if (results) results.classList.remove("show");
  }

  function displayStatus(text) {
    const el = document.getElementById("statusStat");
    if (el) el.textContent = text;
  }

  function setTrainingStatus(text) {
    trainingStatus = text;
    if (!currentPrediction) displayStatus(text);
  }

  function normalizeSplitStrategy(value) {
    const normalized = String(value || "").trim().toLowerCase();
    if (normalized.includes("scaffold")) return "scaffold";
    if (normalized.includes("random")) return "random";
    return normalized;
  }

  function selectSplitStrategy(value) {
    const select = document.getElementById("splitStrategy");
    if (!select) return;
    const expected = normalizeSplitStrategy(value);
    const option = Array.from(select.options).find(function (item) {
      return normalizeSplitStrategy(item.value) === expected;
    });
    if (option) select.value = option.value;
  }

  function bindFileDisplay(inputId, infoId, prefix) {
    const input = document.getElementById(inputId);
    const info = document.getElementById(infoId);
    if (!input || !info) return;

    input.addEventListener("change", function () {
      if (!input.files || !input.files[0]) {
        info.textContent = "未选择文件";
        return;
      }

      const file = input.files[0];
      info.textContent = `${prefix}: ${file.name} (${(file.size / 1024).toFixed(1)} KB)`;
    });
  }

  function setBatchValidationMessage(text) {
    const message = document.getElementById("batchValidationMessage");
    if (!message) return;
    message.textContent = text || "";
    message.hidden = !text;
  }

  async function handlePredict(url, formData, btnId) {
    const btn = document.getElementById(btnId);
    const isSingleRequest = btnId === "submitBtn";
    if (activeTab !== (isSingleRequest ? "single" : "batch") || btn.disabled) return;
    const prediction = {button: btn};
    currentPrediction = prediction;
    const loading = document.getElementById("loading");
    const results = document.getElementById("results");
    btn.disabled = true;
    if (loading) loading.classList.add("show");
    if (results) results.classList.remove("show");
    ActivityResults.resetViews();
    displayStatus("Predicting");

    try {
      // Keep stale sessions safe: the API returns a structured failure when no
      // explicit family target is selected; never invent a legacy fallback.
      if (formData.get("target") === "") formData.delete("target");
      const res = await fetch(url, { method: "POST", body: formData });
      const data = await res.json();
      if (currentPrediction !== prediction) return;

      if (res.ok === false || !Array.isArray(data.results)) {
        throw new Error(data.detail || data.error || "预测失败");
      }

      ActivityResults.renderPredictionResults(data, {isSingleRequest});
      const status = data.results.length
        ? data.status || (data.success === true ? "passed" : "failed")
        : "failed";
      displayStatus(ActivityResults.statusLabel(status));
    } catch (e) {
      if (currentPrediction !== prediction) return;
      displayStatus("Error");
      alert("请求失败: " + e.message);
    } finally {
      if (currentPrediction === prediction) {
        btn.disabled = false;
        if (loading) loading.classList.remove("show");
      }
    }
  }

  function exportConfig() {
    const cfg = {
      task_type: document.getElementById("trainTaskType").value,
      epochs: document.getElementById("epochs").value,
      learning_rate: document.getElementById("learningRate").value,
      batch_size: document.getElementById("batchSize").value,
      dropout: document.getElementById("dropout").value,
      target_column: document.getElementById("targetColumn").value,
      smiles_column: document.getElementById("smilesColumn").value,
      classification_threshold: document.getElementById("classificationThreshold").value,
      classification_direction: document.getElementById("classificationDirection").value,
      split_strategy: normalizeSplitStrategy(
        document.getElementById("splitStrategy").value,
      ),
      exported_at: new Date().toISOString(),
    };
    const blob = new Blob([JSON.stringify(cfg, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "train_config.json";
    a.click();
    URL.revokeObjectURL(url);
  }

  function importConfig() {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".json";
    input.onchange = function (e) {
      const file = e.target.files && e.target.files[0];
      if (!file) return;

      const reader = new FileReader();
      reader.onload = function (ev) {
        try {
          const cfg = JSON.parse(ev.target.result);
          if (cfg.task_type) document.getElementById("trainTaskType").value = cfg.task_type;
          if (cfg.epochs) document.getElementById("epochs").value = cfg.epochs;
          if (cfg.learning_rate) {
            document.getElementById("learningRate").value = cfg.learning_rate;
          }
          if (cfg.batch_size) {
            document.getElementById("batchSize").value = cfg.batch_size;
          }
          if (cfg.dropout) document.getElementById("dropout").value = cfg.dropout;
          if (cfg.target_column) {
            document.getElementById("targetColumn").value = cfg.target_column;
            delete document.getElementById("targetColumn").dataset.suggested;
          }
          if (cfg.smiles_column) {
            document.getElementById("smilesColumn").value = cfg.smiles_column;
            delete document.getElementById("smilesColumn").dataset.suggested;
          }
          if (cfg.classification_threshold !== undefined) {
            document.getElementById("classificationThreshold").value = cfg.classification_threshold;
          }
          if (cfg.classification_direction) {
            document.getElementById("classificationDirection").value = cfg.classification_direction;
          }
          if (cfg.split_strategy) {
            selectSplitStrategy(cfg.split_strategy);
          }
          ActivityPreflight.setTrainHint("✅ 配置已导入", "ok");
        } catch (err) {
          alert("JSON 格式错误，无法解析。");
        }
      };
      reader.readAsText(file);
    };
    input.click();
  }

  const trainingCallbacks = {
    onFinish: function (state) {
      if (state === "canceled") setTrainingStatus("训练已取消");
      const trainBtn = document.getElementById("trainBtn");
      if (trainBtn) {
        trainBtn.disabled = false;
        trainBtn.textContent = "🚀 重新训练";
      }
    },
    onCompleted: async function () {
      setTrainingStatus("Model Ready");
      await ActivityModels.loadModels();
    },
    onFailed: function () {
      setTrainingStatus("Failed");
    },
  };

  async function startTraining() {
    const formData = ActivityPreflight.takeLaunch();
    const trainBtn = document.getElementById("trainBtn");
    if (!formData || trainBtn.disabled) return;
    const totalEpochs = Number(formData.get("epochs"));
    const taskType = formData.get("task_type");
    trainBtn.disabled = true;
    trainBtn.textContent = "⏳ 提交中...";
    setTrainingStatus("Starting Train...");

    ActivityCharts.resetMetricsChartState(taskType);

    const progressBar = document.getElementById("drawerProgressBar");
    if (progressBar) progressBar.style.width = "0%";
    const logBox = document.getElementById("trainLog");
    if (logBox) {
      logBox.textContent = "[System] 正在上传数据集与训练配置...\n";
    }
    const resultArea = document.getElementById("trainResultArea");
    if (resultArea) {
      resultArea.style.display = "none";
      resultArea.innerHTML = "";
    }
    ActivityTraining.updateDrawerStats(0, totalEpochs, null, null, "running");
    ActivityTraining.openDrawer();

    try {
      const response = await fetch("/api/activity/train", {
        method: "POST",
        body: formData,
      });
      const data = await response.json();
      if (!response.ok || !data.success) {
        throw new Error(data.detail || data.error || "启动训练失败");
      }

      const jobPayload = {
        jobId: data.job_id,
        totalEpochs: totalEpochs,
        taskType: taskType,
      };
      localStorage.setItem("activeTrainJobId", JSON.stringify(jobPayload));

      if (logBox) logBox.textContent += `[System] 任务已提交, Job ID: ${data.job_id}\n`;
      if (trainBtn) trainBtn.textContent = "⏳ 训练中...";
      setTrainingStatus("Training");

      ActivityTraining.startPolling(data.job_id, totalEpochs, trainingCallbacks);
    } catch (e) {
      alert("训练错误: " + e.message);
      if (trainBtn) {
        trainBtn.disabled = false;
        trainBtn.textContent = "🚀 启动训练";
      }
      setTrainingStatus("Error");
      ActivityTraining.closeDrawer();
    }
  }

  function bridgeGlobals() {
    window.fillExample = fillExample;
    window.switchTab = switchTab;
    window.importConfig = importConfig;
    window.exportConfig = exportConfig;
    window.startTraining = startTraining;
    window.confirmDeleteModel = ActivityModels.confirmDeleteModel;
    window.openPreflight = ActivityPreflight.open;
    window.closePreflight = ActivityPreflight.close;
    window.confirmPreflight = ActivityPreflight.confirm;
    window.showLaunchModal = ActivityPreflight.showLaunchModal;
    window.closeLaunchModal = ActivityPreflight.closeLaunchModal;
    window.confirmLaunch = ActivityPreflight.confirmLaunch;
    window.openDrawer = ActivityTraining.openDrawer;
    window.minimizeDrawer = ActivityTraining.minimizeDrawer;
    window.closeDrawer = ActivityTraining.closeDrawer;
    window.toggleDrawerFull = ActivityTraining.toggleDrawerFull;
  }

  function init() {
    bindFileDisplay("batchFile", "fileInfo", "批量文件");
    const batchFile = document.getElementById("batchFile");
    batchFile && batchFile.addEventListener("change", () => setBatchValidationMessage(""));
    ActivityPreflight.init();
    ActivityModels.initPopover();
    ActivityModels.loadModels();
    ActivityCharts.initMetricsChart();

    for (const [formId, buttonId, endpoint] of [
      ["predictForm", "submitBtn", "/api/activity/predict"],
      ["batchForm", "batchSubmitBtn", "/api/activity/batch_predict"],
    ]) {
      document.getElementById(buttonId).addEventListener("click", event => {
        if (event.detail > 1) event.preventDefault();
      });
      document.getElementById(formId).addEventListener("submit", event => {
        event.preventDefault();
        if (formId === "batchForm") {
          setBatchValidationMessage("");
          if (!batchFile.files[0]) {
            setBatchValidationMessage("请先上传批量分子文件。");
            return;
          }
        }
        handlePredict(endpoint, new FormData(event.target), buttonId);
      });
    }

    ActivityTraining.initRecovery(trainingCallbacks);

    bridgeGlobals();
  }

  return {
    init: init,
    fillExample: fillExample,
    switchTab: switchTab,
    setStatus: setTrainingStatus,
    bindFileDisplay: bindFileDisplay,
    handlePredict: handlePredict,
    exportConfig: exportConfig,
    importConfig: importConfig,
    startTraining: startTraining,
  };
})();
