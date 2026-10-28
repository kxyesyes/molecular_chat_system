"use strict";

// DOM-only Agent task panel renderer. Runtime state and event ownership stay
// in home/main.js; this module never decides whether a task succeeded.
window.HomeTaskPanel = {
  create(container, ownerDocument = window.document, {reuse = true} = {}) {
    if (!container) return null;
    const existing = reuse && container.querySelector?.(".agent-task-panel");
    if (existing) return existing;

    const panel = ownerDocument.createElement("div");
    panel.className = "agent-task-panel";
    const header = ownerDocument.createElement("div");
    header.className = "agent-task-header";
    const copy = ownerDocument.createElement("div");
    const title = ownerDocument.createElement("div");
    title.className = "agent-task-title";
    title.textContent = "智能任务执行";
    copy.appendChild(title);
    const progress = ownerDocument.createElement("div");
    progress.className = "agent-task-progress";
    progress.textContent = "准备中";
    header.appendChild(copy);
    header.appendChild(progress);
    const list = ownerDocument.createElement("div");
    list.className = "agent-task-list";
    panel.appendChild(header);
    panel.appendChild(list);
    container.appendChild(panel);
    return panel;
  },

  reset(panel) {
    if (!panel) return;
    panel.classList.add("is-active");
    const progress = panel.querySelector?.(".agent-task-progress");
    const list = panel.querySelector?.(".agent-task-list");
    if (progress) progress.textContent = "执行中";
    if (list) {
      if (typeof list.replaceChildren === "function") list.replaceChildren();
      else list.textContent = "";
    }
  },

  showWaiting(panel) {
    if (!panel) return;
    const progress = panel.querySelector?.(".agent-task-progress");
    if (progress) progress.textContent = "等待补充输入";
    // The caller owns the trace and verified that the last lifecycle event
    // was a provisional rejection, now superseded by a valid continuation.
    const item = panel.querySelector?.(".agent-task-list")?.lastChild;
    if (!item) return;
    item.className = "agent-task-item is-warning";
    const heading = item.querySelector("strong");
    const detail = item.querySelector(".agent-task-copy span");
    if (heading) heading.textContent = "等待补充输入";
    if (detail) detail.textContent = "请补充所需信息后继续当前任务。";
  },

  appendEvent(panel, {label, message, itemClass, progressText}, ownerDocument = window.document) {
    if (!panel) return false;
    const list = panel.querySelector?.(".agent-task-list");
    if (!list) return false;
    panel.classList.add("is-active");

    const item = ownerDocument.createElement("div");
    item.className = `agent-task-item ${itemClass || "is-running"}`;
    const dot = ownerDocument.createElement("span");
    dot.className = "agent-task-dot";
    const copy = ownerDocument.createElement("div");
    copy.className = "agent-task-copy";
    const heading = ownerDocument.createElement("strong");
    heading.textContent = label;
    copy.appendChild(heading);
    if (message && message !== label) {
      const detail = ownerDocument.createElement("span");
      detail.textContent = message;
      copy.appendChild(detail);
    }
    item.appendChild(dot);
    item.appendChild(copy);
    list.appendChild(item);

    const progress = panel.querySelector?.(".agent-task-progress");
    if (progress) progress.textContent = progressText;
    return true;
  },
};
