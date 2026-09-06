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

  function startPolling(jobId, totalEpochs, callbacks) {
    const options = callbacks || {};
    const startTime = Date.now();

    stopPolling();

    activeTimer = setInterval(async function () {
      try {
        const res = await fetch(`/api/activity/train/status/${jobId}`);
        const payload = await res.json();
        if (!res.ok || !payload.success) return;

        const status = payload.status;
        const rawProgress = Number(status.progress ?? 0);
        const progress = Number.isFinite(rawProgress)
          ? Math.max(0, Math.min(100, rawProgress))
          : 0;
        const progressBar = document.getElementById("drawerProgressBar");
        if (progressBar) progressBar.style.width = `${progress}%`;

        const elapsed = (Date.now() - startTime) / 1000;
        const etaSec =
          progress > 2 ? Math.round((elapsed / progress) * (100 - progress)) : null;
        const etaStr = etaSec
          ? etaSec > 60
            ? `${Math.floor(etaSec / 60)}m ${etaSec % 60}s`
            : `${etaSec}s`
          : null;

        const metricStr = ActivityCharts.buildMetricSummary(status.metrics) || "—";

        updateDrawerStats(
          status.epoch ?? 0,
          totalEpochs,
          metricStr,
          etaStr,
          "running",
        );
        updateDock(status.epoch ?? 0, totalEpochs, metricStr, etaStr, "running");

        if (status.metrics) {
          ActivityCharts.updateMetricsChart(
            status.epoch,
            status.metrics,
            status.best_epoch,
            status.best_metrics,
          );
        }

        if (status.logs && status.logs.length) {
          const logBox = document.getElementById("trainLog");
          if (logBox) {
            logBox.textContent = status.logs.join("\n");
            logBox.scrollTop = 99999;
          }
        }

        if (status.state === "completed" || status.state === "failed") {
          stopPolling();
          localStorage.removeItem("activeTrainJobId");

          if (options.onFinish) options.onFinish(status.state);

          if (status.state === "completed") {
            const logBox = document.getElementById("trainLog");
            if (logBox) logBox.textContent += "\n[Done] 训练完成!";

            renderTrainingSummary(status, elapsed);
            const bestMetric =
              ActivityCharts.buildMetricSummary(status.best_metrics || status.metrics) ||
              metricStr;
            updateDrawerStats(totalEpochs, totalEpochs, bestMetric, null, "done");
            updateDock(totalEpochs, totalEpochs, bestMetric, null, "done");

            if (options.onCompleted) options.onCompleted();
          } else {
            const logBox = document.getElementById("trainLog");
            if (logBox) logBox.textContent += `\n[Error] ${status.error || "训练失败"}`;

            updateDrawerStats(status.epoch ?? 0, totalEpochs, null, null, "error");
            updateDock(status.epoch ?? 0, totalEpochs, null, null, "error");

            if (options.onFailed) options.onFailed(status.error || "训练失败");
          }
        }
      } catch (err) {
        console.error("Polling error:", err);
      }
    }, 1000);
  }

  function initRecovery(callbacks) {
    const saved = localStorage.getItem("activeTrainJobId");
    if (!saved) return;

    try {
      const parsed = JSON.parse(saved);
      if (!parsed.jobId) return;

      const dock = document.getElementById("train-dock");
      if (dock) dock.style.display = "flex";
      ActivityUtils.setText("dock-status-text", "Reconnecting...");

      fetch(`/api/activity/train/status/${parsed.jobId}`)
        .then(function (response) {
          return response.json();
        })
        .then(function (payload) {
          if (
            !payload.success ||
            !payload.status ||
            payload.status.state === "completed" ||
            payload.status.state === "failed"
          ) {
            localStorage.removeItem("activeTrainJobId");
            if (dock) dock.style.display = "none";
            return;
          }

          updateDock(payload.status.epoch ?? 0, parsed.totalEpochs, null, null, "running");
          startPolling(parsed.jobId, parsed.totalEpochs, callbacks);
        })
        .catch(function () {
          localStorage.removeItem("activeTrainJobId");
          if (dock) dock.style.display = "none";
        });
    } catch (err) {
      localStorage.removeItem("activeTrainJobId");
    }
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
