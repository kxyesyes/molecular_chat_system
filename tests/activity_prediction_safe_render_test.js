const fs = require("fs");
const path = require("path");
const vm = require("vm");

const root = path.join(__dirname, "..");
const failures = [];

function read(relPath) {
  return fs.readFileSync(path.join(root, relPath), "utf8");
}

function check(condition, message) {
  if (!condition) failures.push(message);
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function createCaptureDom(requiredIds) {
  const elements = new Map();

  class FakeElement {
    constructor(label, isRoot) {
      this.label = label;
      this.isRoot = Boolean(isRoot);
      this.children = [];
      this.parentNode = null;
      this.style = {};
      this.classList = { add() {}, remove() {} };
      this.attributes = {};
      this.value = "";
      this.files = [];
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

    addEventListener() {}
    remove() {}
  }

  requiredIds.forEach((id) =>
    elements.set(id, new FakeElement(`#${id}`, true)),
  );
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
    getElementById(id) {
      return elements.get(id) || null;
    },
    addEventListener() {},
  };

  return { document, elements };
}

function runModule(relPath, dom, globals) {
  const sandbox = {
    console,
    document: dom.document,
    alert() {},
    confirm() {
      return false;
    },
    setTimeout() {},
    ...globals,
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(read("src/web/static/js/shared/safe_render.js"), sandbox, {
    filename: "safe_render.js",
  });
  vm.runInContext(read(relPath), sandbox, { filename: relPath });
  return sandbox;
}

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

function assertSafelyRendered(
  dom,
  payload,
  label,
  requiredRootLabels,
  failureTarget,
) {
  const targetFailures = failureTarget || failures;
  function record(condition, message) {
    if (!condition) targetFailures.push(message);
  }

  function collectNodes(root) {
    const nodes = [root];
    root.children.forEach((child) => nodes.push(...collectNodes(child)));
    return nodes;
  }

  const roots = [
    ...dom.elements.values(),
    dom.document.body,
    dom.document.head,
  ];
  const connectedNodes = roots.flatMap(collectNodes);
  const rawHtmlSinks = connectedNodes.filter((node) =>
    node.innerHTML.includes(payload),
  );
  record(
    rawHtmlSinks.length === 0,
    `${label} reached innerHTML without escaping (${rawHtmlSinks
      .map((node) => node.label)
      .join(", ")})`,
  );

  const executableAttributeSinks = findExecutableAttributeViolations(
    connectedNodes,
    payload,
  );
  record(
    executableAttributeSinks.length === 0,
    `${label} reached executable attributes (${executableAttributeSinks
      .map((violation) => `${violation.node.label}.${violation.name}`)
      .join(", ")})`,
  );

  const escapedPayload = escapeHtml(payload);
  function nodeContainsSafePayload(node) {
    return (
      node.textContent.includes(payload) ||
      node.innerHTML.includes(escapedPayload) ||
      node.children.some(nodeContainsSafePayload)
    );
  }

  const expectedRoots = requiredRootLabels || [];
  if (expectedRoots.length) {
    expectedRoots.forEach((rootLabel) => {
      const root = roots.find((node) => node.label === rootLabel);
      record(Boolean(root), `${label} expected DOM root ${rootLabel}`);
      record(
        Boolean(root) && nodeContainsSafePayload(root),
        `${label} must reach ${rootLabel} through textContent/createTextNode or escaped innerHTML`,
      );
    });
    return;
  }

  const reachedSafeSink = connectedNodes.some((node) =>
    nodeContainsSafePayload(node),
  );
  record(
    reachedSafeSink,
    `${label} must reach the rendered DOM through textContent/createTextNode or escaped innerHTML`,
  );
}

const attributeProbePayload = '<img src=x onerror="attribute-probe-xss">';
const attributeProbeDom = createCaptureDom(["attribute-probe-root"]);
const attributeProbeRoot = attributeProbeDom.elements.get(
  "attribute-probe-root",
);
const attributeProbeText = attributeProbeDom.document.createElement("span");
attributeProbeText.textContent = attributeProbePayload;
const attributeProbeFrame = attributeProbeDom.document.createElement("iframe");
attributeProbeFrame.srcdoc = attributeProbePayload;
const attributeProbeButton = attributeProbeDom.document.createElement("button");
attributeProbeButton.setAttribute("onclick", attributeProbePayload);
const attributeProbeLink = attributeProbeDom.document.createElement("a");
attributeProbeLink.href = "javascript:alert(1)";
const attributeProbeImage = attributeProbeDom.document.createElement("img");
attributeProbeImage.src = "javascript:alert(1)";
attributeProbeRoot.append(
  attributeProbeText,
  attributeProbeFrame,
  attributeProbeButton,
  attributeProbeLink,
  attributeProbeImage,
);
const attributeProbeFailures = [];
assertSafelyRendered(
  attributeProbeDom,
  attributeProbePayload,
  "executable attribute mutation probe",
  ["#attribute-probe-root"],
  attributeProbeFailures,
);
const attributeProbeNodes = [attributeProbeRoot].flatMap(function collect(node) {
  return [node, ...node.children.flatMap(collect)];
});
const attributeProbeViolationNames = new Set(
  findExecutableAttributeViolations(
    attributeProbeNodes,
    attributeProbePayload,
  ).map((violation) => violation.name),
);
check(
  attributeProbeText.textContent === attributeProbePayload,
  "Executable attribute mutation probe must also render the payload safely as text",
);
check(
  ["srcdoc", "onclick", "href", "src"].every((name) =>
    attributeProbeViolationNames.has(name),
  ) &&
    attributeProbeFailures.some((message) =>
      message.includes("reached executable attributes"),
    ),
  "Executable attribute mutation probe must make the rendering assertion reject srcdoc, on*, and javascript: href/src",
);

const activityHtml = read("src/web/templates/activity_prediction.html");
const safeRenderScript = "/static/js/shared/safe_render.js";
const modelManagerScript = "/static/js/activity_prediction/model_manager.js";
const safeRenderIndex = activityHtml.indexOf(safeRenderScript);
const modelManagerIndex = activityHtml.indexOf(modelManagerScript);

check(
  safeRenderIndex >= 0,
  "Activity prediction page must load /static/js/shared/safe_render.js",
);
check(
  modelManagerIndex >= 0 && safeRenderIndex >= 0 && safeRenderIndex < modelManagerIndex,
  "Activity prediction page must load safe_render.js before model_manager.js",
);

try {
  const dom = createCaptureDom(["modelSelector", "popoverContent"]);
  dom.elements.get("modelSelector").value = "unsafe-model";
  const sandbox = runModule(
    "src/web/static/js/activity_prediction/model_manager.js",
    dom,
    {
      ActivityUtils: {
        DEFAULT_ACTIVITY_RANGE: {
          regression: { label: "regression" },
        },
      },
    },
  );
  const namePayload = '<img src=x onerror="model-name-xss">';
  const targetPayload = '<svg onload="model-target-xss">';
  sandbox.ActivityModels.setAll([
    {
      model_id: "unsafe-model",
      weights_file: "unsafe.pt",
      name: namePayload,
      target: targetPayload,
      task_type: "regression",
      samples: 1,
      created_at: 0,
    },
  ]);
  sandbox.ActivityModels.updatePopoverContent();
  assertSafelyRendered(dom, namePayload, "model.name", ["#popoverContent"]);
  assertSafelyRendered(dom, targetPayload, "model.target", ["#popoverContent"]);
} catch (error) {
  failures.push(`Model popover render contract could not execute: ${error.message}`);
}

try {
  const dom = createCaptureDom(["modelSelector", "popoverContent"]);
  dom.elements.get("modelSelector").value = "legacy.pt";
  const sandbox = runModule(
    "src/web/static/js/activity_prediction/model_manager.js",
    dom,
    {
      ActivityUtils: {
        DEFAULT_ACTIVITY_RANGE: {
          regression: { label: "regression" },
        },
      },
    },
  );
  sandbox.ActivityModels.setAll([
    {
      weights_file: "legacy.pt",
      name: "legacy record without model id",
      task_type: "regression",
    },
  ]);
  check(
    sandbox.ActivityModels.getAll()[0].model_id === undefined,
    "ActivityModels.setAll must not derive model_id from weights_file",
  );
  check(
    sandbox.ActivityModels.getSelectedMeta() === null,
    "Activity model selection must reject records without model_id",
  );
} catch (error) {
  failures.push(`Strict model_id contract could not execute: ${error.message}`);
}

try {
  const dom = createCaptureDom([
    "preflight-meta",
    "preflight-cols",
    "preflight-preview",
    "preflight-modal",
    "smilesColumn",
    "targetColumn",
  ]);
  const sandbox = runModule(
    "src/web/static/js/activity_prediction/preflight.js",
    dom,
    {},
  );
  const headerPayload = '<img src=x onerror="header-xss">';
  const cellPayload = '<svg onload="cell-xss">';
  sandbox.ActivityPreflight.setData({
    headers: [headerPayload],
    rows: [[cellPayload]],
    sep: ",",
    total: 1,
  });
  sandbox.ActivityPreflight.open();
  assertSafelyRendered(
    dom,
    headerPayload,
    "preflight header in column mapping and preview table",
    ["#preflight-cols", "#preflight-preview"],
  );
  assertSafelyRendered(dom, cellPayload, "preflight preview cell", [
    "#preflight-preview",
  ]);
} catch (error) {
  failures.push(`Preflight render contract could not execute: ${error.message}`);
}

try {
  const dom = createCaptureDom(["resultsBody"]);
  const sandbox = runModule(
    "src/web/static/js/activity_prediction/results_renderer.js",
    dom,
    {
      ActivityModels: {
        getTaskType() {
          return "regression";
        },
        getRangeConfig() {
          return {};
        },
      },
      ActivityUtils: {
        animateNumber() {},
        getTagClass() {
          return "tag-neutral";
        },
      },
    },
  );
  const failedSmilesPayload = '<img src=x onerror="smiles-xss">';
  const errorPayload = '<svg onload="error-xss">';
  const successSmilesPayload = '<img src=x onerror="success-smiles-xss">';
  const classPayload = '<svg onload="class-xss">';
  sandbox.ActivityResults.renderResultsTable([
    {
      success: false,
      smiles: failedSmilesPayload,
      error: errorPayload,
    },
    {
      success: true,
      smiles: successSmilesPayload,
      class: classPayload,
      activity_score: 1,
      confidence: 0.5,
    },
  ]);
  assertSafelyRendered(dom, failedSmilesPayload, "failed result SMILES", [
    "#resultsBody",
  ]);
  assertSafelyRendered(dom, errorPayload, "failed result error", [
    "#resultsBody",
  ]);
  assertSafelyRendered(dom, successSmilesPayload, "successful result SMILES", [
    "#resultsBody",
  ]);
  assertSafelyRendered(dom, classPayload, "successful result class", [
    "#resultsBody",
  ]);
} catch (error) {
  failures.push(`Results table render contract could not execute: ${error.message}`);
}

if (failures.length) {
  console.error(failures.map((message) => `- ${message}`).join("\n"));
  process.exit(1);
}

console.log("Activity prediction safe rendering static XSS checks passed");
