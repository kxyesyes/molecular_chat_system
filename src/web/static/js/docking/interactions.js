// ==================== 分子相互作用检测与显示 ====================

// 聚焦显示：配体 + 参与相互作用残基
function focusLigandAndResidues(residueKeySet, clearStyles) {
  try {
    residueKeySet.forEach((rk) => {
      const [resn, resi, chain] = rk.split(":");
      viewer.setStyle(
        { resn, resi: parseInt(resi), chain },
        { stick: { radius: 0.3, colorscheme: "blueCarbon" } },
      );
    });
    viewer.render();
  } catch (e) {
    console.warn("focusLigandAndResidues 渲染失败:", e);
  }
}

// 切换氢键显示
function toggleHydrogenBonds() {
  if (!viewer) {
    alert("请先加载分子结构");
    return;
  }
  interactionsState.hydrogenBonds = !interactionsState.hydrogenBonds;
  const btn = document.getElementById("show-hbonds-btn");
  if (interactionsState.hydrogenBonds) {
    btn.classList.add("active");
    displayHydrogenBonds();
  } else {
    btn.classList.remove("active");
    removeInteractions("hydrogen_bond");
  }
}

// 切换π-π相互作用显示
function togglePiPiInteractions() {
  if (!viewer) {
    alert("请先加载分子结构");
    return;
  }
  interactionsState.piPiInteractions = !interactionsState.piPiInteractions;
  const btn = document.getElementById("show-pi-btn");
  if (interactionsState.piPiInteractions) {
    btn.classList.add("active");
    displayPiPiInteractions();
  } else {
    btn.classList.remove("active");
    removeInteractions("pi_pi");
  }
}

// 切换疏水相互作用显示
function toggleHydrophobicInteractions() {
  if (!viewer) {
    alert("请先加载分子结构");
    return;
  }
  interactionsState.hydrophobicInteractions =
    !interactionsState.hydrophobicInteractions;
  const btn = document.getElementById("show-hydrophobic-btn");
  if (interactionsState.hydrophobicInteractions) {
    btn.classList.add("active");
    displayHydrophobicInteractions();
  } else {
    btn.classList.remove("active");
    removeInteractions("hydrophobic");
  }
}

const INTERACTION_COLORS = {
  hydrogen_bond: "#16a3ff",
  pi_pi: "#a855f7",
  hydrophobic: "#f59e0b",
};

// A single backend analysis supplies all interaction classes for one job/pose.
// Cache the in-flight promise as well as its response so toggling several
// overlays does not launch duplicate (and comparatively expensive) analyses.
const interactionAnalysisCache = new Map();
const interactionShapes = new Map();
const interactionSwitch = {hydrogen_bond: "hydrogenBonds", pi_pi: "piPiInteractions", hydrophobic: "hydrophobicInteractions"};

function interactionAnalysisKey(jobId, pose) {
  return `${jobId}:${pose}`;
}

function fetchInteractionAnalysis(jobId, pose) {
  const key = interactionAnalysisKey(jobId, pose);
  const cached = interactionAnalysisCache.get(key);
  if (cached) return cached;

  const request = fetch(
    `/api/docking/interactions/${encodeURIComponent(jobId)}?pose=${pose}`,
  )
    .then(async (response) => {
      const analysis = await response.json();
      if (!response.ok) {
        throw new Error(analysis.detail || analysis.message || "interaction analysis failed");
      }
      return analysis;
    })
    .catch((error) => {
      // A transient transport failure should be retryable; a valid unavailable
      // or failed scientific response remains cached and fail-closed.
      interactionAnalysisCache.delete(key);
      throw error;
    });
  interactionAnalysisCache.set(key, request);
  return request;
}

function interactionGeometryOccurrences(interaction) {
  if (Array.isArray(interaction?.geometry_occurrences)) {
    return interaction.geometry_occurrences;
  }
  return [interaction];
}

// 只绘制分析器根据同一组构象解析出的坐标；浏览器端不自行映射原子索引。
function renderInteractionEvidence(kind, interactions) {
  if (!viewer || !Array.isArray(interactions)) return 0;
  const color = INTERACTION_COLORS[kind] || "#16a3ff";
  removeInteractions(kind);
  const shapes = [];
  interactions.forEach((interaction) => {
    interactionGeometryOccurrences(interaction).forEach((geometry) => {
      const displayGeometry = geometry?.display_geometry;
      const start = displayGeometry?.protein;
      const end = displayGeometry?.ligand;
      if (
        !start ||
        !end ||
        ![start, end].every((point) =>
          ["x", "y", "z"].every((axis) => Number.isFinite(Number(point[axis]))),
        )
      ) {
        return;
      }
      const shape = viewer.addCylinder({
        start,
        end,
        radius: 0.1,
        color,
        dashed: true,
        fromCap: 1,
        toCap: 1,
      });
      shapes.push(shape);
    });
  });
  if (shapes.length) {
    interactionShapes.set(kind, shapes);
    viewer.render();
  }
  return shapes.length;
}

// 相互作用只展示后端成熟分析器的结构化证据；浏览器端不再自行猜测化学作用。
async function requestInteractionAnalysis(kind) {
  const jobId = typeof getCurrentJobId === "function" ? getCurrentJobId() : null;
  const viewSequence = typeof getDockingViewSequence === "function" ? getDockingViewSequence() : null;
  const poseSelect = document.getElementById("pose-select");
  const pose = poseSelect ? parseInt(poseSelect.value || "1", 10) : 1;
  if (!jobId || !Number.isInteger(pose) || pose <= 0) {
    Utils.showToast("请先加载有效的对接任务和构象", "warning");
    return;
  }
  const isCurrent = () => interactionsState[interactionSwitch[kind]] &&
    getCurrentJobId() === jobId && Number(poseSelect?.value || 1) === pose &&
    (typeof getDockingViewSequence !== "function" || getDockingViewSequence() === viewSequence) &&
    (typeof isRenderedInteractionPose !== "function" || isRenderedInteractionPose(jobId, pose));
  if (!isCurrent()) return;
  const status = document.getElementById("interaction-status");
  if (status) status.textContent = "";
  try {
    const analysis = await fetchInteractionAnalysis(jobId, pose);
    if (!isCurrent()) return;
    if (analysis.status === "failed") {
      const detail = analysis.warnings?.[0] || analysis.detail;
      Utils.showToast(
        detail
          ? `相互作用分析失败：${detail}`
          : "相互作用分析失败，未显示推测结果",
        "warning",
      );
      return;
    }
    const interactions = (analysis.interactions || []).filter((item) => item.type === kind);
    if (analysis.status !== "success") {
      const message = analysis.warnings?.[0] === "protein_explicit_hydrogens_are_required_by_prolif"
        ? "受体缺少显式氢，无法进行相互作用分析；请准备带显式氢的受体后重试。"
        : "缺少可靠结构证据，无法分析该类相互作用。";
      if (status) status.textContent = `Pose ${pose}：${message}`;
      Utils.showToast(message, "warning");
      return;
    }
    const rendered = renderInteractionEvidence(kind, interactions);
    if (interactions.length === 0) {
      Utils.showToast("后端分析未报告该类相互作用", "warning");
    } else if (rendered === 0) {
      Utils.showToast(
        `后端分析确认 ${interactions.length} 条${kind}证据，但缺少可绘制的分析坐标`,
        "warning",
      );
    } else {
      Utils.showToast(`已显示 ${rendered} 条${kind}相互作用证据`, "success");
    }
  } catch (error) {
    if (!isCurrent()) return;
    console.warn("后端相互作用分析不可用:", error);
    Utils.showToast("相互作用分析服务不可用，未显示推测结果", "warning");
  }
}

function displayHydrogenBonds() {
  return requestInteractionAnalysis("hydrogen_bond");
}

function displayPiPiInteractions() {
  return requestInteractionAnalysis("pi_pi");
}

function displayHydrophobicInteractions() {
  return requestInteractionAnalysis("hydrophobic");
}

// 3Dmol reuses shape positions after clearing the scene. Only remove the
// original object if it still occupies its slot.
function removeInteractions(kind) {
  const shapes = interactionShapes.get(kind);
  if (!shapes) return;
  for (const shape of shapes) {
    if (viewer.shapes[shape.shapePosition] === shape) viewer.removeShape(shape);
  }
  interactionShapes.delete(kind);
  viewer.render();
}

function clearInteractionOverlays() {
  for (const kind of interactionShapes.keys()) removeInteractions(kind);
}

// 显示相互作用信息提示
function showInteractionInfo(message) {
  const info = document.createElement("div");
  info.style.cssText = `
    position: fixed;
    top: 100px;
    right: 30px;
    background: rgba(0, 0, 0, 0.8);
    color: white;
    padding: 15px 20px;
    border-radius: 8px;
    z-index: 1000;
    font-size: 14px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.3);
    animation: fadeIn 0.3s ease-in;
  `;
  info.textContent = message;
  document.body.appendChild(info);
  setTimeout(() => {
    info.style.animation = "fadeOut 0.3s ease-out";
    setTimeout(() => info.remove(), 300);
  }, 3000);
}
