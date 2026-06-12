/* ═══════════════════════════════════════════════════════════
   分子设计模块 – API 客户端
   所有后端 fetch 调用集中于此
   ═══════════════════════════════════════════════════════════ */
"use strict";

var DesignApi = (function () {
  var _BASE = "/api/design";

  /** 获取片段列表（带搜索/筛选/分页） */
  async function fetchFragments(params) {
    var qs = new URLSearchParams(params);
    var r = await fetch(_BASE + "/fragments?" + qs);
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
  }

  /** 检测取代位点 */
  async function detectSites(smiles) {
    var r = await fetch(_BASE + "/detect_sites", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ smiles: smiles }),
    });
    return r.json();
  }

  /** 执行基团取代 */
  async function substitute(parentSmiles, fragmentSmiles) {
    var r = await fetch(_BASE + "/substitute", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        parent_smiles: parentSmiles,
        fragment_smiles: fragmentSmiles,
      }),
    });
    return r.json();
  }

  /** 计算分子属性 */
  async function calcProperties(smiles) {
    var r = await fetch(_BASE + "/properties", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ smiles: smiles }),
    });
    return r.json();
  }

  /** AI 推荐 */
  async function aiRecommend(command, currentSmiles, currentProps) {
    var r = await fetch(_BASE + "/ai_recommend", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        command: command,
        current_smiles: currentSmiles,
        current_props: currentProps,
      }),
    });
    return r.json();
  }

  /** 保存分子 */
  async function saveMolecule(smiles, properties) {
    var r = await fetch(_BASE + "/save_molecule", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ smiles: smiles, properties: properties }),
    });
    return r.json();
  }

  /** 导出历史 (返回 Response) */
  async function exportHistory(history) {
    return fetch(_BASE + "/export_history", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ history: history }),
    });
  }

  return {
    fetchFragments: fetchFragments,
    detectSites: detectSites,
    substitute: substitute,
    calcProperties: calcProperties,
    aiRecommend: aiRecommend,
    saveMolecule: saveMolecule,
    exportHistory: exportHistory,
  };
})();
