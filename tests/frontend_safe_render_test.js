const fs = require("fs");
const path = require("path");
const vm = require("vm");

const root = path.join(__dirname, "..");

function read(relPath) {
  return fs.readFileSync(path.join(root, relPath), "utf8");
}

function fail(message) {
  console.error(message);
  process.exit(1);
}

function assert(condition, message) {
  if (!condition) fail(message);
}

const helperPath = path.join(
  root,
  "src",
  "web",
  "static",
  "js",
  "shared",
  "safe_render.js",
);
assert(fs.existsSync(helperPath), "Missing shared safe_render.js helper");

const sandbox = { window: {}, console };
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(helperPath, "utf8"), sandbox, {
  filename: helperPath,
});

const Safe = sandbox.window.MedChatSafeRender || sandbox.MedChatSafeRender;
assert(Safe, "safe_render.js must expose window.MedChatSafeRender");

const payload = `<img src=x onerror="alert(1)">&'"`;
assert(
  Safe.escapeHtml(payload) ===
    "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;&amp;&#039;&quot;",
  "escapeHtml must encode HTML-sensitive characters",
);
assert(
  Safe.safeUrl("javascript:alert(1)") === "#",
  "safeUrl must block javascript: URLs",
);
assert(
  Safe.safeUrl("https://example.com/a?q=1") === "https://example.com/a?q=1",
  "safeUrl must keep http/https URLs",
);
assert(
  Safe.escapeInlineJsString("job');alert(1)//") === "job\\&#039;);alert(1)//",
  "escapeInlineJsString must protect single-quoted inline handlers",
);
assert(
  Safe.escapeCssIdent('job"\\]') === 'job\\"\\\\]',
  "escapeCssIdent must protect quoted CSS attribute selectors",
);

const indexHtml = read("src/web/templates/index.html");
const designHtml = read("src/web/templates/molecular_design.html");
const dockingHtml = read("src/web/templates/molecular_docking.html");

assert(
  indexHtml.indexOf("/static/js/shared/safe_render.js") <
    indexHtml.indexOf("/static/js/home/formatters.js"),
  "Homepage must load safe_render.js before home formatters",
);
assert(
  designHtml.indexOf("/static/js/shared/safe_render.js") <
    designHtml.indexOf("/static/js/design/main.js"),
  "Molecular design must load safe_render.js before design main",
);
assert(
  dockingHtml.indexOf("/static/js/shared/safe_render.js") <
    dockingHtml.indexOf("/static/js/docking/ui_manager.js"),
  "Docking page must load safe_render.js before docking ui_manager",
);

const homeFormatters = read("src/web/static/js/home/formatters.js");
const homeMain = read("src/web/static/js/home/main.js");
const homeChatRenderer = read("src/web/static/js/home/chat_renderer.js");

function extractFunction(source, functionName) {
  const marker = `function ${functionName}`;
  const start = source.indexOf(marker);
  assert(start >= 0, `Missing ${functionName}`);
  const braceStart = source.indexOf("{", start);
  let depth = 0;
  for (let i = braceStart; i < source.length; i += 1) {
    if (source[i] === "{") depth += 1;
    if (source[i] === "}") {
      depth -= 1;
      if (depth === 0) return source.slice(start, i + 1);
    }
  }
  fail(`Could not parse ${functionName}`);
}

assert(
  homeFormatters.includes("MedChatSafeRender"),
  "Homepage formatters must use the shared safe rendering helper",
);
assert(
  !homeFormatters.includes('href="$2"'),
  "Homepage markdown links must not inject raw href attributes",
);
assert(
  !homeMain.includes("<strong>${file.name}</strong>"),
  "Homepage file manager must not inject raw file.name into innerHTML",
);
assert(
  homeMain.includes("fileName.textContent = file.name"),
  "Homepage file manager must render file.name through textContent",
);
assert(
  !homeMain.includes("notification.innerHTML = `<span>${icons[type]}</span><span>${message}</span>`"),
  "Homepage notifications must not concatenate raw message into innerHTML",
);
assert(
  homeMain.includes("messageSpan.textContent = message"),
  "Homepage notifications must render message through textContent",
);
["showStatusMessage", "showToolStatus"].forEach((functionName) => {
  const body = extractFunction(homeMain, functionName);
  assert(
    !body.includes(".innerHTML"),
    `${functionName} must not use innerHTML for runtime status text`,
  );
  assert(
    body.includes(".textContent = message"),
    `${functionName} must render runtime status text with textContent`,
  );
});
[
  "formatReactionPrediction",
  "formatADMETResults",
  "formatReActResults",
].forEach((functionName) => {
  const body = extractFunction(homeMain, functionName);
  assert(
    body.includes("escapeHtml(content)") || body.includes("safeContent"),
    `${functionName} must escape raw content before legacy HTML formatting`,
  );
  assert(
    !body.includes("return content.replace"),
    `${functionName} fallback must not return raw content.replace(...) HTML`,
  );
});

["showToast", "showNotification"].forEach((functionName) => {
  const body = extractFunction(homeChatRenderer, functionName);
  assert(
    !body.includes("<span>${message}</span>"),
    `${functionName} must not inject raw message into innerHTML`,
  );
  assert(
    body.includes(".textContent = message") ||
      body.includes("Safe.escapeHtml(message"),
    `${functionName} must render message through textContent or the shared safe helper`,
  );
});

const designMain = read("src/web/static/js/design/main.js");
assert(
  !designMain.includes('modeBadge + "<br>" + d.reply'),
  "Design AI reply must not concatenate raw backend text into innerHTML",
);
assert(
  designMain.includes("Safe.escapeHtml(d.reply"),
  "Design AI reply must escape backend text with the shared helper",
);

const dockingUi = read("src/web/static/js/docking/ui_manager.js");
[
  "Safe.escapeHtml(file.name",
  "Safe.escapeInlineJsString(inputId",
  "Safe.escapeInlineJsString(displayId",
  "Safe.escapeHtml(item.job_id",
  "Safe.escapeAttr(item.job_id",
  "Safe.escapeInlineJsString(item.job_id",
  "Safe.escapeHtml(error.message",
].forEach((snippet) => {
  assert(
    dockingUi.includes(snippet),
    `Docking UI must use shared safe helper for ${snippet}`,
  );
});
[
  "<div style=\"font-size: 14px;\">${error.message}</div>",
  "onclick=\"downloadResults('${data.job_id}')",
  "onclick=\"generateReport('${data.job_id}')",
  "onclick=\"viewBatchLigand('${item.job_id}')",
  "onclick=\"downloadResults('${item.job_id}')",
  "`.history-card[data-jobid=\"${jobId}\"]`",
].forEach((snippet) => {
  assert(
    !dockingUi.includes(snippet),
    `Docking UI must not concatenate unsafe runtime values: ${snippet}`,
  );
});
[
  "Safe.escapeInlineJsString(data.job_id",
  "Safe.escapeInlineJsString(item.job_id",
  "Safe.escapeCssIdent(jobId",
].forEach((snippet) => {
  assert(
    dockingUi.includes(snippet),
    `Docking UI must protect dynamic job identifiers with ${snippet}`,
  );
});

console.log("Frontend safe rendering static XSS checks passed");
