"use strict";
const assert = require("assert");
const fs = require("fs");
const source = fs.readFileSync(require("path").join(__dirname, "../src/web/static/js/home/main.js"), "utf8");
const start = source.indexOf("  function renderMoleculeCandidates(");
const end = source.indexOf("\n  window.HomeMain =", start);
assert(start >= 0 && end > start);

class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.style = {}; this.attributes = {}; this.isConnected = true; }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren(...children) { this.children = children; }
  setAttribute(key, value) { this.attributes[key] = value; }
  getAttribute(key) { return this.attributes[key]; }
  addEventListener() {}
  contains(child) { return this.children.includes(child) || this.children.some(item => item.contains(child)); }
  querySelectorAll() { return []; }
}
const document = {createElement: tag => new Element(tag)};
const render = new Function("document", "window", "scientificReferences", "evidenceReportViews",
  `${source.slice(start, end)}\nreturn renderMoleculeCandidates;`)(document, {}, null, new WeakMap());
function renderCases(candidates, singleCandidate) {
  const box = new Element("div");
  const wrapper = {querySelector: () => box};
  const message = {closest: () => wrapper, querySelectorAll: () => []};
  render(message, {candidates, warnings: []}, singleCandidate);
  const all = node => [node, ...node.children.flatMap(all)];
  return all(box).filter(node => node.tag === "details");
}
const one = {candidate_id: "candidate-1", canonical_smiles: "CCO"};
assert.equal(renderCases([one], true)[0].open, true, "single candidate SMILES should be visible");
assert.notEqual(renderCases([one, {...one, candidate_id: "candidate-2", canonical_smiles: "CCN"}], false)[0].open, true,
  "multiple candidates should retain compact cards");
const displayStart = source.indexOf("  function displayCandidateCollections(");
const displayEnd = source.indexOf("\n  // DOM元素缓存", displayStart);
assert(displayStart >= 0 && displayEnd > displayStart);
const options = [];
const display = new Function("evidenceReportLifecycle", "evidenceReportViews", "scientificReferences", "renderMoleculeCandidates",
  `${source.slice(displayStart, displayEnd)}\nreturn displayCandidateCollections;`)(
  {take: () => null}, new WeakMap(), null, (_element, _payload, single) => options.push(single));
display({}, [{candidates: [one]}]);
display({}, [{candidates: [one]}, {candidates: [{...one, candidate_id: "candidate-2"}]}]);
assert.deepEqual(options, [true, false, false], "count candidates across all displayed collections");
console.log("single-candidate SMILES visibility passed");
