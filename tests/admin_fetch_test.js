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

(() => {
  const adminFetchSource = readSource("src/web/static/js/shared/admin_fetch.js");
  const modelManager = readSource(
    "src/web/static/js/activity_prediction/model_manager.js",
  );
  const dockingTemplate = readSource(
    "src/web/templates/molecular_docking.html",
  );
  const dockingUi = readSource("src/web/static/js/docking/ui_manager.js");
  const homeSource = readSource("src/web/static/js/home/main.js");
  const homeTemplate = readSource("src/web/templates/index.html");
  const activityMain = readSource("src/web/static/js/activity_prediction/main.js");
  const trainingMonitor = readSource(
    "src/web/static/js/activity_prediction/training_monitor.js",
  );
  const activityTemplate = readSource(
    "src/web/templates/activity_prediction.html",
  );
  const failures = [];

  collectCheck(failures, "admin fetch helper no longer exposes auth symbols", () => {
    assert.strictEqual(
      adminFetchSource.includes("MedChatAdminAuth"),
      false,
      "src/web/static/js/shared/admin_fetch.js still exposes MedChatAdminAuth",
    );
    assert.strictEqual(
      adminFetchSource.includes("medchat_admin_token"),
      false,
      "src/web/static/js/shared/admin_fetch.js still stores medchat_admin_token",
    );
    assert.strictEqual(
      adminFetchSource.includes("X-MedChat-Admin-Token"),
      false,
      "src/web/static/js/shared/admin_fetch.js still injects X-MedChat-Admin-Token",
    );
  });
  collectCheck(failures, "admin fetch helper no longer prompts for tokens", () => {
    assert.strictEqual(
      adminFetchSource.includes("prompt("),
      false,
      "src/web/static/js/shared/admin_fetch.js still prompts for the admin token",
    );
  });
  collectCheck(failures, "home runtime no longer mentions admin auth symbols", () => {
    assert.strictEqual(
      homeSource.includes("MedChatAdminAuth"),
      false,
      "src/web/static/js/home/main.js still references MedChatAdminAuth",
    );
  });
  collectCheck(failures, "docking runtime no longer mentions admin auth symbols", () => {
    assert.strictEqual(
      dockingUi.includes("MedChatAdminAuth"),
      false,
      "src/web/static/js/docking/ui_manager.js still references MedChatAdminAuth",
    );
  });
  collectCheck(
    failures,
    "activity model runtime no longer mentions admin auth symbols",
    () => {
      assert.strictEqual(
        modelManager.includes("MedChatAdminAuth"),
        false,
        "src/web/static/js/activity_prediction/model_manager.js still references MedChatAdminAuth",
      );
    },
  );
  collectCheck(
    failures,
    "training monitor runtime no longer mentions admin auth symbols",
    () => {
      assert.strictEqual(
        trainingMonitor.includes("MedChatAdminAuth"),
        false,
        "src/web/static/js/activity_prediction/training_monitor.js still references MedChatAdminAuth",
      );
    },
  );
  collectCheck(failures, "activity main runtime no longer mentions admin auth symbols", () => {
    assert.strictEqual(
      activityMain.includes("MedChatAdminAuth"),
      false,
      "src/web/static/js/activity_prediction/main.js still references MedChatAdminAuth",
    );
  });
  collectCheck(failures, "templates no longer load admin_fetch.js", () => {
    assert.strictEqual(
      homeTemplate.includes("/static/js/shared/admin_fetch.js"),
      false,
      "src/web/templates/index.html still loads admin_fetch.js",
    );
    assert.strictEqual(
      dockingTemplate.includes("/static/js/shared/admin_fetch.js"),
      false,
      "src/web/templates/molecular_docking.html still loads admin_fetch.js",
    );
    assert.strictEqual(
      activityTemplate.includes("/static/js/shared/admin_fetch.js"),
      false,
      "src/web/templates/activity_prediction.html still loads admin_fetch.js",
    );
  });
  collectCheck(failures, "home fetches LLM config directly", () => {
    assert.match(homeSource, /fetch\("\/api\/llm\/config"/);
  });
  collectCheck(failures, "docking UI fetches history directly", () => {
    assert.match(dockingUi, /fetch\("\/api\/docking\/history"/);
  });
  collectCheck(failures, "activity model manager fetches models directly", () => {
    assert.match(modelManager, /fetch\("\/api\/activity\/models"/);
  });
  collectCheck(failures, "training monitor fetches status directly", () => {
    assert.match(trainingMonitor, /fetch\(`\/api\/activity\/train\/status\/\$\{jobId\}`/);
  });
  collectCheck(failures, "activity main fetches training directly", () => {
    assert.match(activityMain, /fetch\("\/api\/activity\/train"/);
  });

  if (failures.length) {
    throw new Error(`Protected caller checks failed:\n- ${failures.join("\n- ")}`);
  }

  console.log("Protected admin caller source checks passed");
})();
