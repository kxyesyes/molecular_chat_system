"use strict";

(function () {
  var S = DesignState;
  var UI = DesignUI;
  var Api = DesignApi;
  var Frag = FragmentBrowser;
  var Editor = MoleculeEditor;
  var Props = PropertiesPanel;
  var Hist = HistoryManager;
  var Safe = window.MedChatSafeRender;
  var SMILES_POLL_INTERVAL_MS = 2000;
  var PROPS_DEBOUNCE_MS = 350;

  function setFeedback(msg, type) {
    UI.setFeedback(msg, type);
  }

  async function init() {
    Frag.renderCommon();
    await Frag.loadFrags();
  }

  var KF = document.getElementById("ketcher-frame");
  KF.addEventListener("load", function () {
    setTimeout(function () {
      S.ketcherReady = true;
      UI.toast("编辑器就绪", "success");
      startTimer();
    }, 900);
  });

  function updateCurrentSmilesText(smi) {
    document.getElementById("curSmiles").textContent =
      smi || "等待绘制分子...";
  }

  function schedulePropsCalculation(smi) {
    S.pendingPropsSmiles = smi;
    S.propsRequestSeq = (S.propsRequestSeq || 0) + 1;
    if (S.propsDebounceTimer) clearTimeout(S.propsDebounceTimer);
    S.propsDebounceTimer = setTimeout(runLatestPropsCalculation, PROPS_DEBOUNCE_MS);
  }

  async function runLatestPropsCalculation() {
    S.propsDebounceTimer = null;
    if (S.propsCalcInFlight) return;

    var requestedSmiles = S.pendingPropsSmiles || "";
    if (!requestedSmiles) {
      Props.clearPropsUI();
      return;
    }

    S.propsCalcInFlight = true;
    var requestToken = S.propsRequestSeq;
    try {
      await Props.calcProps(requestedSmiles, "", requestToken);
    } finally {
      S.propsCalcInFlight = false;
      if (S.pendingPropsSmiles !== requestedSmiles) {
        schedulePropsCalculation(S.pendingPropsSmiles);
      }
    }
  }

  async function syncSmilesFromEditor() {
    if (document.hidden || S.propsPollInFlight) return;

    S.propsPollInFlight = true;
    try {
      var smi = await Editor.getSMILES();
      if (smi !== S.smiles) {
        S.smiles = smi;
        updateCurrentSmilesText(smi);
        schedulePropsCalculation(smi);
      }
    } catch (err) {
      console.warn("同步 Ketcher SMILES 失败:", err);
    } finally {
      S.propsPollInFlight = false;
    }
  }

  function startTimer() {
    if (S.propsTimer) clearInterval(S.propsTimer);
    syncSmilesFromEditor();
    S.propsTimer = setInterval(syncSmilesFromEditor, SMILES_POLL_INTERVAL_MS);
  }

  document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
      if (S.propsDebounceTimer) clearTimeout(S.propsDebounceTimer);
      S.propsDebounceTimer = null;
      return;
    }
    if (S.pendingPropsSmiles !== undefined && !S.propsDebounceTimer) {
      schedulePropsCalculation(S.pendingPropsSmiles);
    }
    if (S.ketcherReady) syncSmilesFromEditor();
  });

  async function sendAI() {
    var cmd = document.getElementById("aiInput").value.trim();
    if (!cmd) {
      setFeedback("请输入优化指令，例如：提高QED、降低LogP、增加水溶性。", "warn");
      return;
    }

    S.optimizationCommand = cmd;
    var replyEl = document.getElementById("aiReply");
    replyEl.style.display = "block";
    replyEl.textContent = "思考中...";
    setFeedback("正在分析当前分子和片段库...", "info");

    try {
      var d = await Api.aiRecommend(cmd, S.smiles, S.curProps || {});
      if (d.success) {
        var modeBadge = d.recommendation_mode === "llm"
          ? "AI 推荐"
          : "本地规则推荐";
        replyEl.innerHTML = "<strong>" + modeBadge + "</strong><br>" + Safe.escapeHtml(d.reply || "");
        if (d.fallback_used) {
          setFeedback("AI 模型暂不可用，已切换为本地规则推荐。", "warn");
        } else {
          setFeedback("已生成推荐，并同步刷新左侧候选片段。", "success");
        }
        if (d.warning) UI.toast(d.warning, "info");
        if (d.recommended_fragments && d.recommended_fragments.length) {
          Frag.renderFragGrid(d.recommended_fragments);
        }
      } else {
        replyEl.innerHTML =
          "<strong>AI建议:</strong><br>" +
          Safe.escapeHtml(d.error || "AI 推荐暂时不可用，请稍后重试。");
        setFeedback(d.error || "AI 推荐失败，请稍后重试。", "error");
        UI.toast(d.error || "AI 推荐失败", "error");
      }
    } catch (_) {
      replyEl.innerHTML =
        "<strong>AI建议:</strong><br>" +
        Safe.escapeHtml("网络异常，请稍后重试。");
      setFeedback("网络异常，AI 推荐没有完成。", "error");
      UI.toast("网络错误", "error");
    }
  }

  function fillAI(btn) {
    document.getElementById("aiInput").value = btn.textContent;
  }

  async function saveMol() {
    var smi = await Editor.getSMILES();
    if (!smi) {
      UI.toast("请先绘制分子", "error");
      return;
    }

    try {
      var d = await Api.saveMolecule(smi, S.curProps);
      if (d.success) {
        setFeedback("当前分子已保存到设计结果目录。", "success");
        UI.toast("分子已成功保存", "success");
      } else {
        setFeedback(d.error || "保存失败。", "error");
        UI.toast("保存失败: " + d.error, "error");
      }
    } catch (_) {
      setFeedback("网络异常，分子保存没有完成。", "error");
      UI.toast("网络错误", "error");
    }
  }

  async function exportAll() {
    if (!S.history || S.history.length === 0) {
      UI.toast("历史记录为空", "error");
      return;
    }

    try {
      var r = await Api.exportHistory(S.history);
      if (r.ok) {
        var blob = await r.blob();
        var url = window.URL.createObjectURL(blob);
        var a = document.createElement("a");
        a.href = url;
        a.download = "molecular_design_history_" + Date.now() + ".csv";
        document.body.appendChild(a);
        a.click();
        a.remove();
        setFeedback("迭代历史已导出为 CSV。", "success");
        UI.toast("历史导出成功", "success");
      } else {
        setFeedback("历史导出失败，请检查历史记录。", "error");
        UI.toast("导出失败", "error");
      }
    } catch (_) {
      setFeedback("网络异常，历史导出没有完成。", "error");
      UI.toast("网络错误", "error");
    }
  }

  function handleUpload(event) {
    var file = event && event.target && event.target.files && event.target.files[0];
    if (!file) return;
    var reader = new FileReader();
    reader.onload = function () {
      var text = String(reader.result || "").trim();
      var lines = text.split(/\r?\n/).map(function (line) { return line.trim(); }).filter(Boolean);
      var smiles = lines[0] || "";
      if (lines.length > 1 && /^\s*(name\s*,\s*)?smiles\s*$/i.test(lines[0])) smiles = lines[1].split(",").pop().trim();
      if (!smiles) { UI.toast("文件中没有找到 SMILES", "error"); return; }
      Editor.setSMILES(smiles).then(function () {
        S.smiles = smiles;
        updateCurrentSmilesText(smiles);
        schedulePropsCalculation(smiles);
        UI.toast("已导入分子", "success");
      }).catch(function () { UI.toast("文件中的 SMILES 无法载入", "error"); });
    };
    reader.readAsText(file);
    event.target.value = "";
  }

  window.onSearch = Frag.onSearch;
  window.toggleFilter = Frag.toggleFilter;
  window.changePage = Frag.changePage;
  window.selFrag = Frag.selFrag;

  window.showImport = UI.showImport;
  window.closeImport = UI.closeImport;
  window.confirmImport = Editor.confirmImport;
  window.exportSmiles = Editor.exportSmiles;
  window.copySmiles = Editor.copySmiles;
  window.clearCanvas = Editor.clearCanvas;
  window.detectSites = Editor.detectSites;
  window.execSubstitute = Editor.execSubstitute;
  window.restoreCandidate = Editor.restoreCandidate;

  window.clearHist = Hist.clearHist;
  window.restoreHist = Hist.restoreHist;

  window.sendAI = sendAI;
  window.fillAI = fillAI;
  window.handleUpload = handleUpload;
  window.saveMol = saveMol;
  window.exportAll = exportAll;

  window.toggleAcc = UI.toggleAcc;

  init();
})();
