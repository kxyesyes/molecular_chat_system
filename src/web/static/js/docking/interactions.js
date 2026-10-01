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
    removeInteractions("hbonds");
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
    removeInteractions("pipi");
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

// 相互作用只展示后端成熟分析器的结构化证据；浏览器端不再自行猜测化学作用。
async function requestInteractionAnalysis(kind) {
  const jobId = typeof getCurrentJobId === "function" ? getCurrentJobId() : null;
  const poseSelect = document.getElementById("pose-select");
  const pose = poseSelect ? parseInt(poseSelect.value || "1", 10) : 1;
  if (!jobId || !Number.isInteger(pose) || pose <= 0) {
    Utils.showToast("请先加载有效的对接任务和构象", "warning");
    return;
  }
  try {
    const response = await fetch(`/api/docking/interactions/${encodeURIComponent(jobId)}?pose=${pose}`);
    const analysis = await response.json();
    if (!response.ok || analysis.status === "failed") {
      Utils.showToast("相互作用分析失败，未显示推测结果", "warning");
      return;
    }
    const interactions = (analysis.interactions || []).filter((item) => item.type === kind);
    if (analysis.status !== "success") {
      Utils.showToast(
        analysis.warnings?.[0] || "缺少可靠结构证据，无法分析该类相互作用",
        "warning",
      );
      return;
    }
    Utils.showToast(
      interactions.length > 0
        ? `后端分析确认 ${interactions.length} 条${kind}证据`
        : "后端分析未报告该类相互作用",
      interactions.length > 0 ? "success" : "warning",
    );
  } catch (error) {
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

// 移除特定类型的相互作用
function removeInteractions(type) {
  viewer.removeAllShapes();
  // 不要在这里调用 StyleManager.apply：
  // 它会执行全局 setStyle({},{}) 并依赖配体识别，某些场景会导致配体样式丢失/看起来消失。
  // 这里仅清理 shape，然后按当前开关重新绘制其余相互作用。

  const hasOtherInteractions =
    (type !== "hbonds" && interactionsState.hydrogenBonds) ||
    (type !== "pipi" && interactionsState.piPiInteractions) ||
    (type !== "hydrophobic" && interactionsState.hydrophobicInteractions);

  // 若所有相互作用都已关闭，恢复一次复合物基础视图，确保蛋白/配体样式稳定存在。
  if (!hasOtherInteractions) {
    try {
      const jobId =
        typeof getCurrentJobId === "function" ? getCurrentJobId() : null;
      const poseSelect = document.getElementById("pose-select");
      const currentPose = poseSelect
        ? parseInt(poseSelect.value || "1", 10)
        : 1;

      // 保持当前 pose，不要默认回到 pose 1
      if (
        jobId &&
        typeof viewPose === "function" &&
        Number.isFinite(currentPose)
      ) {
        viewPose(Math.max(1, currentPose));
      } else if (jobId && typeof loadComplexStructure === "function") {
        loadComplexStructure(jobId);
      } else {
        viewer.render();
      }
    } catch (e) {
      console.warn("恢复复合物视图失败:", e);
      viewer.render();
    }
    return;
  }

  if (type !== "hbonds" && interactionsState.hydrogenBonds)
    displayHydrogenBonds();
  if (type !== "pipi" && interactionsState.piPiInteractions)
    displayPiPiInteractions();
  if (type !== "hydrophobic" && interactionsState.hydrophobicInteractions)
    displayHydrophobicInteractions();
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
