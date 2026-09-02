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

function assertNoLegacyAdminAuth(source, fileLabel) {
  const forbidden = [
    /MedChatAdminAuth/,
    /medchat_admin_token/,
    /X-MedChat-Admin-Token/,
    /prompt\(/,
    /admin_fetch\.js/,
  ];

  forbidden.forEach((pattern) => {
    assert.doesNotMatch(source, pattern, `${fileLabel} still references ${pattern}`);
  });
}

function assertUsesPlainFetch(source, routePattern, description) {
  assert.match(source, routePattern, `${description} should use plain fetch`);
  assert.doesNotMatch(
    source,
    /window\.MedChatAdminAuth\.fetch|Admin\.fetch/,
    `${description} should not use the legacy admin helper`,
  );
}

(function () {
  const failures = [];

  const sharedAdminFetch = readSource("src/web/static/js/shared/admin_fetch.js");
  const homeSource = readSource("src/web/static/js/home/main.js");
  const modelManager = readSource(
    "src/web/static/js/activity_prediction/model_manager.js",
  );
  const trainingMonitor = readSource(
    "src/web/static/js/activity_prediction/training_monitor.js",
  );
  const dockingUi = readSource("src/web/static/js/docking/ui_manager.js");
  const indexTemplate = readSource("src/web/templates/index.html");
  const activityTemplate = readSource("src/web/templates/activity_prediction.html");
  const dockingTemplate = readSource("src/web/templates/molecular_docking.html");

  collectCheck(
    failures,
    "legacy admin helper source is retired",
    () => assertNoLegacyAdminAuth(sharedAdminFetch, "admin_fetch.js"),
  );
  collectCheck(
    failures,
    "home runtime source is free of legacy admin auth markers",
    () => assertNoLegacyAdminAuth(homeSource, "home/main.js"),
  );
  collectCheck(
    failures,
    "activity model manager source is free of legacy admin auth markers",
    () => assertNoLegacyAdminAuth(modelManager, "activity_prediction/model_manager.js"),
  );
  collectCheck(
    failures,
    "training monitor source is free of legacy admin auth markers",
    () => assertNoLegacyAdminAuth(trainingMonitor, "activity_prediction/training_monitor.js"),
  );
  collectCheck(
    failures,
    "docking ui source is free of legacy admin auth markers",
    () => assertNoLegacyAdminAuth(dockingUi, "docking/ui_manager.js"),
  );
  collectCheck(
    failures,
    "index template is free of legacy admin auth markers",
    () => assertNoLegacyAdminAuth(indexTemplate, "templates/index.html"),
  );
  collectCheck(
    failures,
    "activity template is free of legacy admin auth markers",
    () => assertNoLegacyAdminAuth(activityTemplate, "templates/activity_prediction.html"),
  );
  collectCheck(
    failures,
    "docking template is free of legacy admin auth markers",
    () => assertNoLegacyAdminAuth(dockingTemplate, "templates/molecular_docking.html"),
  );

  collectCheck(
    failures,
    "home LLM config fetch uses plain fetch",
    () =>
      assertUsesPlainFetch(
        homeSource,
        /(^|[^.\w])fetch\(\s*["']\/api\/llm\/config["']/m,
        "home/main.js LLM config caller",
      ),
  );
  collectCheck(
    failures,
    "home LLM test fetch uses plain fetch",
    () =>
      assertUsesPlainFetch(
        homeSource,
        /(^|[^.\w])fetch\(\s*["']\/api\/llm\/test["']/m,
        "home/main.js LLM test caller",
      ),
  );
  collectCheck(
    failures,
    "home model switch fetch uses plain fetch",
    () =>
      assertUsesPlainFetch(
        homeSource,
        /(^|[^.\w])fetch\(\s*["']\/api\/switch_model["']/m,
        "home/main.js model switch caller",
      ),
  );
  collectCheck(
    failures,
    "activity model list uses plain fetch",
    () =>
      assertUsesPlainFetch(
        modelManager,
        /(^|[^.\w])fetch\(\s*["']\/api\/activity\/models["']/m,
        "activity_prediction/model_manager.js list caller",
      ),
  );
  collectCheck(
    failures,
    "activity model switch uses plain fetch",
    () =>
      assertUsesPlainFetch(
        modelManager,
        /(^|[^.\w])fetch\(\s*["']\/api\/activity\/models\/switch["']/m,
        "activity_prediction/model_manager.js switch caller",
      ),
  );
  collectCheck(
    failures,
    "activity model delete uses plain fetch",
    () =>
      assertUsesPlainFetch(
        modelManager,
        /(^|[^.\w])fetch\(\s*`\/api\/activity\/models\/\$\{modelId\}`/m,
        "activity_prediction/model_manager.js delete caller",
      ),
  );
  collectCheck(
    failures,
    "training monitor status poll uses plain fetch",
    () =>
      assertUsesPlainFetch(
        trainingMonitor,
        /(^|[^.\w])fetch\(\s*`\/api\/activity\/train\/status\/\$\{jobId\}`/m,
        "activity_prediction/training_monitor.js status poller",
      ),
  );
  collectCheck(
    failures,
    "docking history list uses plain fetch",
    () =>
      assertUsesPlainFetch(
        dockingUi,
        /(^|[^.\w])fetch\(\s*["']\/api\/docking\/history["']/m,
        "docking/ui_manager.js history list caller",
      ),
  );
  collectCheck(
    failures,
    "docking history clear uses plain fetch",
    () =>
      assertUsesPlainFetch(
        dockingUi,
        /(^|[^.\w])fetch\(\s*["']\/api\/docking\/history["']\s*,\s*\{\s*method:\s*["']DELETE["']/m,
        "docking/ui_manager.js history clear caller",
      ),
  );
  collectCheck(
    failures,
    "docking history delete uses plain fetch",
    () =>
      assertUsesPlainFetch(
        dockingUi,
        /(^|[^.\w])fetch\(\s*`\/api\/docking\/history\/\$\{jobId\}`\s*,\s*\{\s*method:\s*["']DELETE["']/m,
        "docking/ui_manager.js history delete caller",
      ),
  );

  if (failures.length) {
    throw new Error(`Legacy admin auth still present:\n- ${failures.join("\n- ")}`);
  }

  console.log("Legacy admin auth removed from runtime source surfaces");
})();
