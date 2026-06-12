const TargetSearchApp = (() => {
  let currentResults = [];
  let selectedTargetId = null;
  let currentDetail = null;

  function init() {
    const form = document.getElementById("targetSearchForm");
    const input = document.getElementById("targetQuery");

    renderInitialState();
    loadDatabaseStatus();
    loadPdeOverview();

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      runSearch(input.value);
    });

    document.getElementById("targetResults").addEventListener("click", (event) => {
      if (event.target.closest("button")) return;
      const card = event.target.closest(".target-result-item");
      if (card) loadDetail(card.dataset.targetId);
    });

    document.querySelectorAll(".quick-row button").forEach((button) => {
      button.addEventListener("click", () => {
        input.value = button.dataset.query;
        runSearch(button.dataset.query);
      });
    });

    document.querySelectorAll("#filterTargetType, #filterSource, #filterExperimental, #filterDocking, #filterLigand")
      .forEach((control) => {
        control.addEventListener("change", () => runSearch(input.value));
      });
    document.getElementById("filterDockingGrade").addEventListener("change", () => {
      if (currentDetail) {
        document.getElementById("targetDetail").innerHTML = renderDetail(currentDetail);
      }
    });

    const initialQuery = new URLSearchParams(window.location.search).get("query") || "WDR5";
    input.value = initialQuery;
    runSearch(initialQuery);
  }

  async function loadDatabaseStatus() {
    const panel = document.getElementById("databaseStatusPanel");
    if (!panel) return;

    panel.innerHTML = `<div class="db-status-loading">正在检查本地靶点数据库...</div>`;
    try {
      const [stats, health, validation] = await Promise.all([
        TargetSearchApi.stats(),
        TargetSearchApi.health(),
        TargetSearchApi.validation(),
      ]);
      panel.innerHTML = renderDatabaseStatus(stats, health, validation);
    } catch (error) {
      panel.innerHTML = `
        <div class="db-status-card db-status-error">
          <span class="db-status-label">DB CHECK</span>
          <strong>状态读取失败</strong>
          <p>${escapeHtml(error.message)}</p>
        </div>
      `;
    }
  }

  function renderDatabaseStatus(stats, health, validation = {}) {
    const sourceCounts = stats.source_counts || {};
    const pdeCoverage = validation.pde_coverage || {};
    const quality = validation.structure_quality || {};
    const gradeSummary = quality.docking_grade_summary || {};
    const statusClass = health.status === "ok" ? "ok" : health.status === "warning" ? "warning" : "error";
    const statusText = health.status === "ok" ? "SQLite Ready" : health.status === "warning" ? "Cache Pending" : "Need Init";
    const missingText = `${stats.missing_cache_count || 0} missing`;
    const pdeText = `${pdeCoverage.present_expected_count ?? stats.pde_target_count ?? 0}/${pdeCoverage.expected_count ?? 24}`;

    return `
      <div class="db-status-card primary">
        <span class="db-status-label">LOCAL DB</span>
        <strong>${escapeHtml(statusText)}</strong>
        <p>${escapeHtml(stats.database_file_path || "-")}</p>
      </div>
      <div class="db-status-card">
        <span class="db-status-label">TARGETS</span>
        <strong>${stats.target_count || 0}</strong>
        <p>PDE ${escapeHtml(pdeText)} · 本地靶点记录</p>
      </div>
      <div class="db-status-card">
        <span class="db-status-label">STRUCTURES</span>
        <strong>${stats.structure_count || 0}</strong>
        <p>PDE ${stats.pde_structure_count || 0} · RCSB ${sourceCounts.RCSB_PDB || 0} · AlphaFold ${sourceCounts.AlphaFold || 0}</p>
      </div>
      <div class="db-status-card ${statusClass}">
        <span class="db-status-label">CACHE</span>
        <strong>${stats.cached_structure_count || 0}</strong>
        <p>PDE 覆盖 ${Math.round((stats.pde_cache_coverage || 0) * 100)}% · ${escapeHtml(missingText)}</p>
      </div>
      <div class="db-status-card">
        <span class="db-status-label">QUALITY</span>
        <strong>A ${gradeSummary.A || 0}</strong>
        <p>B ${gradeSummary.B || 0} · C ${gradeSummary.C || 0} · 推荐 ${stats.docking_recommended_count || 0}</p>
      </div>
    `;
  }

  async function loadPdeOverview() {
    const panel = document.getElementById("pdeOverviewPanel");
    if (!panel) return;

    try {
      const data = await TargetSearchApi.pdeOverview(3);
      panel.innerHTML = renderPdeOverview(data);
    } catch (error) {
      panel.innerHTML = `
        <div class="pde-overview-error">
          <strong>PDE 家族概览暂不可用</strong>
          <span>${escapeHtml(error.message)}</span>
        </div>
      `;
    }
  }

  function renderPdeOverview(data) {
    const topTargets = (data.targets || [])
      .filter((item) => item.best_structure)
      .sort((a, b) => (b.best_structure.score || 0) - (a.best_structure.score || 0))
      .slice(0, 6);
    const familyCards = (data.families || []).map((item) => `
      <button type="button" class="pde-family-chip" onclick="TargetSearchApp.searchPreset('${escapeAttr(item.family)}')">
        <strong>${escapeHtml(item.family)}</strong>
        <span>${item.target_count} 靶点 · ${item.structure_count} 结构</span>
      </button>
    `).join("");

    return `
      <div class="pde-overview-head">
        <div>
          <span class="panel-kicker">PDE Family Workspace</span>
          <h2>PDE 专项库</h2>
          <p>按 subtype 汇总结构数量，并用 A/B/C 分级筛出优先 docking 结构。</p>
        </div>
        <div class="pde-grade-summary">
          <span>A 级 ${data.docking_grade_summary?.A || 0}</span>
          <span>B 级 ${data.docking_grade_summary?.B || 0}</span>
          <span>C 级 ${data.docking_grade_summary?.C || 0}</span>
        </div>
      </div>
      <div class="pde-family-grid">${familyCards}</div>
      <div class="pde-top-list">
        ${topTargets.map((item) => `
          <article class="pde-top-item" onclick="TargetSearchApp.searchPreset('${escapeAttr(item.gene_symbol)}')">
            <div>
              <strong>${escapeHtml(item.gene_symbol)}</strong>
              <span>${escapeHtml(item.pde_target_class)}</span>
            </div>
            <div>
              <b>${escapeHtml(item.best_structure.structure_id)}</b>
              <span>${escapeHtml(item.best_structure.docking_grade_label)} · Score ${item.best_structure.score || 0}</span>
            </div>
          </article>
        `).join("")}
      </div>
    `;
  }

  function renderInitialState() {
    document.getElementById("resultSummary").textContent = "输入关键词后开始检索";
    document.getElementById("targetResults").innerHTML = `
      <div class="empty-state">
        <strong>输入靶点、基因、蛋白名或疾病关键词</strong>
        <span>可以先试试 EGFR、WDR5、CYP3A4 或 lung cancer。</span>
      </div>
    `;
    document.getElementById("targetDetail").innerHTML = `
      <div class="detail-empty">
        <strong>尚未选择靶点</strong>
        <span>搜索后点击结果卡片查看结构列表和下载入口。</span>
      </div>
    `;
  }

  async function runSearch(query) {
    const trimmed = (query || "").trim();
    if (!trimmed) {
      showMessage("请输入搜索关键词。", "error");
      return;
    }

    selectedTargetId = null;
    currentDetail = null;
    setLoading(true);
    hideMessage();
    renderResultLoading(trimmed);

    try {
      const data = await TargetSearchApi.search(trimmed, collectFilters());
      currentResults = data.results || [];
      renderResults(data.query, currentResults);

      if (currentResults.length > 0) {
        await loadDetail(currentResults[0].target_id);
      } else {
        renderEmptyResults(trimmed);
      }
    } catch (error) {
      showMessage(error.message, "error");
      renderInitialState();
    } finally {
      setLoading(false);
    }
  }

  function renderResultLoading(query) {
    document.getElementById("resultSummary").textContent = `正在检索 “${query}”`;
    document.getElementById("targetResults").innerHTML = `
      <div class="skeleton-card"></div>
      <div class="skeleton-card short"></div>
    `;
    document.getElementById("targetDetail").innerHTML = `
      <div class="detail-empty">
        <strong>正在加载靶点详情...</strong>
        <span>本地数据库正在匹配相关靶点与结构索引。</span>
      </div>
    `;
  }

  function renderEmptyResults(query) {
    document.getElementById("targetDetail").innerHTML = `
      <div class="detail-empty">
        <strong>没有匹配的靶点</strong>
        <span>可以换用基因名、UniProt ID、蛋白名或疾病关键词继续搜索。</span>
      </div>
    `;
    document.getElementById("targetResults").innerHTML = `
      <div class="empty-state">
        <strong>未找到 “${escapeHtml(query)}”</strong>
        <span>建议尝试 EGFR、WDR5、CYP3A4、KRAS、BRAF 或 lung cancer。</span>
      </div>
    `;
  }

  function renderResults(query, results) {
    const list = document.getElementById("targetResults");
    const summary = document.getElementById("resultSummary");
    summary.textContent = `关键词 “${query}” 返回 ${results.length} 个靶点`;

    if (!results.length) {
      renderEmptyResults(query);
      return;
    }

    list.innerHTML = results.map(renderTargetCard).join("");
  }

  function renderTargetCard(item) {
    const isSelected = Number(item.target_id) === Number(selectedTargetId);
    const reasonText = matchReasonText(item.match_reason);
    return `
      <article class="target-result-item ${isSelected ? "selected" : ""}" data-target-id="${item.target_id}">
        <div class="target-result-main">
          <div class="gene">${escapeHtml(item.gene_symbol)}</div>
          <div class="protein-name">${escapeHtml(item.protein_name || "-")}</div>
          <div class="muted">${escapeHtml(item.organism || "-")}</div>
          <span class="match-reason">${escapeHtml(reasonText)}</span>
        </div>

        <div class="target-result-meta">
          <div><span>UniProt</span><strong>${escapeHtml(item.uniprot_id || "-")}</strong></div>
          <div><span>Type</span><strong>${escapeHtml(item.target_type || "-")}</strong></div>
          <div><span>Structures</span><strong>${item.structure_count || 0}</strong><em>已缓存 ${item.downloaded_structure_count || 0}</em></div>
        </div>

        <div class="target-result-flags">
          ${item.has_experimental_structure ? "<span class=\"badge ok\">实验结构</span>" : ""}
          ${item.has_alphafold_structure ? "<span class=\"badge warn\">AlphaFold</span>" : ""}
        </div>

        <div class="action-row target-actions">
          <button class="small-btn secondary" type="button" onclick="TargetSearchApp.loadDetail(${item.target_id})">详情</button>
          <button class="small-btn primary" type="button" onclick="TargetSearchApp.downloadPreferred(${item.target_id})">下载推荐</button>
        </div>
      </article>
    `;
  }

  async function loadDetail(targetId) {
    hideMessage();
    selectedTargetId = Number(targetId);
    refreshSelectedCards();

    const detail = document.getElementById("targetDetail");
    detail.innerHTML = `
      <div class="detail-empty">
        <strong>正在加载靶点详情...</strong>
        <span>结构推荐分数与下载状态正在同步。</span>
      </div>
    `;

    try {
      const data = await TargetSearchApi.detail(targetId);
      currentDetail = data;
      selectedTargetId = Number(data.target_id);
      detail.innerHTML = renderDetail(data);
      refreshSelectedCards();
    } catch (error) {
      detail.innerHTML = `<div class="detail-empty"><strong>详情加载失败</strong><span>${escapeHtml(error.message)}</span></div>`;
    }
  }

  function refreshSelectedCards() {
    document.querySelectorAll(".target-result-item").forEach((card) => {
      card.classList.toggle("selected", Number(card.dataset.targetId) === Number(selectedTargetId));
    });
  }

  function renderDetail(data) {
    const allStructures = data.structures || [];
    const structures = filterStructuresByGrade(allStructures);
    const visibleStructures = structures.slice(0, 8);
    const hiddenCount = Math.max(structures.length - visibleStructures.length, 0);
    const diseases = (data.disease_keywords || [])
      .map((name) => `<span class="badge warn">${escapeHtml(name)}</span>`)
      .join("");
    const recommended = allStructures[0];
    const grade = getSelectedDockingGrade();
    const filterNote = grade ? `仅显示 ${grade} 级结构` : "按推荐分数排序";

    return `
      <div class="target-detail-workbench">
        <section class="detail-overview" aria-label="靶点概览">
          <span class="panel-kicker">Target Profile</span>
          <div class="detail-title">
            <div>
              <h3>${escapeHtml(data.gene_symbol)}</h3>
              <p class="muted">${escapeHtml(data.protein_name || "-")}</p>
            </div>
            <span class="badge ok">${escapeHtml(data.target_type || "Target")}</span>
          </div>

          <div class="detail-meta">
            <div><span>UniProt</span><strong>${escapeHtml(data.uniprot_id || "-")}</strong></div>
            <div><span>Organism</span><strong>${escapeHtml(data.organism || "-")}</strong></div>
            <div><span>Structures</span><strong>${allStructures.length}</strong></div>
          </div>

          <p class="description">${escapeHtml(data.description || "暂无描述")}</p>
          <div class="disease-list">${diseases || "<span class=\"muted\">暂无疾病关键词</span>"}</div>

          <div class="detail-enrichment">
            <div>
              <span>Pathway</span>
              <strong>${escapeHtml(data.pathway || data.function_summary || "-")}</strong>
            </div>
            <div>
              <span>Known drugs</span>
              <strong>${escapeHtml((data.known_drugs || []).join(", ") || "-")}</strong>
            </div>
            <div>
              <span>Representative ligands</span>
              <strong>${escapeHtml((data.representative_ligands || []).join(", ") || "-")}</strong>
            </div>
          </div>

          <div class="external-links">
            ${(data.external_links || []).map((link) => `<a href="${escapeHtml(link.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(link.label)}</a>`).join("")}
          </div>

          ${recommended ? renderRecommendedStructure(recommended) : ""}
        </section>

        <section class="structure-panel" aria-label="结构列表">
          <div class="structure-section-title">
            <div>
              <span class="panel-kicker">Structure Index</span>
              <h3>结构列表</h3>
            </div>
            <span class="muted">${escapeHtml(filterNote)}</span>
          </div>
          ${renderGradeGuide()}
          <div class="structure-list">
            ${visibleStructures.map(renderStructureCard).join("") || renderNoStructureForCurrentFilter(grade)}
            ${hiddenCount ? `<div class="structure-truncated-note">已按推荐分数展示前 ${visibleStructures.length} 条，另有 ${hiddenCount} 条结构保留在当前筛选结果中。</div>` : ""}
          </div>
        </section>
      </div>
    `;
  }

  function filterStructuresByGrade(structures) {
    const grade = getSelectedDockingGrade();
    if (!grade) return structures;
    return structures.filter((item) => item.docking_grade === grade);
  }

  function getSelectedDockingGrade() {
    const select = document.getElementById("filterDockingGrade");
    return select ? select.value : "";
  }

  function renderNoStructureForCurrentFilter(grade) {
    if (grade) {
      return `<div class="detail-empty"><strong>当前靶点没有 ${escapeHtml(grade)} 级结构</strong><span>可以切换 Docking 等级，或取消等级筛选查看全部结构。</span></div>`;
    }
    return "<div class=\"detail-empty\"><strong>暂无结构</strong><span>该靶点还没有可展示的本地结构索引。</span></div>";
  }

  function renderGradeGuide() {
    return `
      <div class="grade-guide" aria-label="Docking 评级说明">
        <div class="grade-guide-item grade-guide-a">
          <strong>A</strong>
          <span>实验结构、人源、高分辨率、含配体，优先 docking</span>
        </div>
        <div class="grade-guide-item grade-guide-b">
          <strong>B</strong>
          <span>实验结构，但缺少部分 docking 关键条件</span>
        </div>
        <div class="grade-guide-item grade-guide-c">
          <strong>C</strong>
          <span>预测结构或参考结构，不建议直接作为首选 docking 输入</span>
        </div>
      </div>
    `;
  }

  function renderRecommendedStructure(item) {
    return `
      <section class="recommended-structure" aria-label="推荐结构">
        <div>
          <span class="recommend-label">推荐结构</span>
          <h4>${escapeHtml(item.structure_id)}</h4>
          <p>${escapeHtml(item.title || "优先用于 demo 展示的结构记录")}</p>
        </div>
        <div class="recommend-actions">
          <span class="score-pill">Score ${item.score || 0}</span>
          <button class="small-btn primary" type="button" onclick="TargetSearchApp.downloadStructure(${item.id}, '${escapeAttr(item.file_format || "cif")}')">下载推荐</button>
        </div>
      </section>
    `;
  }

  function renderStructureCard(item) {
    const chainText = (item.chain_ids || []).join(", ") || "-";
    const ligandText = (item.ligand_ids || []).join(", ") || "-";
    const resolutionText = item.resolution ? `${item.resolution} A` : "-";
    const noteText = item.quality_note || defaultQualityNote(item);
    const reasons = item.recommendation_reasons || recommendationReasons(item);
    const warnings = item.warnings || [];
    const titleText = item.title || "暂无结构标题";
    const downloadText = item.download_url || "-";

    return `
      <article class="structure-item">
        <div class="structure-main">
          <div class="structure-id-row">
            <span class="structure-id">${escapeHtml(item.structure_id)}</span>
            <span class="grade-badge grade-${escapeHtml(item.docking_grade || "C")}">${escapeHtml(item.docking_grade_label || "参考结构")}</span>
            <span class="badge">${escapeHtml(item.file_format || "-")}</span>
            <span class="badge ${item.is_downloaded ? "ok" : ""}">${item.is_downloaded ? "已缓存" : "未缓存"}</span>
            ${item.docking_recommended ? "<span class=\"badge ok\">Docking</span>" : "<span class=\"badge\">不优先 Docking</span>"}
            ${item.is_preferred ? "<span class=\"badge warn\">推荐</span>" : ""}
          </div>

          <div class="structure-facts">
            <div><span>Source</span><strong>${escapeHtml(item.source || "-")}</strong></div>
            <div><span>Method</span><strong>${escapeHtml(item.method || "-")}</strong></div>
            <div><span>Resolution</span><strong>${escapeHtml(resolutionText)}</strong></div>
            <div><span>Ligands</span><strong>${escapeHtml(ligandText)}</strong></div>
          </div>

          <div class="reason-row">
            ${reasons.map((reason) => `<span>${escapeHtml(reason)}</span>`).join("")}
          </div>
          ${warnings.length ? `<div class="warning-row">${warnings.map((warning) => `<span>${escapeHtml(warning)}</span>`).join("")}</div>` : ""}

          <details class="structure-details">
            <summary>展开结构详情</summary>
            <div class="structure-details-grid">
              <div><span>PDB / Model title</span><strong>${escapeHtml(titleText)}</strong></div>
              <div><span>Chains</span><strong>${escapeHtml(chainText)}</strong></div>
              <div><span>Structure type</span><strong>${escapeHtml(item.structure_type || "-")}</strong></div>
              <div><span>Organism</span><strong>${escapeHtml(item.organism || "-")}</strong></div>
              <div><span>File format</span><strong>${escapeHtml(item.file_format || "-")}</strong></div>
              <div><span>Download URL</span><strong>${escapeHtml(downloadText)}</strong></div>
              <div class="wide"><span>Local cache path</span><strong>${escapeHtml(item.local_file_path || "-")}</strong></div>
            </div>
            <p class="structure-note">${escapeHtml(noteText)}</p>
          </details>
        </div>

        <div class="structure-side">
          <span class="score-pill">Score ${item.score || 0}</span>
          <span class="level-pill">${escapeHtml(item.recommendation_level || "Reference")}</span>
          <button class="small-btn secondary" type="button" onclick="TargetSearchApp.downloadStructure(${item.id}, 'pdb')">下载 PDB</button>
          <button class="small-btn secondary" type="button" onclick="TargetSearchApp.downloadStructure(${item.id}, 'cif')">下载 mmCIF</button>
          <button class="small-btn primary" type="button" onclick="TargetSearchApp.sendToDocking(${item.id})">发送到分子对接</button>
        </div>
      </article>
    `;
  }

  function recommendationReasons(item) {
    const reasons = [];
    if (item.structure_type === "experimental") reasons.push("实验结构");
    if (item.source === "AlphaFold" || item.structure_type === "predicted") reasons.push("预测结构");
    if (Number(item.resolution) > 0 && Number(item.resolution) <= 2.5) reasons.push("分辨率 ≤ 2.5 A");
    if ((item.ligand_ids || []).length > 0) reasons.push("含配体");
    if ((item.organism || "").toLowerCase() === "homo sapiens") reasons.push("人源");
    if (item.docking_recommended) reasons.push("推荐 docking");
    if (!item.docking_recommended && item.source === "AlphaFold") reasons.push("不优先用于 docking");
    return reasons.length ? reasons : ["Demo 结构索引"];
  }

  function collectFilters() {
    return {
      target_type: document.getElementById("filterTargetType").value,
      source: document.getElementById("filterSource").value,
      has_experimental: document.getElementById("filterExperimental").checked ? "true" : "",
      docking_recommended: document.getElementById("filterDocking").checked ? "true" : "",
      has_ligand: document.getElementById("filterLigand").checked ? "true" : "",
    };
  }

  function matchReasonText(reason) {
    const labels = {
      gene_symbol: "命中基因名",
      uniprot_id: "命中 UniProt",
      alias: "命中别名",
      disease: "命中疾病关键词",
      protein_name: "命中蛋白名",
      keyword: "关键词匹配",
    };
    return labels[reason] || "关键词匹配";
  }

  function searchPreset(query) {
    const input = document.getElementById("targetQuery");
    input.value = query;
    runSearch(query);
  }

  function defaultQualityNote(item) {
    if (item.source === "AlphaFold") {
      return "AlphaFold 预测结构，demo 阶段仅用于展示和缓存检查。";
    }
    return "Demo seed structure metadata";
  }

  async function downloadPreferred(targetId) {
    hideMessage();
    try {
      const data = await TargetSearchApi.detail(targetId);
      const preferred = (data.structures || [])[0];

      if (!preferred) {
        showMessage("该靶点没有可下载结构。", "error");
        return;
      }

      await downloadStructure(preferred.id, preferred.file_format || "cif");
    } catch (error) {
      showMessage(error.message, "error");
    }
  }

  async function downloadStructure(structureId, format) {
    hideMessage();
    showMessage("正在检查结构文件状态...", "info");
    try {
      const preflight = await TargetSearchApi.preflight(structureId, format);
      showMessage(formatPreflightMessage(preflight, "download"), preflight.cache_status === "available" ? "success" : "info");
      const { blob, filename } = await TargetSearchApi.download(structureId, format);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      showMessage(`结构文件已开始下载：${filename}。${formatPreflightMessage(preflight, "download")}`, "success");
    } catch (error) {
      showMessage(error.message, "error");
    }
  }

  async function sendToDocking(structureId) {
    hideMessage();
    try {
      const preflight = await TargetSearchApi.preflight(structureId, "cif");
      showMessage(formatPreflightMessage(preflight, "docking"), preflight.suitable_for_direct_docking ? "success" : "info");
      const data = await TargetSearchApi.sendToDocking(structureId);
      if (data.status === "error") {
        throw new Error(data.message || "结构文件准备失败");
      }
      showMessage(`${data.message}：${data.protein_file}。${formatPreflightMessage(preflight, "docking")}`, "success");
    } catch (error) {
      showMessage(error.message, "error");
    }
  }

  function formatPreflightMessage(preflight, intent) {
    const cacheText = preflight.cache_status === "available"
      ? `本地缓存可用${preflight.file_size_bytes ? `，${formatBytes(preflight.file_size_bytes)}` : ""}`
      : "本地缓存缺失，将尝试远程下载并缓存";
    const dockingText = preflight.suitable_for_direct_docking
      ? `${preflight.docking_grade} 级结构，适合作为 docking 输入`
      : `${preflight.docking_grade} 级结构，建议谨慎用于 docking`;
    const convertText = preflight.needs_pdbqt_conversion ? "后续 docking 前需要转换为 PDBQT" : "已是 docking 输入格式";
    const warningText = (preflight.warnings || []).length
      ? `提示：${preflight.warnings.join("；")}`
      : "";
    const actionText = intent === "download" ? "下载前检查" : "对接前检查";
    return [actionText, cacheText, dockingText, convertText, warningText].filter(Boolean).join("；");
  }

  function formatBytes(value) {
    const bytes = Number(value || 0);
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
  }

  function setLoading(isLoading) {
    const button = document.getElementById("targetSearchBtn");
    button.disabled = isLoading;
    button.textContent = isLoading ? "搜索中" : "搜索";
  }

  function showMessage(text, type) {
    const message = document.getElementById("targetMessage");
    message.hidden = false;
    message.className = `message ${type || ""}`;
    message.textContent = text;
  }

  function hideMessage() {
    const message = document.getElementById("targetMessage");
    message.hidden = true;
    message.textContent = "";
  }

  function escapeAttr(value) {
    return String(value ?? "").replaceAll("'", "\\'");
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll("\"", "&quot;")
      .replaceAll("'", "&#039;");
  }

  return {
    init,
    loadDetail,
    downloadPreferred,
    downloadStructure,
    sendToDocking,
    searchPreset,
  };
})();

document.addEventListener("DOMContentLoaded", TargetSearchApp.init);
