const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const projectRoot = path.resolve(__dirname, "..");
const rendererPath = path.join(
  projectRoot,
  "src",
  "web",
  "static",
  "js",
  "reverse_target",
  "results_renderer.js",
);

const source = fs.readFileSync(rendererPath, "utf8");
const context = {
  document: {
    getElementById: () => null,
    querySelector: () => ({ innerHTML: "" }),
  },
  window: {},
  FEAT_META: {},
  RT_CONFIG: { DEFAULTS: { THRESHOLD: 0.6 } },
  RtApi: {},
  RtViewer: {},
};
vm.createContext(context);
const RtResults = vm.runInContext(`${source}\nRtResults;`, context);

const rows = Array.from({ length: 100 }, (_, index) => ({ id: index + 1 }));
const pageOne = RtResults._test.paginateResults(rows, 1, 10);
const pageTen = RtResults._test.paginateResults(rows, 10, 10);
const pageTooLarge = RtResults._test.paginateResults(rows, 99, 10);

assert.strictEqual(pageOne.totalPages, 10);
assert.deepStrictEqual(pageOne.items.map((item) => item.id), [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
assert.deepStrictEqual(pageTen.items.map((item) => item.id), [91, 92, 93, 94, 95, 96, 97, 98, 99, 100]);
assert.strictEqual(pageTooLarge.page, 10);
assert.strictEqual(pageTooLarge.startIndex, 90);
assert.strictEqual(RtResults._test.escapeHtml('<img src=x onerror=alert(1)>'), '&lt;img src=x onerror=alert(1)&gt;');
assert.strictEqual(RtResults._test.escapeJsString("EGFR');alert(1);//"), "EGFR\\');alert(1);//");

const rowHtml = RtResults._test.singleResultRow(
  {
    target_name: "Phosphatidylinositol 4,5-bisphosphate 3-kinase catalytic subunit alpha isoform",
    organism: "Human",
    molecule_chembl_id: "CHEMBL1236962",
    standard_type: "IC50",
    standard_value: 0.04,
    similar_count: 165,
    final_similarity: 1,
    final_3d_score: 1,
    pharm_similarity: 1,
    alignment_score: 1,
    alignment_rmsd: 0,
    alignment_coverage: 1,
  },
  1,
  true,
);

assert.match(rowHtml, /class="rt-result-row"/);
assert.match(rowHtml, /class="rt-target-title"/);
assert.match(rowHtml, /class="rt-score-panel"/);
assert.match(rowHtml, /class="rt-row-actions"/);
assert.match(rowHtml, />相似分子 165<\/button>/);
assert.doesNotMatch(rowHtml, />查看\s*165\s*个分子</);

console.log("reverse target pagination checks passed");
