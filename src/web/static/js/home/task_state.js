"use strict";

// Pure Agent-task runtime state. Connection/protocol modules supply events and
// task_panel.js renders them; this module owns only the small state transition
// needed to start, continue, and finish one task run.
window.HomeTaskState = (() => {
  function create() {
    return {
      active: false,
      lastEventType: null,
      progressText: "准备中",
    };
  }

  function reset(state) {
    void state;
    return {
      active: true,
      lastEventType: null,
      progressText: "执行中",
    };
  }

  function record(state, presentation) {
    if (!presentation || typeof presentation !== "object") {
      throw new TypeError("presentation is required");
    }
    const previous = state && typeof state === "object" ? state : create();
    const freshRun = previous.active ? previous : reset(previous);
    return {
      active: !Boolean(presentation.terminal),
      lastEventType: presentation.eventType || null,
      progressText: presentation.progressText || freshRun.progressText,
    };
  }

  return Object.freeze({create, reset, record});
})();
