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
  const outcomeLabels = Object.freeze({
    timeout: "计算超时",
    unavailable: "工具不可用",
    not_calculated: "尚未计算",
  });
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
    candidate_ranker: "候选排序",
    molecular_docking: "分子对接",
  });

  function displayEventType(type, payload) {
    // Presentation-only variants: the transport still emits tool_completed.
    // main.js selects styling using eventType, without access to the payload.
    if (type === "tool_completed" && payload) {
      if (payload.status === "partial") return "tool_partial";
      if (payload.status === "not_calculated") return "tool_not_calculated";
    }
    return type;
  }

  function notCalculatedLabel(payload) {
    if (payload && payload.status === "not_calculated") {
      const details = payload.error && payload.error.details;
      for (const source of [details, payload.quality]) {
        if (!source || source.reason !== "dependency_blocked" || source.executed !== false) continue;
        return "未执行（依赖未完成）";
      }
    }
    return "尚未计算";
  }

  function outcomeLabel(payload) {
    const details = payload && payload.error && payload.error.details;
    if (payload && payload.status === "timeout" && details
        && details.stage === "allocation" && details.reason === "allocation_timeout") {
      return "任务分配超时";
    }
    return payload && typeof payload.status === "string" ? outcomeLabels[payload.status] : null;
  }

  function resolve(event) {
    const eventType = displayEventType(
      event && (event.event || event.type) || "agent_event", event && event.payload,
    );
    const percent = event && typeof event.progress === "number"
      ? Math.round(Math.max(0, Math.min(1, event.progress)) * 100)
      : null;
    const outcome = outcomeLabel(event && event.payload);
    return {
      eventType,
      progressText: eventType === "tool_partial" ? "部分完成"
        : eventType === "tool_not_calculated" ? notCalculatedLabel(event && event.payload)
        : outcome && eventType === "task_failed"
        ? outcome
        : Object.prototype.hasOwnProperty.call(terminalLabels, eventType)
        ? terminalLabels[eventType]
        : percent !== null ? `${percent}%` : "执行中",
      terminal: terminalEvents.has(eventType),
    };
  }

  function formatToolName(toolName) {
    return toolNames[toolName] || "计算步骤";
  }

  function optionalFailureMessage(text, toolName) {
    const match = /^Optional step [A-Za-z0-9_:.-]{1,128} \(([^()\s]+)\) failed: ([\s\S]*)$/.exec(text);
    return match && match[1] === toolName && Object.prototype.hasOwnProperty.call(toolNames, toolName)
      ? `${toolNames[toolName]}：失败。${match[2]}` : text;
  }

  function rankWarning(text) {
    let match = /^ADMET evidence was absent or unusable for (\d+) ranked candidate\(s\)$/.exec(text);
    if (match) return `${match[1]} 个候选缺少可用的 ADMET 证据。`;
    match = /^Activity evidence was absent, demo, fallback, or unusable for (\d+) ranked candidate\(s\)$/.exec(text);
    return match ? `${match[1]} 个候选缺少可用的活性证据。` : text;
  }

  function message(event) {
    const type = displayEventType(event?.event || event?.type, event?.payload);
    // Lifecycle labels already communicate progress; keep concrete problems,
    // not the executor's internal "Running <step-id>" chatter.
    if (!["tool_partial", "tool_not_calculated", "tool_failed", "task_partial", "task_failed",
      "task_rejected", "task_cancelled", "validation_warning"].includes(type)) return "";
    if (type === "tool_not_calculated") return notCalculatedLabel(event?.payload);
    let text = typeof event?.message === "string" ? event.message : "";
    const toolName = event?.tool || event?.tool_name;
    if (text === "Decision terminal attempt pending verification") return "";
    if (text === 'Observation preparation failed') text = '结果准备或校验失败，无法采用该结果。';
    if (text === 'Decision publication invalidated') text = '结果校验未通过，无法交付本次任务结果。';
    if (type === "validation_warning" && text === "Tool result has validation warnings") {
      const informational = new Set([
        "ADMET-AI outputs are model predictions; they are not experimental results.",
        "Physicochemical endpoints are calculated by RDKit inside ADMET-AI.",
        "Risk summary is a screening heuristic, not a safety conclusion.",
      ]);
      const warnings = Array.isArray(event?.payload?.warnings)
        ? event.payload.warnings.filter(item => typeof item === "string" && !informational.has(item))
        : [];
      const optionalFailures = warnings.filter(item => optionalFailureMessage(item, toolName) !== item);
      const optionalFailureDetails = optionalFailures
        .map(item => /^Optional step [A-Za-z0-9_:.-]{1,128} \(([^()\s]+)\) failed: ([\s\S]*)$/.exec(item))
        .filter(match => match && match[1] === toolName);
      const hasDuplicateFailureReason = optionalFailureDetails.length === 1
        && warnings.includes(optionalFailureDetails[0][2]);
      if (optionalFailureDetails.length === 1
          && (warnings.length === 1 || hasDuplicateFailureReason)) {
        return `${toolNames[toolName] || "计算步骤"}：结果校验未通过；具体原因见失败步骤。`;
      }
      text = warnings.map(item => rankWarning(optionalFailureMessage(item, toolName))).join("；");
    }
    text = optionalFailureMessage(text, toolName);
    for (const label of Object.values(toolNames)) {
      if (text === `${label}：失败。Observation preparation failed`) {
        text = `${label}：失败。结果准备或校验失败，无法采用该结果。`;
      }
    }
    const disclaimer = "；模型预测不等同于实验结论。";
    if ((event?.tool || event?.tool_name) === "activity_predictor" && text.endsWith(disclaimer)) {
      text = text.slice(0, -disclaimer.length) + "。";
    }
    for (const [name, label] of Object.entries(toolNames)) text = text.split(name).join(label);
    return text;
  }

  function label(type, toolName, payload) {
    type = displayEventType(type, payload);
    const toolText = toolName ? formatToolName(toolName) : "";
    if (type === "task_failed" && outcomeLabel(payload)) {
      return outcomeLabel(payload);
    }
    if (type === "tool_started") return toolText ? `调用 ${toolText}` : "工具调用";
    if (type === "tool_partial") return toolText ? `${toolText} 部分完成` : "工具部分完成";
    if (type === "tool_not_calculated") return `${toolText || "工具"} ${notCalculatedLabel(payload)}`;
    if (type === "tool_completed") return toolText ? `${toolText} 完成` : "工具完成";
    if (type === "tool_failed") return toolText ? `${toolText} 失败` : "工具失败";
    return labels[type] || "Agent 事件";
  }

  function className(type) {
    if (type && type.includes("failed")) return "is-error";
    if (type === "task_rejected") return "is-error";
    if (type === "validation_warning" || type === "task_partial") return "is-warning";
    if (type === "task_cancelled") return "is-warning";
    if (type === "tool_partial" || type === "tool_not_calculated") return "is-warning";
    if (type && type.includes("completed")) return "is-complete";
    return "is-running";
  }

  return Object.freeze({resolve, label, className, formatToolName, message});
})();
