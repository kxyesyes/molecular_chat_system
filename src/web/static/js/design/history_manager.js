/* ═══════════════════════════════════════════════════════════
   分子设计模块 – 迭代历史管理
   ═══════════════════════════════════════════════════════════ */
"use strict";

var HistoryManager = (function () {
  var S = DesignState;
  var UI = DesignUI;

  /** 添加一条历史 */
  function addHist(smi, props) {
    S.history.push({ smi: smi, props: props, step: S.iter });
    renderHist();
  }

  /** 渲染历史列表 */
  function renderHist() {
    var list = document.getElementById("histList");
    if (!S.history.length) {
      list.innerHTML =
        '<div style="text-align:center;padding:12px;font-size:12px;">暂无</div>';
      return;
    }
    list.innerHTML = S.history
      .slice()
      .reverse()
      .map(function (h) {
        return (
          '<div class="h-item" onclick="restoreHist(\'' +
          UI.esc(h.smi) +
          "')\">" +
          '<div class="h-preview">⚗️</div>' +
          '<div class="h-info"><div class="h-step">轮 ' +
          h.step +
          "</div>" +
          '<div class="h-smiles">' +
          h.smi.substring(0, 20) +
          "...</div></div>" +
          '<div class="h-qed">' +
          (h.props?.qed ?? 0).toFixed(2) +
          "</div></div>"
        );
      })
      .join("");
  }

  /** 恢复某条历史 */
  async function restoreHist(smi) {
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
