/* ═══════════════════════════════════════════════════════════
   分子设计模块 – 迭代历史管理
   ═══════════════════════════════════════════════════════════ */
"use strict";

var HistoryManager = (function () {
  var iterations = [];
  var UI = DesignUI;

  function formatProp(value) {
    var n = PropertiesPanel.numeric(value);
    return n === null ? "—" : n.toFixed(2);
  }

  function metricDelta(beforeProps, afterProps, key) {
    var beforeValue = PropertiesPanel.numeric(beforeProps && beforeProps[key]);
    var afterValue = PropertiesPanel.numeric(afterProps && afterProps[key]);
    var hasDelta = beforeValue !== null && afterValue !== null;
    return hasDelta ? afterValue - beforeValue : null;
  }

  function renderDelta(label, beforeProps, afterProps, key) {
    var afterValue = afterProps && afterProps[key];
    var delta = metricDelta(beforeProps, afterProps, key);
    var hasDelta = delta !== null;
    return (
      '<div class="candidate-metric">' +
      '<span class="metric-label">' +
      label +
      "</span>" +
      '<strong class="metric-value">' +
      formatProp(afterValue) +
      "</strong>" +
      (hasDelta
        ? '<span class="metric-delta">' + (delta >= 0 ? "+" : "") + delta.toFixed(2) + "</span>"
        : '<span class="metric-delta">—</span>') +
      "</div>"
    );
  }

  function renderCandidateBoard() {
    document.getElementById("iterCount").textContent = iterations.length;
    var box = document.getElementById("candidateCompare");
    if (!box) return;
    // Map keeps the first position and step when a repeated product replaces its card.
    var candidates = new Map();
    iterations.forEach(function (iteration, index) {
      var previous = candidates.get(iteration.smi);
      candidates.set(iteration.smi, {
        step: previous ? previous.step : index + 1,
        iteration: iteration,
      });
    });
    var header =
      '<div class="candidate-compare-title">' +
      "<span>候选分子对比</span><small>按生成顺序</small>" +
      "</div>";
    if (!candidates.size) {
      box.innerHTML =
        header +
        '<div class="candidate-compare-empty">执行一次取代后，这里会显示新旧分子的关键属性变化。</div>';
      return;
    }
    box.innerHTML =
      header +
      '<div class="candidate-list">' +
      Array.from(candidates.values(), function ({step, iteration}) {
        return (
          '<div class="candidate-row">' +
          '<div class="candidate-row-main">' +
          '<div class="candidate-row-top"><strong>#' +
          step +
          " " +
          UI.escText(iteration.fragmentLabel || "fragment") +
          "</strong>" +
          '<button class="ghost-mini" onclick="restoreCandidate(\'' +
          UI.escInlineJs(iteration.smi) +
          "')\">恢复</button></div>" +
          '<div class="candidate-smiles">' +
          UI.escText(iteration.smi || "") +
          "</div>" +
          '<div class="candidate-metrics">' +
          renderDelta("QED", iteration.beforeProps, iteration.props, "qed") +
          renderDelta("LogP", iteration.beforeProps, iteration.props, "logp") +
          renderDelta("MW", iteration.beforeProps, iteration.props, "mw") +
          renderDelta("TPSA", iteration.beforeProps, iteration.props, "tpsa") +
          "</div></div>"
        );
      }).join("") +
      "</div>";
  }

  function recordSubstitution(smi, props, beforeProps, fragmentLabel) {
    iterations.push({
      smi: smi,
      props: Object.assign({}, props),
      beforeProps: beforeProps,
      fragmentLabel: fragmentLabel,
    });
    renderCandidateBoard();
  }

  function exportRows() {
    return iterations.map(function (iteration, index) {
      return {step: index + 1, smi: iteration.smi, props: iteration.props};
    });
  }

  function clearHist() {
    iterations = [];
    renderCandidateBoard();
  }

  return {recordSubstitution: recordSubstitution, exportRows: exportRows, clearHist: clearHist};
})();
