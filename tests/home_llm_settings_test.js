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

function harness(responses = []) {
  const elements = {};
  for (const name of ["llmProvider", "llmStream", "llmBaseUrl", "llmModelName", "llmApiKey", "llmSettingsStatus", "connectionStatus"]) {
    elements[name] = { value: "", checked: false, textContent: "", style: {} };
  }
  elements.llmSettingsOverlay = { classList: { add() {}, remove() {} }, setAttribute() {} };
  elements.connectionStatus.textContent = "未连接";
  elements.connectionStatus.style = { backgroundColor: "gray", color: "black" };
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
    console: { error() {} },
    localStorage: forbiddenStorage,
    sessionStorage: forbiddenStorage,
    window: { localStorage: forbiddenStorage, sessionStorage: forbiddenStorage },
    HomeChatRenderer: { showToast: (...args) => toasts.push(args) },
    fetch: async (url, options) => {
      requests.push({ url, options });
      assert.ok(responses.length, "Unexpected request (real network is never available)");
      const result = responses.shift();
      return { json: async () => result };
    },
  });
  for (const name of ["openLlmSettings", "closeLlmSettings", "fillLlmSettingsForm", "collectLlmSettingsForm", "setLlmSettingsStatus", "testLlmConfig", "saveLlmConfig"]) {
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

test("failed save reports failure without changing connection status", async () => {
  const { context, elements, toasts } = harness([{ success: false, message: "保存被拒绝" }]);
  context.fillLlmSettingsForm(defaults);
  await context.saveLlmConfig();
  assert.match(elements.llmSettingsStatus.textContent, /保存失败/);
  assert.equal(elements.llmSettingsStatus.style.color, "#b91c1c");
  assert.equal(elements.connectionStatus.textContent, "未连接");
  assert.equal(toasts[0][1], "error");
});

test("test connection calls only test endpoint and does not claim persistence", async () => {
  const { context, elements, requests, storageAccess } = harness([{ success: true }]);
  context.fillLlmSettingsForm(defaults);
  elements.llmApiKey.value = "synthetic-test-only-key";
  await context.testLlmConfig();
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, "/api/llm/test");
  assert.equal(JSON.parse(requests[0].options.body).api_key, "synthetic-test-only-key");
  assert.equal(elements.llmSettingsStatus.textContent, "连接测试成功");
  assert.doesNotMatch(elements.llmSettingsStatus.textContent, /已保存/);
  assert.deepEqual(storageAccess, []);
});

test("initial markup selects compatible provider and shows DeepSeek placeholders", () => {
  const provider = html.match(/<select id="llmProvider">([\s\S]*?)<\/select>/)[1];
  assert.match(provider, /<option value="openai_compatible" selected>/);
  assert.match(html, /id="llmBaseUrl"[^>]*placeholder="https:\/\/api\.deepseek\.com\/chat\/completions"/);
  assert.match(html, /id="llmModelName"[^>]*placeholder="deepseek-v4-pro"/);
  assert.match(html, /id="llmApiKey" type="password" autocomplete="off"/);
});

test("homepage model selector contains only the current DeepSeek entry", () => {
  const selector = html.match(/<select id="modelSelect">([\s\S]*?)<\/select>/)[1];
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

test("homepage loads the updated settings script instead of the stale cached version", () => {
  assert.ok(html.includes('<script src="/static/js/home/main.js?v=20261007-smart-tool-removed-v1"></script>'));
});
