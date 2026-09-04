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
  "function completeLastMessage(content)",
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

const completionStart = source.indexOf("function completeLastMessage(content)");
const completionEnd = source.indexOf("// 显示状态消息", completionStart);
const completionBody =
  completionStart >= 0 && completionEnd > completionStart
    ? source.slice(completionStart, completionEnd)
    : "";
const requiredCompletionBodySnippets = [
  "removeTypingIndicator();",
  "appendToLastMessage(content);",
  "HomeFormatters.formatContent(content)",
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
const getAgentEventClass = vm.runInNewContext(`(${eventClassSource})`);
const eventPresentationSource = extractFunction("resolveAgentEventPresentation");
const resolveAgentEventPresentation = vm.runInNewContext(
  `(${eventPresentationSource})`,
  { getAgentEventClass }
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

const requiredLazyPanelSnippets = [
  "const presentation = resolveAgentEventPresentation(event);",
  "presentation.eventType",
  "if (!agentTaskRunActive)",
  "resetAgentTaskPanel();",
  "presentation.itemClass",
  "presentation.progressText",
  "presentation.terminal",
  "agentTaskRunActive = false;",
];
const missingLazyPanelSnippets = requiredLazyPanelSnippets.filter(
  (snippet) => !agentEventBody.includes(snippet)
);

if (missingLazyPanelSnippets.length) {
  console.error(
    `Agent panel is not driven by real workflow lifecycle: ${missingLazyPanelSnippets.join(", ")}`
  );
  process.exit(1);
}

console.log("Homepage agent task panel static checks passed");
