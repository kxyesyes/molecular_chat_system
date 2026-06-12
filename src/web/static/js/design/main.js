"use strict";

(function () {
  var S = DesignState;
  var UI = DesignUI;
  var Api = DesignApi;
  var Frag = FragmentBrowser;
  var Editor = MoleculeEditor;
  var Props = PropertiesPanel;
  var Hist = HistoryManager;

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

  function startTimer() {
    if (S.propsTimer) clearInterval(S.propsTimer);
    S.propsTimer = setInterval(async function () {
      var smi = await Editor.getSMILES();
      if (smi !== S.smiles) {
        S.smiles = smi;
        document.getElementById("curSmiles").textContent =
          smi || "等待绘制分子...";
        if (smi) await Props.calcProps(smi);
        else Props.clearPropsUI();
      }
    }, 2000);
  }

  async function sendAI() {
    var cmd = document.getElementById("aiInput").value.trim();
    if (!cmd) {
      setFeedback("请输入优化指令，例如：提高QED、降低LogP、增加水溶性。", "warn");
      return;
    }

    var replyEl = document.getElementById("aiReply");
    replyEl.style.display = "block";
    replyEl.textContent = "思考中...";
    setFeedback("正在分析当前分子和片段库...", "info");

    try {
      var d = await Api.aiRecommend(cmd, S.smiles, S.curProps);
      if (d.success) {
        var modeBadge = d.fallback_used
          ? '<span class="ai-status fallback">规则推荐</span>'
          : '<span class="ai-status">AI 推荐</span>';
        replyEl.innerHTML = modeBadge + "<br>" + d.reply;
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
          (d.error || "AI 推荐暂时不可用，请稍后重试。");
        setFeedback(d.error || "AI 推荐失败，请稍后重试。", "error");
        UI.toast(d.error || "AI 推荐失败", "error");
      }
    } catch (_) {
      replyEl.innerHTML =
        "<strong>AI建议:</strong><br>网络异常，请稍后重试。";
      setFeedback("网络异常，AI 推荐没有完成。", "error");
      UI.toast("网络错误", "error");
    }
  }

  function aiQuickRec() {
    document.getElementById("aiInput").value = "优化QED";
    sendAI();
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

  window.onSearch = Frag.onSearch;
  window.toggleFilter = Frag.toggleFilter;
  window.changePage = Frag.changePage;
  window.selFrag = Frag.selFrag;
  window.aiQuickRec = aiQuickRec;

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
  window.saveMol = saveMol;
  window.exportAll = exportAll;

  window.toggleAcc = UI.toggleAcc;

  init();
})();
