// ═══════════════════════════════════════════════════════════
//  反向寻靶模块 – 结果渲染器
//  负责将后端返回的 JSON 转换为 DOM 表格/卡片
// ═══════════════════════════════════════════════════════════

const RtResults = (() => {
  const $ = (id) => document.getElementById(id);
  const SINGLE_PAGE_SIZE = 10;
  const singlePageState = {
    results: [],
    is3DMode: false,
    page: 1,
    pageSize: SINGLE_PAGE_SIZE,
  };

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function escapeJsString(value) {
    return String(value ?? "")
      .replace(/\\/g, "\\\\")
      .replace(/'/g, "\\'")
      .replace(/\r/g, "\\r")
      .replace(/\n/g, "\\n")
      .replace(/\u2028/g, "\\u2028")
      .replace(/\u2029/g, "\\u2029");
  }

  function inlineJsArg(value) {
    return escapeHtml(JSON.stringify(String(value ?? "")));
  }

  function paginateResults(items, page = 1, pageSize = SINGLE_PAGE_SIZE) {
    const safeItems = Array.isArray(items) ? items : [];
    const safePageSize = Math.max(1, Number(pageSize) || SINGLE_PAGE_SIZE);
    const totalItems = safeItems.length;
    const totalPages = Math.max(1, Math.ceil(totalItems / safePageSize));
    const safePage = Math.min(Math.max(1, Number(page) || 1), totalPages);
    const startIndex = (safePage - 1) * safePageSize;
    return {
      page: safePage,
      pageSize: safePageSize,
      totalItems,
      totalPages,
      startIndex,
      endIndex: Math.min(startIndex + safePageSize, totalItems),
      items: safeItems.slice(startIndex, startIndex + safePageSize),
    };
  }

  function _ensurePagination() {
    let el = $("rtPagination");
    const table = document.querySelector(".results-table");
    if (!el && table) {
      table.insertAdjacentHTML("afterend", '<div id="rtPagination" class="rt-pagination"></div>');
      el = $("rtPagination");
    }
    return el;
  }

  function _clearPagination() {
    const el = $("rtPagination");
    if (el) el.innerHTML = "";
  }

  // ──────────────────────────────────────
  //  药效团图例渲染（复用 FEAT_META）
  // ──────────────────────────────────────
  function renderPharmLegend(featureCounts, containerId) {
    const el = $(containerId);
    if (!el) return;
    if (!featureCounts || Object.keys(featureCounts).length === 0) {
      el.innerHTML = '<span style="color:#94a3b8">无药效团特征</span>';
      return;
    }
    el.innerHTML = Object.entries(featureCounts)
      .map(([fam, cnt]) => {
        const m = FEAT_META[fam] || { color: "#94a3b8", icon: "◯", label: fam };
        return `<span style="display:inline-flex;align-items:center;gap:4px;background:${m.color}18;
          border:1px solid ${m.color}55;border-radius:20px;padding:3px 10px;font-size:12px;
          font-weight:600;color:${m.color};margin:2px;">
          <span>${m.icon}</span>${m.label} ×${cnt}</span>`;
      })
      .join("");
  }

  // ──────────────────────────────────────
  //  构建单行得分列 HTML
  // ──────────────────────────────────────
  function _buildScoreCell(result, is3DMode) {
    if (is3DMode && result.pharm_combined_3d != null) {
      return `<div style="display:flex;flex-direction:column;gap:4px;">
        <div style="display:flex;align-items:center;gap:6px;">
          <span style="font-size:11px;color:#6d28d9;">🔬 3D</span>
          <div class="similarity-wrapper" style="flex:1;">
            <div class="similarity-bar" style="width:${result.final_3d_score * 100}%;background:linear-gradient(90deg,#7c3aed,#c026d3);"></div>
          </div>
          <span style="font-size:12px;color:#6d28d9;font-weight:700;">${(result.final_3d_score * 100).toFixed(1)}%</span>
        </div>
        <div style="display:flex;gap:6px;font-size:11px;color:#94a3b8;flex-wrap:wrap;">
          <span>药效团: ${(result.pharm_similarity * 100).toFixed(0)}%</span>
          <span>|</span>
          <span>3D叠合: ${((result.alignment_score ?? result.spatial_score) * 100).toFixed(0)}%</span>
          ${
            result.alignment_rmsd != null
              ? `<span>|</span><span>RMSD: ${Number(result.alignment_rmsd).toFixed(2)} Å</span>`
              : ""
          }
          ${
            result.alignment_coverage != null
              ? `<span>|</span><span>覆盖: ${(Number(result.alignment_coverage) * 100).toFixed(0)}%</span>`
              : ""
          }
          <span>|</span>
          <span>2D: ${(result.final_similarity * 100).toFixed(0)}%</span>
        </div>
      </div>`;
    }
    return `<div class="similarity-wrapper">
        <div class="similarity-bar" style="width:${result.final_similarity * 100}%"></div>
      </div>
      <div style="display:flex;justify-content:space-between;font-size:12px;color:#64748b;margin-top:4px;">
        <span>综合: ${(result.final_similarity * 100).toFixed(1)}%</span>
        <span>(Morgan: ${(result.morgan_similarity * 100).toFixed(0)}%)</span>
      </div>`;
  }

  function _buildCompactScorePanel(result, is3DMode) {
    const finalScore = is3DMode && result.final_3d_score != null
      ? Number(result.final_3d_score)
      : Number(result.final_similarity || 0);
    const percent = Math.max(0, Math.min(100, finalScore * 100));
    const scoreLabel = is3DMode ? "3D 综合" : "综合";
    const details = [];
    if (is3DMode && result.pharm_similarity != null) {
      details.push(`药效团 ${Math.round(Number(result.pharm_similarity) * 100)}%`);
    }
    if (is3DMode && (result.alignment_score != null || result.spatial_score != null)) {
      details.push(`叠合 ${Math.round(Number(result.alignment_score ?? result.spatial_score) * 100)}%`);
    }
    if (is3DMode && result.alignment_rmsd != null) {
      details.push(`RMSD ${Number(result.alignment_rmsd).toFixed(2)} Å`);
    }
    if (result.final_similarity != null) {
      details.push(`2D ${Math.round(Number(result.final_similarity) * 100)}%`);
    }

    return `
      <div class="rt-score-panel">
        <div class="rt-score-head">
          <span>${scoreLabel}</span>
          <strong>${percent.toFixed(1)}%</strong>
        </div>
        <div class="rt-score-track">
          <div class="rt-score-fill" style="width:${percent}%;"></div>
        </div>
        <div class="rt-score-details">${details.map(escapeHtml).join(" · ")}</div>
      </div>`;
  }

  function _confidenceMeta(result, is3DMode) {
    const score = is3DMode && result.final_3d_score != null
      ? Number(result.final_3d_score)
      : Number(result.final_similarity || 0);
    if (score >= 0.85) return { label: "高可信", cls: "high" };
    if (score >= 0.65) return { label: "中可信", cls: "medium" };
    return { label: "参考", cls: "low" };
  }

  function _evidenceSummary(result, is3DMode) {
    const parts = [];
    parts.push(`相似分子 ${result.similar_count || 1} 个`);
    if (result.standard_type) parts.push(`${result.standard_type} ${Number(result.standard_value || 0).toFixed(1)} nM`);
    if (is3DMode && result.final_3d_score != null) {
      parts.push(`3D ${(Number(result.final_3d_score) * 100).toFixed(1)}%`);
      if (result.alignment_rmsd != null) parts.push(`RMSD ${Number(result.alignment_rmsd).toFixed(2)} Å`);
    }
    return escapeHtml(parts.join(" · "));
  }

  function _targetDbUrl(targetName) {
    return `/target-search?query=${encodeURIComponent(targetName || "")}`;
  }

  function _organismTag(result) {
    const organism = result.organism || "-";
    const isHuman = organism.toLowerCase() === "human" || organism.toLowerCase() === "homo sapiens";
    return `<span class="tag tag-organism ${isHuman ? "tag-human" : "tag-nonhuman"}">${escapeHtml(organism)}</span>`;
  }

  function _singleResultRow(result, absoluteIndex, is3DMode) {
    const confidence = _confidenceMeta(result, is3DMode);
    const targetName = String(result.target_name || "");
    const moleculeId = String(result.molecule_chembl_id || "");
    const standardType = String(result.standard_type || "-");
    const standardValue = Number(result.standard_value || 0).toFixed(2);
    return `
        <tr class="rt-result-row">
          <td class="rt-rank-cell">#${absoluteIndex + 1}</td>
          <td class="rt-target-cell">
            <div class="rt-target-title" title="${escapeHtml(targetName)}">${escapeHtml(targetName)}</div>
            <span class="confidence-badge confidence-${confidence.cls}">${confidence.label}</span>
          </td>
          <td>${_organismTag(result)}</td>
          <td class="rt-id-cell">
            <a href="https://www.ebi.ac.uk/chembl/compound_report_card/${encodeURIComponent(moleculeId)}/"
               target="_blank">
              ${escapeHtml(moleculeId)}
            </a>
          </td>
          <td class="rt-activity-cell">
            <span class="tag tag-activity">${escapeHtml(standardType)}</span>
            <span class="rt-activity-value">${standardValue} nM</span>
          </td>
          <td class="rt-evidence-cell">
            <div class="rt-evidence-summary">${_evidenceSummary(result, is3DMode)}</div>
            ${_buildCompactScorePanel(result, is3DMode)}
          </td>
          <td class="rt-action-cell">
            <div class="rt-row-actions">
            <button class="rt-action-btn rt-action-secondary" onclick="RtResults.onShowSimilar(${inlineJsArg(targetName)})">相似分子 ${result.similar_count || 1}</button>
            <a class="target-db-link" href="${_targetDbUrl(targetName)}" target="_blank" rel="noopener noreferrer">靶点库</a>
            </div>
          </td>
        </tr>`;
  }

  function _renderSinglePage(page) {
    const resultsBody = $("resultsBody");
    const resultCount = $("resultCount");
    if (!resultsBody || !resultCount) return;

    const pageData = paginateResults(
      singlePageState.results,
      page,
      singlePageState.pageSize,
    );
    singlePageState.page = pageData.page;

    const humanCount = singlePageState.results.filter((item) => (item.organism || "").toLowerCase() === "human").length;
    const summary = window.lastReverseTargetSummary || {};
    const diagnostics = Number.isFinite(Number(summary.unique_target_count_before_top_k))
      ? ` · 聚合前 ${summary.unique_target_count_before_top_k} 个靶点 · 候选 ${summary.raw_candidate_count || 0}/${summary.candidate_pool_size || 0}`
      : "";
    resultCount.textContent = `预测到 ${singlePageState.results.length} 个潜在靶点 · Human ${humanCount} 个${diagnostics} · 第 ${pageData.page}/${pageData.totalPages} 页`;

    resultsBody.innerHTML = pageData.items
      .map((result, index) =>
        _singleResultRow(result, pageData.startIndex + index, singlePageState.is3DMode),
      )
      .join("");
    _renderPagination(pageData);
  }

  function _renderPagination(pageData) {
    const el = _ensurePagination();
    if (!el) return;
    if (!pageData || pageData.totalItems <= pageData.pageSize) {
      el.innerHTML = "";
      return;
    }

    const pages = [];
    const first = 1;
    const last = pageData.totalPages;
    const start = Math.max(first, pageData.page - 2);
    const end = Math.min(last, pageData.page + 2);
    if (start > first) pages.push(first);
    if (start > first + 1) pages.push("...");
    for (let p = start; p <= end; p += 1) pages.push(p);
    if (end < last - 1) pages.push("...");
    if (end < last) pages.push(last);

    el.innerHTML = `
      <div class="rt-pagination-summary">
        显示 ${pageData.startIndex + 1}-${pageData.endIndex} / ${pageData.totalItems}，每页 ${pageData.pageSize} 条
      </div>
      <div class="rt-pagination-actions">
        <button type="button" class="rt-page-btn" data-page="${pageData.page - 1}" ${pageData.page <= 1 ? "disabled" : ""}>上一页</button>
        ${pages.map((p) => p === "..."
          ? '<span class="rt-page-ellipsis">...</span>'
          : `<button type="button" class="rt-page-btn ${p === pageData.page ? "active" : ""}" data-page="${p}">${p}</button>`
        ).join("")}
        <button type="button" class="rt-page-btn" data-page="${pageData.page + 1}" ${pageData.page >= pageData.totalPages ? "disabled" : ""}>下一页</button>
      </div>`;

    el.querySelectorAll("button[data-page]").forEach((button) => {
      button.addEventListener("click", () => {
        _renderSinglePage(Number(button.dataset.page));
      });
    });
  }

  // ──────────────────────────────────────
  //  显示单分子预测结果
  // ──────────────────────────────────────
  function displayResults(resultsData, is3DMode) {
    const results = $("results");
    const resultsBody = $("resultsBody");
    const resultCount = $("resultCount");
    const exportBtn = $("exportBtn");
    window.currentSingleResults = resultsData || [];
    window.currentBatchResults = null;
    if (exportBtn) exportBtn.style.display = resultsData && resultsData.length ? "flex" : "none";

    // 还原表头为单分子模式
    const tableHead = document.querySelector(".results-table thead tr");
    tableHead.innerHTML = `
      <th width="60">排名</th>
      <th>靶点信息</th>
      <th width="105">生物体</th>
      <th width="140">关联ID</th>
      <th width="130">活性</th>
      <th width="360">证据与评分</th>
      <th width="128">操作</th>`;

    if (!resultsData || resultsData.length === 0) {
      resultCount.textContent = "未找到匹配的靶点";
      resultsBody.innerHTML =
        '<tr><td colspan="7" style="text-align:center;padding:40px;color:#64748b;">未找到符合条件的靶点，建议尝试降低相似度阈值，或取消“仅 Human”过滤。</td></tr>';
      _clearPagination();
      results.classList.add("show");
      return;
    }

    singlePageState.results = resultsData || [];
    singlePageState.is3DMode = Boolean(is3DMode);
    singlePageState.page = 1;
    singlePageState.pageSize = SINGLE_PAGE_SIZE;

    // Attach query pharmacophore legend once, then render the first 10 results.
    _attachQueryLegend(is3DMode);
    _renderSinglePage(1);
    results.classList.add("show");
    return;

    const humanCount = resultsData.filter((item) => (item.organism || "").toLowerCase() === "human").length;
    resultCount.textContent = `🎉 成功预测到 ${resultsData.length} 个潜在靶点 · Human ${humanCount} 个`;

    resultsBody.innerHTML = resultsData
      .map(
        (result, index) => {
          const confidence = _confidenceMeta(result, is3DMode);
          return `
        <tr>
          <td style="font-weight:bold;color:#4f46e5;">#${index + 1}</td>
          <td>
            <div style="font-weight:700;color:#1e293b;line-height:1.35;">${result.target_name}</div>
            <span class="confidence-badge confidence-${confidence.cls}">${confidence.label}</span>
          </td>
          <td>${_organismTag(result)}</td>
          <td>
            <a href="https://www.ebi.ac.uk/chembl/compound_report_card/${result.molecule_chembl_id}/"
               target="_blank" style="color:#4f46e5;text-decoration:none;font-weight:500;font-size:14px;">
              ${result.molecule_chembl_id}
            </a>
          </td>
          <td><span class="tag tag-activity">${result.standard_type}</span></td>
          <td style="font-family:monospace;">${result.standard_value.toFixed(2)}</td>
          <td>
            <div class="evidence-text">${_evidenceSummary(result, is3DMode)}</div>
            <button onclick="RtResults.onShowSimilar('${result.target_name.replace(/'/g, "\\'")}')"
                    style="background:#eff6ff;color:#2563eb;border:1px solid #bfdbfe;padding:4px 12px;
                           border-radius:6px;cursor:pointer;font-size:12px;font-weight:600;transition:all 0.2s;">
              查看 ${result.similar_count || 1} 个分子
            </button>
          </td>
          <td>${_buildScoreCell(result, is3DMode)}</td>
          <td>
            <a class="target-db-link" href="${_targetDbUrl(result.target_name)}" target="_blank" rel="noopener noreferrer">靶点库</a>
          </td>
        </tr>`;
        },
      )
      .join("");

    // 3D 模式下附加查询分子药效团图例
    _attachQueryLegend(is3DMode);

    results.classList.add("show");
  }

  // ── 附加查询分子的药效团图例到表格上方 ──
  function _attachQueryLegend(is3DMode) {
    const oldLegend = $("queryLegendBox");
    if (oldLegend) oldLegend.remove();

    if (!is3DMode || !window.lastQuery3DPharmacophore) return;

    const qp = window.lastQuery3DPharmacophore;
    const legendHtml = `
      <div id="queryLegendBox" style="background:linear-gradient(135deg,#f5f3ff,#ede9fe);border:1px solid #c4b5fd;
        border-radius:12px;padding:16px 20px;margin-bottom:16px;width:100%;box-sizing:border-box;">
        <div style="font-size:13px;font-weight:700;color:#5b21b6;margin-bottom:10px;">🔬 查询分子药效团特征</div>
        <div id="queryPharmLegend" style="display:flex;flex-wrap:wrap;gap:6px;"></div>
        <div style="margin-top:10px;font-size:12px;color:#7c3aed;display:flex;gap:16px;flex-wrap:wrap;">
          <span>MW: <b>${qp.properties?.MW || "-"}</b></span>
          <span>LogP: <b>${qp.properties?.LogP || "-"}</b></span>
          <span>TPSA: <b>${qp.properties?.TPSA || "-"}</b></span>
          <span>HBD: <b>${qp.properties?.HBD || "-"}</b></span>
          <span>HBA: <b>${qp.properties?.HBA || "-"}</b></span>
          <span>RotBonds: <b>${qp.properties?.RotBonds || "-"}</b></span>
        </div>
      </div>`;
    const table = document.querySelector(".results-table");
    table.insertAdjacentHTML("beforebegin", legendHtml);
    renderPharmLegend(qp.feature_counts, "queryPharmLegend");
  }

  // ──────────────────────────────────────
  //  显示批量预测结果
  // ──────────────────────────────────────
  function displayBatchResults(resultsData) {
    const results = $("results");
    const resultsBody = $("resultsBody");
    const resultCount = $("resultCount");
    const exportBtn = $("exportBtn");
    window.currentSingleResults = null;
    _clearPagination();
    if (exportBtn) exportBtn.style.display = resultsData && resultsData.length ? "flex" : "none";

    if (!resultsData || resultsData.length === 0) {
      resultCount.textContent = "未找到有效结果";
      resultsBody.innerHTML =
        '<tr><td colspan="8" style="text-align:center;padding:40px;">无数据</td></tr>';
      results.classList.add("show");
      return;
    }

    resultCount.textContent = `🎉 批量预测完成，共 ${resultsData.length} 个分子`;
    window.currentBatchResults = resultsData;

    // 动态切换表头
    const tableHead = document.querySelector(".results-table thead tr");
    tableHead.innerHTML = `
      <th>分子序号</th>
      <th>查询SMILES</th>
      <th>预测靶点</th>
      <th>生物体</th>
      <th>活性类型</th>
      <th>相似度</th>
      <th>操作</th>`;

    let rows = "";
    let idx = 1;

    resultsData.forEach((item) => {
      if (item.success && item.targets && item.targets.length > 0) {
        item.targets.forEach((target) => {
          const querySmiles = String(item.query_smiles || "");
          const targetName = String(target.target_name || "");
          const standardType = String(target.standard_type || "-");
          rows += `
            <tr>
              <td>#${idx++}</td>
              <td style="font-family:monospace;font-size:12px;max-width:150px;overflow:hidden;
                         text-overflow:ellipsis;white-space:nowrap;" title="${escapeHtml(querySmiles)}">
                ${escapeHtml(querySmiles)}
              </td>
              <td style="font-weight:600;color:#1e293b;">${escapeHtml(targetName)}</td>
              <td>${_organismTag(target)}</td>
              <td><span class="tag tag-activity">${escapeHtml(standardType)}</span></td>
              <td>
                <div class="similarity-wrapper">
                  <div class="similarity-bar" style="width:${target.final_similarity * 100}%"></div>
                </div>
                <div style="font-size:12px;color:#64748b;">${(target.final_similarity * 100).toFixed(1)}%</div>
              </td>
              <td>
                <button onclick="RtResults.onShowSimilar(${inlineJsArg(targetName)})"
                        style="font-size:12px;padding:4px 8px;">详情</button>
                <a class="target-db-link compact" href="${_targetDbUrl(targetName)}" target="_blank" rel="noopener noreferrer">靶点库</a>
              </td>
            </tr>`;
        });
      } else {
        const querySmiles = String(item.query_smiles || "");
        const errorText = String(item.error || "无匹配靶点");
        rows += `
          <tr>
            <td>#${idx++}</td>
            <td style="font-family:monospace;font-size:12px;">${escapeHtml(querySmiles)}</td>
            <td colspan="5" style="color:#ef4444;">${escapeHtml(errorText)}</td>
          </tr>`;
      }
    });

    resultsBody.innerHTML = rows;
    results.classList.add("show");
  }

  // ──────────────────────────────────────
  //  显示相似分子模态框
  // ──────────────────────────────────────
  async function showSimilarMolecules(targetName) {
    const modal = $("similarModal");
    const modalTitle = $("modalTitle");
    const modalBody = $("modalBody");

    modal.classList.add("show");
    modalTitle.textContent = `与 ${targetName} 相关的相似分子`;
    modalBody.innerHTML =
      '<div class="loading show"><div class="spinner"></div><p>正在加载分子详情...</p></div>';

    try {
      const data = await RtApi.fetchSimilarMolecules(
        window.currentQuerySmiles,
        targetName,
        window.lastThreshold || RT_CONFIG.DEFAULTS.THRESHOLD,
      );

      if (!data.results || data.results.length === 0) {
        modalBody.innerHTML =
          '<div style="text-align:center;padding:40px;color:#64748b;">暂无更多详细数据</div>';
        return;
      }

      modalBody.innerHTML = _buildSimilarModalContent(data.results);

      // 异步加载 MCS
      _loadMCSSection(data.results[0]?.canonical_smiles);
    } catch (error) {
      modalBody.innerHTML = `<div style="text-align:center;padding:40px;color:#ef4444;">加载失败: ${escapeHtml(error.message)}</div>`;
    }
  }

  function closeSimilarModal() {
    $("similarModal").classList.remove("show");
  }

  // ── 构建相似分子弹窗内部 HTML ──
  function _buildSimilarModalContent(molecules) {
    const querySmiles = window.currentQuerySmiles;
    const queryImgUrl = RtApi.smilesImageUrl(querySmiles);

    const molCards = molecules
      .map(
        (mol, idx) => {
          const molSmiles = String(mol.canonical_smiles || "");
          const molId = String(mol.molecule_chembl_id || "");
          const standardType = String(mol.standard_type || "-");
          return `
      <div style="background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:0;overflow:hidden;
                  transition:all 0.2s;display:flex;flex-direction:column;box-shadow:0 1px 2px rgba(0,0,0,0.05);">
        <div style="padding:12px 16px;border-bottom:1px solid #f1f5f9;display:flex;justify-content:space-between;
                    align-items:center;background:#f8fafc;">
          <div style="font-weight:700;color:#4f46e5;font-size:16px;">#${idx + 1}</div>
          <div style="background:#dcfce7;color:#166534;font-size:12px;font-weight:600;padding:4px 10px;border-radius:20px;">
            ${(mol.final_similarity * 100).toFixed(1)}% 相似
          </div>
        </div>
        <div style="height:180px;display:flex;justify-content:center;align-items:center;background:#fff;
                    padding:10px;border-bottom:1px solid #f1f5f9;">
          <img src="${RtApi.smilesImageUrl(molSmiles, 360, 300)}"
               alt="Structure" style="max-width:100%;max-height:100%;object-fit:contain;" loading="lazy" />
        </div>
        <div style="padding:16px;flex:1;display:flex;flex-direction:column;gap:10px;">
          <div style="display:flex;justify-content:space-between;align-items:center;border-bottom:1px dashed #e2e8f0;padding-bottom:8px;">
            <span style="color:#64748b;font-size:13px;">ChEMBL ID</span>
            <a href="https://www.ebi.ac.uk/chembl/compound_report_card/${encodeURIComponent(molId)}/"
               target="_blank" style="color:#2563eb;text-decoration:none;font-weight:500;font-size:13px;">
              ${escapeHtml(molId)} ↗
            </a>
          </div>
          <div style="display:flex;justify-content:space-between;border-bottom:1px dashed #e2e8f0;padding-bottom:8px;">
            <span style="color:#64748b;font-size:13px;">活性类型</span>
            <span style="color:#334155;font-weight:600;font-size:13px;">${escapeHtml(standardType)}</span>
          </div>
          <div style="display:flex;justify-content:space-between;border-bottom:1px dashed #e2e8f0;padding-bottom:8px;">
            <span style="color:#64748b;font-size:13px;">活性值</span>
            <span style="color:#334155;font-family:monospace;font-weight:600;font-size:13px;">${mol.standard_value} nM</span>
          </div>
          <div style="margin-top:auto;padding-top:8px;">
            <div style="font-size:12px;color:#94a3b8;margin-bottom:4px;">Fingerprint Similarity</div>
            <div style="display:flex;gap:8px;font-size:11px;color:#64748b;">
              <span style="background:#f1f5f9;padding:2px 6px;border-radius:4px;">Morgan: ${(mol.morgan_similarity * 100).toFixed(0)}%</span>
              <span style="background:#f1f5f9;padding:2px 6px;border-radius:4px;">MACCS: ${(mol.maccs_similarity * 100).toFixed(0)}%</span>
            </div>
          </div>
          <details style="margin-top:4px;margin-bottom:8px;">
            <summary style="cursor:pointer;color:#64748b;font-size:12px;user-select:none;">显示 SMILES</summary>
            <div style="margin-top:6px;padding:8px;background:#f8fafc;border-radius:4px;font-family:monospace;
                        font-size:11px;color:#475569;word-break:break-all;border:1px solid #e2e8f0;">
              ${escapeHtml(molSmiles)}
            </div>
          </details>
          <button onclick="RtViewer.openPharmacophore3D(${inlineJsArg(molSmiles)}, ${inlineJsArg(molId)})"
                  class="view-3d-btn" style="width:100%;justify-content:center;">
            🔬 查看 3D 药效团
          </button>
        </div>
      </div>`;
        },
      )
      .join("");

    return `
      <!-- 查询分子 -->
      <div style="background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:20px;margin-bottom:24px;box-shadow:0 2px 4px rgba(0,0,0,0.05);">
        <div style="display:flex;align-items:center;margin-bottom:16px;">
          <div style="width:4px;height:16px;background:#4f46e5;border-radius:2px;margin-right:8px;"></div>
          <h4 style="margin:0;color:#1e293b;font-size:16px;font-weight:600;">查询分子</h4>
        </div>
        <div style="display:flex;gap:24px;align-items:flex-start;flex-wrap:wrap;">
          <div style="width:200px;height:150px;flex-shrink:0;border:1px solid #e2e8f0;border-radius:8px;
                      display:flex;justify-content:center;align-items:center;background:#fff;overflow:hidden;">
            <img src="${queryImgUrl}" alt="Query" style="max-width:100%;max-height:100%;object-fit:contain;" />
          </div>
          <div style="flex:1;display:flex;flex-direction:column;justify-content:center;min-width:200px;">
            <div style="font-size:12px;color:#64748b;margin-bottom:6px;">Canonical SMILES</div>
            <div style="font-family:monospace;background:#f8fafc;padding:12px;border-radius:8px;color:#334155;
                        font-size:13px;line-height:1.5;word-break:break-all;border:1px solid #e2e8f0;">
              ${escapeHtml(querySmiles)}
            </div>
          </div>
        </div>
      </div>

      <!-- MCS 结构解释区 -->
      <div id="mcsSection" style="background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:20px;margin-bottom:24px;box-shadow:0 2px 4px rgba(0,0,0,0.05);">
        <div style="display:flex;align-items:center;margin-bottom:16px;">
          <div style="width:4px;height:16px;background:#0ea5e9;border-radius:2px;margin-right:8px;"></div>
          <h4 style="margin:0;color:#1e293b;font-size:16px;font-weight:600;">结构解释</h4>
          <span style="margin-left:10px;font-size:12px;color:#64748b;">查询分子 vs Top hit 的最大公共子结构 (MCS)</span>
        </div>
        <div id="mcsContent" style="color:#64748b;font-size:13px;">正在计算 MCS...</div>
      </div>

      <!-- 相似分子网格 -->
      <div style="margin-bottom:16px;display:flex;align-items:center;">
        <div style="width:4px;height:16px;background:#10b981;border-radius:2px;margin-right:8px;"></div>
        <h4 style="margin:0;color:#1e293b;font-size:16px;font-weight:600;">相似分子列表 (${molecules.length})</h4>
      </div>
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:20px;">
        ${molCards}
      </div>`;
  }

  // ── 异步加载 MCS ──
  async function _loadMCSSection(topHitSmiles) {
    const mcsEl = $("mcsContent");
    if (!mcsEl) return;
    if (!topHitSmiles) {
      mcsEl.textContent = "无 Top hit 分子，无法生成结构解释";
      return;
    }
    try {
      const { ok, data: mcsData } = await RtApi.fetchMCS(
        window.currentQuerySmiles,
        topHitSmiles,
      );
      if (ok && mcsData && mcsData.success) {
        mcsEl.innerHTML = `
          <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px;align-items:start;">
            <div style="border:1px solid #e2e8f0;border-radius:10px;overflow:hidden;background:#fff;">
              <div style="padding:10px 12px;background:#f8fafc;border-bottom:1px solid #f1f5f9;font-weight:600;color:#334155;font-size:13px;">查询分子（高亮 MCS）</div>
              <div style="padding:10px;display:flex;justify-content:center;">${mcsData.query_svg || ""}</div>
            </div>
            <div style="border:1px solid #e2e8f0;border-radius:10px;overflow:hidden;background:#fff;">
              <div style="padding:10px 12px;background:#f8fafc;border-bottom:1px solid #f1f5f9;font-weight:600;color:#334155;font-size:13px;">Top hit（高亮 MCS）</div>
              <div style="padding:10px;display:flex;justify-content:center;">${mcsData.hit_svg || ""}</div>
            </div>
          </div>
          <div style="margin-top:12px;">
            <div style="font-size:12px;color:#94a3b8;margin-bottom:6px;">MCS SMARTS</div>
            <div style="font-family:monospace;background:#f8fafc;padding:10px 12px;border-radius:8px;color:#334155;
                        font-size:12px;line-height:1.5;word-break:break-all;border:1px solid #e2e8f0;">
              ${escapeHtml(mcsData.mcs_smarts || "")}
            </div>
          </div>`;
      } else {
        mcsEl.textContent = `结构解释生成失败：${(mcsData && (mcsData.error || mcsData.detail)) || "MCS 不可用"}`;
      }
    } catch {
      mcsEl.textContent = "结构解释生成失败";
    }
  }

  // 供外部 onclick 调用的钩子
  function onShowSimilar(targetName) {
    showSimilarMolecules(targetName);
  }

  return {
    renderPharmLegend,
    displayResults,
    displayBatchResults,
    showSimilarMolecules,
    closeSimilarModal,
    onShowSimilar,
    _test: {
      paginateResults,
      escapeHtml,
      escapeJsString,
      singleResultRow: _singleResultRow,
    },
  };
})();
