const fs = require("fs");
const path = require("path");

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

const agentEventStart = source.indexOf("function handleAgentEvent(event)");
const agentEventEnd = source.indexOf("function getAgentEventLabel", agentEventStart);
const agentEventBody =
  agentEventStart >= 0 && agentEventEnd > agentEventStart
    ? source.slice(agentEventStart, agentEventEnd)
    : "";
const requiredLazyPanelSnippets = [
  "if (!agentTaskRunActive)",
  "resetAgentTaskPanel();",
  "event.event || event.type",
  "agentTaskRunActive = false;",
  '"task_partial"',
  '"task_rejected"',
  '"task_cancelled"',
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
