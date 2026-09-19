"use strict";

// Engineering-only DOM acceptance; this does not run scientific inference.
const assert = require("node:assert/strict");
const {setup, cells, assertNoExecutableNodes} = require("./activity_family_results_test.js");

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const {spawnSync} = require("node:child_process");
const MAX_INPUT_BYTES = 1024 * 1024;
const PUBLIC_FAILURE = {status: "failed", error: "family_dom_acceptance_failed"};

const STATUS_LABELS = {passed: "完成", partial: "部分完成", failed: "失败"};
const isRecord = value => value !== null && typeof value === "object" && !Array.isArray(value);

// Independent expectation for displayed family conflicts, including old responses.
function needsReview(row) {
  return row.status !== "failed" && row.execution_status !== "failed"
    && !Object.keys(row.errors || {}).length
    && Number.isFinite(row.activity_probability) && row.activity_probability >= 0
    && row.activity_probability <= 1 && Number.isFinite(row.predicted_pIC50)
    && (row.classification_regression_consistent === false
      || (row.activity_probability >= .5) !== (row.predicted_pIC50 >= 5));
}

function assertDisplayedPrediction(row, expected) {
  const shown = cells(row);
  assert.equal(shown.length, 6);
  assert.equal(shown[0], expected.smiles || "—");
  assert.equal(shown[1], expected.status !== "failed" && Number.isFinite(expected.predicted_pIC50)
    ? expected.predicted_pIC50.toFixed(4) : "不可用");
  assert.equal(shown[2], expected.activity_class || "不可用");
  const probability = expected.activity_probability;
  assert.equal(shown[3], expected.status !== "failed" && Number.isFinite(probability)
    && probability >= 0 && probability <= 1 ? (100 * probability).toFixed(1) + "%" : "不可用");
  assert.equal(shown[4], needsReview(expected) ? "需复核" : STATUS_LABELS[expected.status]);

  const detailCell = row.children[5];
  const source = detailCell.children.find(node => node.tagName === "details");
  assert.ok(source);
  assert.deepEqual(source.children.filter(node => node.tagName === "p").map(node => node.textContent), [
    "请求靶点：" + (expected.requested_target || "不可用"),
    "家族：" + (expected.family_id || expected.provenance.family_id || "不可用"),
    "模型组：" + (expected.bundle_id || expected.provenance.bundle_id || "不可用"),
  ]);
  const evidence = source.children.find(node => node.tagName === "pre");
  assert.ok(evidence);
  assert.deepEqual(JSON.parse(evidence.textContent), expected.provenance);
  const notes = [...expected.warnings];
  if (expected.error) notes.push(expected.error);
  if (expected.note) notes.push(expected.note);
  for (const [stage, error] of Object.entries(expected.errors)) {
    notes.push(`${stage}: ${typeof error === "string" ? error : JSON.stringify(error)}`);
  }
  if (needsReview(expected)) {
    const source = expected.provenance;
    const complete = expected.execution_status === "passed" && expected.bundle_id
      && source?.bundle_id === expected.bundle_id && source.models?.classification?.model_id
      && source.models?.regression?.model_id;
    notes.push(complete ? "计算已完成，分类与回归不一致，需复核；已保留两项原始结果。"
      : "分类与回归结果不一致，需复核；执行状态或来源未确认，已保留返回数值。");
  }
  assert.equal(detailCell.textContent, (notes.join("；") || "—") + source.textContent);
  assertNoExecutableNodes(row);
}

// Accept the API summary itself, not reconstructed rows or current form selections.
// Passing means DOM fidelity only, including faithful display of scientific failure.
function assertSummary(summary) {
  assert.ok(isRecord(summary));
  assert.ok(Array.isArray(summary.results));
  assert.ok(Object.hasOwn(STATUS_LABELS, summary.status));
  assert.equal(summary.success, summary.status === "passed");
  assert.ok(Array.isArray(summary.warnings) && summary.warnings.every(value => typeof value === "string"));
  for (const row of summary.results) {
    assert.ok(isRecord(row));
    assert.equal(typeof row.smiles, "string");
    assert.equal(typeof row.requested_target, "string");
    assert.ok(Object.hasOwn(STATUS_LABELS, row.status));
    assert.equal(row.success, row.status === "passed");
    assert.ok(Array.isArray(row.warnings) && row.warnings.every(value => typeof value === "string"));
    assert.ok(isRecord(row.errors));
    assert.ok(isRecord(row.provenance));
    assert.ok(row.predicted_pIC50 === null || Number.isFinite(row.predicted_pIC50));
    assert.ok(row.activity_probability === null || (Number.isFinite(row.activity_probability)
      && row.activity_probability >= 0 && row.activity_probability <= 1));
    assert.ok(row.activity_class === null || typeof row.activity_class === "string");
    if (row.status !== "failed") {
      for (const key of ["family_id", "bundle_id"]) {
        assert.ok(typeof row[key] === "string" && row[key].length > 0);
      }
    }
    // Input/bundle failure can legitimately have no models. Observations cannot.
    if (row.status !== "failed" || row.provenance.models !== undefined) {
      assert.ok(isRecord(row.provenance.models));
      for (const task of ["classification", "regression"]) {
        const model = row.provenance.models[task];
        assert.ok(isRecord(model));
        for (const key of ["model_id", "weights_sha256", "model_card_sha256"]) {
          assert.ok(typeof model[key] === "string" && model[key].length > 0);
        }
      }
    }
  }
  const before = JSON.stringify(summary);
  const h = setup();
  for (const isSingleRequest of [true, false]) {
    h.ids.activityTarget.value = "WRONG-FORM-TARGET";
    h.ids.batchActivityTarget.value = "WRONG-BATCH-TARGET";
    h.render(summary.results, {isSingleRequest}, summary);
    assert.equal(h.ids.resultsBody.children.length, summary.results.length);
    h.ids.resultsBody.children.forEach((row, index) => assertDisplayedPrediction(row, summary.results[index]));
    assert.equal(h.ids.predictionStatus.textContent, [
      STATUS_LABELS[summary.results.some(needsReview) ? "partial" : summary.results.length ? summary.status : "failed"],
      ...(summary.results.some(needsReview) ? ["含分类与回归不一致的结果，需复核"] : []),
      ...(!summary.results.length ? ["无预测结果"] : []), ...summary.warnings,
    ].join("；"));
    assertNoExecutableNodes(h.ids.predictionStatus);
    assert.ok(h.ids.results.classes.has("show"));
    assert.equal(h.ids.singleSummary.style.display, "none");
    assert.equal(h.ids.batchHistogram.style.display, "none");
    assert.equal(h.calls.model, 0);
    assert.equal(h.calls.histogram.length, 0);
  }
  assert.equal(JSON.stringify(summary), before);
  return {status: "passed", rows: summary.results.length};
}

function readSummaryFile(filename) {
  assert.equal(typeof filename, "string");
  assert.ok(filename && !filename.startsWith("-") && !/^[\\/]{2}/.test(filename));
  assert.ok(!filename.includes(":") || /^[a-z]:[\\/][^:]*$/i.test(filename));
  assert.equal(path.extname(filename).toLowerCase(), ".json");
  const initial = fs.lstatSync(filename);
  assert.ok(initial.isFile() && !initial.isSymbolicLink());
  assert.ok(initial.size <= MAX_INPUT_BYTES);
  const fd = fs.openSync(filename, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0)
    | (fs.constants.O_NONBLOCK || 0));
  try {
    const stat = fs.fstatSync(fd);
    assert.ok(stat.isFile() && stat.size <= MAX_INPUT_BYTES);
    // Bound the read as well as the stat, including a file growing after stat.
    const buffer = Buffer.alloc(MAX_INPUT_BYTES + 1);
    let length = 0;
    while (length < buffer.length) {
      const count = fs.readSync(fd, buffer, length, buffer.length - length, null);
      if (!count) break;
      length += count;
    }
    assert.ok(length <= MAX_INPUT_BYTES);
    return JSON.parse(buffer.subarray(0, length).toString("utf8"));
  } finally {
    fs.closeSync(fd);
  }
}

function child(args) {
  return spawnSync(process.execPath, args, {
    encoding: "utf8", timeout: 10000, maxBuffer: 8192, windowsHide: true, env: {},
  });
}

// These rows are engineering fixtures, NOT model predictions or real assets.
function syntheticSummary() {
  const row = {
    smiles: "CCO", requested_target: "PDE5A", family_id: "synthetic-family",
    bundle_id: "synthetic-bundle", success: true, status: "passed",
    predicted_pIC50: 4.32109, activity_probability: 0.12345, activity_class: "无活性",
    warnings: [], errors: {}, provenance: {
      family_id: "synthetic-family", bundle_id: "synthetic-bundle", scope: "engineering-only",
      source_sha256: "a".repeat(64), models: Object.fromEntries(
        ["classification", "regression"].map(task => [task, {
          model_id: "synthetic-" + task, weights_sha256: "b".repeat(64),
          model_card_sha256: "c".repeat(64),
        }])),
    },
  };
  return {success: true, status: "passed", results: [row], warnings: []};
}

function runSelfTests() {
  const tests = [];
  const test = (name, run) => tests.push({name, run});
  test("fixture exports", () => {
    for (const helper of [setup, cells, assertNoExecutableNodes]) assert.equal(typeof helper, "function");
  });
  test("both imports are silent", () => {
    for (const filename of [require.resolve("./activity_family_results_test.js"), __filename]) {
      const result = child(["-e", "require(process.argv[1])", filename]);
      assert.equal(result.status, 0);
      assert.equal(result.stdout, "");
      assert.equal(result.stderr, "");
    }
  });
  test("returned numbers and provenance", () => {
    assert.deepEqual(assertSummary(syntheticSummary()), {status: "passed", rows: 1});
  });
  test("computed conflict stays partial and visibly needs review", () => {
    const summary = syntheticSummary();
    Object.assign(summary, {status: "partial", success: false});
    Object.assign(summary.results[0], {status: "partial", success: false,
      execution_status: "passed", classification_regression_consistent: false,
      activity_probability: 0.2, predicted_pIC50: 6.1,
      warnings: ["分类与回归预测不一致，需复核；已保留两项原始结果。"]});
    assert.deepEqual(assertSummary(summary), {status: "passed", rows: 1});
  });
  test("legacy conflict does not invent completed execution", () => {
    const summary = syntheticSummary();
    summary.results[0].predicted_pIC50 = 6.1;
    assert.deepEqual(assertSummary(summary), {status: "passed", rows: 1});
  });
  test("mixed order duplicates partial zero null and errors", () => {
    const summary = syntheticSummary();
    const duplicate = JSON.parse(JSON.stringify(summary.results[0]));
    duplicate.status = "partial";
    duplicate.success = false;
    duplicate.predicted_pIC50 = 0;
    duplicate.activity_probability = 0;
    duplicate.warnings = ["engineering warning"];
    duplicate.errors = {regression: {code: "engineering-stage-error"}};
    duplicate.requested_target = "BuChE";
    duplicate.family_id = "other-engineering-family";
    duplicate.provenance.models.regression.model_id = "other-engineering-model";
    const failed = {...duplicate, smiles: "invalid", status: "failed", predicted_pIC50: null,
      activity_probability: null, activity_class: null, family_id: null, bundle_id: null,
      provenance: {}, errors: {input: "invalid_smiles"}};
    summary.results.push(failed, duplicate);
    summary.status = "partial";
    summary.success = false;
    summary.warnings = ["aggregate engineering warning"];
    const before = JSON.stringify(summary);
    assert.deepEqual(assertSummary(summary), {status: "passed", rows: 3});
    assert.equal(JSON.stringify(summary), before, "Do not relabel or mutate API rows");
    duplicate.predicted_pIC50 = null;
    duplicate.activity_probability = null;
    assert.deepEqual(assertSummary(summary), {status: "passed", rows: 3});
  });
  test("failed and empty summaries", () => {
    assert.deepEqual(assertSummary({success: false, status: "failed", results: [], warnings: []}),
      {status: "passed", rows: 0});
    const summary = syntheticSummary();
    Object.assign(summary, {success: false, status: "failed"});
    Object.assign(summary.results[0], {success: false, status: "failed", predicted_pIC50: null,
      activity_probability: null, activity_class: null, errors: {bundle: "unavailable"}});
    assert.deepEqual(assertSummary(summary), {status: "passed", rows: 1});
  });
  test("hostile engineering strings remain inert", () => {
    const summary = syntheticSummary();
    const hostile = '<img src=x onerror="alert(1)"><script>throw 1</script>';
    const row = summary.results[0];
    // Dedicated text-safety fixture only; never rewrite a supplied API response.
    for (const key of ["smiles", "requested_target", "family_id", "bundle_id", "activity_class"]) row[key] = hostile;
    row.warnings = [hostile];
    row.errors = {[hostile]: hostile};
    row.provenance.extra = {nested: hostile};
    row.provenance.models.classification.model_id = hostile;
    summary.warnings = [hostile];
    assert.deepEqual(assertSummary(summary), {status: "passed", rows: 1});
  });
  test("malformed summary rejected", () => {
    for (const summary of [null, [], {}, {results: {}}, {...syntheticSummary(), status: "unknown"},
      {...syntheticSummary(), success: "true"}, {...syntheticSummary(), results: [null]}]) {
      assert.throws(() => assertSummary(summary));
    }
  });
  test("missing model identities and hashes rejected", () => {
    for (const task of ["classification", "regression"]) {
      for (const key of ["model_id", "weights_sha256", "model_card_sha256"]) {
        const summary = syntheticSummary();
        delete summary.results[0].provenance.models[task][key];
        assert.throws(() => assertSummary(summary));
      }
    }
  });
  test("missing or malformed returned observations rejected", () => {
    for (const key of ["predicted_pIC50", "activity_probability", "activity_class"]) {
      const summary = syntheticSummary();
      delete summary.results[0][key];
      assert.throws(() => assertSummary(summary));
    }
    for (const [key, value] of [["predicted_pIC50", "0"], ["activity_probability", false],
      ["activity_probability", -0.1], ["activity_probability", 1.1], ["activity_class", {}]]) {
      const summary = syntheticSummary();
      summary.results[0][key] = value;
      assert.throws(() => assertSummary(summary));
    }
  });
  test("observed rows require returned family and bundle identities", () => {
    for (const key of ["family_id", "bundle_id"]) {
      const summary = syntheticSummary();
      delete summary.results[0][key];
      assert.throws(() => assertSummary(summary));
    }
  });

  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "family-dom-engineering-"));
  try {
    const input = path.join(directory, "summary.json");
    fs.writeFileSync(input, JSON.stringify(syntheticSummary()));
    test("existing JSON is read unchanged", () => {
      assert.deepEqual(readSummaryFile(input), syntheticSummary());
    });
    test("input CLI emits only small protocol JSON", () => {
      const result = child([__filename, "--input", input]);
      assert.equal(result.status, 0);
      assert.equal(result.stderr, "");
      assert.deepEqual(JSON.parse(result.stdout), {status: "passed", rows: 1});
    });
    test("one MiB inclusive byte limit", () => {
      const filename = path.join(directory, "boundary.json");
      const content = JSON.stringify(syntheticSummary());
      fs.writeFileSync(filename, content + " ".repeat(MAX_INPUT_BYTES - Buffer.byteLength(content)));
      assert.deepEqual(readSummaryFile(filename), syntheticSummary());
      fs.appendFileSync(filename, " ");
      assert.throws(() => readSummaryFile(filename));
    });
    test("missing nonregular nonJSON and executable inputs rejected", () => {
      const script = path.join(directory, "payload.js");
      fs.writeFileSync(script, "throw new Error('engineering payload must never execute')");
      const malformed = path.join(directory, "malformed.json");
      fs.writeFileSync(malformed, "throw new Error('engineering payload must never execute')");
      const folder = path.join(directory, "folder.json");
      fs.mkdirSync(folder);
      for (const filename of [script, malformed, folder, path.join(directory, "missing.json"),
        "https://invalid.example/summary.json", "//invalid/share/summary.json", "--eval=payload.json"]) {
        assert.throws(() => readSummaryFile(filename));
      }
    });
    test("CLI rejects extra or executable arguments without payload disclosure", () => {
      const privateInput = path.join(directory, "private.json");
      fs.writeFileSync(privateInput, JSON.stringify({results: [{smiles: "DO-NOT-ECHO-ENGINEERING"}]}));
      for (const args of [["--eval", "throw 1"], ["--input"], [input],
        ["--input", input, "--require", "payload.js"], ["--input", privateInput],
        ["--input", path.join(directory, "missing.json")]]) {
        const result = child([__filename, ...args]);
        assert.equal(result.status, 1);
        assert.equal(result.stderr, "");
        assert.deepEqual(JSON.parse(result.stdout), PUBLIC_FAILURE);
      }
    });
    let passed = 0;
    const failures = [];
    for (const {name, run} of tests) {
      try { run(); passed++; } catch { failures.push(name); }
    }
    if (failures.length) {
      process.exitCode = 1;
      return {status: "failed", tests: tests.length, passed, failures};
    }
    return {status: "passed", tests: tests.length, passed, scope: "engineering-only"};
  } finally {
    // Only this invocation's fresh, owned temporary fixture directory.
    assert.equal(path.dirname(path.resolve(directory)), path.resolve(os.tmpdir()));
    assert.ok(path.basename(directory).startsWith("family-dom-engineering-"));
    fs.rmSync(directory, {recursive: true, force: true});
  }
}

module.exports = {assertSummary, assertDisplayedPrediction, readSummaryFile};

if (require.main === module) {
  try {
    const args = process.argv.slice(2);
    let result;
    if (!args.length) result = runSelfTests();
    else {
      assert.equal(args.length, 2);
      assert.equal(args[0], "--input");
      result = assertSummary(readSummaryFile(args[1]));
    }
    process.stdout.write(JSON.stringify(result) + "\n");
  } catch {
    process.stdout.write(JSON.stringify(PUBLIC_FAILURE) + "\n");
    process.exitCode = 1;
  }
}
