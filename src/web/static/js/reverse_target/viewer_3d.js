// ═══════════════════════════════════════════════════════════
//  反向寻靶模块 – 3Dmol.js 药效团可视化管理器
//  负责 3D 构象渲染、药效团球体、镜头控制
// ═══════════════════════════════════════════════════════════

const RtViewer = (() => {
  const $ = (id) => document.getElementById(id);

  let glViewer = null;
  let isSpinning = false;

  function safeColor(value) {
    const color = String(value || "").trim();
    return /^(?:#[0-9a-f]{3,8}|rgba?\([0-9.,%\s]+\))$/i.test(color)
      ? color
      : "#64748b";
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
    title.replaceChildren();
    const titleIcon = document.createElement("span");
    titleIcon.textContent = "🔬";
    titleIcon.style.fontSize = "24px";
    const titleText = document.createTextNode(" 3D 药效团分析: ");
    const titleId = document.createElement("span");
    titleId.textContent = String(chemblId ?? "");
    titleId.style.color = "#6366f1";
    titleId.style.marginLeft = "8px";
    title.append(titleIcon, titleText, titleId);
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
        const color = safeColor(f.color);
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
      list.replaceChildren();
      data.features.forEach((f, i) => {
        const position = Array.isArray(f.pos) ? f.pos.slice(0, 3).map(Number) : [];
        if (position.length !== 3 || position.some((value) => !Number.isFinite(value))) return;
        const color = safeColor(f.color);
        const item = document.createElement("div");
        item.className = "feat-item";
        item.id = `feat-item-${i}`;
        item.addEventListener("click", () => focusOnFeature(i, ...position));

        const icon = document.createElement("div");
        icon.className = "feat-icon";
        icon.textContent = String(f.icon ?? "");
        icon.style.background = `${color}20`;
        icon.style.color = color;

        const details = document.createElement("div");
        details.style.flex = "1";
        const label = document.createElement("div");
        label.textContent = String(f.label ?? "");
        label.style.fontSize = "13px";
        label.style.fontWeight = "700";
        label.style.color = "#334155";
        const coordinates = document.createElement("div");
        coordinates.textContent = `XYZ: ${position.map((value) => value.toFixed(1)).join(", ")}`;
        coordinates.style.fontSize = "11px";
        coordinates.style.color = "#94a3b8";
        coordinates.style.fontFamily = "monospace";
        details.append(label, coordinates);

        const marker = document.createElement("div");
        marker.style.width = "6px";
        marker.style.height = "6px";
        marker.style.borderRadius = "50%";
        marker.style.background = color;
        item.append(icon, details, marker);
        list.appendChild(item);
      });
    } catch (err) {
      info.textContent = `❌ ${String(err?.message || "药效团分析失败")}`;
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
    const create = (tag, text, className) => {
      const node = document.createElement(tag);
      if (className) node.className = className;
      if (text !== undefined) node.textContent = String(text);
      return node;
    };
    const value = (name, fallback = "-") =>
      p && p[name] !== null && p[name] !== undefined ? p[name] : fallback;
    const title = create("div", "物理化学性质");
    title.style.fontSize = "11px";
    title.style.color = "#94a3b8";
    title.style.textTransform = "uppercase";
    title.style.letterSpacing = "1px";
    title.style.marginBottom = "12px";
    title.style.fontWeight = "700";

    const grid = create("div");
    grid.style.display = "grid";
    grid.style.gridTemplateColumns = "1fr 1fr";
    grid.style.gap = "12px";
    const property = (label, content) => {
      const item = create("div", undefined, "prop-item");
      const name = create("span", label);
      name.style.color = "#64748b";
      name.style.fontSize = "12px";
      const output = create("div", content);
      output.style.color = "#1e293b";
      output.style.fontWeight = "700";
      output.style.fontSize = "14px";
      item.append(name, output);
      return item;
    };
    grid.append(
      property("MW", value("MW")),
      property("LogP", value("LogP")),
      property("HBD/HBA", `${value("HBD", 0)} / ${value("HBA", 0)}`),
      property("RotBonds", value("RotBonds", 0)),
    );

    const timing = create("div", `⚡ 构象计算耗时: ${timeMs ?? "-"}ms`);
    timing.style.marginTop = "15px";
    timing.style.paddingTop = "12px";
    timing.style.borderTop = "1px solid #f1f5f9";
    timing.style.fontSize = "11px";
    timing.style.color = "#94a3b8";
    container.replaceChildren(title, grid, timing);
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
