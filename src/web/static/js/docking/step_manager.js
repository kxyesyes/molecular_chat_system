// ==================== 步骤管理器 ====================
class DockingStepManager {
  constructor() {
    this.currentStep = 0;
    this.steps = DOCKING_STEPS;
    this.stepElements = [];
    this.createStepElements();
    this.updateOverallProgress();
  }

  createStepElements() {
    const container = document.getElementById("steps-list");
    if (!container) return;
    container.innerHTML = "";
    this.steps.forEach((step) => {
      const el = document.createElement("div");
      el.className = "step-item";
      el.id = `step-${step.id}`;
      el.innerHTML = `
        <div class="step-icon">${step.icon}</div>
        <div class="step-content">
          <div class="step-title">${step.title}</div>
          <div class="step-description">${step.description}</div>
          <div class="step-details">${step.details}</div>
          <div class="step-progress"><div class="step-progress-bar" id="progress-${step.id}"></div></div>
        </div>
      `;
      container.appendChild(el);
      this.stepElements.push(el);
    });
  }

  startStep(stepId) {
    const idx = this.steps.findIndex((s) => s.id === stepId);
    if (idx === -1) return;
    this.stepElements.forEach((el) => el.classList.remove("active"));
    this.stepElements[idx].classList.add("active");
    this.currentStep = idx;
    this.updateOverallProgress();
  }

  completeStep(stepId, details = "") {
    const idx = this.steps.findIndex((s) => s.id === stepId);
    if (idx === -1) return;
    const el = this.stepElements[idx];
    el.classList.remove("active");
    el.classList.add("completed");
    if (details) {
      const d = el.querySelector(".step-details");
      if (d) d.textContent = details;
    }
    const bar = el.querySelector(".step-progress-bar");
    if (bar) bar.style.width = "100%";
    this.updateOverallProgress();
  }

  errorStep(stepId, message) {
    const idx = this.steps.findIndex((s) => s.id === stepId);
    if (idx === -1) return;
    const el = this.stepElements[idx];
    el.classList.remove("active");
    el.classList.add("error");
    const d = el.querySelector(".step-details");
    if (d) d.textContent = `错误: ${message}`;

    if (window.showStepLog) {
      window.showStepLog(stepId, `Failed！ - ${message}`);
    }
  }

  updateStepProgress(stepId, p) {
    const bar = document.getElementById(`progress-${stepId}`);
    if (bar) bar.style.width = `${Math.max(0, Math.min(100, p))}%`;
  }

  updateOverallProgress() {
    const done = this.stepElements.filter((el) =>
      el.classList.contains("completed"),
    ).length;
    const pct = Math.round((done / this.steps.length) * 100);
    const t = document.getElementById("overall-progress-text");
    if (t) t.textContent = `${pct}%`;
  }

  show() {
    const c = document.getElementById("steps-container");
    if (c) c.style.display = "none";
    const log = document.getElementById("progress-log");
    if (log) log.style.display = "block";
    const pc = document.querySelector(".progress-container");
    if (pc) pc.style.display = "block";
  }

  hide() {
    const c = document.getElementById("steps-container");
    if (c) c.style.display = "none";
  }

  reset() {
    this.currentStep = 0;
    this.stepElements = [];
    this.createStepElements();
    this.updateOverallProgress();
  }
}
