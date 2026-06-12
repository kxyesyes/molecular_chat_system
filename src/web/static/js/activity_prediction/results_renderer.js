"use strict";

window.ActivityResults = (function () {
  function resetViews() {
    const singleSummary = document.getElementById("singleSummary");
    const batchHistEl = document.getElementById("batchHistogram");

    if (singleSummary) singleSummary.style.display = "none";
    if (batchHistEl) batchHistEl.style.display = "none";

    if (batchHistEl) {
      const chart = echarts.getInstanceByDom(batchHistEl);
      if (chart) chart.clear();
    }
  }

  function renderSingleSummary(item) {
    const score = Number(item.activity_score || 0);
    const conf = Number(item.confidence || 0);
    const label = item.class || "Unknown";
    const taskType = ActivityModels.getTaskType();
    const model = ActivityModels.getSelectedMeta();
    const range = ActivityModels.getRangeConfig();
    const markerPct = Math.max(
      0,
      Math.min(100, ((score - range.min) / (range.max - range.min || 1)) * 100),
    );

    ActivityUtils.setText(
      "singleConfidenceValue",
      `${(conf * 100).toFixed(1)}%`,
    );
    ActivityUtils.setText("singleClassValue", label);
    ActivityUtils.setText(
      "singleTaskTypeValue",
      taskType === "classification" ? "分类" : "回归",
    );
    ActivityUtils.setText(
      "singleModelMeta",
      `${(model && model.target) || range.label} / ${
        (model && model.name) || "当前模型"
      }`,
    );
    ActivityUtils.setText(
      "activityBandSubtitle",
      taskType === "classification"
        ? "标记线展示当前预测置信度在概率区间中的位置。"
        : "标记线展示当前预测活性值在低、中、高区间中的位置。",
    );
    ActivityUtils.setText(
      "activityBandRange",
      `${range.min.toFixed(1)} - ${range.max.toFixed(1)}`,
    );
    ActivityUtils.setText(
      "activityBandMarkerLabel",
      ActivityUtils.formatScore(score),
    );
    ActivityUtils.setText("bandTickStart", range.min.toFixed(1));
    ActivityUtils.setText("bandTickLow", range.lowUpper.toFixed(1));
    ActivityUtils.setText("bandTickHigh", range.highUpper.toFixed(1));
    ActivityUtils.setText("bandTickEnd", range.max.toFixed(1));

    const scoreEl = document.getElementById("singleScoreValue");
    if (scoreEl) {
      scoreEl.textContent = "0.000";
      ActivityUtils.animateNumber(scoreEl, score);
    }

    const classTag = document.getElementById("singleClassTag");
    if (classTag) {
      classTag.className = `tag ${ActivityUtils.getTagClass(
        label,
        score,
        true,
        taskType,
        range,
      )}`;
      classTag.textContent = label;
    }

    const marker = document.getElementById("activityBandMarker");
    if (marker) marker.style.left = `${markerPct}%`;

    const summary = document.getElementById("singleSummary");
    if (summary) summary.style.display = "block";
  }

  function renderResultsTable(dataList) {
    const tbody = document.getElementById("resultsBody");
    if (!tbody) return;

    tbody.innerHTML = "";

    dataList.forEach(function (item, index) {
      const score = Number(item.activity_score || 0);
      const conf = Number(item.confidence || 0);
      const cls = item.class || (item.success === false ? "Failed" : "Unknown");

      const rowId = `row-score-${index}`;
      const row = document.createElement("tr");
      row.className = "stagger-item";
      row.style.animationDelay = `${index * 0.05}s`;

      if (item.success === false) {
        row.innerHTML = `
          <td style="max-width: 360px; word-break: break-all; font-family: Consolas, monospace;">${item.smiles || "-"}</td>
          <td style="font-weight:700; min-width: 80px; color: #b91c1c;">${item.error || "Error"}</td>
          <td><span class="tag tag-error">Failed</span></td>
          <td>--</td>
        `;
      } else {
        row.innerHTML = `
          <td style="max-width: 360px; word-break: break-all; font-family: Consolas, monospace;">${item.smiles || "-"}</td>
          <td id="${rowId}" style="font-weight:700; min-width: 80px;">0.000</td>
          <td><span class="tag ${ActivityUtils.getTagClass(
            cls,
            score,
            true,
            ActivityModels.getTaskType(),
            ActivityModels.getRangeConfig(),
          )}">${cls}</span></td>
          <td>${(conf * 100).toFixed(1)}%</td>
        `;
      }

      tbody.appendChild(row);

      if (item.success !== false) {
        ActivityUtils.animateNumber(document.getElementById(rowId), score);
      }
    });
  }

  function renderPredictionResults(data, options) {
    const results = document.getElementById("results");
    const successfulResults = data.results.filter(function (item) {
      return item && item.success !== false;
    });
    const isSingleRequest = Boolean(options && options.isSingleRequest);

    renderResultsTable(data.results);

    if (!successfulResults.length) {
      throw new Error("No valid prediction results returned.");
    }

    if (isSingleRequest && successfulResults.length === 1) {
      renderSingleSummary(successfulResults[0]);
    } else {
      ActivityCharts.renderHistogram(successfulResults);
    }

    if (results) results.classList.add("show");
  }

  return {
    resetViews: resetViews,
    renderSingleSummary: renderSingleSummary,
    renderResultsTable: renderResultsTable,
    renderPredictionResults: renderPredictionResults,
  };
})();
