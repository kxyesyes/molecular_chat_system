"use strict";

window.ActivityResults = (function () {
  function isNumber(value) {
    return typeof value === "number" && Number.isFinite(value);
  }

  function formatProbability(value) {
    return isNumber(value) && value >= 0 && value <= 1
      ? `${(value * 100).toFixed(1)}%` : "不可用";
  }

  function statusLabel(status) {
    return {passed: "完成", partial: "部分完成", failed: "失败"}[status] || "失败";
  }

  function rowStatus(item) {
    if (item.status === "partial") return "partial";
    return item.success === true && (!item.status || item.status === "passed")
      ? "passed" : "failed";
  }

  function warningStrings(warnings) {
    return Array.isArray(warnings) ? warnings.filter(value => typeof value === "string") : [];
  }

  function createCell(text, styleText) {
    const cell = document.createElement("td");
    if (styleText) cell.style.cssText = styleText;
    cell.textContent = text;
    return cell;
  }

  function createTagCell(text, className) {
    const cell = document.createElement("td");
    const tag = document.createElement("span");
    tag.className = className;
    tag.textContent = text;
    cell.appendChild(tag);
    return cell;
  }

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
    if (!isNumber(item.activity_score) || item.success === false) return;
    const score = item.activity_score;
    const label = item.class || "Unknown";
    const taskType = ActivityModels.getTaskType();
    const range = ActivityModels.getRangeConfig();
    const markerPct = Math.max(
      0,
      Math.min(100, ((score - range.min) / (range.max - range.min || 1)) * 100),
    );

    ActivityUtils.setText(
      "singleConfidenceValue",
      formatProbability(item.confidence),
    );
    ActivityUtils.setText("singleClassValue", label);
    ActivityUtils.setText(
      "singleTaskTypeValue",
      taskType === "classification" ? "分类" : "回归",
    );
    ActivityUtils.setText(
      "singleModelMeta",
      `结果靶点：${item.requested_target || item.target || "不可用"} / 模型：${item.model_id || "不可用"}`,
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

    tbody.replaceChildren();

    dataList.forEach(function (item, index) {
      const score = item.activity_score;
      const cls = item.class || (item.success === false ? "Failed" : "Unknown");

      const rowId = `row-score-${index}`;
      const row = document.createElement("tr");
      row.className = "stagger-item";
      row.style.animationDelay = `${index * 0.05}s`;

      const smilesCell = createCell(
        item.smiles || "-",
        "max-width: 360px; word-break: break-all; font-family: Consolas, monospace;",
      );

      let animatedScoreCell = null;
      if (item.success === false) {
        row.append(
          smilesCell,
          createCell(
            item.error || "Error",
            "font-weight:700; min-width: 80px; color: #b91c1c;",
          ),
          createTagCell("Failed", "tag tag-error"),
          createCell("--"),
        );
      } else if (!isNumber(score)) {
        row.append(smilesCell, createCell("不可用"),
          createTagCell(cls, "tag tag-neutral"), createCell(formatProbability(item.confidence)));
      } else {
        const scoreCell = createCell(
          "0.000",
          "font-weight:700; min-width: 80px;",
        );
        scoreCell.id = rowId;
        const tagClass = ActivityUtils.getTagClass(
          cls,
          score,
          true,
          ActivityModels.getTaskType(),
          ActivityModels.getRangeConfig(),
        );
        row.append(
          smilesCell,
          scoreCell,
          createTagCell(cls, `tag ${tagClass}`),
          createCell(formatProbability(item.confidence)),
        );
        animatedScoreCell = scoreCell;
      }

      tbody.appendChild(row);
      if (animatedScoreCell) {
        ActivityUtils.animateNumber(animatedScoreCell, score);
      }
    });
  }

  function renderPredictionResults(data, options) {
    const results = document.getElementById("results");
    resetViews();
    const status = data.results.length
      ? data.status || (data.success === true ? "passed" : "failed") : "failed";
    ActivityUtils.setText("predictionStatus", [statusLabel(status),
      !data.results.length ? "无预测结果" : "",
      ...warningStrings(data.warnings)].filter(Boolean).join("；"));

    // Identify the returned contract, never the form selection or global model.
    if (data.results.some(item => item && ["predicted_pIC50", "activity_probability",
      "activity_class", "requested_target", "family_id"].some(key =>
      Object.prototype.hasOwnProperty.call(item, key)))) {
      renderFamilyResults(data.results);
      if (results) results.classList.add("show");
      return;
    }
    if (data.results.some(item => item && item.task_type)) {
      renderTaskResults(data.results);
      if (results) results.classList.add("show");
      return;
    }
    setHeaders(["SMILES", "预测活性值", "分类", "置信度"]);
    const successfulResults = data.results.filter(function (item) {
      return item && item.success !== false && isNumber(item.activity_score);
    });
    const isSingleRequest = Boolean(options && options.isSingleRequest);

    renderResultsTable(data.results);

    if (!successfulResults.length) {
      if (results) results.classList.add("show");
      return;
    }

    if (isSingleRequest && successfulResults.length === 1) {
      renderSingleSummary(successfulResults[0]);
    } else {
      ActivityCharts.renderHistogram(successfulResults);
    }

    if (results) results.classList.add("show");
  }

  function setHeaders(labels) {
    const header = document.getElementById("resultsHeader");
    if (!header) return;
    header.replaceChildren();
    labels.forEach(function (label) {
      const cell = document.createElement("th");
      cell.textContent = label;
      header.appendChild(cell);
    });
  }

  function resultDetails(item) {
    const notes = warningStrings(item.warnings);
    if (item.error) notes.push(item.error);
    if (item.note) notes.push(item.note);
    if (item.errors && typeof item.errors === "object") {
      Object.entries(item.errors).forEach(function ([stage, error]) {
        notes.push(`${stage}: ${typeof error === "string" ? error : JSON.stringify(error)}`);
      });
    }
    if (item.classification_regression_consistent === false) {
      notes.push("分类与回归预测不一致，保留两项原始结果。");
    }
    const cell = createCell(notes.join("；") || "—", "word-break: break-word;");
    const provenance = item.provenance || {};
    const source = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = "查看本次预测来源";
    source.appendChild(summary);
    [["请求靶点", item.requested_target], ["家族", item.family_id || provenance.family_id],
      ["模型组", item.bundle_id || provenance.bundle_id]].forEach(function ([label, value]) {
      const line = document.createElement("p");
      line.textContent = `${label}：${value || "不可用"}`;
      source.appendChild(line);
    });
    // Keep the complete returned evidence (hashes, scope, model identities, flags).
    // JSON is inert text, not HTML, and does not depend on mutable form state.
    const evidence = document.createElement("pre");
    evidence.style.cssText = "white-space: pre-wrap; overflow-wrap: anywhere;";
    evidence.textContent = item.provenance ? JSON.stringify(item.provenance, null, 2) : "来源不可用";
    source.appendChild(evidence);
    cell.appendChild(source);
    return cell;
  }

  function renderFamilyResults(rows) {
    setHeaders(["SMILES", "预测 pIC50", "活性分类", "活性概率", "状态", "说明 / 来源"]);
    const tbody = document.getElementById("resultsBody");
    if (!tbody) return;
    tbody.replaceChildren();
    rows.forEach(function (item) {
      const row = document.createElement("tr");
      const status = rowStatus(item);
      const value = item.predicted_pIC50;
      row.append(
        createCell(item.smiles || "—", "word-break: break-all;"),
        createCell(status !== "failed" && isNumber(value) ? value.toFixed(4) : "不可用"),
        createCell(item.activity_class || "不可用"),
        createCell(status !== "failed" ? formatProbability(item.activity_probability) : "不可用"),
        createCell(statusLabel(status)), resultDetails(item),
      );
      tbody.appendChild(row);
    });
  }

  function renderTaskResults(rows) {
    setHeaders(["SMILES", "任务", "Endpoint", "预测值", "单位", "状态 / 说明 / 来源"]);
    const tbody = document.getElementById("resultsBody");
    if (!tbody) return;
    tbody.replaceChildren();
    rows.forEach(function (item) {
      const classification = item.task_type === "classification";
      const value = classification ? item.probability : item.value;
      const valid = rowStatus(item) !== "failed" && isNumber(value)
        && (!classification || (value >= 0 && value <= 1));
      const row = document.createElement("tr");
      const details = resultDetails(item);
      const status = document.createElement("p");
      status.textContent = statusLabel(valid ? rowStatus(item) : "failed");
      details.appendChild(status);
      row.append(createCell(item.smiles || "—"), createCell(item.task_type || "不可用"),
        createCell(item.endpoint || "不可用"), createCell(valid ? value.toFixed(4) : "不可用"),
        createCell(item.units || "不可用"), details);
      tbody.appendChild(row);
    });
  }

  return {
    statusLabel: statusLabel,
    resetViews: resetViews,
    renderSingleSummary: renderSingleSummary,
    renderResultsTable: renderResultsTable,
    renderPredictionResults: renderPredictionResults,
  };
})();
