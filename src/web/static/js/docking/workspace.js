// A cavity belongs to the uploaded receptor, independently of the displayed pose.
let dockingCavities = [];
let cavityReceptor = null;
let regionEdit = 0;

function clearDockingCavities() {
  regionEdit++;
  cavityReceptor = null;
  dockingCavities = [];
  const select = document.getElementById("cavity-choice");
  select.replaceChildren(new Option("自定义区域", ""));
  document.getElementById("cavity-status").textContent = "";
}

async function detectDockingCavities(button, preserveRegion = false) {
  const input = document.getElementById("protein-file");
  const file = input.files[0];
  const status = document.getElementById("cavity-status");
  clearDockingCavities();
  const startedAtEdit = regionEdit;
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
    if (dockingCavities.length) {
      status.textContent = "";
      if (!preserveRegion && regionEdit === startedAtEdit && !document.getElementById("start-btn").disabled) {
        select.value = "0";
        applyDockingCavity(select);
      }
    } else {
      status.textContent = "未识别到候选空腔，可输入自定义区域。";
    }
  } catch (error) {
    if (cavityReceptor === file) status.textContent = error.message;
  } finally {
    if (cavityReceptor === file || !cavityReceptor) button.disabled = false;
  }
}

function applyDockingCavity(select) {
  regionEdit++;
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
      regionEdit++;
      document.getElementById("cavity-choice").value = "";
    });
  }
});
