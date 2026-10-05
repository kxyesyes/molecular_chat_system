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
    if (!k) return Promise.resolve();
    S.editorWriteSeq = (S.editorWriteSeq || 0) + 1;
    S.editorWriteDepth = (S.editorWriteDepth || 0) + 1;
    S.editorWriteInFlight = true;
    var previous = S.editorWriteQueue || Promise.resolve();
    var operation = previous.catch(function () {}).then(async function () {
      try {
        await k.setMolecule(smi);
      } finally {
        S.editorWriteDepth = Math.max(0, (S.editorWriteDepth || 1) - 1);
        S.editorWriteInFlight = S.editorWriteDepth > 0;
      }
    });
    S.editorWriteQueue = operation.catch(function () {});
    return operation;
  }

  function resetConnectionState() {
    S.selectedFrag = null;
    S.detectedSiteCount = null;
    S.detectedSitesSmiles = null;
    S.siteRequestSeq = (S.siteRequestSeq || 0) + 1;
    var substituteButton = document.getElementById("subBtn");
    if (substituteButton) substituteButton.disabled = true;
    if (document.querySelectorAll) {
      document.querySelectorAll(".fragment-card.selected,.common-chip.selected").forEach(function (el) {
        el.classList.remove("selected");
      });
    }
  }

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

  function renderCandidateComparison(beforeProps, afterProps, smiles, fragmentLabel) {
    addCandidateComparison(beforeProps, afterProps, smiles, fragmentLabel);
  }

  function renderCandidateBoard() {
    var box = document.getElementById("candidateCompare");
    if (!box) return;
    var candidates = S.candidates || [];
    var header =
      '<div class="candidate-compare-title">' +
      "<span>候选分子对比</span><small>按生成顺序</small>" +
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
          return (
            '<div class="candidate-row">' +
            '<div class="candidate-row-main">' +
            '<div class="candidate-row-top"><strong>#' +
            c.step +
            " " +
            UI.escText(c.fragmentLabel || "fragment") +
            "</strong>" +
            '<button class="ghost-mini" onclick="restoreCandidate(\'' +
            UI.escInlineJs(c.smiles) +
            "')\">恢复</button></div>" +
            '<div class="candidate-smiles">' +
            UI.escText(c.smiles || "") +
            "</div>" +
            '<div class="candidate-metrics">' +
            renderDelta("QED", c.beforeProps, c.props, "qed") +
            renderDelta("LogP", c.beforeProps, c.props, "logp") +
            renderDelta("MW", c.beforeProps, c.props, "mw") +
            renderDelta("TPSA", c.beforeProps, c.props, "tpsa") +
            "</div></div>"
          );
        })
        .join("") +
      "</div>";
  }

  function addCandidateComparison(beforeProps, afterProps, smiles, fragmentLabel) {
    if (!afterProps) return;
    S.candidates = S.candidates || [];
    var existingIndex = S.candidates.findIndex(function (candidate) {
      return candidate.smiles === smiles;
    });
    if (existingIndex >= 0) {
      // A repeated structure is not a new design result. Refresh its
      // evidence in place so the list remains unique and generation-ordered.
      S.candidates[existingIndex].beforeProps = beforeProps || {};
      S.candidates[existingIndex].props = Object.assign({}, afterProps);
      S.candidates[existingIndex].fragmentLabel = fragmentLabel;
      renderCandidateBoard();
      return;
    }
    S.candidates.push({
      step: S.iter,
      smiles: smiles,
      fragmentLabel: fragmentLabel,
      beforeProps: beforeProps || {},
      props: Object.assign({}, afterProps),
    });
    renderCandidateBoard();
  }

  async function restoreCandidate(smiles, successMessage) {
    if (!smiles) return;
    resetConnectionState();
    var mutationToken = (S.mutationSeq || 0) + 1;
    S.mutationSeq = mutationToken;
    S.propsRequestSeq = (S.propsRequestSeq || 0) + 1;
    S.curProps = null;
    S.prevProps = null;
    S.curGoals = null;
    S.propsSmiles = "";
    S.pendingPropsSmiles = "";
    await setSMILES(smiles);
    if (mutationToken !== S.mutationSeq) return;
    S.smiles = smiles;
    document.getElementById("curSmiles").textContent = smiles;
    var result = await PropertiesPanel.calcProps(smiles);
    if (mutationToken !== S.mutationSeq) return;
    if (!result || result.stale || !result.success) return;
    UI.setFeedback(successMessage || "已恢复候选分子，可继续选择片段做下一轮设计。", "success");
    return true;
  }

  function resetCandidateComparison() {
    S.candidates = [];
    renderCandidateBoard();
  }

  /* ── 位点检测 ── */
  async function detectSites() {
    var requestSeq = (S.siteRequestSeq || 0) + 1;
    S.siteRequestSeq = requestSeq;
    var writeSeq = S.editorWriteSeq;
    var smi = await getSMILES();
    if (!smi || requestSeq !== S.siteRequestSeq || writeSeq !== S.editorWriteSeq || S.editorWriteInFlight) return;
    S.smiles = smi;
    S.detectedSiteCount = null;
    S.detectedSitesSmiles = null;
    document.getElementById("subBtn").disabled = true;
    document.getElementById("curSmiles").textContent = smi;
    try {
      var d = await Api.detectSites(smi);
      if (requestSeq !== S.siteRequestSeq) return;
      var currentSmiles = await getSMILES();
      if (requestSeq !== S.siteRequestSeq || writeSeq !== S.editorWriteSeq || currentSmiles !== smi || S.smiles !== smi) return;
      if (d.success) {
        var n = d.sites.length;
        S.detectedSiteCount = n;
        S.detectedSitesSmiles = smi;
        document.getElementById("siteText").textContent =
          n > 0 ? "检测到 " + n + " 个位点" : "请标记[*]";
        var substituteButton = document.getElementById("subBtn");
        if (substituteButton) substituteButton.disabled = n !== 1 || !S.selectedFrag;
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
    var startMutation = S.mutationSeq;
    var writeSeq = S.editorWriteSeq;
    var smi = await getSMILES();
    if (startMutation !== S.mutationSeq || writeSeq !== S.editorWriteSeq || S.editorWriteInFlight) return;
    if (!smi || !S.selectedFrag) {
      UI.setFeedback("请先绘制分子并选择一个片段。", "warn");
      return;
    }
    if (S.detectedSiteCount !== 1 || S.detectedSitesSmiles !== smi) {
      UI.setFeedback("请先识别连接点，并确保母体恰好包含一个可用的 [*] 单键连接点。", "warn");
      return;
    }
    var beforeProps = S.propsSmiles === smi && S.curProps ? Object.assign({}, S.curProps) : null;
    var fragment = Object.assign({}, S.selectedFrag);
    // Capture the editor's current value before the request. The polling loop
    // may not have observed a just-drawn molecule yet.
    S.smiles = smi;
    document.getElementById("curSmiles").textContent = smi;
    var mutationToken = (S.mutationSeq || 0) + 1;
    S.mutationSeq = mutationToken;
    var ov = document.getElementById("loadingOverlay");
    ov.style.display = "flex";
    try {
      var d = await Api.substitute(smi, fragment.smi);
      if (mutationToken !== S.mutationSeq) return;
      if (d.success) {
        if (mutationToken !== S.mutationSeq || S.smiles !== smi) return;
        S.smiles = d.new_smiles;
        document.getElementById("curSmiles").textContent = d.new_smiles;
        await setSMILES(d.new_smiles);
        if (mutationToken !== S.mutationSeq) return;
        var propertyResult = await PropertiesPanel.calcProps(d.new_smiles, smi);
        if (mutationToken !== S.mutationSeq) return;
        if (!propertyResult || propertyResult.success !== true || propertyResult.stale || !S.curProps || S.propsSmiles !== d.new_smiles) {
          resetConnectionState();
          UI.setFeedback("结构取代已完成，但属性计算失败；该候选未加入历史，请检查计算工具后重试。", "warn");
          return;
        }
        S.iter++;
        var iterEl = document.getElementById("iterCount");
        if (iterEl) iterEl.textContent = S.iter;
        HistoryManager.addHist(d.new_smiles, S.curProps);
        renderCandidateComparison(beforeProps, S.curProps, d.new_smiles, fragment.label);
        resetConnectionState();
        UI.setFeedback("取代完成，候选分子的属性变化已更新。", "success");
        UI.toast("取代成功", "success");
      } else {
        UI.setFeedback(d.error || "取代失败，请检查母体和片段连接点。", "error");
        UI.toast("取代失败: " + (d.error || "未知错误"), "error");
      }
    } catch (e) {
      if (mutationToken !== S.mutationSeq) return;
      console.error("execSubstitute error:", e);
      UI.setFeedback("取代请求异常：" + e.message, "error");
      UI.toast("取代异常: " + e.message, "error");
    } finally {
      if (mutationToken === S.mutationSeq) ov.style.display = "none";
    }
  }

  /* ── 导入确认 ── */
  async function confirmImport() {
    var smi = document.getElementById("importInput").value.trim();
    if (smi) {
      if (await restoreCandidate(smi, "已导入分子，属性已更新。")) UI.closeImport();
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

  async function clearCanvas() {
    S.mutationSeq = (S.mutationSeq || 0) + 1;
    S.propsRequestSeq = (S.propsRequestSeq || 0) + 1;
    S.pendingPropsSmiles = "";
    S.smiles = "";
    S.propsSmiles = "";
    S.curProps = null;
    S.prevProps = null;
    S.curGoals = null;
    resetConnectionState();
    document.getElementById("loadingOverlay").style.display = "none";
    HistoryManager.clearHist();
    document.getElementById("curSmiles").textContent = "等待绘制...";
    PropertiesPanel.clearPropsUI();
    resetCandidateComparison();
    UI.setFeedback("", "info");
    await setSMILES("");
  }

  return {
    getK: getK,
    getSMILES: getSMILES,
    setSMILES: setSMILES,
    resetConnectionState: resetConnectionState,
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
