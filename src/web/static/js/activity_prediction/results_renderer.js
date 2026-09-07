"use strict";

window.ActivityResults = (function () {
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

    tbody.replaceChildren();

    dataList.forEach(function (item, index) {
      const score = Number(item.activity_score || 0);
      const conf = Number(item.confidence || 0);
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
          createCell(`${(conf * 100).toFixed(1)}%`),
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
    const isFamily = data.results.some(function (item) {
      return item && Object.prototype.hasOwnProperty.call(item, "predicted_pIC50");
    });
    if (isFamily) {
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
      return item && item.success !== false;
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

  function renderFamilyResults(rows) {
    setHeaders(["SMILES", "预测 pIC50", "活性分类", "活性概率", "状态", "说明"]);
    const tbody = document.getElementById("resultsBody");
    if (!tbody) return;
    tbody.replaceChildren();
    rows.forEach(function (item) {
      const row = document.createElement("tr");
      const status = item.status === "passed" && item.success === true ? "passed"
        : item.status === "partial" ? "partial" : "failed";
      const probability = item.activity_probability;
      const hasClass = status !== "failed" && typeof probability === "number" &&
        Number.isFinite(probability) && probability >= 0 && probability <= 1;
      const value = item.predicted_pIC50;
      const notes = [];
      if (Array.isArray(item.warnings)) notes.push(...item.warnings);
      if (item.errors && typeof item.errors === "object") {
        Object.entries(item.errors).forEach(function ([stage, error]) {
          notes.push(`${stage}: ${error}`);
        });
      }
      if (item.classification_regression_consistent === false && !notes.length) {
        notes.push("分类与回归预测不一致，保留两项原始结果。");
      }
      const detailCell = createCell(notes.join("；") || "—");
      const provenance = item.provenance || {};
      const source = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent = "查看本次预测来源";
      source.appendChild(summary);
      const identities = [["请求靶点", item.requested_target],
        ["模型组", item.bundle_id || provenance.bundle_id],
        ["分类模型", provenance.models && provenance.models.classification && provenance.models.classification.model_id],
        ["回归模型", provenance.models && provenance.models.regression && provenance.models.regression.model_id]];
      identities.forEach(function ([label, identity]) {
        const line = document.createElement("p");
        line.textContent = `${label}：${identity || "不可用"}`;
        source.appendChild(line);
      });
      detailCell.appendChild(source);
      row.append(
        createCell(item.smiles || "—", "word-break: break-all;"),
        createCell(status === "passed" && typeof value === "number" && Number.isFinite(value)
          ? value.toFixed(4) : "—"),
        createCell(hasClass ? item.activity_class || "—" : "—"),
        createCell(hasClass ? `${(probability * 100).toFixed(1)}%` : "—"),
        createCell({passed: "完成", partial: "部分完成", failed: "失败"}[status]),
        detailCell,
      );
      tbody.appendChild(row);
    });
  }

  function renderTaskResults(rows) {
    setHeaders(["SMILES", "任务", "Endpoint", "预测值", "单位", "状态 / 说明"]);
    const tbody = document.getElementById("resultsBody");
    if (!tbody) return;
    tbody.replaceChildren();
    rows.forEach(function (item) {
      const classification = item.task_type === "classification";
      const value = classification ? item.probability : item.value;
      const valid = item.success === true && typeof value === "number" && Number.isFinite(value)
        && (!classification || (value >= 0 && value <= 1));
      const row = document.createElement("tr");
      row.append(createCell(item.smiles || "—"), createCell(item.task_type || "—"),
        createCell(item.endpoint || "—"), createCell(valid ? value.toFixed(4) : "—"),
        createCell(item.units || "—"),
        createCell([valid ? "完成" : "失败", item.error || item.note || "",
          ...(Array.isArray(item.warnings) ? item.warnings : [])].filter(Boolean).join("；")));
      tbody.appendChild(row);
    });
  }

  return {
    resetViews: resetViews,
    renderSingleSummary: renderSingleSummary,
    renderResultsTable: renderResultsTable,
    renderPredictionResults: renderPredictionResults,
  };
})();
