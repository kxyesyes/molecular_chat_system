"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");

const values = new Map();
let captured = null;
global.window = {
  sessionStorage: {
    getItem: (key) => values.get(key) || null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  },
  prompt: () => "unit-admin-token",
  fetch: async (input, init) => {
    captured = { input, init };
    return { status: 200 };
  },
};

require("../src/web/static/js/shared/admin_fetch.js");

function readSource(relativePath) {
  return fs.readFileSync(path.join(__dirname, "..", relativePath), "utf8");
}

function collectCheck(failures, description, check) {
  try {
    check();
  } catch (error) {
    failures.push(`${description}: ${error.message}`);
  }
}

function assertAdminHelperGuard(source) {
  assert.match(source, /const Admin = window\.MedChatAdminAuth;/);
  assert.match(
    source,
    /throw new Error\(\s*"MedChat admin authentication helper is unavailable"\s*\)/,
  );
}

(async function () {
  await window.MedChatAdminAuth.fetch("/api/llm/config");
  assert.strictEqual(captured.input, "/api/llm/config");
  assert.strictEqual(
    captured.init.headers.get("X-MedChat-Admin-Token"),
    "unit-admin-token",
  );
  assert.strictEqual(
    window.sessionStorage.getItem("medchat_admin_token"),
    "unit-admin-token",
  );
  console.log("Admin fetch session-token checks passed");

  const modelManager = readSource(
    "src/web/static/js/activity_prediction/model_manager.js",
  );
  const dockingTemplate = readSource(
    "src/web/templates/molecular_docking.html",
  );
  const dockingUi = readSource("src/web/static/js/docking/ui_manager.js");
  const homeSource = readSource("src/web/static/js/home/main.js");
  const homeTemplate = readSource("src/web/templates/index.html");
  const failures = [];

  collectCheck(failures, "LLM settings expose an explicit key-clear control", () => {
    assert.match(homeTemplate, /id=["']clearLlmApiKey["']/);
    assert.match(homeTemplate, /\.llm-key-clear\s*\{/);
    assert.match(homeSource, /llmClearApiKey/);
    assert.match(homeSource, /clear_api_key\s*:/);
  });

  collectCheck(failures, "activity model manager requires the admin helper", () => {
    assertAdminHelperGuard(modelManager);
  });
  collectCheck(failures, "activity model list uses Admin.fetch", () => {
    assert.match(
      modelManager,
      /Admin\.fetch\(\s*["']\/api\/activity\/models["']\s*\)/,
    );
  });
  collectCheck(failures, "activity model switch uses Admin.fetch", () => {
    assert.match(
      modelManager,
      /Admin\.fetch\(\s*["']\/api\/activity\/models\/switch["']/,
    );
  });
  collectCheck(failures, "activity model DELETE uses Admin.fetch", () => {
    assert.match(
      modelManager,
      /Admin\.fetch\(\s*`\/api\/activity\/models\/\$\{modelId\}`/,
    );
  });
  collectCheck(failures, "activity model routes have no raw fetch", () => {
    assert.doesNotMatch(
      modelManager,
      /(^|[^.\w])fetch\(\s*(?:["']\/api\/activity\/models|`\/api\/activity\/models)/m,
    );
  });

  collectCheck(
    failures,
    "docking template loads admin_fetch.js before ui_manager.js",
    () => {
      const adminIndex = dockingTemplate.indexOf(
        '/static/js/shared/admin_fetch.js',
      );
      const uiIndex = dockingTemplate.indexOf(
        '/static/js/docking/ui_manager.js',
      );
      assert.ok(adminIndex >= 0, "admin_fetch.js script is missing");
      assert.ok(uiIndex >= 0, "ui_manager.js script is missing");
      assert.ok(adminIndex < uiIndex, "admin_fetch.js must load first");
    },
  );
  collectCheck(failures, "docking UI requires the admin helper", () => {
    assertAdminHelperGuard(dockingUi);
  });
  collectCheck(failures, "docking history GET uses Admin.fetch", () => {
    assert.match(
      dockingUi,
      /Admin\.fetch\(\s*["']\/api\/docking\/history["']\s*\)/,
    );
  });
  collectCheck(
    failures,
    "docking history collection DELETE uses Admin.fetch",
    () => {
      assert.match(
        dockingUi,
        /Admin\.fetch\(\s*["']\/api\/docking\/history["']\s*,\s*\{\s*method:\s*["']DELETE["']\s*\}\s*\)/,
      );
    },
  );
  collectCheck(failures, "docking history item DELETE uses Admin.fetch", () => {
    assert.match(
      dockingUi,
      /Admin\.fetch\(\s*`\/api\/docking\/history\/\$\{jobId\}`\s*,\s*\{\s*method:\s*["']DELETE["']\s*\}\s*\)/,
    );
  });
  collectCheck(failures, "docking history routes have no raw fetch", () => {
    assert.doesNotMatch(
      dockingUi,
      /(^|[^.\w])fetch\(\s*(?:["']\/api\/docking\/history|`\/api\/docking\/history)/m,
    );
  });
  collectCheck(failures, "public docking result requests remain ordinary fetch", () => {
    assert.match(
      dockingUi,
      /(^|[^.\w])fetch\(\s*`\/api\/docking\/result\/\$\{jobId\}`/m,
    );
  });

  if (failures.length) {
    throw new Error(`Protected caller checks failed:\n- ${failures.join("\n- ")}`);
  }

  console.log("Protected admin caller source checks passed");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
