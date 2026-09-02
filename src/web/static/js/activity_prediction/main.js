"use strict";

window.ActivityMain = (function () {
  function fillExample() {
    const input = document.getElementById("smilesInput");
    if (!input) return;
    input.value = "CC(=O)OC1=CC=CC=C1C(=O)O";
    input.focus();
  }

  function switchTab(mode, tabEl) {
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

  function setStatus(text) {
    const el = document.getElementById("statusStat");
    if (el) el.textContent = text;
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

  async function handlePredict(url, formData, btnId) {
    const btn = document.getElementById(btnId);
    const loading = document.getElementById("loading");
    const results = document.getElementById("results");
    const isSingleRequest =
      url.includes("/api/activity/predict") && !url.includes("batch");

    if (btn) btn.disabled = true;
    if (loading) loading.classList.add("show");
    if (results) results.classList.remove("show");
    ActivityResults.resetViews();
    setStatus("Predicting");

    try {
      const res = await fetch(url, { method: "POST", body: formData });
      const data = await res.json();

      if (!data.success || !Array.isArray(data.results)) {
        throw new Error(data.error || "预测失败");
      }

      ActivityResults.renderPredictionResults(data, {
        isSingleRequest: isSingleRequest,
      });
      setStatus("Done");
    } catch (e) {
      alert("请求失败: " + e.message);
      setStatus("Error");
    } finally {
      if (btn) btn.disabled = false;
      if (loading) loading.classList.remove("show");
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
          }
          if (cfg.smiles_column) {
            document.getElementById("smilesColumn").value = cfg.smiles_column;
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

  async function startTraining() {
    const trainFile =
      document.getElementById("trainFile").files &&
      document.getElementById("trainFile").files[0];
    const targetColumn = document.getElementById("targetColumn").value.trim();
    const totalEpochs = Math.max(
      1,
      parseInt(document.getElementById("epochs").value || "50", 10),
    );
    const taskType = document.getElementById("trainTaskType").value;
    const splitStrategy = normalizeSplitStrategy(
      document.getElementById("splitStrategy").value,
    );

    if (!trainFile || !targetColumn) {
      alert("请先上传训练数据并填写目标列名。");
      return;
    }

    const trainBtn = document.getElementById("trainBtn");
    if (trainBtn) {
      trainBtn.disabled = true;
      trainBtn.textContent = "⏳ 提交中...";
    }
    setStatus("Starting Train...");

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

    const formData = new FormData();
    formData.append("file", trainFile);
    formData.append("target_column", targetColumn);
    formData.append("task_type", taskType);
    formData.append("split_strategy", splitStrategy);
    formData.append("epochs", totalEpochs);
    formData.append("learning_rate", document.getElementById("learningRate").value);
    formData.append("batch_size", document.getElementById("batchSize").value);
    formData.append("dropout", document.getElementById("dropout").value);
    formData.append("num_layers", document.getElementById("trainLayers").value);
    formData.append("hidden_size", document.getElementById("trainHidden").value);
    formData.append(
      "weight_decay",
      document.getElementById("trainWeightDecay").value,
    );
    formData.append("patience", document.getElementById("trainPatience").value);
    formData.append("loss_metric", document.getElementById("trainLoss").value);
    formData.append("lr_scheduler", document.getElementById("trainScheduler").value);

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
      setStatus("Training");

      ActivityTraining.startPolling(data.job_id, totalEpochs, {
        onFinish: function () {
          if (trainBtn) {
            trainBtn.disabled = false;
            trainBtn.textContent = "🚀 重新训练";
          }
        },
        onCompleted: async function () {
          setStatus("Model Ready");
          await ActivityModels.loadModels();
        },
        onFailed: function () {
          setStatus("Failed");
        },
      });
    } catch (e) {
      alert("训练错误: " + e.message);
      if (trainBtn) {
        trainBtn.disabled = false;
        trainBtn.textContent = "🚀 启动训练";
      }
      setStatus("Error");
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
    ActivityPreflight.init();
    ActivityModels.initPopover();
    ActivityModels.loadModels();
    ActivityCharts.initMetricsChart();

    const predictForm = document.getElementById("predictForm");
    predictForm &&
      predictForm.addEventListener("submit", function (e) {
        e.preventDefault();
        handlePredict("/api/activity/predict", new FormData(e.target), "submitBtn");
      });

    const batchForm = document.getElementById("batchForm");
    batchForm &&
      batchForm.addEventListener("submit", function (e) {
        e.preventDefault();
        handlePredict(
          "/api/activity/batch_predict",
          new FormData(e.target),
          "batchSubmitBtn",
        );
      });

    ActivityTraining.initRecovery({
      onFinish: function () {
        const trainBtn = document.getElementById("trainBtn");
        if (trainBtn) {
          trainBtn.disabled = false;
          trainBtn.textContent = "🚀 重新训练";
        }
      },
      onCompleted: async function () {
        setStatus("Model Ready");
        await ActivityModels.loadModels();
      },
      onFailed: function () {
        setStatus("Failed");
      },
    });

    bridgeGlobals();
  }

  return {
    init: init,
    fillExample: fillExample,
    switchTab: switchTab,
    setStatus: setStatus,
    bindFileDisplay: bindFileDisplay,
    handlePredict: handlePredict,
    exportConfig: exportConfig,
    importConfig: importConfig,
    startTraining: startTraining,
  };
})();
