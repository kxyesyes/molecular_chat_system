// ═══════════════════════════════════════════════════════════
//  反向寻靶模块 – API 请求层
//  所有与后端的网络通信收拢在此，方便统一错误处理与缓存
// ═══════════════════════════════════════════════════════════

const RtApi = (() => {
  /**
   * 加载数据库统计信息（记录总数、分子数、靶点数）
   */
  async function fetchStats() {
    const resp = await fetch(RT_CONFIG.API.STATS);
    const data = await resp.json();
    if (!data.success) throw new Error("加载统计信息失败");
    return data.stats;
  }

  /**
   * 轻量检查本地反向寻靶数据库，不加载大型指纹矩阵
   */
  async function fetchHealth() {
    const resp = await fetch(RT_CONFIG.API.HEALTH);
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "本地库健康检查失败");
    return data;
  }

  /**
   * 单分子 2D 预测
   */
  async function predict2D(formData) {
    const resp = await fetch(RT_CONFIG.API.PREDICT_2D, {
      method: "POST",
      body: formData,
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "预测失败");
    return data;
  }

  /**
   * 单分子 3D 药效团精修预测
   */
  async function predict3D(formData) {
    const resp = await fetch(RT_CONFIG.API.PREDICT_3D, {
      method: "POST",
      body: formData,
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "3D预测失败");
    return data;
  }

  /**
   * 批量预测
   */
  async function batchPredict(formData) {
    const resp = await fetch(RT_CONFIG.API.BATCH_PREDICT, {
      method: "POST",
      body: formData,
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "批量预测失败");
    return data;
  }

  /**
   * 获取相似分子列表
   */
  async function fetchSimilarMolecules(smiles, targetName, threshold, limit = 50) {
    const params = new URLSearchParams({
      smiles,
      target_name: targetName,
      threshold: String(threshold),
      limit: String(limit),
    });
    if (window.lastOrganismFilter) {
      params.set("organism_filter", window.lastOrganismFilter);
    }
    const resp = await fetch(`${RT_CONFIG.API.SIMILAR_MOLECULES}?${params}`);
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "获取失败");
    return data;
  }

  /**
   * 获取 3D 药效团数据
   */
  async function fetchPharmacophore(smiles) {
    const resp = await fetch(RT_CONFIG.API.PHARMACOPHORE, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: `smiles=${encodeURIComponent(smiles)}`,
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "提取失败");
    return data;
  }

  /**
   * 获取 MCS（最大公共子结构）
   */
  async function fetchMCS(smiles1, smiles2, width = 360, height = 260, timeout = 3) {
    const params = new URLSearchParams({
      smiles1, smiles2,
      width: String(width),
      height: String(height),
      timeout: String(timeout),
    });
    const resp = await fetch(`${RT_CONFIG.API.MCS}?${params}`);
    const data = await resp.json();
    return { ok: resp.ok, data };
  }

  /**
   * 构建分子图片 URL
   */
  function smilesImageUrl(smiles, width = 400, height = 300) {
    return `${RT_CONFIG.API.SMILES_TO_IMAGE}?smiles=${encodeURIComponent(smiles)}&width=${width}&height=${height}`;
  }

  return {
    fetchStats,
    fetchHealth,
    predict2D,
    predict3D,
    batchPredict,
    fetchSimilarMolecules,
    fetchPharmacophore,
    fetchMCS,
    smilesImageUrl,
  };
})();
