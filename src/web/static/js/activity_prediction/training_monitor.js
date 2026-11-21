"use strict";

window.ActivityTraining = (function () {
  let activeTimer = null;

  function getMetricDescriptor(metrics) {
    const source = metrics || {};
    if (source.r2 !== undefined) return { key: "r2", label: "R2" };
    if (source.auc_pr !== undefined) return { key: "auc_pr", label: "AUC-PR" };
    if (source.accuracy !== undefined) return { key: "accuracy", label: "Accuracy" };
    return { key: "value", label: "Metric" };
  }

  function normalizeState(state) {
    return state === "done" || state === "error" ? state : "running";
  }

  function formatMetricValue(value) {
    return value !== undefined && value !== null
      ? Number(value).toFixed(4)
      : "--";
  }

  function createMetricCard(label, value, valueId) {
    const card = document.createElement("div");
    card.className = "metric-card";

    const name = document.createElement("div");
    name.className = "metric-name";
    name.textContent = label;

    const metricValue = document.createElement("div");
    metricValue.className = "metric-value";
    metricValue.id = valueId;
    metricValue.textContent = value;

    card.append(name, metricValue);
    return card;
  }

  function openDrawer() {
    const dock = document.getElementById("train-dock");
    const drawer = document.getElementById("train-drawer");
    if (!drawer || !dock) return;

    dock.style.display = "none";
    drawer.style.display = "flex";

    requestAnimationFrame(function () {
      drawer.style.transform = "translateY(0)";
    });

    ActivityCharts.initMetricsChart();
  }

  function minimizeDrawer() {
    const drawer = document.getElementById("train-drawer");
    const dock = document.getElementById("train-dock");
    if (!drawer || !dock) return;

    drawer.style.transform = "translateY(100%)";
    setTimeout(function () {
      drawer.style.display = "none";
    }, 400);
    dock.style.display = "flex";
  }

  function closeDrawer() {
    const drawer = document.getElementById("train-drawer");
    const dock = document.getElementById("train-dock");
    if (!drawer || !dock) return;

    drawer.style.transform = "translateY(100%)";
    setTimeout(function () {
      drawer.style.display = "none";
      drawer.style.transform = "";
    }, 400);
    dock.style.display = "none";
  }

  function toggleDrawerFull() {
    const drawer = document.getElementById("train-drawer");
    if (!drawer) return;
    drawer.classList.toggle("fullscreen");
    setTimeout(function () {
      ActivityCharts.initMetricsChart();
    }, 420);
  }

  function updateDock(epoch, total, metric, eta, state) {
    const safeState = normalizeState(state);
    const trainBtn = document.getElementById("trainBtn");
    if (trainBtn && safeState === "running") trainBtn.textContent = "⏳ 训练中...";
    const dot = document.getElementById("dock-dot");
    if (dot) dot.className = `dock-dot ${safeState}`;

    ActivityUtils.setText("dock-epoch-text", `Epoch ${epoch}/${total}`);
    ActivityUtils.setText("dock-metric-text", metric || "—");
    ActivityUtils.setText("dock-eta-text", eta ? `ETA ${eta}` : "ETA —");
    ActivityUtils.setText(
      "dock-status-text",
      safeState === "running"
        ? "Training"
        : safeState === "done"
          ? "Done ✓"
          : "Error ✗",
    );
  }

  function updateDrawerStats(epoch, total, metric, eta, state) {
    const safeState = normalizeState(state);
    const epochEl = document.getElementById("d-epoch");
    if (epochEl) {
      const totalEl = document.createElement("span");
      totalEl.id = "d-total";
      totalEl.textContent = total;
      epochEl.replaceChildren(
        document.createTextNode(`${epoch} / `),
        totalEl,
      );
    }

    ActivityUtils.setText("d-metric", metric || "—");
    ActivityUtils.setText("d-eta", eta || "—");

    const statusEl = document.getElementById("d-status");
    if (!statusEl) return;
    statusEl.textContent =
      safeState === "running"
        ? "Running"
        : safeState === "done"
          ? "Done ✓"
          : "Error ✗";
    statusEl.className = `dstat-value val-${safeState}`;
  }

  function renderTrainingSummary(status, elapsedSeconds) {
    const area = document.getElementById("trainResultArea");
    if (!area) return;

    const source = status || {};
    const bestMetrics = source.best_metrics || source.metrics || {};
    const descriptor = getMetricDescriptor(bestMetrics);
    const secondaryLabel = descriptor.key === "r2" ? "RMSE" : "Accuracy";
    const secondaryValue =
      descriptor.key === "r2" ? bestMetrics.rmse : bestMetrics.accuracy;
    const safeTrainLoss = source.train_loss ?? bestMetrics.train_loss;
    const safeValLoss = source.val_loss ?? bestMetrics.val_loss;
    const min = Math.floor((elapsedSeconds || 0) / 60);
    const sec = Math.floor((elapsedSeconds || 0) % 60);

    const metricGrid = document.createElement("div");
    metricGrid.className = "metric-grid";
    metricGrid.append(
      createMetricCard(
        descriptor.label,
        formatMetricValue(bestMetrics[descriptor.key]),
        "resR2",
      ),
      createMetricCard(
        secondaryLabel,
        formatMetricValue(secondaryValue),
        "resRMSE",
      ),
      createMetricCard("Best Epoch", source.best_epoch || "--", "resBestEpoch"),
      createMetricCard(
        "Train Loss",
        formatMetricValue(safeTrainLoss),
        "resTrainLoss",
      ),
      createMetricCard(
        "Val Loss",
        formatMetricValue(safeValLoss),
        "resValLoss",
      ),
      createMetricCard(
        "训练耗时",
        elapsedSeconds ? `${min}m ${sec}s` : "--",
        "resTime",
      ),
    );
    area.replaceChildren(metricGrid);
    area.style.display = "block";
  }

  function stopPolling() {
    if (!activeTimer) return;
    clearInterval(activeTimer);
    activeTimer = null;
  }

  function renderPolledStatus(status, totalEpochs, elapsed) {
    const rawProgress = Number(status.progress ?? 0);
    const progress = Number.isFinite(rawProgress)
      ? Math.max(0, Math.min(100, rawProgress))
      : 0;
    const progressBar = document.getElementById("drawerProgressBar");
    if (progressBar) progressBar.style.width = `${progress}%`;

    const etaSec =
      progress > 2 ? Math.round((elapsed / progress) * (100 - progress)) : null;
    let eta = etaSec
      ? etaSec > 60
        ? `${Math.floor(etaSec / 60)}m ${etaSec % 60}s`
        : `${etaSec}s`
      : null;

    let metric = ActivityCharts.buildMetricSummary(status.metrics) || "—";

    if (status.metrics) {
      ActivityCharts.updateMetricsChart(
        status.epoch,
        status.metrics,
        status.best_epoch,
        status.best_metrics,
      );
    }

    const logBox = document.getElementById("trainLog");
    if (logBox && status.logs?.length) {
      logBox.textContent = status.logs.join("\n");
      logBox.scrollTop = 99999;
    }

    let epoch = status.epoch ?? 0;
    let displayState = "running";
    if (status.state === "completed") {
      if (logBox) logBox.textContent += "\n[Done] 训练完成!";
      renderTrainingSummary(status, elapsed);
      epoch = totalEpochs;
      metric = ActivityCharts.buildMetricSummary(status.best_metrics || status.metrics) || metric;
      eta = null;
      displayState = "done";
    } else if (status.state === "failed") {
      if (logBox) logBox.textContent += `\n[Error] ${status.error || "训练失败"}`;
      metric = null;
      eta = null;
      displayState = "error";
    }
    updateDrawerStats(epoch, totalEpochs, metric, eta, displayState);
    updateDock(epoch, totalEpochs, metric, eta, displayState);
  }

  async function startPolling(jobId, totalEpochs, callbacks, recovering = false) {
    stopPolling();
    let startTime;
    async function checkStatus(firstRead) {
      let status = null;
      try {
        const response = await fetch(`/api/activity/train/status/${jobId}`);
        if (response.status !== 404) {
          const payload = response.ok && await response.json();
          if (!payload?.success || !payload.status) throw new Error("状态暂不可用");
          status = payload.status;
        }
      } catch {
        ActivityUtils.setText("dock-status-text", "状态暂不可用，正在重试");
        return false;
      }

      const state = status?.state;
      const finished = !status || ["completed", "failed", "canceled"].includes(state);
      if (finished) {
        stopPolling();
        localStorage.removeItem("activeTrainJobId");
        callbacks?.onFinish?.(state || "failed");
      }
      if (state === "canceled") {
        ActivityUtils.setText("dock-status-text", "训练已取消");
        ActivityUtils.setText("d-status", "训练已取消");
        const dot = document.getElementById("dock-dot");
        if (dot) dot.className = "dock-dot";
      } else if (firstRead) {
        if (finished) {
          const dock = document.getElementById("train-dock");
          if (dock) dock.style.display = "none";
        } else {
          updateDock(status.epoch ?? 0, totalEpochs, null, null, "running");
        }
      } else if (status) {
        renderPolledStatus(status, totalEpochs, (Date.now() - startTime) / 1000);
        if (state === "completed") callbacks?.onCompleted?.();
        if (state === "failed") callbacks?.onFailed?.(status.error || "训练失败");
      } else {
        ActivityUtils.setText("dock-status-text", "任务不存在");
      }

      return finished;
    }

    if (recovering && await checkStatus(true)) return;
    startTime = Date.now();
    activeTimer = setInterval(() => checkStatus(false), 1000);
  }

  function initRecovery(callbacks) {
    const saved = localStorage.getItem("activeTrainJobId");
    if (!saved) return;

    let jobId, totalEpochs;
    try {
      ({jobId, totalEpochs} = JSON.parse(saved));
    } catch {
      localStorage.removeItem("activeTrainJobId");
      return;
    }
    if (!jobId) return;

    const trainBtn = document.getElementById("trainBtn");
    if (trainBtn) {
      trainBtn.disabled = true;
      trainBtn.textContent = "正在恢复训练任务…";
    }
    const dock = document.getElementById("train-dock");
    if (dock) dock.style.display = "flex";
    ActivityUtils.setText("dock-status-text", "Reconnecting...");
    startPolling(jobId, totalEpochs, callbacks, true);
  }

  return {
    openDrawer: openDrawer,
    minimizeDrawer: minimizeDrawer,
    closeDrawer: closeDrawer,
    toggleDrawerFull: toggleDrawerFull,
    updateDock: updateDock,
    updateDrawerStats: updateDrawerStats,
    renderTrainingSummary: renderTrainingSummary,
    startPolling: startPolling,
    stopPolling: stopPolling,
    initRecovery: initRecovery,
  };
})();
