"use strict";

// Pure Agent-task event presentation. DOM mutation and task state ownership
// remain in home/main.js; this module only maps backend event data to labels.
window.HomeTaskStatus = (() => {
  const terminalLabels = Object.freeze({
    task_completed: "已完成",
    task_partial: "部分完成",
    task_failed: "失败",
    task_rejected: "已拒绝",
    task_cancelled: "已取消",
  });
  const terminalEvents = new Set(Object.keys(terminalLabels));
  const labels = Object.freeze({
    planning_started: "任务规划",
    planning_completed: "规划完成",
    task_started: "任务开始",
    task_completed: "任务完成",
    task_failed: "任务失败",
    task_partial: "部分完成",
    task_rejected: "已拒绝",
    task_cancelled: "已取消",
    validation_warning: "结果校验提醒",
  });
  const toolNames = Object.freeze({
    property_calculator: "属性计算",
    admet_predictor: "ADMET预测",
    activity_predictor: "活性预测",
    reverse_target_predictor: "反向寻靶",
    target_database_search: "靶点库检索",
    llm_molecular_generator: "分子生成",
    molecular_docking: "分子对接",
  });

  function resolve(event) {
    const eventType = event && (event.event || event.type) || "agent_event";
    const percent = event && typeof event.progress === "number"
      ? Math.round(Math.max(0, Math.min(1, event.progress)) * 100)
      : null;
    return {
      eventType,
      progressText: Object.prototype.hasOwnProperty.call(terminalLabels, eventType)
        ? terminalLabels[eventType]
        : percent !== null ? `${percent}%` : "执行中",
      terminal: terminalEvents.has(eventType),
    };
  }

  function formatToolName(toolName) {
    return toolNames[toolName] || toolName;
  }

  function label(type, toolName) {
    const toolText = toolName ? formatToolName(toolName) : "";
    if (type === "tool_started") return toolText ? `调用 ${toolText}` : "工具调用";
    if (type === "tool_completed") return toolText ? `${toolText} 完成` : "工具完成";
    if (type === "tool_failed") return toolText ? `${toolText} 失败` : "工具失败";
    return labels[type] || "Agent 事件";
  }

  function className(type) {
    if (type && type.includes("failed")) return "is-error";
    if (type === "validation_warning" || type === "task_partial") return "is-warning";
    if (type && type.includes("completed")) return "is-complete";
    return "is-running";
  }

  return Object.freeze({resolve, label, className, formatToolName});
})();
