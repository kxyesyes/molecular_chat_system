// ═══════════════════════════════════════════════════════════
//  反向寻靶模块 – 3Dmol.js 药效团可视化管理器
//  负责 3D 构象渲染、药效团球体、镜头控制
// ═══════════════════════════════════════════════════════════

const RtViewer = (() => {
  const $ = (id) => document.getElementById(id);

  let glViewer = null;
  let isSpinning = false;

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // ──────────────────────────────────────
  //  打开 3D 药效团模态框
  // ──────────────────────────────────────
  async function openPharmacophore3D(smiles, chemblId) {
    const modal = $("pharm3DModal");
    const title = $("pharm3DTitle");
    const info = $("pharmInfoCard");
    const list = $("pharmPointsList");
    const countTag = $("featCountTag");

    modal.classList.add("show");
    title.innerHTML = `<span style="font-size:24px;">🔬</span> 3D 药效团分析: <span style="color:#6366f1;margin-left:8px;">${escapeHtml(chemblId)}</span>`;
    info.innerHTML =
      '<div style="text-align:center;padding:20px;"><div class="spinner" style="margin:0 auto 10px;"></div>' +
      '<p style="color:#64748b;font-size:13px;">正在生成 3D 构象并提取特征...</p></div>';
    list.innerHTML = "";
    countTag.textContent = "计算中...";

    // 等待模态框渲染完毕再初始化 WebGL
    await new Promise((r) => setTimeout(r, 50));

    try {
      _ensureViewer();
      glViewer.clear();

      const data = await RtApi.fetchPharmacophore(smiles);

      // 1. 渲染分子棒球模型
      glViewer.addModel(data.mol_block, "sdf");
      glViewer.setStyle(
        {},
        {
          stick: { radius: 0.12, colorscheme: "Jmol" },
          sphere: { radius: 0.3, colorscheme: "Jmol" },
        },
      );

      // 2. 渲染药效团特征球
      data.features.forEach((f, idx) => {
        const color = f.color || "#ffffff";
        glViewer.addSphere({
          center: { x: f.pos[0], y: f.pos[1], z: f.pos[2] },
          radius: 0.85,
          color,
          alpha: 0.85,
          clickable: true,
          callback: () => focusOnFeature(idx, f.pos[0], f.pos[1], f.pos[2]),
        });
        glViewer.addSphere({
          center: { x: f.pos[0], y: f.pos[1], z: f.pos[2] },
          radius: 1.4,
          color,
          wireframe: true,
          alpha: 0.5,
        });
      });

      glViewer.zoomTo();
      glViewer.render();

      // 3. 信息卡
      _renderInfoCard(info, data.properties || {}, data.conformer_time_ms);

      // 4. 特征点列表
      countTag.textContent = `${data.features.length} 个特征`;
      list.innerHTML = data.features
        .map(
          (f, i) => `
        <div class="feat-item" id="feat-item-${i}"
             onclick="RtViewer.focusOnFeature(${i}, ${f.pos[0]}, ${f.pos[1]}, ${f.pos[2]})">
          <div class="feat-icon" style="background:${f.color}20;color:${f.color};">${f.icon}</div>
          <div style="flex:1">
            <div style="font-size:13px;font-weight:700;color:#334155;">${escapeHtml(f.label)}</div>
            <div style="font-size:11px;color:#94a3b8;font-family:monospace;">XYZ: ${f.pos.map((v) => v.toFixed(1)).join(", ")}</div>
          </div>
          <div style="width:6px;height:6px;border-radius:50%;background:${f.color}"></div>
        </div>`,
        )
        .join("");
    } catch (err) {
      info.innerHTML = `<div style="color:#ef4444;padding:20px;text-align:center;">❌ ${escapeHtml(err.message)}</div>`;
      countTag.textContent = "错误";
    }
  }

  // ── 确保 3Dmol viewer 存在 ──
  function _ensureViewer() {
    if (glViewer) return;
    if (
      typeof $3Dmol === "undefined" ||
      window.reverseTargetDependencyStatus?.threeDmolLoaded === false
    ) {
      throw new Error("3Dmol.js 加载失败，当前网络可能无法访问外部 CDN；请联网后刷新页面，或后续改为本地静态依赖。");
    }
    glViewer = $3Dmol.createViewer("viewer-3d", {
      backgroundColor: "#020617",
      antialias: true,
    });
  }

  // ── 渲染信息卡 ──
  function _renderInfoCard(container, p, timeMs) {
    container.innerHTML = `
      <div style="font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:1px;margin-bottom:12px;font-weight:700;">物理化学性质</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
        <div class="prop-item">
          <span style="color:#64748b;font-size:12px;">MW</span>
          <div style="color:#1e293b;font-weight:700;font-size:14px;">${p.MW || "-"}</div>
        </div>
        <div class="prop-item">
          <span style="color:#64748b;font-size:12px;">LogP</span>
          <div style="color:#1e293b;font-weight:700;font-size:14px;">${p.LogP || "-"}</div>
        </div>
        <div class="prop-item">
          <span style="color:#64748b;font-size:12px;">HBD/HBA</span>
          <div style="color:#1e293b;font-weight:700;font-size:14px;">${p.HBD || 0} / ${p.HBA || 0}</div>
        </div>
        <div class="prop-item">
          <span style="color:#64748b;font-size:12px;">RotBonds</span>
          <div style="color:#1e293b;font-weight:700;font-size:14px;">${p.RotBonds || 0}</div>
        </div>
      </div>
      <div style="margin-top:15px;padding-top:12px;border-top:1px solid #f1f5f9;font-size:11px;color:#94a3b8;">
        ⚡ 构象计算耗时: ${timeMs}ms
      </div>`;
  }

  // ── 聚焦到某个特征点 ──
  function focusOnFeature(idx, x, y, z) {
    document
      .querySelectorAll(".feat-item")
      .forEach((el) => el.classList.remove("active"));
    const el = $(`feat-item-${idx}`);
    if (el) el.classList.add("active");
    if (glViewer) {
      glViewer.setCenter({ x, y, z });
      glViewer.setZoom(20);
      glViewer.render();
    }
  }

  // ── 重置视角 ──
  function resetCamera() {
    if (glViewer) {
      glViewer.zoomTo();
      glViewer.render();
    }
  }

  // ── 自动旋转 ──
  function toggleSpin() {
    if (!glViewer) return;
    isSpinning = !isSpinning;
    const btn = $("spinBtn");
    if (isSpinning) {
      glViewer.spin(true);
      btn.textContent = "⏹ 停止旋转";
      btn.style.background = "rgba(239, 68, 68, 0.2)";
    } else {
      glViewer.spin(false);
      btn.textContent = "🔄 自动旋转";
      btn.style.background = "rgba(255,255,255,0.1)";
    }
  }

  // ── 关闭 3D 模态框 ──
  function closePharm3DModal() {
    $("pharm3DModal").classList.remove("show");
    if (isSpinning) toggleSpin();
  }

  return {
    openPharmacophore3D,
    focusOnFeature,
    resetCamera,
    toggleSpin,
    closePharm3DModal,
  };
})();
