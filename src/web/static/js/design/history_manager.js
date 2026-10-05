/* ═══════════════════════════════════════════════════════════
   分子设计模块 – 迭代历史管理
   ═══════════════════════════════════════════════════════════ */
"use strict";

var HistoryManager = (function () {
  var S = DesignState;
  var UI = DesignUI;

  /** 添加一条历史 */
  function addHist(smi, props) {
    S.history.push({ smi: smi, props: props || {}, step: S.iter });
    renderHist();
  }

  /** 渲染历史列表 */
  function renderHist() {
    // Candidate comparison is the single visible iteration history.  Keeping
    // a second list caused two divergent sources of truth in the old layout.
  }

  /** 恢复某条历史 */
  async function restoreHist(smi) {
    S.mutationSeq = (S.mutationSeq || 0) + 1;
    S.propsRequestSeq = (S.propsRequestSeq || 0) + 1;
    S.curProps = null;
    S.prevProps = null;
    S.curGoals = null;
    S.propsSmiles = "";
    await MoleculeEditor.setSMILES(smi);
    S.smiles = smi;
    document.getElementById("curSmiles").textContent = smi;
    await PropertiesPanel.calcProps(smi);
    UI.toast("已恢复", "info");
  }

  /** 清空历史 */
  function clearHist() {
    S.history = [];
    S.iter = 0;
    var iterEl = document.getElementById("iterCount");
    if (iterEl) iterEl.textContent = "0";
    renderHist();
  }

  return {
    addHist: addHist,
    renderHist: renderHist,
    restoreHist: restoreHist,
    clearHist: clearHist,
  };
})();
