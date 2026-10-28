const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const jsPath = path.join(
  __dirname,
  "..",
  "src",
  "web",
  "static",
  "js",
  "home",
  "main.js"
);
const source = fs.readFileSync(jsPath, "utf8");
const taskStatusSource = fs.readFileSync(
  path.join(__dirname, "..", "src", "web", "static", "js", "home", "task_status.js"),
  "utf8",
);
const template = fs.readFileSync(
  path.join(__dirname, "..", "src", "web", "templates", "index.html"),
  "utf8"
);
assert(template.includes("/static/js/shared/status.js"),
  "homepage must load the shared status contract before the Agent entrypoint");
assert(!source.includes("const decisionStatuses = {"),
  "homepage workflow labels belong to the shared status contract");
assert(!source.includes('["failed", "rejected", "cancelled", "timeout", "unavailable", "not_calculated"]'),
  "homepage must not duplicate the shared non-success status list");
assert(template.includes("/static/js/home/task_status.js"),
  "homepage must load the Agent task status module before the entrypoint");
assert(template.includes("/static/js/home/task_status.js?v=20261027-task-status-dedupe-v2"),
  "optional-failure presentation changes require a refreshed task-status cache key");
assert(template.includes("/static/js/home/task_panel.js"),
  "homepage must load the Agent task panel renderer before the entrypoint");

const taskStatusSandbox = {window: {}, Object, Math};
taskStatusSandbox.globalThis = taskStatusSandbox;
vm.createContext(taskStatusSandbox);
vm.runInContext(taskStatusSource, taskStatusSandbox, {filename: "task_status.js"});
const HomeTaskStatus = taskStatusSandbox.window.HomeTaskStatus;

function extractFunction(functionName) {
  const start = source.indexOf(`function ${functionName}(`);
  assert.notStrictEqual(start, -1, `Missing ${functionName}()`);

  const bodyStart = source.indexOf("{", start);
  let depth = 0;
  for (let index = bodyStart; index < source.length; index += 1) {
    if (source[index] === "{") depth += 1;
    if (source[index] === "}") depth -= 1;
    if (depth === 0) return source.slice(start, index + 1);
  }

  throw new Error(`Could not parse ${functionName}()`);
}

const requiredSnippets = [
  "agentTaskPanel",
  "createAgentTaskPanel",
  "handleAgentEvent",
  'case "agent_event"',
  "agent-task-panel",
];

const missing = requiredSnippets.filter((snippet) => !source.includes(snippet));

if (missing.length) {
  console.error(`Missing homepage agent task panel snippets: ${missing.join(", ")}`);
  process.exit(1);
}

const completionSnippets = [
  "completeLastMessage(message.content);",
  "function completeLastMessage(content, displayOptions)",
  "resolveCompletionAction({",
  "removeTypingIndicator();",
];

const missingCompletionSnippets = completionSnippets.filter(
  (snippet) => !source.includes(snippet)
);

if (missingCompletionSnippets.length) {
  console.error(
    `Missing workflow completion rendering snippets: ${missingCompletionSnippets.join(", ")}`
  );
  process.exit(1);
}

const completionStart = source.indexOf("function completeLastMessage(content, displayOptions)");
const completionEnd = source.indexOf("// 显示状态消息", completionStart);
const completionBody =
  completionStart >= 0 && completionEnd > completionStart
    ? source.slice(completionStart, completionEnd)
    : "";
const requiredCompletionBodySnippets = [
  "removeTypingIndicator();",
  "appendToLastMessage(content);",
  "HomeFormatters.formatContent(content, displayOptions)",
];
const missingCompletionBodySnippets = requiredCompletionBodySnippets.filter(
  (snippet) => !completionBody.includes(snippet)
);

if (missingCompletionBodySnippets.length) {
  console.error(
    `Incomplete workflow completion handler: ${missingCompletionBodySnippets.join(", ")}`
  );
  process.exit(1);
}

const sendStart = source.indexOf("function sendMessage()");
const sendEnd = source.indexOf("// 添加用户消息 - 美化版", sendStart);
const sendBody =
  sendStart >= 0 && sendEnd > sendStart
    ? source.slice(sendStart, sendEnd)
    : "";

if (sendBody.includes("resetAgentTaskPanel();")) {
  console.error("Plain message submission must not create an Agent workflow panel");
  process.exit(1);
}

const eventClassSource = extractFunction("getAgentEventClass");
const helperContext = {window: {HomeTaskStatus}};
const getAgentEventClass = vm.runInNewContext(`(${eventClassSource})`, helperContext);
const eventPresentationSource = extractFunction("resolveAgentEventPresentation");
const resolveAgentEventPresentation = vm.runInNewContext(
  `(${eventPresentationSource})`,
  { getAgentEventClass, window: {HomeTaskStatus} }
);
const agentEventBody = extractFunction("handleAgentEvent");

assert.strictEqual(
  resolveAgentEventPresentation({ type: "tool_started" }).eventType,
  "tool_started",
  "resolver must fall back from event to type"
);
assert.strictEqual(
  resolveAgentEventPresentation({}).eventType,
  "agent_event",
  "resolver must use agent_event when both event fields are absent"
);
assert.strictEqual(
  resolveAgentEventPresentation({
    event: "task_partial",
    type: "task_completed",
  }).eventType,
  "task_partial",
  "resolver must prefer the canonical event field"
);

const terminalTypes = [
  "task_completed",
  "task_failed",
  "task_partial",
  "task_rejected",
  "task_cancelled",
];
for (const eventType of terminalTypes) {
  assert.strictEqual(
    resolveAgentEventPresentation({ event: eventType }).terminal,
    true,
    `${eventType} must terminate the active workflow panel`
  );
}
for (const eventType of ["agent_event", "task_started", "tool_completed"]) {
  assert.strictEqual(
    resolveAgentEventPresentation({ event: eventType }).terminal,
    false,
    `${eventType} must not terminate the active workflow panel`
  );
}

for (const event of ["planning_completed", "tool_completed"]) {
  for (const [progress, progressText] of [
    [undefined, "执行中"], [0.4, "40%"], [1, "100%"],
  ]) {
    const result = resolveAgentEventPresentation({ event, progress });
    assert.strictEqual(result.progressText, progressText,
      `${event} must show intermediate progress, not task completion`);
    assert.strictEqual(result.terminal, false);
    assert.strictEqual(result.itemClass, "is-complete",
      "completed steps must retain their existing styling");
  }
}

const terminalLabels = {
  task_completed: "已完成",
  task_partial: "部分完成",
  task_failed: "失败",
  task_rejected: "已拒绝",
  task_cancelled: "已取消",
};
const terminalClasses = {
  task_completed: "is-complete",
  task_partial: "is-warning",
  task_failed: "is-error",
  task_rejected: "is-error",
  task_cancelled: "is-warning",
};
for (const [event, label] of Object.entries(terminalLabels)) {
  for (const progress of [undefined, 0, 0.4, 1]) {
    const result = resolveAgentEventPresentation({ event, progress });
    assert.strictEqual(result.progressText, label,
      `${event} label must take precedence over progress=${progress}`);
    assert.strictEqual(result.terminal, true);
    assert.strictEqual(result.itemClass, terminalClasses[event]);
  }
}
assert.strictEqual(
  resolveAgentEventPresentation({ event: "task_partial", type: "task_completed", progress: 1 }).progressText,
  "部分完成",
  "the canonical event must also determine the terminal label"
);
assert.strictEqual(
  resolveAgentEventPresentation({ type: "task_cancelled", progress: 1 }).progressText,
  "已取消",
  "terminal labels must also support the legacy type field"
);

for (const event of ["future_event", "task_completed_extra", "constructor", "toString", "__proto__", "hasOwnProperty"]) {
  for (const [progress, progressText] of [
    [undefined, "执行中"], [-0.1, "0%"], [0.4, "40%"], [1.1, "100%"],
  ]) {
    const result = resolveAgentEventPresentation({ event, progress });
    assert.strictEqual(result.progressText, progressText,
      `${event} must fall back to normal progress, not a task-terminal label`);
    assert.strictEqual(result.terminal, false);
  }
}

const formatToolName = vm.runInNewContext(
  `(${extractFunction("formatToolName")})`,
  {window: {HomeTaskStatus}},
);
const getAgentEventLabel = vm.runInNewContext(
  `(${extractFunction("getAgentEventLabel")})`,
  { formatToolName, window: {HomeTaskStatus} }
);
for (const event of ["task_partial", "task_rejected", "task_cancelled"]) {
  assert.strictEqual(getAgentEventLabel(event), terminalLabels[event]);
  assert.strictEqual(getAgentEventLabel(event, "property_calculator"), terminalLabels[event]);
}
assert.strictEqual(getAgentEventLabel("task_completed"), "任务完成");
assert.strictEqual(getAgentEventLabel("task_failed"), "任务失败");
assert.strictEqual(getAgentEventLabel("future_event"), "Agent 事件");
for (const [event, label, toolLabel] of [
  ["tool_started", "工具调用", "调用 属性计算"],
  ["tool_completed", "工具完成", "属性计算 完成"],
  ["tool_failed", "工具失败", "属性计算 失败"],
]) {
  assert.strictEqual(getAgentEventLabel(event), label);
  assert.strictEqual(getAgentEventLabel(event, "property_calculator"), toolLabel);
}

// Exercise the actual handler and state module rather than requiring state
// ownership to remain inside main.js under a particular variable name.
assert(template.indexOf("/static/js/home/task_state.js") >= 0);
assert(template.indexOf("/static/js/home/task_state.js") <
  template.indexOf("/static/js/home/main.js"));
const taskStateSource = fs.readFileSync(
  path.join(__dirname, "..", "src", "web", "static", "js", "home", "task_state.js"),
  "utf8",
);
vm.runInContext(taskStateSource, taskStatusSandbox, {filename: "task_state.js"});
const HomeTaskState = taskStatusSandbox.window.HomeTaskState;

for (const [terminal, label] of Object.entries(terminalLabels)) {
  const panel = {rows: [], progress: "", resets: 0, attributes: {},
    setAttribute(name, value) { this.attributes[name] = value; }};
  let mounted = true;
  let renderSucceeds = true;
  let scrolls = 0;
  const context = {
    window: {HomeTaskState, HomeTaskStatus, HomeTaskPanel: {
      reset(target) { target.rows = []; target.resets += 1; },
      appendEvent(target, row) {
        if (!renderSucceeds) return false;
        target.rows.push(row);
        target.progress = row.progressText;
        return true;
      },
    }},
    document: {},
    agentTaskState: HomeTaskState.create(),
    decisionMode: false,
    createAgentTaskPanel: () => mounted ? panel : null,
    resolveAgentEventPresentation,
    getAgentEventLabel,
    HomeChatRenderer: {scrollToBottom() { scrolls += 1; }},
  };
  vm.createContext(context);
  vm.runInContext(extractFunction("resetAgentTaskPanel"), context);
  vm.runInContext(agentEventBody, context);

  context.handleAgentEvent({event: "task_started"});
  context.handleAgentEvent({event: "planning_completed", progress: 1});
  assert.strictEqual(context.agentTaskState.active, true);
  assert.strictEqual(panel.resets, 1);
  assert.strictEqual(panel.rows.length, 2);
  assert.strictEqual(panel.progress, "100%");
  context.handleAgentEvent({event: "validation_warning", message: "Tool result has validation warnings",
    payload: {warnings: ["Risk summary is a screening heuristic, not a safety conclusion."]}});
  assert.strictEqual(panel.rows.length, 2, "informational-only notices do not add empty warning rows");

  context.handleAgentEvent({event: terminal, progress: 1});
  assert.strictEqual(context.agentTaskState.active, false, terminal);
  assert.strictEqual(context.agentTaskState.lastEventType, terminal);
  assert.strictEqual(panel.progress, label);
  assert.strictEqual(panel.rows.length, 3, "terminal must retain prior steps");

  context.handleAgentEvent({event: "task_started"});
  assert.strictEqual(context.agentTaskState.active, true);
  assert.strictEqual(panel.resets, 2);
  assert.strictEqual(panel.rows.length, 1, "new run must clear old steps");

  const before = context.agentTaskState;
  renderSucceeds = false;
  context.handleAgentEvent({event: terminal});
  assert.strictEqual(context.agentTaskState, before,
    "failed mount must not consume the event or mark completion");
  mounted = false;
  context.handleAgentEvent({event: terminal});
  assert.strictEqual(context.agentTaskState, before);
  assert.strictEqual(scrolls, 4);
}

for (const [status, label] of [["partial", "部分完成"], ["not_calculated", "未执行（依赖未完成）"]]) {
  const payload = {status, success: false, error: status === "partial" ? null : {
    details: {reason: "dependency_blocked", blocked_by: ["reverse"], executed: false},
  }};
  const result = resolveAgentEventPresentation({event: "tool_completed", progress: 1, payload});
  assert.strictEqual(result.terminal, false);
  assert.strictEqual(result.itemClass, "is-warning", `${status} must not look fully successful`);
  assert.strictEqual(result.progressText, label);
  assert.strictEqual(getAgentEventLabel("tool_completed", "activity_predictor", payload), `活性预测 ${label}`);
  assert.strictEqual(getAgentEventLabel(result.eventType, "activity_predictor", payload), `活性预测 ${label}`);
}
const genuineFailure = resolveAgentEventPresentation({event: "tool_failed", payload: {status: "failed"}});
assert.strictEqual(genuineFailure.itemClass, "is-error");
assert.strictEqual(getAgentEventLabel(genuineFailure.eventType, "activity_predictor"), "活性预测 失败");

for (const [details, expected] of [
  [{stage: "allocation", reason: "allocation_timeout"}, "任务分配超时"],
  [{stage: "tool", reason: "timeout"}, "计算超时"],
]) {
  const payload = {status: "timeout", error: {details}};
  const result = resolveAgentEventPresentation({event: "task_failed", payload});
  assert.strictEqual(result.progressText, expected);
  assert.strictEqual(getAgentEventLabel("task_failed", null, payload), expected);
}

for (const metadata of ["details", "quality"]) {
  const cause = {reason: "dependency_blocked", executed: false,
    blocked_by: ["reverse", "C:/private/source.tsv", "<script>alert(1)</script>", "Cookie: session=synthetic-only"]};
  const payload = {status: "not_calculated", success: false};
  if (metadata === "details") payload.error = {details: cause};
  else payload.quality = cause;
  const result = resolveAgentEventPresentation({event: "tool_completed", message: "Generic message", payload});
  assert.strictEqual(result.progressText, "未执行（依赖未完成）");
  assert.strictEqual(getAgentEventLabel(result.eventType, "molecular_docking", payload), "分子对接 未执行（依赖未完成）");
  assert.strictEqual(getAgentEventLabel("tool_failed", "molecular_docking", payload), "分子对接 失败");
}
assert.strictEqual(getAgentEventLabel("tool_completed", "molecular_docking", {status: "not_calculated"}),
  "分子对接 尚未计算");

const informationalWarnings = [
  "ADMET-AI outputs are model predictions; they are not experimental results.",
  "Physicochemical endpoints are calculated by RDKit inside ADMET-AI.",
  "Risk summary is a screening heuristic, not a safety conclusion.",
];
assert.strictEqual(HomeTaskStatus.message({event: "validation_warning",
  message: "Tool result has validation warnings", payload: {warnings: informationalWarnings}}), "");
assert.strictEqual(HomeTaskStatus.message({event: "validation_warning",
  message: "Tool result has validation warnings", payload: {warnings: [...informationalWarnings,
    "分类与回归预测不一致，需复核；已保留两项原始结果。"]}}),
  "分类与回归预测不一致，需复核；已保留两项原始结果。");
assert.strictEqual(HomeTaskStatus.message({event: "tool_completed", tool: "activity_predictor",
  payload: {status: "partial"}, message: "家族活性预测部分完成；含需复核结果；模型预测不等同于实验结论。"}),
  "家族活性预测部分完成；含需复核结果。");
assert.strictEqual(HomeTaskStatus.message({event: "tool_failed", message: "缺少所选靶点的模型权重"}),
  "缺少所选靶点的模型权重");
const optionalStep = "web-" + "a".repeat(64);
const optionalReason = "缺少所选靶点的模型权重；该候选未参与排序。";
const optionalWarning = `Optional step ${optionalStep} (activity_predictor) failed: ${optionalReason}`;
assert.strictEqual(HomeTaskStatus.message({event: "validation_warning", tool: "activity_predictor",
  message: "Tool result has validation warnings", payload: {warnings: [optionalWarning, "端点 X 缺失。"]}}),
  `活性预测：失败。${optionalReason}；端点 X 缺失。`);
assert.strictEqual(HomeTaskStatus.message({event: "validation_warning", tool: "activity_predictor",
  message: "Tool result has validation warnings", payload: {warnings: [optionalWarning]}}),
  "活性预测：结果校验未通过；具体原因见失败步骤。");
assert.strictEqual(HomeTaskStatus.message({event: "validation_warning", tool: "activity_predictor",
  message: "Tool result has validation warnings", payload: {warnings: [optionalReason, optionalWarning]}}),
  "活性预测：结果校验未通过；具体原因见失败步骤。");
assert.strictEqual(HomeTaskStatus.message({event: "tool_failed", tool: "activity_predictor",
  message: optionalWarning}), `活性预测：失败。${optionalReason}`);
assert(HomeTaskStatus.message({event: "validation_warning", tool: "admet_predictor",
  message: "Tool result has validation warnings", payload: {warnings: [optionalWarning]}}).includes(optionalStep),
  "a warning for a different tool must not be reinterpreted as this event's wrapper");

console.log("Homepage agent task panel static and lifecycle checks passed");
