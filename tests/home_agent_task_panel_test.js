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

console.log("Homepage agent task panel static checks passed");
