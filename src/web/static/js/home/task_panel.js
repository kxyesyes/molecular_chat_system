"use strict";

// DOM-only Agent task panel renderer. Runtime state and event ownership stay
// in home/main.js; this module never decides whether a task succeeded.
window.HomeTaskPanel = {
  create(container, ownerDocument = window.document) {
    if (!container) return null;
    const existing = container.querySelector?.(".agent-task-panel");
    if (existing) return existing;

    const panel = ownerDocument.createElement("div");
    panel.className = "agent-task-panel";
    const header = ownerDocument.createElement("div");
    header.className = "agent-task-header";
    const copy = ownerDocument.createElement("div");
    const kicker = ownerDocument.createElement("div");
    kicker.className = "agent-task-kicker";
    kicker.textContent = "AGENT WORKFLOW";
    const title = ownerDocument.createElement("div");
    title.className = "agent-task-title";
    title.textContent = "智能任务执行";
    copy.appendChild(kicker);
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
    const detail = ownerDocument.createElement("span");
    detail.textContent = message;
    copy.appendChild(heading);
    copy.appendChild(detail);
    item.appendChild(dot);
    item.appendChild(copy);
    list.appendChild(item);

    const progress = panel.querySelector?.(".agent-task-progress");
    if (progress) progress.textContent = progressText;
    return true;
  },
};
