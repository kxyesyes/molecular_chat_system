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
  const escapedName = functionName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = new RegExp(
    `^([ \\t]*)(?:async\\s+)?function\\s+${escapedName}\\s*\\(`,
    "m",
  ).exec(source);
  assert(match, `Missing ${functionName}`);
  const indentation = match[1].replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const nextDeclaration = new RegExp(
    `^${indentation}(?:async\\s+)?function\\s+[A-Za-z_$][\\w$]*\\s*\\(`,
    "gm",
  );
  nextDeclaration.lastIndex = match.index + match[0].length;
  const nextMatch = nextDeclaration.exec(source);
  return source.slice(match.index, nextMatch ? nextMatch.index : source.length);
}

const extractionProbe = [
  "  function probe() {",
  '    const quoted = "}";',
  "    const templated = `value { still template }`;",
  "    // } ignored comment brace",
  "    /* { ignored block comment brace } */",
  "    return quoted + templated;",
  "  }",
  "  function afterProbe() {}",
].join("\n");
const extractedProbe = extractFunction(extractionProbe, "probe");
assert(
  extractedProbe.includes("return quoted + templated;") &&
    !extractedProbe.includes("function afterProbe"),
  "extractFunction must use declaration boundaries instead of braces in strings, templates, or comments",
);

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

function exerciseChatMessageSink(functionName) {
  class FakeElement {
    constructor(label, isRoot) {
      this.label = label;
      this.isRoot = Boolean(isRoot);
      this.style = {};
      this.children = [];
      this.parentNode = null;
      this._innerHTML = "";
      this._textContent = "";
    }

    set innerHTML(value) {
      this.children.forEach((child) => {
        child.parentNode = null;
      });
      this.children = [];
      this._textContent = "";
      this._innerHTML = String(value);
    }

    get innerHTML() {
      return this._innerHTML;
    }

    set textContent(value) {
      this.children.forEach((child) => {
        child.parentNode = null;
      });
      this.children = [];
      this._innerHTML = "";
      this._textContent = String(value);
    }

    get textContent() {
      return this._textContent;
    }

    appendChild(child) {
      child.parentNode = this;
      this.children.push(child);
      return child;
    }

    append(...children) {
      children.forEach((child) => {
        if (typeof child === "string") {
          const textNode = new FakeElement("#text", false);
          textNode.textContent = child;
          this.appendChild(textNode);
        } else {
          this.appendChild(child);
        }
      });
    }

    removeChild(child) {
      this.children = this.children.filter((candidate) => candidate !== child);
      child.parentNode = null;
    }

    remove() {
      if (this.parentNode) this.parentNode.removeChild(this);
    }

    replaceChildren(...children) {
      this.children.forEach((child) => {
        child.parentNode = null;
      });
      this.children = [];
      this._innerHTML = "";
      this._textContent = "";
      this.append(...children);
    }

    setAttribute(name, value) {
      this[name] = String(value);
    }
  }

  const document = {
    body: new FakeElement("body", true),
    head: new FakeElement("head", true),
    createElement(tagName) {
      return new FakeElement(`<${tagName}>`, false);
    },
    createTextNode(value) {
      const node = new FakeElement("#text", false);
      node.textContent = value;
      return node;
    },
    getElementById() {
      return null;
    },
  };
  const sandbox = {
    console,
    document,
    HomeState: { elements: {} },
    setTimeout() {},
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(helperPath, "utf8"), sandbox, {
    filename: helperPath,
  });
  sandbox.Safe = sandbox.MedChatSafeRender;
  vm.runInContext(homeChatRenderer, sandbox, {
    filename: "src/web/static/js/home/chat_renderer.js",
  });

  const payload = `<img src=x onerror="${functionName}-xss">`;
  sandbox.HomeChatRenderer[functionName](payload, "info");

  function collectNodes(root) {
    const nodes = [root];
    root.children.forEach((child) => nodes.push(...collectNodes(child)));
    return nodes;
  }

  const renderedNodes = [document.body, document.head].flatMap(collectNodes);
  const rawHtmlSinks = renderedNodes.filter((node) =>
    node.innerHTML.includes(payload),
  );
  const safelyRendered =
    renderedNodes.some((node) => node.textContent.includes(payload)) ||
    renderedNodes.some((node) =>
      node.innerHTML.includes(Safe.escapeHtml(payload)),
    );

  return {
    payload,
    rawHtmlSinks,
    safelyRendered,
  };
}

const chatSinkFailures = [];
["showToast", "showNotification"].forEach((functionName) => {
  const result = exerciseChatMessageSink(functionName);
  if (result.rawHtmlSinks.length) {
    chatSinkFailures.push(
      `${functionName} inserted raw message into innerHTML (${result.rawHtmlSinks
        .map((node) => node.label)
        .join(", ")})`,
    );
  }
  if (!result.safelyRendered) {
    chatSinkFailures.push(
      `${functionName} must deliver message through textContent/createTextNode or escaped innerHTML`,
    );
  }
});
assert(chatSinkFailures.length === 0, chatSinkFailures.join("\n"));

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
