/* ═══════════════════════════════════════════════════════════
   分子设计模块 – 分子编辑器桥接
   Ketcher iframe 通讯 / 位点检测 / 取代执行 / 导入导出
   ═══════════════════════════════════════════════════════════ */
"use strict";

var MoleculeEditor = (function () {
  var S = DesignState;
  var Api = DesignApi;
  var UI = DesignUI;

  /* ── Ketcher iframe 桥接 ── */
  function getK() {
    try {
      return document.getElementById("ketcher-frame").contentWindow.ketcher;
    } catch (_) {
      return null;
    }
  }

  async function getSMILES() {
    var k = getK();
    return k ? await k.getSmiles() : "";
  }

  async function setSMILES(smi) {
    var k = getK();
    if (k) await k.setMolecule(smi);
  }

  function formatProp(value) {
    if (value === undefined || value === null || value === "") return "-";
    var n = Number(value);
    return Number.isFinite(n) ? n.toFixed(2) : String(value);
  }

  function metricDelta(beforeProps, afterProps, key) {
    var beforeValue = beforeProps ? Number(beforeProps[key]) : NaN;
    var afterValue = afterProps ? Number(afterProps[key]) : NaN;
    var hasDelta = Number.isFinite(beforeValue) && Number.isFinite(afterValue);
    return hasDelta ? afterValue - beforeValue : null;
  }

  function renderDelta(label, beforeProps, afterProps, key, lowerBetter) {
    var afterValue = afterProps ? Number(afterProps[key]) : NaN;
    var delta = metricDelta(beforeProps, afterProps, key);
    var hasDelta = delta !== null;
    var good = lowerBetter ? delta <= 0 : delta >= 0;
    return (
      '<div class="candidate-metric ' +
      (hasDelta ? (good ? "good" : "warn") : "") +
      '">' +
      '<span class="metric-label">' +
      label +
      "</span>" +
      '<strong class="metric-value">' +
      formatProp(afterValue) +
      "</strong>" +
      (hasDelta
        ? '<span class="metric-delta">' + (delta >= 0 ? "+" : "") + delta.toFixed(2) + "</span>"
        : '<span class="metric-delta">new</span>') +
      "</div>"
    );
  }

  function renderCandidateComparison(beforeProps, afterProps, smiles, fragmentLabel) {
    addCandidateComparison(beforeProps, afterProps, smiles, fragmentLabel);
  }

  function renderCandidateBoard() {
    var box = document.getElementById("candidateCompare");
    if (!box) return;
    var candidates = S.candidates || [];
    var header =
      '<div class="candidate-compare-title">' +
      "<span>候选分子对比</span><small>保留最近 5 次取代</small>" +
      "</div>";
    if (!candidates.length) {
      box.innerHTML =
        header +
        '<div class="candidate-compare-empty">执行一次取代后，这里会显示新旧分子的关键属性变化。</div>';
      return;
    }
    box.innerHTML =
      header +
      '<div class="candidate-list">' +
      candidates
        .map(function (c) {
          var qedDelta = metricDelta(c.beforeProps, c.props, "qed");
          var logpDelta = metricDelta(c.beforeProps, c.props, "logp");
          return (
            '<div class="candidate-row">' +
            '<div class="candidate-row-main">' +
            '<div class="candidate-row-top"><strong>#' +
            c.step +
            " " +
            UI.esc(c.fragmentLabel || "fragment") +
            "</strong>" +
            '<button class="ghost-mini" onclick="restoreCandidate(\'' +
            UI.esc(c.smiles) +
            "')\">恢复</button></div>" +
            '<div class="candidate-smiles">' +
            UI.esc(c.smiles || "") +
            "</div>" +
            '<div class="candidate-metrics">' +
            renderDelta("QED", c.beforeProps, c.props, "qed", false) +
            renderDelta("LogP", c.beforeProps, c.props, "logp", true) +
            renderDelta("MW", c.beforeProps, c.props, "mw", true) +
            renderDelta("TPSA", c.beforeProps, c.props, "tpsa", true) +
            "</div></div>" +
            '<div class="candidate-score">' +
            '<span>QED ' +
            (qedDelta === null ? "new" : (qedDelta >= 0 ? "+" : "") + qedDelta.toFixed(2)) +
            "</span>" +
            '<span>LogP ' +
            (logpDelta === null ? "new" : (logpDelta >= 0 ? "+" : "") + logpDelta.toFixed(2)) +
            "</span>" +
            "</div></div>"
          );
        })
        .join("") +
      "</div>";
  }

  function addCandidateComparison(beforeProps, afterProps, smiles, fragmentLabel) {
    if (!afterProps) return;
    S.candidates = S.candidates || [];
    S.candidates.unshift({
      step: S.iter,
      smiles: smiles,
      fragmentLabel: fragmentLabel,
      beforeProps: beforeProps || {},
      props: Object.assign({}, afterProps),
    });
    S.candidates = S.candidates.slice(0, 5);
    renderCandidateBoard();
  }

  async function restoreCandidate(smiles) {
    if (!smiles) return;
    await setSMILES(smiles);
    S.smiles = smiles;
    document.getElementById("curSmiles").textContent = smiles;
    await PropertiesPanel.calcProps(smiles);
    UI.setFeedback("已恢复候选分子，可继续选择片段做下一轮设计。", "success");
  }

  function resetCandidateComparison() {
    S.candidates = [];
    renderCandidateBoard();
  }

  function renderLegacyCandidateComparison(beforeProps, afterProps, smiles, fragmentLabel) {
    var box = document.getElementById("candidateCompare");
    if (!box || !afterProps) return;
    box.innerHTML =
      '<div class="candidate-compare-head">' +
      '<div><span class="mini-label">Latest candidate</span><strong>' +
      UI.esc(fragmentLabel || "Selected fragment") +
      "</strong></div>" +
      '<button class="ghost-mini" onclick="copySmiles()">Copy SMILES</button>' +
      "</div>" +
      '<div class="candidate-smiles">' +
      UI.esc(smiles || "") +
      "</div>" +
      '<div class="candidate-metrics">' +
      renderDelta("QED", beforeProps, afterProps, "qed", false) +
      renderDelta("LogP", beforeProps, afterProps, "logp", true) +
      renderDelta("MW", beforeProps, afterProps, "mw", true) +
      renderDelta("TPSA", beforeProps, afterProps, "tpsa", true) +
      "</div>";
  }

  /* ── 位点检测 ── */
  async function detectSites() {
    var smi = await getSMILES();
    if (!smi) return;
    S.smiles = smi;
    document.getElementById("curSmiles").textContent = smi;
    try {
      var d = await Api.detectSites(smi);
      if (d.success) {
        var n = d.sites.length;
        document.getElementById("siteText").textContent =
          n > 0 ? "检测到 " + n + " 个位点" : "请标记[*]";
        var ind = document.getElementById("siteInd");
        ind.style.opacity = "1";
        setTimeout(function () {
          ind.style.opacity = "0";
        }, 3000);
        if (n > 0) UI.toast("位点: " + n, "success");
      }
    } catch (_) {
      /* 静默 */
    }
  }

  /* ── 执行取代 ── */
  async function execSubstitute() {
    var smi = await getSMILES();
    if (!smi || !S.selectedFrag) {
      UI.setFeedback("请先绘制分子并选择一个片段。", "warn");
      return;
    }
    var beforeProps = S.curProps ? Object.assign({}, S.curProps) : null;
    var ov = document.getElementById("loadingOverlay");
    ov.style.display = "flex";
    try {
      var d = await Api.substitute(smi, S.selectedFrag.smi);
      if (d.success) {
        S.smiles = d.new_smiles;
        document.getElementById("curSmiles").textContent = d.new_smiles;
        await setSMILES(d.new_smiles);
        S.iter++;
        var iterEl = document.getElementById("iterCount");
        if (iterEl) iterEl.textContent = S.iter;
        await PropertiesPanel.calcProps(d.new_smiles);
        HistoryManager.addHist(d.new_smiles, S.curProps);
        renderCandidateComparison(beforeProps, S.curProps, d.new_smiles, S.selectedFrag.label);
        UI.setFeedback("取代完成，候选分子的属性变化已更新。", "success");
        UI.toast("取代成功", "success");
      } else {
        UI.setFeedback(d.error || "取代失败，请检查母体和片段连接点。", "error");
        UI.toast("取代失败: " + (d.error || "未知错误"), "error");
      }
    } catch (e) {
      console.error("execSubstitute error:", e);
      UI.setFeedback("取代请求异常：" + e.message, "error");
      UI.toast("取代异常: " + e.message, "error");
    } finally {
      ov.style.display = "none";
    }
  }

  /* ── 导入确认 ── */
  function confirmImport() {
    var smi = document.getElementById("importInput").value.trim();
    if (smi) {
      setSMILES(smi);
      S.smiles = smi;
      document.getElementById("curSmiles").textContent = smi;
      PropertiesPanel.calcProps(smi);
      UI.closeImport();
    }
  }

  /* ── 导出 / 复制 / 清空 ── */
  function exportSmiles() {
    getSMILES().then(function (s) {
      if (s) {
        navigator.clipboard.writeText(s).then(function () {
          UI.toast("已复制", "success");
        });
      }
    });
  }

  function copySmiles() {
    var smi = document.getElementById("curSmiles").textContent;
    if (smi && smi !== "等待绘制分子..." && smi !== "等待绘制...") {
      navigator.clipboard.writeText(smi).then(function () {
        UI.toast("SMILES 已复制", "success");
      });
    }
  }

  function clearCanvas() {
    setSMILES("");
    S.smiles = "";
    document.getElementById("curSmiles").textContent = "等待绘制...";
    PropertiesPanel.clearPropsUI();
    resetCandidateComparison();
    UI.setFeedback("", "info");
  }

  return {
    getK: getK,
    getSMILES: getSMILES,
    setSMILES: setSMILES,
    addCandidateComparison: addCandidateComparison,
    renderCandidateComparison: renderCandidateComparison,
    restoreCandidate: restoreCandidate,
    detectSites: detectSites,
    execSubstitute: execSubstitute,
    confirmImport: confirmImport,
    exportSmiles: exportSmiles,
    copySmiles: copySmiles,
    clearCanvas: clearCanvas,
  };
})();
