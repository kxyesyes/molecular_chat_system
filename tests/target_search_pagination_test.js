const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("node:vm");
const { test } = require("node:test");

const projectRoot = path.resolve(__dirname, "..");
const source = fs.readFileSync(
  path.join(projectRoot, "src", "web", "static", "js", "target_search", "main.js"),
  "utf8",
);
const styles = fs.readFileSync(
  path.join(projectRoot, "src", "web", "static", "css", "target_search.css"),
  "utf8",
);

assert.match(source, /STRUCTURES_PER_PAGE\s*=\s*8/);
assert.match(source, /structurePage/);
assert.doesNotMatch(source, /const visibleStructures = structures\.slice\(0, 8\);/);
assert.match(source, /data-structure-page/);
assert.match(source, /structure-page-status/);
assert.match(source, /structure-page-btn/);
assert.match(styles, /\.structure-pagination\s*\{/);
assert.match(source, /window\.location\.href\s*=\s*`\/molecular-docking\?target_structure=/);

const dockingSource = fs.readFileSync(
  path.join(projectRoot, "src", "web", "static", "js", "docking", "main.js"),
  "utf8",
);
assert.match(dockingSource, /loadTargetStructureFromQuery/);
assert.match(dockingSource, /target_structure/);
assert.match(dockingSource, /target_structure_id/);
assert.match(dockingSource, /download\?format=pdb/);
assert.match(dockingSource, /请提供配体并确认对接盒后再提交任务/);

console.log("target search structure pagination checks passed");

function pending() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function createBrowser() {
  const searches = [], details = [], handoffs = [], elements = new Map();
  const location = { href: "/target-search" };
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      value: "", textContent: "", innerHTML: "", querySelector: () => null,
      appendChild() {}, replaceChildren() { this.innerHTML = ""; },
    });
    return elements.get(id);
  }
  function queue(requests) {
    const request = pending();
    requests.push(request);
    return request.promise;
  }
  const context = vm.createContext({
    window: { location }, document: {
      addEventListener() {}, getElementById: element, querySelectorAll: () => [],
      createElement: () => ({ appendChild() {} }),
    },
    TargetSearchApi: {
      search: () => queue(searches), detail: () => queue(details),
      sendToDocking: () => queue(handoffs),
    },
  });
  vm.runInContext(source, context);
  return { app: vm.runInContext("TargetSearchApp", context), element, searches, details, handoffs, location };
}

const settle = () => new Promise(resolve => setImmediate(resolve));

const prepared = id => ({ file_format: "pdb", structure_db_id: id, preflight: { cache_status: "available" } });

test("only the last structure selected for docking can navigate", async () => {
  const { app, handoffs, location } = createBrowser();
  const firstButton = {}, secondButton = {};
  const first = app.sendToDocking(11, firstButton);
  const second = app.sendToDocking(22, secondButton);
  handoffs[0].resolve(prepared(11));
  await first;
  assert.equal(location.href, "/target-search");
  assert.equal(firstButton.disabled, false);
  assert.equal(secondButton.disabled, true);
  handoffs[1].resolve(prepared(22));
  await second;
  assert.equal(location.href, "/molecular-docking?target_structure=22");
});

test("a superseded handoff cannot replace a failure or navigate after clearing the search", async () => {
  const { app, handoffs, location, element } = createBrowser();
  const first = app.sendToDocking(11, {});
  const second = app.sendToDocking(22, {});
  handoffs[1].reject(new Error("current failure"));
  await second;
  const message = element("targetMessage").textContent;
  handoffs[0].reject(new Error("older failure"));
  await first;
  assert.match(message, /current failure/);
  assert.equal(element("targetMessage").textContent, message);
  const retry = app.sendToDocking(22, {});
  app.searchPreset("");
  handoffs[2].resolve(prepared(22));
  await retry;
  assert.equal(location.href, "/target-search");
});

test("an older query cannot publish results, errors, or release a newer query's loading state", async () => {
  const { app, element, searches } = createBrowser();
  app.searchPreset("old");
  app.searchPreset("new");
  searches[0].resolve({ query: "old", results: [] });
  await settle();
  assert.match(element("resultSummary").textContent, /new/);
  assert.equal(element("targetSearchBtn").disabled, true);
  app.searchPreset("final");
  searches[2].resolve({ query: "final", results: [] });
  await settle();
  searches[1].reject(new Error("old error"));
  await settle();
  assert.match(element("resultSummary").textContent, /final/);
  assert.equal(element("targetMessage").hidden, true);
  assert.equal(element("targetSearchBtn").disabled, false);
});

test("detail responses and errors cannot replace a later selection or restore a cleared search", async () => {
  const { app, element, details, searches } = createBrowser();
  const a = app.loadDetail(1), b = app.loadDetail(2), c = app.loadDetail(3);
  details[2].resolve({ target_id: 3, gene_symbol: "THIRD" });
  await c;
  details[0].resolve({ target_id: 1, gene_symbol: "FIRST" });
  details[1].reject(new Error("second failed"));
  await Promise.all([a, b]);
  assert.match(element("targetDetail").innerHTML, /THIRD/);
  app.searchPreset("pending");
  app.searchPreset("");
  searches[0].resolve({ query: "pending", results: [{ target_id: 4 }] });
  await settle();
  assert.equal(details.length, 3);
  assert.doesNotMatch(element("resultSummary").textContent, /pending/);
});
