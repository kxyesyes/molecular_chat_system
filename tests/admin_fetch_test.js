"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");

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

(async function () {
  const modelManager = readSource(
    "src/web/static/js/activity_prediction/model_manager.js",
  );
  const dockingTemplate = readSource(
    "src/web/templates/molecular_docking.html",
  );
  const dockingUi = readSource("src/web/static/js/docking/ui_manager.js");
  const homeSource = readSource("src/web/static/js/home/main.js");
  const homeTemplate = readSource("src/web/templates/index.html");
  const activityTemplate = readSource(
    "src/web/templates/activity_prediction.html",
  );
  const trainingMonitor = readSource(
    "src/web/static/js/activity_prediction/training_monitor.js",
  );
  const activityMain = readSource(
    "src/web/static/js/activity_prediction/main.js",
  );
  const failures = [];

  const productionSources = [
    modelManager,
    dockingTemplate,
    dockingUi,
    homeSource,
    homeTemplate,
    activityTemplate,
    trainingMonitor,
    activityMain,
  ].join("\n");

  collectCheck(failures, "admin token frontend contract is fully removed", () => {
    assert.doesNotMatch(productionSources, /MedChatAdminAuth/);
    assert.doesNotMatch(productionSources, /X-MedChat-Admin-Token/);
    assert.doesNotMatch(productionSources, /medchat_admin_token/);
    assert.doesNotMatch(productionSources, /MedChat 管理员令牌/);
    assert.doesNotMatch(productionSources, /admin_fetch\.js/);
  });
  collectCheck(failures, "changed callers use cache-busted script references", () => {
    const version = "v=[^\"']+";
    assert.match(homeTemplate, new RegExp(`home/main\\.js\\?${version}`));
    assert.match(dockingTemplate, new RegExp(`docking/ui_manager\\.js\\?${version}`));
    for (const script of ["model_manager", "training_monitor", "main"]) {
      assert.match(
        activityTemplate,
        new RegExp(`activity_prediction/${script}\\.js\\?${version}`),
      );
    }
  });

  collectCheck(failures, "LLM settings expose an explicit key-clear control", () => {
    assert.match(homeTemplate, /id=["']clearLlmApiKey["']/);
    assert.match(homeTemplate, /\.llm-key-clear\s*\{/);
    assert.match(homeSource, /llmClearApiKey/);
    assert.match(homeSource, /clear_api_key\s*:/);
  });

  collectCheck(failures, "activity model list uses ordinary fetch", () => {
    assert.match(
      modelManager,
      /fetch\(\s*["']\/api\/activity\/models["']\s*\)/,
    );
  });
  collectCheck(failures, "activity model switch uses ordinary fetch", () => {
    assert.match(
      modelManager,
      /fetch\(\s*["']\/api\/activity\/models\/switch["']/,
    );
  });
  collectCheck(failures, "activity model DELETE uses ordinary fetch", () => {
    assert.match(
      modelManager,
      /fetch\(\s*`\/api\/activity\/models\/\$\{modelId\}`/,
    );
  });
  collectCheck(failures, "docking history GET uses ordinary fetch", () => {
    assert.match(
      dockingUi,
      /fetch\(\s*["']\/api\/docking\/history["']\s*\)/,
    );
  });
  collectCheck(
    failures,
    "docking history collection DELETE uses ordinary fetch",
    () => {
      assert.match(
        dockingUi,
        /fetch\(\s*["']\/api\/docking\/history["']\s*,\s*\{\s*method:\s*["']DELETE["']\s*\}\s*\)/,
      );
    },
  );
  collectCheck(failures, "docking history item DELETE uses ordinary fetch", () => {
    assert.match(
      dockingUi,
      /fetch\(\s*`\/api\/docking\/history\/\$\{jobId\}`\s*,\s*\{\s*method:\s*["']DELETE["']\s*\}\s*\)/,
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

  console.log("Admin token removal source checks passed");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
