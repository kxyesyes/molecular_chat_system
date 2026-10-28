const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const root = path.join(__dirname, "..");
const source = fs.readFileSync(path.join(root, "src/web/static/js/home/main.js"), "utf8");
const html = fs.readFileSync(path.join(root, "src/web/templates/index.html"), "utf8");
const defaults = {
  provider: "openai_compatible",
  base_url: "https://api.deepseek.com/chat/completions",
  model_name: "deepseek-v4-pro",
  stream: true,
};

// Execute production declarations, not copies of their implementation. Boundaries
// use sibling declarations so braces inside strings/templates cannot truncate them.
function extractFunction(name) {
  const start = new RegExp(`^([ \\t]*)(?:async\\s+)?function ${name}\\(`, "m").exec(source);
  assert.ok(start, `Missing production function ${name}`);
  const next = new RegExp(`^${start[1]}(?:async\\s+)?function \\w+\\(`, "gm");
  next.lastIndex = start.index + start[0].length;
  const end = next.exec(source);
  assert.ok(end, `Missing next declaration after ${name}`);
  return source.slice(start.index, end.index);
}

function createDomElement(document, initialText = "") {
  let ownText = initialText;
  const children = [];
  return {
    value: "", checked: false, style: {}, attributes: {}, listeners: {}, ownerDocument: document,
    get textContent() {
      return ownText + children.map((child) => child.textContent).join("");
    },
    set textContent(value) {
      ownText = String(value ?? "");
      children.splice(0, children.length);
    },
    get children() {
      return children.slice();
    },
    appendChild(child) {
      ownText = "";
      children.push(child);
      child.parentNode = this;
      return child;
    },
    contains(target) {
      return target === this || children.some((child) => child.contains(target));
    },
    addEventListener(type, handler) { this.listeners[type] = handler; },
    setAttribute(name, value) { this.attributes[name] = value; },
  };
}

function createSelect(document, options = []) {
  const select = createDomElement(document);
  let selectedValue = "";
  for (const [value, text] of options) {
    const option = createDomElement(document, text);
    option.value = value;
    select.appendChild(option);
  }
  Object.defineProperty(select, "value", {
    get() {
      return select.children.some((option) => String(option.value) === selectedValue)
        ? selectedValue
        : "";
    },
    set(value) {
      selectedValue = String(value ?? "");
    },
  });
  return select;
}

function harness(responses = []) {
  const document = { createElement: () => createDomElement(document) };
  const elements = {};
  for (const name of ["settingsBtn", "llmProvider", "llmStream", "llmBaseUrl", "llmModelName", "llmApiKey", "llmSettingsStatus", "connectionStatus", "modelSelect", "testLlmConfig", "saveLlmConfig", "input"]) {
    elements[name] = createDomElement(document);
  }
  elements.agentModelStatus = createDomElement(document);
  elements.agentModelStatus.setAttribute("role", "status");
  elements.agentModelStatus.setAttribute("aria-live", "polite");
  elements.agentModelStatus.setAttribute("aria-label", "Agent 模型状态");
  elements.agentModelRole = createDomElement(document, "Agent");
  elements.agentModelRole.setAttribute("aria-label", "模型角色");
  elements.agentModelReadiness = createDomElement(document, "状态未知");
  elements.agentModelReadiness.setAttribute("aria-label", "Agent 就绪状态");
  elements.agentModelStatus.appendChild(elements.agentModelRole);
  elements.agentModelStatus.appendChild(createDomElement(document, "·"));
  elements.agentModelStatus.appendChild(elements.agentModelReadiness);
  elements.llmSettingsOverlay = createDomElement(document);
  elements.llmSettingsOverlay.classList = { add() {}, remove() {} };
  elements.connectionStatus.textContent = "未连接";
  elements.connectionStatus.style = { backgroundColor: "gray", color: "black" };
  elements.modelSelect = createSelect(document, [["deepseek", "DeepSeek"]]);
  elements.modelSelect.value = "deepseek";
  const requests = [];
  const toasts = [];
  const storageAccess = [];
  const forbiddenStorage = new Proxy({}, {
    get(_target, property) {
      storageAccess.push(String(property));
      throw new Error("Settings must not access browser storage");
    },
    set(_target, property) {
      storageAccess.push(String(property));
      throw new Error("Settings must not access browser storage");
    },
  });
  const context = vm.createContext({
    elements,
    console: { error() {}, log() {}, warn() {} },
    localStorage: forbiddenStorage,
    sessionStorage: forbiddenStorage,
    window: { localStorage: forbiddenStorage, sessionStorage: forbiddenStorage },
    chatMode: false,
    decisionActive: () => false,
    document: { ...document, addEventListener() {} },
    HomeChatRenderer: {
      showToast: (...args) => toasts.push(args),
      showNotification: (...args) => toasts.push(args),
      updateConnectionStatus: status => {
        elements.connectionStatus.textContent = status === "connected" ? "传输已连接" : "传输未连接";
      },
    },
    fetch: async (url, options) => {
      requests.push({ url, options });
      assert.ok(responses.length, "Unexpected request (real network is never available)");
      const result = responses.shift();
      return { json: async () => result };
    },
  });
  vm.runInContext("let agentModelStatusRequestVersion = 0; let agentReadinessRequestVersion = 0; let agentReadiness = \"unknown\"; let agentConfigInputVersion = 0; let agentConfigSavedInputVersion = null; let agentConfigDraftKeyPresent = false; let agentConfigFingerprint = \"\"; let agentWireMode = \"native\"; let authoritativeAgentConfigFingerprint = \"\"; let llmConfigSaveGeneration = 0; let modelSwitchRequestVersion = 0; let authoritativeAgentModelName = \"\"; let ws = null; let decisionMode = true; let decisionWaiting = null; let decisionTurn = null; let decisionAbandonAwait = null; let protocolDesyncedSocket = null; const WebSocket = { OPEN: 1 };", context);
  for (const name of ["openLlmSettings", "closeLlmSettings", "fillLlmSettingsForm", "collectLlmSettingsForm", "markLlmConfigInputChanged", "getAgentConfigFingerprint", "setLlmSettingsStatus", "testLlmConfig", "saveLlmConfig", "applyAgentModelLabel", "setAgentReadiness", "markAgentReadyFromSuccessfulTurn", "refreshAgentModelStatus", "refreshAgentReadiness", "ensureAgentReady", "sendDecisionMessage", "renderTransportStatus", "updateConnectionStatus", "bindEvents", "handleModelChange", "getModelDisplayName"]) {
    vm.runInContext(extractFunction(name), context, { filename: `main.js:${name}` });
  }
  return { context, elements, requests, toasts, storageAccess };
}

function formValues(context) {
  return JSON.parse(JSON.stringify(context.collectLlmSettingsForm()));
}

test("empty configuration fills DeepSeek defaults and a blank password", () => {
  const { context } = harness();
  context.fillLlmSettingsForm({});
  assert.deepEqual(formValues(context), { ...defaults, api_key: "" });
});

test("missing compatible fields use DeepSeek defaults", () => {
  const { context } = harness();
  context.fillLlmSettingsForm({ provider: "openai_compatible" });
  assert.deepEqual(formValues(context), { ...defaults, api_key: "" });
});

test("explicit other providers preserve configured and empty fields", () => {
  const { context } = harness();
  for (const provider of ["ollama", "modelscope", "custom", "openai_compatible"]) {
    for (const [base_url, model_name] of [["https://example.invalid/chat", "chosen-model"], ["", ""]]) {
      const config = { provider, base_url, model_name, stream: false };
      context.fillLlmSettingsForm(config);
      assert.deepEqual(formValues(context), { ...config, api_key: "" });
    }
  }
});

test("other providers without fields do not inherit DeepSeek fields", () => {
  const { context } = harness();
  context.fillLlmSettingsForm({ provider: "ollama" });
  assert.equal(formValues(context).base_url, "");
  assert.equal(formValues(context).model_name, "");
});

test("fill always clears the password field", () => {
  const { context, elements } = harness();
  for (const api_key_hint of ["SYNTHETIC_PREFIX***SYNTHETIC_SUFFIX", "<img src=x onerror=alert(1)>", ""]) {
    elements.llmApiKey.value = "synthetic-unsaved-value";
    context.fillLlmSettingsForm({ ...defaults, api_key: "synthetic-server-value", api_key_configured: true, api_key_hint });
    assert.equal(elements.llmApiKey.value, "");
  }
});

test("server key hints are not rendered into the settings panel", () => {
  const { context } = harness();
  context.fillLlmSettingsForm({ api_key_configured: false, api_key_hint: "SYNTHETIC_FRAGMENT" });
  assert.doesNotMatch(source, /api_key_hint|llmApiKeyHint|clearLlmApiKey/);
});

test("opening without persisted config uses defaults; reopening never refills password", async () => {
  const { context, elements, requests, storageAccess } = harness([
    { success: true },
    { success: true, config: { ...defaults, api_key_configured: true, api_key_hint: "SYNTHETIC_FRAGMENT" } },
  ]);
  await context.openLlmSettings();
  assert.deepEqual(formValues(context), { ...defaults, api_key: "" });
  elements.llmApiKey.value = "synthetic-unsaved-value";
  context.closeLlmSettings();
  await context.openLlmSettings();
  assert.equal(elements.llmApiKey.value, "");
  assert.deepEqual(requests.map((request) => request.url), ["/api/llm/config", "/api/llm/config"]);
  assert.equal(elements.llmSettingsStatus.textContent, "");
  assert.deepEqual(storageAccess, []);
});

test("stale settings read failure cannot overwrite a newer open result", async () => {
  let rejectFirst;
  let resolveSecond;
  const firstResponse = new Promise((_, reject) => { rejectFirst = reject; });
  const secondResponse = new Promise(resolve => { resolveSecond = resolve; });
  const { context, elements } = harness([firstResponse, secondResponse]);

  const firstOpen = context.openLlmSettings();
  const secondOpen = context.openLlmSettings();
  resolveSecond({
    success: true,
    config: { ...defaults, model_name: "latest-form-model" },
    agent: { ...defaults, model_name: "latest-agent-model", role: "agent" },
  });
  await secondOpen;
  rejectFirst(new Error("stale settings read failure"));
  await firstOpen;

  assert.equal(elements.modelSelect.value, "latest-agent-model");
  assert.equal(elements.llmModelName.value, "latest-form-model");
  assert.equal(elements.llmSettingsStatus.textContent, "");
});

test("late settings read cannot overwrite a draft edited after open", async () => {
  let resolveRead;
  const readResponse = new Promise(resolve => { resolveRead = resolve; });
  const { context, elements } = harness([readResponse]);
  context.fillLlmSettingsForm({ ...defaults, model_name: "initial-draft" });

  const open = context.openLlmSettings();
  elements.llmModelName.value = "user-edited-draft";
  context.markLlmConfigInputChanged();
  context.setAgentReadiness("testing");
  elements.llmSettingsStatus.textContent = "正在测试连接...";
  resolveRead({
    success: true,
    config: { ...defaults, model_name: "stale-server-model" },
    agent: { ...defaults, model_name: "stale-agent-model", role: "agent" },
  });
  await open;

  assert.equal(elements.llmModelName.value, "user-edited-draft");
  assert.equal(elements.modelSelect.value, "deepseek");
  assert.equal(elements.agentModelReadiness.textContent, "检测中");
  assert.equal(elements.llmSettingsStatus.textContent, "正在测试连接...");
});

test("late settings read failure cannot replace a draft status after edit", async () => {
  let rejectRead;
  const readResponse = new Promise((_, reject) => { rejectRead = reject; });
  const { context, elements } = harness([readResponse]);
  context.fillLlmSettingsForm({ ...defaults, model_name: "initial-draft" });

  const open = context.openLlmSettings();
  elements.llmModelName.value = "user-edited-draft";
  context.markLlmConfigInputChanged();
  context.setAgentReadiness("testing");
  elements.llmSettingsStatus.textContent = "正在测试连接...";
  rejectRead(new Error("stale read failure"));
  await open;

  assert.equal(elements.llmModelName.value, "user-edited-draft");
  assert.equal(elements.modelSelect.value, "deepseek");
  assert.equal(elements.agentModelReadiness.textContent, "检测中");
  assert.equal(elements.llmSettingsStatus.textContent, "正在测试连接...");
});

test("starting an Agent test invalidates an in-flight settings read", async () => {
  let resolveRead;
  const readResponse = new Promise(resolve => { resolveRead = resolve; });
  const authoritative = {
    provider: "ollama",
    base_url: "http://ollama.invalid",
    model_name: "current-agent-model",
    stream: true,
    wire_mode: "native",
  };
  const { context, elements } = harness([readResponse, { success: false, message: "probe failed" }]);
  context.applyAgentModelLabel(authoritative);
  context.fillLlmSettingsForm(authoritative);

  const open = context.openLlmSettings();
  await context.testLlmConfig();
  resolveRead({
    success: true,
    config: { ...authoritative, model_name: "stale-form-model" },
    agent: { ...authoritative, model_name: "stale-agent-model", role: "agent" },
  });
  await open;

  assert.equal(elements.llmModelName.value, authoritative.model_name);
  assert.equal(elements.modelSelect.value, authoritative.model_name);
  assert.equal(elements.agentModelReadiness.textContent, "不可用");
  assert.equal(elements.llmSettingsStatus.textContent, "probe failed");
});

test("settings read uses Agent metadata as authoritative before filling the config form", async () => {
  const { context, elements } = harness([{
    success: true,
    config: { ...defaults, model_name: "legacy-config-model" },
    agent: { ...defaults, model_name: "authoritative-agent-model", role: "agent" },
  }]);

  await context.openLlmSettings();

  assert.equal(elements.modelSelect.value, "authoritative-agent-model");
  assert.equal(elements.llmModelName.value, "legacy-config-model");
  assert.equal(elements.agentModelReadiness.textContent, "待测试");
});

test("collect fallback provider is compatible when provider element is absent", () => {
  const { context, elements } = harness();
  elements.llmProvider = null;
  assert.equal(formValues(context).provider, defaults.provider);
});

test("saving sends entered key only in POST and reports saved, never connected", async () => {
  const { context, elements, requests, storageAccess, toasts } = harness([
    { success: true, config: { ...defaults, api_key_configured: true } },
  ]);
  context.fillLlmSettingsForm(defaults);
  elements.llmApiKey.value = "synthetic-test-only-key";
  const originalConnection = JSON.stringify(elements.connectionStatus);
  await context.saveLlmConfig();
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, "/api/llm/config");
  assert.equal(requests[0].options.method, "POST");
  assert.deepEqual(JSON.parse(requests[0].options.body), { ...defaults, api_key: "synthetic-test-only-key" });
  assert.equal(JSON.stringify(elements.connectionStatus), originalConnection);
  assert.equal(elements.llmApiKey.value, "");
  assert.equal(elements.llmSettingsStatus.textContent, "配置已保存并启用");
  assert.doesNotMatch(elements.llmSettingsStatus.textContent, /API Key|远程连接|测试连接|本机用户目录/);
  assert.doesNotMatch(elements.llmSettingsStatus.textContent, /已连接/);
  assert.equal(toasts[0][1], "success");
  assert.deepEqual(storageAccess, []);
});

test("blank save preserves the existing key without exposing key controls", async () => {
  const { context, requests, storageAccess } = harness([
    { success: true, config: { ...defaults, api_key_configured: true } },
  ]);
  context.fillLlmSettingsForm({ ...defaults, api_key_configured: true });
  await context.saveLlmConfig();
  assert.deepEqual(JSON.parse(requests[0].options.body), { ...defaults, api_key: "" });
  assert.deepEqual(storageAccess, []);
});

test("save uses authoritative Agent metadata and resets readiness", async () => {
  const { context, elements } = harness([{
    success: true,
    config: { ...defaults, model_name: "legacy-config-model" },
    agent: { provider: "ollama", model_name: "saved-agent-model", role: "agent", readiness: "unknown" },
  }]);
  context.setAgentReadiness("ready");
  await context.saveLlmConfig();
  assert.equal(elements.modelSelect.value, "saved-agent-model");
  assert.equal(elements.agentModelReadiness.textContent, "待测试");
});

test("save success applies authoritative state without overwriting a form edited while saving", async () => {
  let resolveSave;
  const saveResponse = new Promise(resolve => { resolveSave = resolve; });
  const { context, elements } = harness([saveResponse]);
  context.fillLlmSettingsForm({ ...defaults, model_name: "saved-model" });

  const save = context.saveLlmConfig();
  elements.llmModelName.value = "new-user-input";
  resolveSave({
    success: true,
    config: { ...defaults, model_name: "saved-model" },
    agent: { ...defaults, model_name: "saved-agent-model", role: "agent" },
  });
  await save;

  assert.equal(elements.llmModelName.value, "new-user-input");
  assert.equal(elements.modelSelect.value, "saved-agent-model");
  assert.equal(elements.agentModelReadiness.textContent, "待测试");
  assert.equal(elements.llmSettingsStatus.textContent, "配置已保存并启用");
});

test("save success does not clear a key edited while saving", async () => {
  let resolveSave;
  const saveResponse = new Promise(resolve => { resolveSave = resolve; });
  const { context, elements } = harness([saveResponse]);
  context.fillLlmSettingsForm(defaults);

  const save = context.saveLlmConfig();
  elements.llmApiKey.value = "new-user-key";
  context.markLlmConfigInputChanged();
  resolveSave({
    success: true,
    config: { ...defaults },
    agent: { ...defaults, model_name: "saved-agent-model", role: "agent" },
  });
  await save;

  assert.equal(elements.llmApiKey.value, "new-user-key");
  assert.equal(elements.modelSelect.value, "saved-agent-model");
  assert.equal(elements.llmSettingsStatus.textContent, "配置已保存并启用");
});

test("save success survives a readiness test started while saving and invalidates that probe", async () => {
  let resolveSave;
  let resolveProbe;
  const saveResponse = new Promise(resolve => { resolveSave = resolve; });
  const probeResponse = new Promise(resolve => { resolveProbe = resolve; });
  const { context, elements, requests } = harness([saveResponse, probeResponse]);
  context.applyAgentModelLabel("model-a");
  context.fillLlmSettingsForm({ ...defaults, model_name: "model-a" });

  const save = context.saveLlmConfig();
  const probe = context.refreshAgentReadiness();
  resolveSave({
    success: true,
    config: { ...defaults, model_name: "saved-model" },
    agent: { ...defaults, model_name: "saved-agent-model", role: "agent" },
  });
  await save;

  assert.deepEqual(requests.map(request => request.url), ["/api/llm/config", "/api/llm/test"]);
  assert.equal(elements.modelSelect.value, "saved-agent-model");
  assert.equal(elements.agentModelReadiness.textContent, "待测试");
  assert.equal(elements.llmSettingsStatus.textContent, "配置已保存并启用");

  resolveProbe({ success: true, model: "model-a" });
  await probe;
  assert.equal(elements.agentModelReadiness.textContent, "待测试");
});

test("save success survives a model switch started while saving", async () => {
  let resolveSave;
  let resolveSwitch;
  const saveResponse = new Promise(resolve => { resolveSave = resolve; });
  const switchResponse = new Promise(resolve => { resolveSwitch = resolve; });
  const { context, elements, requests } = harness([
    saveResponse,
    switchResponse,
    { success: true, agent: { model_name: "switched-agent-model", role: "agent" } },
  ]);
  context.fillLlmSettingsForm(defaults);

  const save = context.saveLlmConfig();
  const switchModel = context.handleModelChange({ target: { value: "deepseek" } });
  resolveSave({
    success: true,
    config: { ...defaults, model_name: "saved-model" },
    agent: { ...defaults, model_name: "saved-agent-model", role: "agent" },
  });
  await save;

  assert.equal(elements.modelSelect.value, "saved-agent-model");
  assert.equal(elements.agentModelReadiness.textContent, "待测试");
  assert.equal(elements.llmSettingsStatus.textContent, "配置已保存并启用");

  resolveSwitch({ success: true, message: "切换成功" });
  await switchModel;
  assert.deepEqual(requests.map(request => request.url), [
    "/api/llm/config", "/api/switch_model", "/api/llm/config",
  ]);
});

test("Agent model and readiness updates preserve the readiness child in the status DOM", () => {
  const { context, elements } = harness();
  context.applyAgentModelLabel("agent-model");
  assert.equal(elements.modelSelect.value, "agent-model");
  assert.equal(elements.agentModelStatus.children.length, 3);
  assert.ok(elements.agentModelStatus.contains(elements.agentModelReadiness));
  context.setAgentReadiness("ready");
  assert.equal(elements.agentModelRole.textContent, "Agent");
  assert.equal(elements.agentModelReadiness.textContent, "已就绪");
  assert.equal(elements.agentModelStatus.children.length, 3);
  assert.ok(elements.agentModelStatus.contains(elements.agentModelReadiness));
  assert.equal(elements.agentModelStatus.attributes["aria-label"], "Agent 模型状态");
});

test("fake model select follows DOM behavior for unknown values and appends the Agent option", () => {
  const { context, elements } = harness();
  elements.modelSelect.value = "missing-model";
  assert.equal(elements.modelSelect.value, "");
  context.applyAgentModelLabel("agent-model");
  assert.equal(elements.modelSelect.value, "agent-model");
  assert.equal(elements.modelSelect.children.length, 2);
  assert.equal(elements.modelSelect.children[1].value, "agent-model");
  assert.equal(elements.modelSelect.children[1].textContent, "agent-model");
});

test("failed save reports failure without changing connection status", async () => {
  const { context, elements, toasts } = harness([{ success: false, message: "保存被拒绝" }]);
  context.fillLlmSettingsForm(defaults);
  await context.saveLlmConfig();
  assert.match(elements.llmSettingsStatus.textContent, /保存失败/);
  assert.equal(elements.llmSettingsStatus.style.color, "#b91c1c");
  assert.equal(elements.connectionStatus.textContent, "未连接");
  assert.equal(toasts[0][1], "error");
});

test("test connection button calls the Agent readiness path for the saved form", async () => {
  const { context, elements, requests, storageAccess } = harness([{ success: true }]);
  context.applyAgentModelLabel(defaults.model_name);
  context.fillLlmSettingsForm(defaults);
  context.bindEvents();
  await elements.testLlmConfig.listeners.click();
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, "/api/llm/test");
  assert.equal(JSON.parse(requests[0].options.body).api_key, "");
  assert.equal(elements.llmSettingsStatus.textContent, "连接测试成功");
  assert.equal(elements.agentModelRole.textContent, "Agent");
  assert.equal(elements.agentModelReadiness.textContent, "已就绪");
  assert.doesNotMatch(elements.llmSettingsStatus.textContent, /已保存/);
  assert.deepEqual(storageAccess, []);
});

test("key-only edits cannot claim current Agent readiness and never leak into state or DOM", async () => {
  const authoritative = {
    provider: "openai_compatible",
    base_url: defaults.base_url,
    model_name: defaults.model_name,
    stream: true,
    wire_mode: "native",
  };
  const secret = "synthetic-key-must-not-leak";
  const { context, elements, requests } = harness([{ success: true, model: defaults.model_name }]);
  context.applyAgentModelLabel(authoritative);
  context.fillLlmSettingsForm(authoritative);
  context.bindEvents();
  elements.llmApiKey.value = secret;
  elements.llmApiKey.listeners.input();

  await context.refreshAgentReadiness();

  assert.equal(elements.agentModelReadiness.textContent, "待保存/待重新测试");
  assert.doesNotMatch(elements.agentModelStatus.textContent, new RegExp(secret));
  assert.doesNotMatch(elements.llmSettingsStatus.textContent, new RegExp(secret));
  assert.doesNotMatch(elements.modelSelect.textContent, new RegExp(secret));
  assert.doesNotMatch(context.getAgentConfigFingerprint({ ...authoritative, api_key: secret }), new RegExp(secret));
  assert.equal(JSON.parse(requests[0].options.body).api_key, secret);
});

test("test connection button reports Agent unavailable on failure", async () => {
  const { context, elements } = harness([{ success: false, message: "模型探测失败：请检查 DeepSeek API Key" }]);
  context.applyAgentModelLabel(defaults.model_name);
  context.fillLlmSettingsForm(defaults);
  context.bindEvents();
  await elements.testLlmConfig.listeners.click();
  assert.match(elements.llmSettingsStatus.textContent, /模型探测失败：请检查 DeepSeek API Key/);
  assert.equal(elements.agentModelRole.textContent, "Agent");
  assert.equal(elements.agentModelReadiness.textContent, "不可用");
});

test("test result for an unsaved model is pending and cannot claim current Agent readiness", async () => {
  const { context, elements } = harness([{ success: true, model: "model-b" }]);
  context.applyAgentModelLabel("model-a");
  context.fillLlmSettingsForm({ ...defaults, model_name: "model-b" });
  await context.refreshAgentReadiness();
  assert.equal(elements.agentModelReadiness.textContent, "待保存/待重新测试");
  assert.equal(elements.agentModelStatus.textContent, "Agent·待保存/待重新测试");
});

test("same model with a different provider or base URL cannot claim Agent readiness", async () => {
  const authoritative = {
    provider: "ollama",
    base_url: "http://ollama-a.invalid",
    model_name: "shared-model",
    stream: true,
    wire_mode: "native",
  };
  const { context, elements } = harness([{ success: true, model: "shared-model" }]);
  context.applyAgentModelLabel(authoritative);
  context.fillLlmSettingsForm({
    provider: "openai_compatible",
    base_url: "https://provider-b.invalid/v1",
    model_name: "shared-model",
    stream: true,
  });
  await context.refreshAgentReadiness();
  assert.notEqual(elements.agentModelReadiness.textContent, "已就绪");
  assert.equal(elements.agentModelReadiness.textContent, "待保存/待重新测试");
});

test("readiness fingerprint includes safe transport fields but never the API key", () => {
  const { context } = harness();
  const base = {
    provider: "ollama",
    base_url: "http://ollama.invalid",
    model_name: "shared-model",
    wire_mode: "native",
    stream: true,
  };
  const withKey = context.getAgentConfigFingerprint({ ...base, api_key: "synthetic-secret" });
  const withoutKey = context.getAgentConfigFingerprint(base);
  const changed = context.getAgentConfigFingerprint({ ...base, stream: false });
  assert.equal(withKey, withoutKey);
  assert.doesNotMatch(withKey, /synthetic-secret|api_key/i);
  assert.notEqual(withKey, changed);
  assert.match(withKey, /ollama|ollama\.invalid|shared-model|native/);
});

test("editing the form after a delayed readiness test invalidates its result", async () => {
  let resolveProbe;
  const probeResponse = new Promise(resolve => { resolveProbe = resolve; });
  const authoritative = {
    provider: "ollama",
    base_url: "http://ollama.invalid",
    model_name: "shared-model",
    stream: true,
    wire_mode: "native",
  };
  const { context, elements } = harness([probeResponse]);
  context.applyAgentModelLabel(authoritative);
  context.fillLlmSettingsForm(authoritative);
  const probe = context.refreshAgentReadiness();
  elements.llmBaseUrl.value = "http://edited.invalid";
  resolveProbe({ success: true, model: "shared-model" });
  await probe;
  assert.notEqual(elements.agentModelReadiness.textContent, "已就绪");
  assert.equal(elements.agentModelReadiness.textContent, "待保存/待重新测试");
});

test("stale readiness probe errors cannot mark edited input unavailable", async () => {
  let rejectProbe;
  const probeResponse = new Promise((_, reject) => { rejectProbe = reject; });
  const authoritative = {
    provider: "ollama",
    base_url: "http://ollama.invalid",
    model_name: "shared-model",
    stream: true,
    wire_mode: "native",
  };
  const { context, elements } = harness([probeResponse]);
  context.applyAgentModelLabel(authoritative);
  context.fillLlmSettingsForm(authoritative);
  const probe = context.refreshAgentReadiness();
  elements.llmModelName.value = "edited-model";
  rejectProbe(new Error("stale probe failure"));
  await probe;

  assert.equal(elements.agentModelReadiness.textContent, "待保存/待重新测试");
});

test("late readiness result cannot overwrite a newer pending model identity", async () => {
  let resolveFirst;
  let resolveSecond;
  const firstResponse = new Promise(resolve => { resolveFirst = resolve; });
  const secondResponse = new Promise(resolve => { resolveSecond = resolve; });
  const { context, elements } = harness([firstResponse, secondResponse]);
  context.applyAgentModelLabel("model-a");
  context.fillLlmSettingsForm({ ...defaults, model_name: "model-a" });
  const first = context.refreshAgentReadiness();
  context.fillLlmSettingsForm({ ...defaults, model_name: "model-b" });
  const second = context.refreshAgentReadiness();
  resolveSecond({ success: true, model: "model-b" });
  await second;
  assert.equal(elements.agentModelReadiness.textContent, "待保存/待重新测试");
  resolveFirst({ success: true, model: "model-a" });
  await first;
  assert.equal(elements.agentModelReadiness.textContent, "待保存/待重新测试");
});

test("saving invalidates an in-flight readiness result", async () => {
  let resolveProbe;
  const probeResponse = new Promise(resolve => { resolveProbe = resolve; });
  const { context, elements } = harness([
    probeResponse,
    { success: true, config: { ...defaults }, agent: { model_name: "model-a", role: "agent" } },
  ]);
  context.applyAgentModelLabel("model-a");
  context.fillLlmSettingsForm({ ...defaults, model_name: "model-a" });
  const probe = context.refreshAgentReadiness();
  await context.saveLlmConfig();
  assert.equal(elements.agentModelReadiness.textContent, "待测试");
  resolveProbe({ success: true, model: "model-a" });
  await probe;
  assert.equal(elements.agentModelReadiness.textContent, "待测试");
});

test("model configuration refresh invalidates an in-flight readiness result", async () => {
  let resolveProbe;
  const probeResponse = new Promise(resolve => { resolveProbe = resolve; });
  const { context, elements } = harness([
    probeResponse,
    { success: true, agent: { model_name: "model-a", role: "agent" } },
  ]);
  context.applyAgentModelLabel("model-a");
  context.fillLlmSettingsForm({ ...defaults, model_name: "model-a" });
  const probe = context.refreshAgentReadiness();
  await context.refreshAgentModelStatus();
  assert.equal(elements.agentModelReadiness.textContent, "待测试");
  resolveProbe({ success: true, model: "model-a" });
  await probe;
  assert.equal(elements.agentModelReadiness.textContent, "待测试");
});

test("refreshAgentModelStatus selects the configured Agent model and labels it", async () => {
  const { context, elements, requests } = harness([
    {
      success: true,
      config: { provider: "ollama", model_name: "legacy-config-model" },
      agent: { provider: "ollama", model_name: "agent-model", role: "agent", readiness: "unknown" },
    },
  ]);
  await context.refreshAgentModelStatus();
  assert.deepEqual(requests.map((request) => request.url), ["/api/llm/config"]);
  assert.equal(elements.modelSelect.value, "agent-model");
  assert.match(elements.agentModelStatus.textContent, /Agent/);
  assert.equal(elements.agentModelReadiness.textContent, "待测试");
  assert.equal(elements.modelSelect.attributes["aria-label"], "Agent 模型：agent-model");
});

test("stale Agent model status responses cannot overwrite the latest state", async () => {
  let resolveFirst;
  let resolveSecond;
  const firstResponse = new Promise((resolve) => { resolveFirst = resolve; });
  const secondResponse = new Promise((resolve) => { resolveSecond = resolve; });
  const { context, elements, requests } = harness([firstResponse, secondResponse]);

  const firstRefresh = context.refreshAgentModelStatus();
  const secondRefresh = context.refreshAgentModelStatus();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(requests.length, 2);

  resolveSecond({
    success: true,
    agent: { model_name: "latest-agent-model", role: "agent", readiness: "unknown" },
  });
  await secondRefresh;
  context.setAgentReadiness("ready");

  resolveFirst({ success: false, message: "stale response failure" });
  await firstRefresh;

  assert.equal(elements.modelSelect.value, "latest-agent-model");
  assert.equal(elements.modelSelect.attributes["aria-label"], "Agent 模型：latest-agent-model");
  assert.equal(elements.agentModelRole.textContent, "Agent");
  assert.equal(elements.agentModelReadiness.textContent, "已就绪");
});

test("failed Agent config refresh preserves the last known model label", async () => {
  const { context, elements } = harness([{ success: false, message: "读取失败" }]);
  context.applyAgentModelLabel("last-known-model");
  context.setAgentReadiness("ready");
  await context.refreshAgentModelStatus();
  assert.equal(elements.modelSelect.value, "last-known-model");
  assert.equal(elements.modelSelect.attributes["aria-label"], "Agent 模型：last-known-model");
  assert.equal(elements.agentModelReadiness.textContent, "不可用");
});

test("stale save failure cannot overwrite a newer successful save", async () => {
  let resolveFirst;
  let resolveSecond;
  const firstResponse = new Promise(resolve => { resolveFirst = resolve; });
  const secondResponse = new Promise(resolve => { resolveSecond = resolve; });
  const { context, elements } = harness([firstResponse, secondResponse]);
  context.fillLlmSettingsForm({ ...defaults, model_name: "first-model" });
  const firstSave = context.saveLlmConfig();
  context.fillLlmSettingsForm({ ...defaults, model_name: "second-model" });
  const secondSave = context.saveLlmConfig();
  resolveSecond({
    success: true,
    config: { ...defaults, model_name: "second-model" },
    agent: { ...defaults, model_name: "second-model", role: "agent" },
  });
  await secondSave;
  assert.equal(elements.llmSettingsStatus.textContent, "配置已保存并启用");
  resolveFirst({ success: false, message: "旧保存失败" });
  await firstSave;
  assert.equal(elements.llmSettingsStatus.textContent, "配置已保存并启用");
  assert.equal(elements.modelSelect.value, "second-model");
});

test("stale model switch errors cannot overwrite a newer successful switch", async () => {
  let rejectFirst;
  let resolveSecondSwitch;
  const firstResponse = new Promise((_, reject) => { rejectFirst = reject; });
  const secondResponse = new Promise(resolve => { resolveSecondSwitch = resolve; });
  const { context, toasts } = harness([
    firstResponse,
    secondResponse,
    { success: true, agent: { model_name: "latest-model", role: "agent" } },
  ]);

  const firstSwitch = context.handleModelChange({ target: { value: "deepseek" } });
  const secondSwitch = context.handleModelChange({ target: { value: "deepseek" } });
  resolveSecondSwitch({ success: true, message: "切换成功" });
  await secondSwitch;
  rejectFirst(new Error("stale switch failure"));
  await firstSwitch;

  assert.equal(toasts.filter((toast) => toast[1] === "error").length, 0);
});

test("connected transport status does not claim Agent readiness", () => {
  const { context, elements } = harness();
  context.setAgentReadiness("unknown");
  context.updateConnectionStatus("connected");
  assert.match(elements.connectionStatus.textContent, /传输已连接$/);
  assert.doesNotMatch(elements.connectionStatus.textContent, /模型已就绪|Agent.*就绪|ready/i);
  assert.equal(elements.agentModelReadiness.textContent, "待测试");
});

test("a successful Agent turn proves readiness for the unchanged saved model", () => {
  const { context, elements } = harness();
  context.applyAgentModelLabel("deepseek-v4-pro");
  context.markAgentReadyFromSuccessfulTurn();
  assert.equal(elements.agentModelReadiness.textContent, "已就绪");

  context.setAgentReadiness("unknown");
  context.markLlmConfigInputChanged();
  context.markAgentReadyFromSuccessfulTurn();
  assert.equal(elements.agentModelReadiness.textContent, "待测试");
});

test("failed Agent readiness test reports Agent unavailable", async () => {
  const { context, elements, requests } = harness([
    { success: false, message: "模型探测失败" },
  ]);
  await context.refreshAgentReadiness();
  assert.deepEqual(requests.map((request) => request.url), ["/api/llm/test"]);
  assert.equal(JSON.parse(requests[0].options.body).model_name, "");
  assert.match(elements.agentModelStatus.textContent, /Agent.*不可用/);
  assert.equal(elements.agentModelReadiness.textContent, "不可用");
});

test("model change rereads authoritative Agent metadata and resets readiness", async () => {
  const { context, elements, requests } = harness([
    { success: true, message: "切换成功" },
    {
      success: true,
      config: { ...defaults, model_name: "legacy-config-model" },
      agent: { provider: "ollama", model_name: "authoritative-agent-model", role: "agent", readiness: "unknown" },
    },
  ]);
  context.setAgentReadiness("ready");
  await context.handleModelChange({ target: { value: "deepseek" } });
  assert.deepEqual(requests.map((request) => request.url), ["/api/switch_model", "/api/llm/config"]);
  assert.equal(elements.modelSelect.value, "authoritative-agent-model");
  assert.equal(elements.agentModelReadiness.textContent, "待测试");
});

test("model switch invalidates an in-flight readiness result", async () => {
  let resolveProbe;
  const probeResponse = new Promise(resolve => { resolveProbe = resolve; });
  const { context, elements } = harness([
    probeResponse,
    { success: true, message: "切换成功" },
    { success: true, agent: { model_name: "model-a", role: "agent" } },
  ]);
  context.applyAgentModelLabel("model-a");
  context.fillLlmSettingsForm({ ...defaults, model_name: "model-a" });
  const probe = context.refreshAgentReadiness();
  await context.handleModelChange({ target: { value: "deepseek" } });
  assert.equal(elements.agentModelReadiness.textContent, "待测试");
  resolveProbe({ success: true, model: "model-a" });
  await probe;
  assert.equal(elements.agentModelReadiness.textContent, "待测试");
});

test("successful model change renders transport from a non-open socket", async () => {
  const { context, elements } = harness([
    { success: true, message: "切换成功" },
    { success: true, agent: { model_name: "model-a", role: "agent" } },
  ]);
  await context.handleModelChange({ target: { value: "deepseek" } });
  assert.equal(elements.connectionStatus.textContent, "传输未连接");
  assert.doesNotMatch(elements.connectionStatus.textContent, /已连接$/);
});

test("initial markup selects compatible provider and shows DeepSeek placeholders", () => {
  const provider = html.match(/<select id="llmProvider">([\s\S]*?)<\/select>/)[1];
  assert.match(provider, /<option value="openai_compatible" selected>/);
  assert.match(html, /id="llmBaseUrl"[^>]*placeholder="https:\/\/api\.deepseek\.com\/chat\/completions"/);
  assert.match(html, /id="llmModelName"[^>]*placeholder="deepseek-v4-pro"/);
  assert.match(html, /id="llmApiKey" type="password" autocomplete="off"/);
  assert.match(html, /<select id="modelSelect"[^>]*aria-label="Agent 模型"/);
});

test("settings entry is a native accessible button and preserves its icon", () => {
  const match = html.match(/<button[^>]*id="settingsBtn"[\s\S]*?<\/button>/);
  assert.ok(match, "settings entry must be a native button");
  assert.match(match[0], /type="button"/);
  assert.match(match[0], /aria-label="模型接入设置"/);
  assert.match(match[0], /title="模型接入设置"/);
  assert.match(match[0], /src="\/static\/images\/top2\.png"/);
});

test("settings modal uses compact portrait dimensions", () => {
  const panel = html.match(/\.llm-settings-panel \{([\s\S]*?)\n    \}/)[1];
  assert.match(panel, /width:\s*min\(470px, 100%\)/);
  assert.match(panel, /min-height:\s*560px/);
  assert.match(panel, /max-height:\s*calc\(100vh - 48px\)/);
  assert.match(panel, /display:\s*flex/);
  assert.match(html, /\.llm-settings-body \{([\s\S]*?)\n    \}/);
  assert.match(html, /\.llm-settings-actions \{([\s\S]*?)margin-top:\s*auto/);
});

test("homepage model selector contains only the current DeepSeek entry", () => {
  const selector = html.match(/<select id="modelSelect"[^>]*>([\s\S]*?)<\/select>/)[1];
  assert.match(selector, /value="deepseek"/);
  assert.doesNotMatch(selector, /GLM|Qwen|魔搭|glm4|qwen3/i);
  assert.doesNotMatch(source, /GLM-5\.1|Qwen3-235B|魔搭社区|ZhipuAI\/GLM|Qwen\/Qwen3/);
});

test("settings markup omits verbose persistence and API-key instructions", () => {
  const settings = html.slice(html.indexOf('<div class="llm-settings-overlay"'), html.indexOf('id="saveLlmConfig"'));
  assert.doesNotMatch(
    settings,
    /用户配置目录|仓库之外|保存一次|已保存|无需重复填写|同一台电脑|同一服务商|接口地址下|重启.*更新代码|勾选.*保存|清除.*API Key|本机 \.env|配置已读取/
  );
  assert.doesNotMatch(
    settings,
    /id="llmApiKeyHint"|id="clearLlmApiKey"|llm-key-clear|本地 Ollama 可留空|外部 API 请填写 Key/,
  );
  assert.doesNotMatch(source, /已保存，无需重复填写|清除本机用户配置目录|配置保存在仓库之外|配置已读取。API Key 不会回传明文/);
});

test("unready Agent probe blocks a decision chat send", async () => {
  const { context, elements, requests } = harness([
    { success: true, config: defaults, agent: { ...defaults, role: "agent", wire_mode: "native" } },
    { success: false, model: defaults.model_name, message: "Agent 连接测试失败" },
  ]);
  let sent = 0;
  context.ws = { readyState: 1, send: () => { sent += 1; } };
  elements.input.value = "你好";

  await context.sendDecisionMessage();

  assert.deepEqual(requests.map(request => request.url), ["/api/llm/config", "/api/llm/test"]);
  assert.equal(sent, 0);
  assert.equal(elements.agentModelReadiness.textContent, "不可用");
});

test("send readiness does not overwrite a draft typed before config load", async () => {
  const { context, elements, requests } = harness([
    { success: false, model: "draft-model", message: "Agent 连接测试失败" },
  ]);
  let sent = 0;
  context.ws = { readyState: 1, send: () => { sent += 1; } };
  elements.input.value = "你好";
  elements.llmModelName.value = "draft-model";
  context.markLlmConfigInputChanged();

  await context.sendDecisionMessage();

  assert.deepEqual(requests.map(request => request.url), ["/api/llm/test"]);
  assert.equal(JSON.parse(requests[0].options.body).model_name, "draft-model");
  assert.equal(sent, 0);
  assert.equal(elements.llmModelName.value, "draft-model");
});

test("homepage loads the updated settings script instead of the stale cached version", () => {
  const activeHtml = html.replace(/<!--[\s\S]*?-->/g, "");
  assert.ok(activeHtml.includes('<script src="/static/js/home/main.js?v=20261028-quick-actions-v1"></script>'));
});
