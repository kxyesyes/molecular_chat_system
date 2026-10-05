const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");

const root = path.join(__dirname, "..");
const html = fs.readFileSync(path.join(root, "src/web/templates/index.html"), "utf8");
const source = fs.readFileSync(path.join(root, "src/web/static/js/home/main.js"), "utf8");

test("LLM settings keep only concise key status and controls", () => {
  const settings = html.slice(
    html.indexOf('<div class="llm-settings-overlay"'),
    html.indexOf('id="saveLlmConfig"')
  );

  assert.doesNotMatch(settings, /配置保存在仓库之外/);
  assert.doesNotMatch(settings, /同一台电脑.*同一操作系统账号/);
  assert.doesNotMatch(settings, /API Key 以明文保存/);
  assert.match(settings, /清除已保存的 API Key/);
  assert.match(settings, /id="clearLlmApiKey"/);
  assert.match(settings, /id="llmApiKeyHint"/);
  assert.match(settings, /id="llmSettingsStatus"/);

  assert.doesNotMatch(source, /已保存，无需重复填写/);
  assert.doesNotMatch(source, /API Key 不会回传明文/);
});
