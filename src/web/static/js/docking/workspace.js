// A cavity belongs to the uploaded receptor, independently of the displayed pose.
let dockingCavities = [];
let cavityReceptor = null;

function clearDockingCavities() {
  cavityReceptor = null;
  dockingCavities = [];
  const select = document.getElementById("cavity-choice");
  select.replaceChildren(new Option("自定义区域", ""));
  document.getElementById("cavity-status").textContent = "";
}

async function detectDockingCavities(button) {
  const input = document.getElementById("protein-file");
  const file = input.files[0];
  const status = document.getElementById("cavity-status");
  clearDockingCavities();
  if (!file || file !== loadedDockingProteinFile) {
    status.textContent = "请先载入受体结构。";
    return;
  }
  cavityReceptor = file;
  button.disabled = true;
  status.textContent = "正在识别空腔…";
  try {
    const form = new FormData();
    form.append("protein_file", file);
    const response = await fetch("/api/docking/cavities", { method: "POST", body: form });
    const data = await response.json();
    if (cavityReceptor !== file || input.files[0] !== file) return;
    if (!response.ok) throw new Error(data.detail || "空腔识别失败");
    dockingCavities = data.cavities;
    const select = document.getElementById("cavity-choice");
    dockingCavities.forEach((pocket, i) => {
      select.add(new Option(`空腔 ${i + 1} · 约 ${pocket.volume} Å³`, String(i)));
    });
    status.textContent = dockingCavities.length ?
      "选择空腔可预览并使用其搜索盒。" : "未识别到候选空腔，可输入自定义区域。";
  } catch (error) {
    if (cavityReceptor === file) status.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

function applyDockingCavity(select) {
  if (select.value === "") {
    document.getElementById("center_x").focus();
    return;
  }
  if (document.getElementById("protein-file").files[0] !== cavityReceptor) return;
  const pocket = dockingCavities[Number(select.value)];
  ["x", "y", "z"].forEach((axis, i) => {
    document.getElementById(`center_${axis}`).value = pocket.center[i];
    document.getElementById(`size_${axis}`).value = pocket.size[i];
  });
  AppState.dockingBoxCenterTouched = true;
  showProtein();
  if (dockingBoxVisible) drawDockingBox();
  else toggleDockingBox();
}

document.addEventListener("DOMContentLoaded", () => {
  for (const id of ["center_x", "center_y", "center_z", "size_x", "size_y", "size_z", "auto-box-btn"]) {
    document.getElementById(id).addEventListener(id === "auto-box-btn" ? "click" : "input", () => {
      document.getElementById("cavity-choice").value = "";
    });
  }
});

function mountDockingConsensus(taskId, jobId, canView) {
  const panel = document.createElement("details");
  panel.className = "docking-comparison";
  panel.innerHTML = `<summary>FitDock 与 Vina 姿势比较</summary>
    <div class="docking-comparison">
      <label>比较方式 <select><option value="import">导入同一受体坐标系中的 FitDock 姿势</option>
        <option value="template" disabled>运行 FitDock（检查中）</option></select></label>
      <label>配体结构 <input type="file" accept=".mol2,.sdf,.mol"></label>
      <button type="button" class="action-btn btn-secondary">计算共识排序</button>
      <p role="status" aria-live="polite"></p><div class="comparison-output"></div>
    </div>`;
  document.getElementById("results-content").append(panel);
  const select = panel.querySelector("select");
  const fileInput = panel.querySelector("input");
  const button = panel.querySelector("button");
  const status = panel.querySelector("p");
  const output = panel.querySelector(".comparison-output");
  panel.addEventListener("toggle", async () => {
    if (!panel.open) return;
    try {
      const response = await fetch("/api/docking/fitdock/status");
      if (!response.ok) throw new Error();
      const {available} = await response.json();
      select.options[1].disabled = !available;
      select.options[1].textContent = available ?
        "运行 FitDock（已对齐模板配体 MOL2）" : "运行 FitDock（本机未配置）";
    } catch {
      select.options[1].textContent = "运行 FitDock（无法读取引擎状态）";
    }
  });
  select.onchange = () => {
    fileInput.value = "";
    fileInput.accept = select.value === "template" ? ".mol2" : ".mol2,.sdf,.mol";
    output.replaceChildren();
    status.textContent = "";
  };
  fileInput.onchange = () => { output.replaceChildren(); status.textContent = ""; };
  button.onclick = async () => {
    const file = fileInput.files[0];
    if (!file) { status.textContent = "请先选择配体结构文件。"; return; }
    const form = new FormData();
    form.append("structure", file);
    form.append("mode", select.value);
    select.disabled = fileInput.disabled = button.disabled = true;
    status.textContent = "正在比较姿势…";
    output.replaceChildren();
    try {
      const response = await fetch(`/api/docking/tasks/${encodeURIComponent(taskId)}/consensus`, {method: "POST", body: form});
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "比较失败");
      if (!panel.isConnected) return;
      status.textContent = "按 Vina 评分名次与相对 FitDock 姿势的重原子 RMSD 名次取平均，越小越优。";
      const table = document.createElement("table");
      table.className = "results-table";
      const header = table.createTHead().insertRow();
      for (const name of ["Vina 姿势", "评分 (kcal/mol)", "RMSD (Å)", "平均名次"]) {
        const th = document.createElement("th");
        th.textContent = name;
        header.append(th);
      }
      const body = table.createTBody();
      for (const row of result.rows) {
        const tr = body.insertRow();
        const cell = tr.insertCell();
        if (canView) {
          const view = document.createElement("button");
          view.type = "button";
          view.textContent = `叠加 Pose ${row.pose}`;
          view.onclick = () => viewDockingAgreement(jobId, row.pose, result.fitdock_sdf, status);
          cell.append(view);
        } else cell.textContent = `Pose ${row.pose}`;
        for (const value of [row.vina_energy, row.rmsd, row.mean_rank]) tr.insertCell().textContent = value.toFixed(2);
      }
      output.append(table);
    } catch (error) {
      status.textContent = error.message;
    } finally {
      select.disabled = fileInput.disabled = button.disabled = false;
    }
  };
}

async function viewDockingAgreement(jobId, pose, fitdockSdf, status) {
  if (getCurrentJobId() !== jobId || !currentProteinData) return;
  const sequence = beginDockingView();
  try {
    const response = await fetch(`/api/docking/pose_sdf/${encodeURIComponent(jobId)}?pose=${pose}`);
    if (!response.ok) throw new Error("无法读取 Vina 姿势");
    const sdf = await response.text();
    if (sequence !== getDockingViewSequence() || !status.isConnected) return;
    viewer.clear();
    viewer.addModel(currentProteinData, AppState.currentProteinFormat || "pdb").setStyle({}, {cartoon: {color: "lightgray", opacity: 0.65}});
    const vina = viewer.addModel(sdf, "sdf");
    vina.setStyle({}, {stick: {colorscheme: "greenCarbon"}});
    const fit = viewer.addModel(fitdockSdf, "sdf");
    fit.setStyle({}, {stick: {colorscheme: "orangeCarbon"}});
    viewer.zoomTo({model: [vina, fit]});
    viewer.render();
    syncViewerModeState("complex");
    document.getElementById("pose-select").value = String(pose);
    status.textContent = `绿色为 Vina Pose ${pose}，橙色为 FitDock 姿势。`;
  } catch (error) {
    if (sequence === getDockingViewSequence()) status.textContent = error.message;
  }
}
