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

const sandbox = { window: {}, console, URL };
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
  Safe.safeUrl("//evil.example/redirect") === "#",
  "safeUrl must block protocol-relative external URLs",
);
assert(
  Safe.safeUrl("https://example.com/a?q=1") === "https://example.com/a?q=1",
  "safeUrl must keep http/https URLs",
);
assert(
  Safe.safeExternalUrl("https://www.rcsb.org/structure/1ABC") ===
    "https://www.rcsb.org/structure/1ABC",
  "safeExternalUrl must allow trusted HTTPS scientific sources",
);
assert(
  Safe.safeChatUrl("https://example.com/redirect") === "#",
  "safeChatUrl must reject untrusted external domains",
);
assert(
  Safe.safeChatUrl("https://www.rcsb.org/structure/1ABC") ===
    "https://www.rcsb.org/structure/1ABC",
  "safeChatUrl must allow trusted scientific domains",
);
assert(
  Safe.safeChatUrl("/target-search") === "/target-search",
  "safeChatUrl must allow same-origin relative paths",
);
assert(
  Safe.safeExternalUrl("https://evil.example/redirect") === "#" &&
    Safe.safeExternalUrl("http://www.rcsb.org/structure/1ABC") === "#" &&
    Safe.safeExternalUrl("mailto:research@example.org") === "#",
  "safeExternalUrl must reject untrusted, insecure and mailto destinations",
);
assert(
  Safe.escapeInlineJsString("job');alert(1)//") === "job\\&#039;);alert(1)//",
  "escapeInlineJsString must protect single-quoted inline handlers",
);
assert(
  Safe.escapeCssIdent('job"\\]') === 'job\\"\\\\]',
  "escapeCssIdent must protect quoted CSS attribute selectors",
);
assert(
  typeof Safe.appendSafeSvg === "function",
  "safe_render.js must expose the DOM-based SVG sanitizer",
);

const indexHtml = read("src/web/templates/index.html");
const designHtml = read("src/web/templates/molecular_design.html");
const dockingHtml = read("src/web/templates/molecular_docking.html");
const targetSearchHtml = read("src/web/templates/target_search.html");
const targetSearchMain = read("src/web/static/js/target_search/main.js");
const reverseTargetHtml = read("src/web/templates/reverse_target.html");
const reverseResults = read("src/web/static/js/reverse_target/results_renderer.js");

assert(
  indexHtml.indexOf("/static/js/shared/safe_render.js") <
    indexHtml.indexOf("/static/js/home/formatters.js"),
  "Homepage must load safe_render.js before home formatters",
);
assert(
  indexHtml.includes("/static/js/shared/safe_render.js?v=20261007-url-policy-v2") &&
    indexHtml.includes("/static/js/home/formatters.js?v=20261007-chat-url-policy-v1"),
  "Homepage must invalidate cached chat URL policy scripts",
);
assert(
  indexHtml.indexOf("/static/js/shared/safe_render.js") <
    indexHtml.indexOf("/static/js/home/molecule_candidates.js?v=") &&
    indexHtml.indexOf("/static/js/home/molecule_candidates.js?v=") <
      indexHtml.indexOf("/static/js/home/main.js?v="),
  "Homepage must load the versioned molecule helper after shared safety and before versioned main",
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
assert(
  targetSearchHtml.indexOf("/static/js/shared/safe_render.js") <
    targetSearchHtml.indexOf("/static/js/target_search/main.js"),
  "Target search must load safe_render.js before target_search main",
);
assert(
  targetSearchMain.includes("hydrateExternalLinks") &&
    targetSearchMain.includes("trustedExternalUrlValue(link && link.url)") &&
    targetSearchMain.includes('anchor.textContent = String'),
  "Target search external links must use DOM APIs and the trusted URL policy",
);
assert(
  targetSearchHtml.includes('/static/js/target_search/main.js?v=20261007-safe-dom-v4'),
  "Target search must invalidate the cached script after safety changes",
);
assert(
  targetSearchMain.includes("escapeInlineJsString") &&
    targetSearchMain.includes("inlineJsArg(item.target_id)") &&
    targetSearchMain.includes("inlineJsArg(item.id)") &&
    !targetSearchMain.includes("loadDetail(${item.target_id})") &&
    !targetSearchMain.includes("downloadStructure(${item.id},") &&
    !targetSearchMain.includes("escapeAttr(item.family)") &&
    !targetSearchMain.includes("escapeAttr(item.gene_symbol)") &&
    !targetSearchMain.includes("escapeAttr(item.file_format"),
  "Target search inline handlers must use the shared JavaScript string policy",
);
assert(
  reverseTargetHtml.indexOf("/static/js/shared/safe_render.js") <
    reverseTargetHtml.indexOf("/static/js/reverse_target/results_renderer.js"),
  "Reverse target results must load the shared rendering policy first",
);
assert(
  reverseResults.includes("appendSafeSvg") &&
    !reverseResults.includes("${mcsData.query_svg || \"\"}") &&
    !reverseResults.includes("${mcsData.hit_svg || \"\"}"),
  "MCS SVG must be parsed through the SVG allowlist before DOM insertion",
);
assert(
  reverseResults.includes("setSameOriginImage") &&
    reverseResults.includes("parsed.origin !== window.location.origin") &&
    !reverseResults.includes("src=\"${escapeHtml(RtApi.smilesImageUrl(molSmiles, 360, 300))}\""),
  "Reverse-target structure image URLs must be same-origin DOM properties",
);
assert(
  reverseResults.includes('data-field="query-mw"') &&
    reverseResults.includes("queryLegend.querySelector('[data-field=\"query-mw\"]').textContent") &&
    !reverseResults.includes('${qp.properties?.MW || "-"}'),
  "Reverse-target query pharmacophore properties must use textContent",
);
assert(
  reverseResults.includes("modalBody.textContent = `加载失败:") &&
    !reverseResults.includes("modalBody.innerHTML = `<div style=\"text-align:center;padding:40px;color:#ef4444;\">加载失败"),
  "Reverse-target request errors must use textContent instead of HTML parsing",
);
const reverseViewer = read("src/web/static/js/reverse_target/viewer_3d.js");
assert(
  reverseViewer.includes("info.textContent = `❌") &&
    !reverseViewer.includes("info.innerHTML = `<div style=\"color:#ef4444;padding:20px;text-align:center;\">"),
  "3D viewer errors must use textContent instead of HTML parsing",
);
assert(
  reverseViewer.includes("function safeColor") &&
    reverseViewer.includes("safeColor(f.color)") &&
    !reverseViewer.includes("${f.color}") &&
    !reverseViewer.includes("<div class=\"feat-icon\" style=\"background:${f.color}"),
  "3D feature colors must pass the style allowlist",
);
assert(
  reverseResults.includes("modalBody.replaceChildren(_buildSimilarModalContent(data.results))") &&
    reverseResults.includes("smilesNode.textContent = molSmiles") &&
    !reverseResults.includes("${escapeHtml(molSmiles)}") &&
    !reverseResults.includes("${escapeHtml(RtApi.smilesImageUrl(molSmiles, 360, 300))}"),
  "Similar-molecule SMILES and image URLs must be assigned through DOM APIs",
);
assert(
  reverseResults.includes("rowBindings") &&
    reverseResults.includes("smilesCell.textContent = binding.querySmiles") &&
    reverseResults.includes("errorCell.textContent = binding.errorText") &&
    !reverseResults.includes("${escapeHtml(querySmiles)}") &&
    !reverseResults.includes("${escapeHtml(errorText)}"),
  "Batch reverse-target SMILES and errors must be assigned through DOM APIs",
);
assert(
  reverseResults.includes("mcsSmart.textContent = String(mcsData.mcs_smarts || \"\")") &&
    !reverseResults.includes("${escapeHtml(mcsData.mcs_smarts || \"\")}"),
  "MCS SMARTS must be rendered as text rather than HTML",
);
const reverseUi = read("src/web/static/js/reverse_target/ui_manager.js");
assert(
  reverseUi.includes('statItems.forEach((item) => {') &&
    reverseUi.includes('item.textContent = "加载失败"') &&
    !reverseUi.includes("statItems[0].innerHTML = errorHtml"),
  "Reverse-target statistics errors must use textContent",
);
assert(
  targetSearchMain.includes("function renderInlineError") &&
    targetSearchMain.includes("errorBox.textContent = String(message") &&
    !targetSearchMain.includes("detail.innerHTML = `<div class=\"detail-empty\"><strong>详情加载失败</strong><span>${escapeHtml(error.message)}</span></div>`"),
  "Target search errors must use DOM text nodes instead of HTML parsing",
);

const homeFormatters = read("src/web/static/js/home/formatters.js");
const homeMain = read("src/web/static/js/home/main.js");
const homeChatRenderer = read("src/web/static/js/home/chat_renderer.js");
const homeMoleculeCandidates = read(
  "src/web/static/js/home/molecule_candidates.js",
);

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
  homeFormatters.includes("Safe.safeChatUrl(url)"),
  "Homepage markdown links must use the restricted chat URL policy",
);
assert(
  homeMoleculeCandidates.includes("HomeMoleculeCandidates") &&
    homeMoleculeCandidates.includes("cloneJson"),
  "Homepage molecule events must pass through a fresh structured normalizer",
);
assert(
  homeMoleculeCandidates.includes("const ObjectPrototype = Object.prototype") &&
    homeMoleculeCandidates.includes("const ArrayPrototype = Array.prototype") &&
    !homeMoleculeCandidates.includes("Object.prototype.toString"),
  "Molecule normalization must use captured intrinsic prototype identity",
);
assert(
  homeMain.includes("HomeMoleculeCandidates.createLifecycle") &&
    homeMain.includes("maxWebSocketMessageLength") &&
    homeMain.indexOf("data.length > maxWebSocketMessageLength") <
      homeMain.indexOf("JSON.parse(data)"),
  "Homepage candidate handling must be request-scoped and bound raw messages before parsing",
);
assert(
  homeMoleculeCandidates.includes("canonicalSmiles: new Set()") &&
    homeMain.includes("const socket = new WebSocket(wsUrl)") &&
    homeMain.includes("protocolDesyncedSocket"),
  "Candidate rendering must dedupe canonical identities and correlate protocol state to one socket",
);
assert(
  !homeMain.includes("detectAndRenderMolecules") &&
    !homeMain.includes("fetchMoleculePropertiesForToolMolecule") &&
    !homeMain.includes("/api/molecule/properties"),
  "Homepage must not scan assistant prose or retain the dead molecule properties path",
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

const errorMessageBody = extractFunction(homeMain, "showErrorMessage");
assert(
  !errorMessageBody.includes(".innerHTML"),
  "showErrorMessage must not concatenate runtime error text into innerHTML",
);
assert(
  errorMessageBody.includes("messageSpan.textContent = String(message ?? \"\")"),
  "showErrorMessage must render runtime error text through textContent",
);
assert(
  !homeMain.includes("${smiles}\n        </div>"),
  "Homepage molecule cards must not inject raw SMILES into HTML",
);
assert(
  homeMain.includes("smilesValue.textContent = smiles"),
  "Homepage molecule cards must render SMILES through textContent",
);
assert(
  !homeMain.includes("${reactants}") && !homeMain.includes("${products}"),
  "Synthesis route cards must not inject raw reaction structures into HTML",
);
assert(
  !homeMain.includes("${infoMessage || `共找到"),
  "RAG result headers must not inject raw backend status text into HTML",
);
assert(
  homeMain.includes("escapeHtml(infoMessage || `共找到"),
  "RAG result headers must escape backend status text",
);
assert(
  homeMain.includes("${escapeHtml(propertyLabels[prop] || prop)}"),
  "Molecule property labels must be escaped before HTML insertion",
);
assert(
  !homeMain.includes("connectionStatus.innerHTML"),
  "Homepage connection status must not use innerHTML",
);

const homeChatRendererSource = read("src/web/static/js/home/chat_renderer.js");
assert(
  !homeChatRendererSource.includes("connectionStatus.innerHTML"),
  "Connection status must not use innerHTML",
);
assert(
  homeChatRendererSource.includes("connectionStatus.textContent"),
  "Connection status must render through textContent",
);
[
  "formatSynthesisRoute",
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

const executableUrlAttributes = new Set([
  "formaction",
  "href",
  "src",
  "srcset",
  "xlink:href",
]);

function findExecutableAttributeViolations(nodes, payload) {
  const violations = [];

  nodes.forEach((node) => {
    const candidates = new Map();
    Object.entries(node.attributes || {}).forEach(([name, value]) => {
      candidates.set(name.toLowerCase(), String(value));
    });
    Object.keys(node).forEach((name) => {
      const normalizedName = name.toLowerCase();
      if (
        normalizedName === "srcdoc" ||
        normalizedName.startsWith("on") ||
        executableUrlAttributes.has(normalizedName)
      ) {
        candidates.set(normalizedName, String(node[name]));
      }
    });

    candidates.forEach((value, name) => {
      const isExecutableAttribute =
        name === "srcdoc" ||
        name.startsWith("on") ||
        executableUrlAttributes.has(name);
      const unsafeUrl =
        executableUrlAttributes.has(name) &&
        /^(?:javascript:|data:\s*text\/html)/i.test(value.trim());
      if (isExecutableAttribute && (value.includes(payload) || unsafeUrl)) {
        violations.push({ node, name, value });
      }
    });
  });

  return violations;
}

function exerciseChatMessageSink(functionName, options) {
  class FakeElement {
    constructor(label, isRoot) {
      this.label = label;
      this.isRoot = Boolean(isRoot);
      this.style = {};
      this.children = [];
      this.parentNode = null;
      this.attributes = {};
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
      this.attributes[name] = String(value);
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

  const scenarioName = functionName || "attribute-probe";
  const payload = `<img src=x onerror="${scenarioName}-xss">`;
  if (options && options.attributeProbe) {
    const text = document.createElement("span");
    text.textContent = payload;
    const frame = document.createElement("iframe");
    frame.srcdoc = payload;
    const button = document.createElement("button");
    button.setAttribute("onclick", payload);
    const link = document.createElement("a");
    link.href = "javascript:alert(1)";
    const image = document.createElement("img");
    image.src = "javascript:alert(1)";
    document.body.append(text, frame, button, link, image);
  } else {
    sandbox.HomeChatRenderer[functionName](payload, "info");
  }

  function collectNodes(root) {
    const nodes = [root];
    root.children.forEach((child) => nodes.push(...collectNodes(child)));
    return nodes;
  }

  const renderedNodes = [document.body, document.head].flatMap(collectNodes);
  const rawHtmlSinks = renderedNodes.filter((node) =>
    node.innerHTML.includes(payload),
  );
  const executableAttributeSinks = findExecutableAttributeViolations(
    renderedNodes,
    payload,
  );
  const safelyRendered =
    renderedNodes.some((node) => node.textContent.includes(payload)) ||
    renderedNodes.some((node) =>
      node.innerHTML.includes(Safe.escapeHtml(payload)),
    );

  return {
    payload,
    rawHtmlSinks,
    executableAttributeSinks,
    safelyRendered,
  };
}

function collectChatSinkFailures(result, functionName) {
  const sinkFailures = [];
  if (result.rawHtmlSinks.length) {
    sinkFailures.push(
      `${functionName} inserted raw message into innerHTML (${result.rawHtmlSinks
        .map((node) => node.label)
        .join(", ")})`,
    );
  }
  if (result.executableAttributeSinks.length) {
    sinkFailures.push(
      `${functionName} inserted message into executable attributes (${result.executableAttributeSinks
        .map((violation) => `${violation.node.label}.${violation.name}`)
        .join(", ")})`,
    );
  }
  if (!result.safelyRendered) {
    sinkFailures.push(
      `${functionName} must deliver message through textContent/createTextNode or escaped innerHTML`,
    );
  }
  return sinkFailures;
}

const chatAttributeProbe = exerciseChatMessageSink(null, {
  attributeProbe: true,
});
const chatAttributeProbeFailures = collectChatSinkFailures(
  chatAttributeProbe,
  "chat attribute mutation probe",
);
assert(
  chatAttributeProbe.safelyRendered,
  "Chat executable attribute mutation probe must also render the payload safely as text",
);
assert(
  ["srcdoc", "onclick", "href", "src"].every((name) =>
    chatAttributeProbe.executableAttributeSinks.some(
      (violation) => violation.name === name,
    ),
  ) &&
    chatAttributeProbeFailures.some((message) =>
      message.includes("inserted message into executable attributes"),
    ),
  "Chat rendering assertion must reject srcdoc, on*, and javascript: href/src even when textContent is safe",
);

const chatSinkFailures = [];
["showToast", "showNotification"].forEach((functionName) => {
  const result = exerciseChatMessageSink(functionName);
  chatSinkFailures.push(...collectChatSinkFailures(result, functionName));
});
assert(chatSinkFailures.length === 0, chatSinkFailures.join("\n"));

const designMain = read("src/web/static/js/design/main.js");
assert(
  !designMain.includes("d.reply") && !designMain.includes("aiReply"),
  "Design UI must not render verbose model prose into the result surface",
);

const dockingUi = read("src/web/static/js/docking/ui_manager.js");
const dockingMain = read("src/web/static/js/docking/main.js");
const dockingViewer = read("src/web/static/js/docking/viewer_manager.js");
assert(
  dockingUi.includes("statusNode.textContent = statusText") &&
    !dockingUi.includes("log.innerHTML = `${stepInfo.icon"),
  "Docking progress status must use DOM text nodes",
);
assert(
  dockingViewer.includes('title.textContent = "3D 查看器加载失败"') &&
    !dockingViewer.includes("text.innerHTML = `"),
  "Docking viewer errors must use DOM text nodes",
);
assert(
  dockingMain.includes("Safe.escapeHtml(String(s))") &&
    !dockingMain.includes("<li>${s}</li>"),
  "Docking environment suggestions must be escaped before HTML insertion",
);
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
