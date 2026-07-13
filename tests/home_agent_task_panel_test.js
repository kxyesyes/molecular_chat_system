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

console.log("Homepage agent task panel static checks passed");
