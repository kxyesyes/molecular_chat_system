// ═══════════════════════════════════════════════════════════
//  反向寻靶模块 – UI 交互管理器
//  Tab 切换、文件上传、加载状态、统计卡片、导出 CSV
// ═══════════════════════════════════════════════════════════

const RtUI = (() => {
  // ── DOM 缓存 ──
  const $ = (id) => document.getElementById(id);

  // ── Tab 切换 ──
  function switchTab(tabName) {
    document
      .querySelectorAll(".tab")
      .forEach((t) => t.classList.remove("active"));
    event.currentTarget.classList.add("active");

    $("single-panel").style.display = tabName === "single" ? "block" : "none";
    $("batch-panel").style.display = tabName === "batch" ? "block" : "none";

    // 重置结果和错误
    $("results").classList.remove("show");
    $("error").style.display = "none";
  }

  // ── 加载/错误状态 ──
  function showLoading() {
    $("loading").classList.add("show");
    $("error").style.display = "none";
    $("results").classList.remove("show");
  }

  function hideLoading() {
    $("loading").classList.remove("show");
  }

  function showError(message) {
    $("error").style.display = "block";
    $("errorText").textContent = message;
  }

  function setSubmitEnabled(btnId, enabled) {
    const btn = $(btnId);
    if (btn) btn.disabled = !enabled;
  }

  // ── 统计卡片加载 ──
  async function loadStats() {
    const statItems = document.querySelectorAll(".stat-item .stat-value");
    const statLabels = document.querySelectorAll(".stat-item .stat-label");
    if (statItems.length < 3) return;

    const loadingHtml =
      '<span style="color: #94a3b8; font-size: 14px;">检查中...</span>';
    const errorHtml = '<span style="color: #ef4444;">加载失败</span>';
    statItems[0].innerHTML = loadingHtml;
    statItems[1].innerHTML = loadingHtml;
    statItems[2].innerHTML = loadingHtml;
    if (statItems[3]) statItems[3].innerHTML = loadingHtml;

    const [statsResult, healthResult] = await Promise.allSettled([
        RtApi.fetchStats(),
        RtApi.fetchHealth(),
      ]);

    const stats = statsResult.status === "fulfilled" ? statsResult.value : null;
    const health = healthResult.status === "fulfilled" ? healthResult.value : null;

    if (!stats && !health) {
      statItems[0].innerHTML = errorHtml;
      statItems[1].innerHTML = errorHtml;
      statItems[2].innerHTML = errorHtml;
      if (statItems[3]) statItems[3].innerHTML = errorHtml;
      return;
    }

    if (stats) {
      statItems[0].textContent = stats.total_records.toLocaleString();
      statItems[1].textContent = stats.unique_molecules.toLocaleString();
      statItems[2].textContent = stats.unique_targets.toLocaleString();
    } else if (health) {
      statItems[0].textContent = health.record_count.toLocaleString();
      statItems[1].textContent = "-";
      statItems[2].textContent = "-";
    }

    if (health) {
      if (statItems[3]) {
        statItems[3].textContent = health.ready ? "Ready" : "Need Init";
        statItems[3].style.color = health.ready ? "#166534" : "#b91c1c";
      }
      if (statLabels[3]) {
        statLabels[3].textContent = health.ready
          ? `指纹维度 ${stats?.morgan_bits || health.metadata?.morgan_bits || 2048} + ${stats?.maccs_bits || health.metadata?.maccs_bits || 166}`
          : `${health.missing_files?.length || 0} 个核心文件缺失`;
      }
    }
  }

  // ── 文件上传交互（拖拽 + 选择） ──
  function initFileUpload() {
    const fileInput = $("batchFile");
    const fileInfo = $("fileInfo");
    const uploadArea = $("uploadArea");

    if (!fileInput || !uploadArea) return;

    fileInput.addEventListener("change", function () {
      if (this.files && this.files[0]) {
        const f = this.files[0];
        fileInfo.textContent = `已选择: ${f.name} (${(f.size / 1024).toFixed(1)} KB)`;
        uploadArea.style.borderColor = "#4f46e5";
        uploadArea.style.backgroundColor = "#eff6ff";
      }
    });

    uploadArea.addEventListener("dragover", (e) => {
      e.preventDefault();
      uploadArea.style.borderColor = "#4f46e5";
      uploadArea.style.backgroundColor = "#eff6ff";
    });

    uploadArea.addEventListener("dragleave", (e) => {
      e.preventDefault();
      uploadArea.style.borderColor = "#cbd5e1";
      uploadArea.style.backgroundColor = "#f8fafc";
    });

    uploadArea.addEventListener("drop", (e) => {
      e.preventDefault();
      if (e.dataTransfer.files.length > 0) {
        fileInput.files = e.dataTransfer.files;
        fileInput.dispatchEvent(new Event("change"));
      }
    });
  }

  // ── CSV 导出（占位） ──
  function exportData() {
    const data = window.currentBatchResults || window.currentSingleResults;
    if (!data) return;
    const rows = _flattenExportRows(data);
    if (!rows.length) {
      alert("暂无可导出的结果");
      return;
    }

    const headers = [
      "query_smiles",
      "target_name",
      "organism",
      "molecule_chembl_id",
      "standard_type",
      "standard_value",
      "similar_count",
      "morgan_similarity",
      "maccs_similarity",
      "final_similarity",
      "final_3d_score",
      "alignment_score",
      "alignment_rmsd",
      "alignment_coverage",
    ];
    const csv = [
      headers.join(","),
      ...rows.map((row) => headers.map((key) => _csvCell(row[key])).join(",")),
    ].join("\n");
    const blob = new Blob(["\ufeff" + csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `reverse_target_results_${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  function _flattenExportRows(data) {
    if (!Array.isArray(data)) return [];
    const rows = [];
    data.forEach((item) => {
      if (item && item.success && Array.isArray(item.targets)) {
        item.targets.forEach((target) => {
          rows.push({ query_smiles: item.query_smiles || "", ...target });
        });
      } else if (item && item.target_name) {
        rows.push({ query_smiles: window.currentQuerySmiles || "", ...item });
      }
    });
    return rows;
  }

  function _csvCell(value) {
    const text = value == null ? "" : String(value);
    return `"${text.replaceAll('"', '""')}"`;
  }

  return {
    $,
    switchTab,
    showLoading,
    hideLoading,
    showError,
    setSubmitEnabled,
    loadStats,
    initFileUpload,
    exportData,
  };
})();
