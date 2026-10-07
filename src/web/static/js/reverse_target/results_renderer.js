// ═══════════════════════════════════════════════════════════
//  反向寻靶模块 – 结果渲染器
//  负责将后端返回的 JSON 转换为 DOM 表格/卡片
// ═══════════════════════════════════════════════════════════

const RtResults = (() => {
  const Safe = window.MedChatSafeRender || {};
  const $ = (id) => document.getElementById(id);
  const SINGLE_PAGE_SIZE = 10;
  const SINGLE_RESULT_COLUMN_COUNT = 7;
  const BATCH_RESULT_COLUMN_COUNT = 7;
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
          <span>${escapeHtml(m.icon)}</span>${escapeHtml(m.label)} ×${escapeHtml(cnt)}</span>`;
      })
      .join("");
  }

  // ──────────────────────────────────────
  //  构建单行得分列 HTML
  // ──────────────────────────────────────
  function _buildScoreCell(result, is3DMode) {
    const isRefined3D = is3DMode && result.pharm_refinement_status === "refined"
      && result.pharm_combined_3d != null && result.final_3d_score != null;
    if (isRefined3D) {
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
    const fallbackLabel = is3DMode && result.pharm_refinement_status !== "refined"
      ? "2D fallback"
      : "综合";
    return `<div class="similarity-wrapper">
        <div class="similarity-bar" style="width:${result.final_similarity * 100}%"></div>
      </div>
      <div style="display:flex;justify-content:space-between;font-size:12px;color:#64748b;margin-top:4px;">
        <span>${fallbackLabel}: ${(result.final_similarity * 100).toFixed(1)}%</span>
        <span>(Morgan: ${(result.morgan_similarity * 100).toFixed(0)}%)</span>
      </div>`;
  }

  function _buildCompactScorePanel(result, is3DMode) {
    const isRefined3D = is3DMode && result.pharm_refinement_status === "refined"
      && result.final_3d_score != null;
    const finalScore = isRefined3D
      ? Number(result.final_3d_score)
      : Number(result.final_similarity || 0);
    const percent = Math.max(0, Math.min(100, finalScore * 100));
    const scoreLabel = isRefined3D
      ? "3D 综合"
      : (is3DMode && result.pharm_refinement_status !== "refined" ? "2D fallback" : "综合");
    const details = [];
    if (isRefined3D && result.pharm_similarity != null) {
      details.push(`药效团 ${Math.round(Number(result.pharm_similarity) * 100)}%`);
    }
    if (isRefined3D && (result.alignment_score != null || result.spatial_score != null)) {
      details.push(`叠合 ${Math.round(Number(result.alignment_score ?? result.spatial_score) * 100)}%`);
    }
    if (isRefined3D && result.alignment_rmsd != null) {
      details.push(`RMSD ${Number(result.alignment_rmsd).toFixed(2)} Å`);
    }
    if (result.final_similarity != null) {
      details.push(`2D ${Math.round(Number(result.final_similarity) * 100)}%`);
    }
    if (is3DMode && result.pharm_refinement_status && result.pharm_refinement_status !== "refined") {
      details.push(result.pharm_error ? `未精修：${result.pharm_error}` : "未完成 3D 精修");
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
    const isRefined3D = is3DMode && result.pharm_refinement_status === "refined"
      && result.final_3d_score != null;
    if (is3DMode && !isRefined3D) return { label: "2D参考", cls: "low" };
    const score = isRefined3D
      ? Number(result.final_3d_score)
      : Number(result.final_similarity || 0);
    if (score >= 0.85) return { label: "高可信", cls: "high" };
    if (score >= 0.65) return { label: "中可信", cls: "medium" };
    return { label: "参考", cls: "low" };
  }

  function _similarCount(result) {
    return Number.isSafeInteger(result.similar_count) && result.similar_count >= 0
      ? String(result.similar_count) : "未提供";
  }

  function _evidenceSummary(result, is3DMode) {
    const parts = [];
    parts.push(`相似分子 ${_similarCount(result)} 个`);
    if (result.standard_type) parts.push(`${result.standard_type} ${Number(result.standard_value || 0).toFixed(1)} nM`);
    if (is3DMode && result.pharm_refinement_status === "refined" && result.final_3d_score != null) {
      parts.push(`3D ${(Number(result.final_3d_score) * 100).toFixed(1)}%`);
      if (result.alignment_rmsd != null) parts.push(`RMSD ${Number(result.alignment_rmsd).toFixed(2)} Å`);
    } else if (is3DMode && result.pharm_refinement_status && result.pharm_refinement_status !== "refined") {
      parts.push("2D fallback");
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
            <button class="rt-action-btn rt-action-secondary" onclick="RtResults.onShowSimilar(${inlineJsArg(targetName)}, ${inlineJsArg(window.currentQuerySmiles || '')})">相似分子 ${_similarCount(result)}</button>
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
        `<tr><td colspan="${SINGLE_RESULT_COLUMN_COUNT}" style="text-align:center;padding:40px;color:#64748b;">未找到符合条件的靶点，建议尝试降低相似度阈值，或取消“仅 Human”过滤。</td></tr>`;
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
          <span>MW: <b data-field="query-mw"></b></span>
          <span>LogP: <b data-field="query-logp"></b></span>
          <span>TPSA: <b data-field="query-tpsa"></b></span>
          <span>HBD: <b data-field="query-hbd"></b></span>
          <span>HBA: <b data-field="query-hba"></b></span>
          <span>RotBonds: <b data-field="query-rotbonds"></b></span>
        </div>
      </div>`;
    const table = document.querySelector(".results-table");
    if (!table) return;
    table.insertAdjacentHTML("beforebegin", legendHtml);
    const queryLegend = $("queryLegendBox");
    const properties = qp.properties && typeof qp.properties === "object" ? qp.properties : {};
    queryLegend.querySelector('[data-field="query-mw"]').textContent = String(properties.MW ?? "-");
    queryLegend.querySelector('[data-field="query-logp"]').textContent = String(properties.LogP ?? "-");
    queryLegend.querySelector('[data-field="query-tpsa"]').textContent = String(properties.TPSA ?? "-");
    queryLegend.querySelector('[data-field="query-hbd"]').textContent = String(properties.HBD ?? "-");
    queryLegend.querySelector('[data-field="query-hba"]').textContent = String(properties.HBA ?? "-");
    queryLegend.querySelector('[data-field="query-rotbonds"]').textContent = String(properties.RotBonds ?? "-");
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
        `<tr><td colspan="${BATCH_RESULT_COLUMN_COUNT}" style="text-align:center;padding:40px;">无数据</td></tr>`;
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
    const rowBindings = [];

    resultsData.forEach((item) => {
      if (item.success && item.targets && item.targets.length > 0) {
        item.targets.forEach((target) => {
          const querySmiles = String(item.query_smiles || "");
          const targetName = String(target.target_name || "");
          const standardType = String(target.standard_type || "-");
          const rowKey = rowBindings.length;
          rowBindings.push({ querySmiles, targetName });
          rows += `
            <tr data-batch-row="${rowKey}">
              <td>#${idx++}</td>
              <td data-batch-smiles style="font-family:monospace;font-size:12px;max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"></td>
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
                <button data-batch-similar
                        style="font-size:12px;padding:4px 8px;">详情</button>
                <a class="target-db-link compact" href="${_targetDbUrl(targetName)}" target="_blank" rel="noopener noreferrer">靶点库</a>
              </td>
            </tr>`;
        });
      } else {
        const querySmiles = String(item.query_smiles || "");
        const errorText = String(item.error || "无匹配靶点");
        const rowKey = rowBindings.length;
        rowBindings.push({ querySmiles, targetName: "", errorText });
        rows += `
          <tr data-batch-row="${rowKey}">
            <td>#${idx++}</td>
            <td data-batch-smiles style="font-family:monospace;font-size:12px;"></td>
            <td data-batch-error colspan="${BATCH_RESULT_COLUMN_COUNT - 2}" style="color:#ef4444;"></td>
          </tr>`;
      }
    });

    resultsBody.innerHTML = rows;
    rowBindings.forEach((binding, rowKey) => {
      const row = resultsBody.querySelector(`[data-batch-row="${rowKey}"]`);
      if (!row) return;
      const smilesCell = row.querySelector("[data-batch-smiles]");
      if (smilesCell) {
        smilesCell.textContent = binding.querySmiles;
        smilesCell.title = binding.querySmiles;
      }
      const errorCell = row.querySelector("[data-batch-error]");
      if (errorCell) errorCell.textContent = binding.errorText || "无匹配靶点";
      const similarButton = row.querySelector("[data-batch-similar]");
      if (similarButton) {
        similarButton.addEventListener("click", () => onShowSimilar(binding.targetName, binding.querySmiles));
      }
    });
    results.classList.add("show");
  }

  // ──────────────────────────────────────
  //  显示相似分子模态框
  // ──────────────────────────────────────
  let similarRequestId = 0;
  let similarAbortController = null;

  async function showSimilarMolecules(targetName, querySmiles) {
    const requestId = ++similarRequestId;
    if (similarAbortController) similarAbortController.abort();
    similarAbortController = new AbortController();
    const resolvedQuerySmiles = String(querySmiles || window.currentQuerySmiles || "");
    const modal = $("similarModal");
    const modalTitle = $("modalTitle");
    const modalBody = $("modalBody");

    modal.classList.add("show");
    modalTitle.textContent = `与 ${targetName} 相关的相似分子`;
    modalBody.innerHTML =
      '<div class="loading show"><div class="spinner"></div><p>正在加载分子详情...</p></div>';

    try {
      const data = await RtApi.fetchSimilarMolecules(
        resolvedQuerySmiles,
        targetName,
        window.lastThreshold || RT_CONFIG.DEFAULTS.THRESHOLD,
        50,
        similarAbortController.signal,
      );

      if (requestId !== similarRequestId) return;

      if (!data.results || data.results.length === 0) {
        modalBody.innerHTML =
          '<div style="text-align:center;padding:40px;color:#64748b;">暂无更多详细数据</div>';
        return;
      }

      modalBody.replaceChildren(_buildSimilarModalContent(data.results));

      // 异步加载 MCS
      _loadMCSSection(resolvedQuerySmiles, data.results[0]?.canonical_smiles);
    } catch (error) {
      if (error && error.name === "AbortError") return;
      if (requestId !== similarRequestId) return;
      modalBody.textContent = `加载失败: ${String(error?.message || "未知错误")}`;
    }
  }

  function closeSimilarModal() {
    $("similarModal").classList.remove("show");
  }

  // ── 构建相似分子弹窗内部 HTML ──
  function _buildSimilarModalContent(molecules) {
    const querySmiles = String(window.currentQuerySmiles || "");
    const root = document.createElement("div");
    const cardSkeletons = molecules.map((_, idx) => `
      <div data-molecule-index="${idx}" style="background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:0;overflow:hidden;transition:all 0.2s;display:flex;flex-direction:column;box-shadow:0 1px 2px rgba(0,0,0,0.05);">
        <div style="padding:12px 16px;border-bottom:1px solid #f1f5f9;display:flex;justify-content:space-between;align-items:center;background:#f8fafc;">
          <div data-field="rank" style="font-weight:700;color:#4f46e5;font-size:16px;"></div>
          <div data-field="similarity" style="background:#dcfce7;color:#166534;font-size:12px;font-weight:600;padding:4px 10px;border-radius:20px;"></div>
        </div>
        <div style="height:180px;display:flex;justify-content:center;align-items:center;background:#fff;padding:10px;border-bottom:1px solid #f1f5f9;">
          <img data-field="image" alt="Structure" style="max-width:100%;max-height:100%;object-fit:contain;" loading="lazy" />
        </div>
        <div style="padding:16px;flex:1;display:flex;flex-direction:column;gap:10px;">
          <div style="display:flex;justify-content:space-between;align-items:center;border-bottom:1px dashed #e2e8f0;padding-bottom:8px;">
            <span style="color:#64748b;font-size:13px;">ChEMBL ID</span>
            <a data-field="mol-id" target="_blank" rel="noopener noreferrer" style="color:#2563eb;text-decoration:none;font-weight:500;font-size:13px;"></a>
          </div>
          <div style="display:flex;justify-content:space-between;border-bottom:1px dashed #e2e8f0;padding-bottom:8px;"><span style="color:#64748b;font-size:13px;">活性类型</span><span data-field="standard-type" style="color:#334155;font-weight:600;font-size:13px;"></span></div>
          <div style="display:flex;justify-content:space-between;border-bottom:1px dashed #e2e8f0;padding-bottom:8px;"><span style="color:#64748b;font-size:13px;">活性值</span><span data-field="standard-value" style="color:#334155;font-family:monospace;font-weight:600;font-size:13px;"></span></div>
          <div style="margin-top:auto;padding-top:8px;"><div style="font-size:12px;color:#94a3b8;margin-bottom:4px;">Fingerprint Similarity</div><div style="display:flex;gap:8px;font-size:11px;color:#64748b;"><span data-field="morgan" style="background:#f1f5f9;padding:2px 6px;border-radius:4px;"></span><span data-field="maccs" style="background:#f1f5f9;padding:2px 6px;border-radius:4px;"></span></div></div>
          <details style="margin-top:4px;margin-bottom:8px;"><summary style="cursor:pointer;color:#64748b;font-size:12px;user-select:none;">显示 SMILES</summary><div data-field="smiles" style="margin-top:6px;padding:8px;background:#f8fafc;border-radius:4px;font-family:monospace;font-size:11px;color:#475569;word-break:break-all;border:1px solid #e2e8f0;"></div></details>
          <button data-action="3d" class="view-3d-btn" type="button" style="width:100%;justify-content:center;">🔬 查看 3D 药效团</button>
        </div>
      </div>`).join("");

    root.innerHTML = `
      <div style="background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:20px;margin-bottom:24px;box-shadow:0 2px 4px rgba(0,0,0,0.05);">
        <div style="display:flex;align-items:center;margin-bottom:16px;"><div style="width:4px;height:16px;background:#4f46e5;border-radius:2px;margin-right:8px;"></div><h4 style="margin:0;color:#1e293b;font-size:16px;font-weight:600;">查询分子</h4></div>
        <div style="display:flex;gap:24px;align-items:flex-start;flex-wrap:wrap;"><div style="width:200px;height:150px;flex-shrink:0;border:1px solid #e2e8f0;border-radius:8px;display:flex;justify-content:center;align-items:center;background:#fff;overflow:hidden;"><img data-field="query-image" alt="Query" style="max-width:100%;max-height:100%;object-fit:contain;" /></div><div style="flex:1;display:flex;flex-direction:column;justify-content:center;min-width:200px;"><div style="font-size:12px;color:#64748b;margin-bottom:6px;">Canonical SMILES</div><div data-field="query-smiles" style="font-family:monospace;background:#f8fafc;padding:12px;border-radius:8px;color:#334155;font-size:13px;line-height:1.5;word-break:break-all;border:1px solid #e2e8f0;"></div></div></div>
      </div>
      <div id="mcsSection" style="background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:20px;margin-bottom:24px;box-shadow:0 2px 4px rgba(0,0,0,0.05);"><div style="display:flex;align-items:center;margin-bottom:16px;"><div style="width:4px;height:16px;background:#0ea5e9;border-radius:2px;margin-right:8px;"></div><h4 style="margin:0;color:#1e293b;font-size:16px;font-weight:600;">结构解释</h4><span style="margin-left:10px;font-size:12px;color:#64748b;">查询分子 vs Top hit 的最大公共子结构 (MCS)</span></div><div id="mcsContent" style="color:#64748b;font-size:13px;">正在计算 MCS...</div></div>
      <div style="margin-bottom:16px;display:flex;align-items:center;"><div style="width:4px;height:16px;background:#10b981;border-radius:2px;margin-right:8px;"></div><h4 style="margin:0;color:#1e293b;font-size:16px;font-weight:600;">相似分子列表 (${molecules.length})</h4></div>
      <div data-field="molecule-grid" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:20px;">${cardSkeletons}</div>`;

    const setSameOriginImage = (image, url) => {
      try {
        const parsed = new URL(String(url || ""), window.location.href);
        if (parsed.origin !== window.location.origin) throw new Error("external image blocked");
        image.src = parsed.href;
      } catch (_) {
        image.removeAttribute("src");
      }
    };
    setSameOriginImage(root.querySelector('[data-field="query-image"]'), RtApi.smilesImageUrl(querySmiles));
    root.querySelector('[data-field="query-smiles"]').textContent = querySmiles;

    molecules.forEach((mol, idx) => {
      const card = root.querySelector(`[data-molecule-index="${idx}"]`);
      const molSmiles = String(mol.canonical_smiles || "");
      const molId = String(mol.molecule_chembl_id || "");
      const similarity = Number(mol.final_similarity);
      const standardValue = Number.isFinite(Number(mol.standard_value)) ? Number(mol.standard_value).toFixed(2) : "未计算";
      card.querySelector('[data-field="rank"]').textContent = `#${idx + 1}`;
      card.querySelector('[data-field="similarity"]').textContent = `${Number.isFinite(similarity) ? (similarity * 100).toFixed(1) : "-"}% 相似`;
      setSameOriginImage(card.querySelector('[data-field="image"]'), RtApi.smilesImageUrl(molSmiles, 360, 300));
      const molIdNode = card.querySelector('[data-field="mol-id"]');
      molIdNode.textContent = `${molId} ↗`;
      if (/^[A-Za-z0-9._-]{1,128}$/.test(molId)) {
        molIdNode.href = `https://www.ebi.ac.uk/chembl/compound_report_card/${encodeURIComponent(molId)}/`;
      } else {
        molIdNode.removeAttribute("href");
      }
      card.querySelector('[data-field="standard-type"]').textContent = String(mol.standard_type || "-");
      card.querySelector('[data-field="standard-value"]').textContent = `${standardValue} nM`;
      card.querySelector('[data-field="morgan"]').textContent = `Morgan: ${(Number(mol.morgan_similarity || 0) * 100).toFixed(0)}%`;
      card.querySelector('[data-field="maccs"]').textContent = `MACCS: ${(Number(mol.maccs_similarity || 0) * 100).toFixed(0)}%`;
      const smilesNode = card.querySelector('[data-field="smiles"]');
      smilesNode.textContent = molSmiles;
      card.querySelector('[data-action="3d"]').addEventListener("click", () => RtViewer.openPharmacophore3D(molSmiles, molId));
    });
    return root;
  }

  // ── 异步加载 MCS ──
  async function _loadMCSSection(querySmiles, topHitSmiles) {
    const mcsEl = $("mcsContent");
    if (!mcsEl) return;
    if (!topHitSmiles) {
      mcsEl.textContent = "无 Top hit 分子，无法生成结构解释";
      return;
    }
    try {
      const { ok, data: mcsData } = await RtApi.fetchMCS(
        querySmiles,
        topHitSmiles,
      );
      if (ok && mcsData && mcsData.success) {
        mcsEl.innerHTML = `
          <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px;align-items:start;">
            <div style="border:1px solid #e2e8f0;border-radius:10px;overflow:hidden;background:#fff;">
              <div style="padding:10px 12px;background:#f8fafc;border-bottom:1px solid #f1f5f9;font-weight:600;color:#334155;font-size:13px;">查询分子（高亮 MCS）</div>
              <div id="mcsQuerySvg" style="padding:10px;display:flex;justify-content:center;"></div>
            </div>
            <div style="border:1px solid #e2e8f0;border-radius:10px;overflow:hidden;background:#fff;">
              <div style="padding:10px 12px;background:#f8fafc;border-bottom:1px solid #f1f5f9;font-weight:600;color:#334155;font-size:13px;">Top hit（高亮 MCS）</div>
              <div id="mcsHitSvg" style="padding:10px;display:flex;justify-content:center;"></div>
            </div>
          </div>
          <div style="margin-top:12px;">
            <div style="font-size:12px;color:#94a3b8;margin-bottom:6px;">MCS SMARTS</div>
            <div style="font-family:monospace;background:#f8fafc;padding:10px 12px;border-radius:8px;color:#334155;
                        font-size:12px;line-height:1.5;word-break:break-all;border:1px solid #e2e8f0;">
              <span id="mcsSmart"></span>
            </div>
          </div>`;
        const mcsSmart = $("mcsSmart");
        mcsSmart.textContent = String(mcsData.mcs_smarts || "");
        const querySvg = $("mcsQuerySvg");
        const hitSvg = $("mcsHitSvg");
        const querySafe = Safe.appendSafeSvg && Safe.appendSafeSvg(querySvg, mcsData.query_svg);
        const hitSafe = Safe.appendSafeSvg && Safe.appendSafeSvg(hitSvg, mcsData.hit_svg);
        if (!querySafe || !hitSafe) {
          mcsEl.textContent = "结构解释生成失败：返回的 SVG 不符合安全格式";
          return;
        }
      } else {
        mcsEl.textContent = `结构解释生成失败：${(mcsData && (mcsData.error || mcsData.detail)) || "MCS 不可用"}`;
      }
    } catch {
      mcsEl.textContent = "结构解释生成失败";
    }
  }

  // 供外部 onclick 调用的钩子
  function onShowSimilar(targetName, querySmiles) {
    showSimilarMolecules(targetName, querySmiles);
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
