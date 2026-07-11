const fs = require("fs");
const path = require("path");

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

const activityHtml = read("src/web/templates/activity_prediction.html");
const modelManager = read(
  "src/web/static/js/activity_prediction/model_manager.js",
);
const preflight = read("src/web/static/js/activity_prediction/preflight.js");
const resultsRenderer = read(
  "src/web/static/js/activity_prediction/results_renderer.js",
);

const safeRenderScript = "/static/js/shared/safe_render.js";
const modelManagerScript = "/static/js/activity_prediction/model_manager.js";
const safeRenderIndex = activityHtml.indexOf(safeRenderScript);
const modelManagerIndex = activityHtml.indexOf(modelManagerScript);

assert(
  safeRenderIndex >= 0,
  "Activity prediction page must load /static/js/shared/safe_render.js",
);
assert(
  modelManagerIndex >= 0 && safeRenderIndex < modelManagerIndex,
  "Activity prediction page must load safe_render.js before model_manager.js",
);

[
  '<div class="popover-title">${model.name}</div>',
  '<span class="metric-value">${model.target}</span>',
].forEach((snippet) => {
  assert(
    !modelManager.includes(snippet),
    `Activity model popover must not inject raw runtime metadata: ${snippet}`,
  );
});

["return `<th>${header}</th>`;", "return `<td>${cell}</td>`;"].forEach(
  (snippet) => {
    assert(
      !preflight.includes(snippet),
      `Activity preflight table must not inject raw CSV values: ${snippet}`,
    );
  },
);

[
  "${item.smiles || \"-\"}</td>",
  "${item.error || \"Error\"}</td>",
  "${cls}</span></td>",
].forEach((snippet) => {
  assert(
    !resultsRenderer.includes(snippet),
    `Activity results table must not inject raw prediction values: ${snippet}`,
  );
});

console.log("Activity prediction safe rendering static XSS checks passed");
