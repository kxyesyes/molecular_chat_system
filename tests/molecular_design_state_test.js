"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

function setup() {
  const elements = new Map();
  const requests = [];
  const context = vm.createContext({
    console, setTimeout, clearTimeout,
    document: { getElementById(id) {
      if (!elements.has(id)) elements.set(id, {
        textContent: "", innerHTML: "", className: "", style: {},
        classList: { add() {} },
        contentWindow: { ketcher: { async setMolecule() {} } },
      });
      return elements.get(id);
    } },
    DesignApi: { calcProperties: () => new Promise(resolve => requests.push(resolve)) },
    DesignUI: { esc: String, toast() {}, setFeedback() {}, closeImport() {} },
  });
  for (const file of ["config", "properties_panel", "molecule_editor", "history_manager"]) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, "../src/web/static/js/design", file + ".js"), "utf8"), context);
  }
  return { context, elements, requests, S: context.DesignState, P: context.PropertiesPanel, E: context.MoleculeEditor };
}

test("missing properties never become zero or passing rules", () => {
  const { P, elements } = setup();
  for (const missing of [null, undefined, NaN, Infinity, "", "unavailable", false]) {
    P.renderProps({ logp: missing, mw: missing, qed: missing, tpsa: missing, sa_score: missing });
    for (const id of ["pLogP", "pMW", "pQED", "pTPSA", "pSAS"]) assert.equal(elements.get(id).textContent, "—");
    P.renderRo5({ mw: missing, logp: missing, hbd: missing, hba: missing, rotbonds: missing, tpsa: missing });
    assert.doesNotMatch(elements.get("ro5Grid").innerHTML, /通过|ro5-dot pass/);
  }
  P.renderProps({ logp: 0 });
  assert.equal(elements.get("pLogP").textContent, "0.00");
});

test("candidate changes are neutral and missing values have no fabricated delta", () => {
  const { E, elements } = setup();
  E.addCandidateComparison({ logp: null, mw: 100 }, { logp: null, mw: 120 }, "CCO", "test");
  const html = elements.get("candidateCompare").innerHTML;
  assert.doesNotMatch(html, /candidate-metric (good|warn)|>\+?0\.00</);
  assert.match(html, /120\.00/);
  assert.match(html, /\+20\.00/);
});

test("repeated candidate keeps one entry in generation order", () => {
  const { E, S, elements } = setup();
  S.iter = 1;
  E.addCandidateComparison({ logp: 2 }, { logp: 1 }, "CCO", "first");
  S.iter = 2;
  E.addCandidateComparison({ logp: 2 }, { logp: 1 }, "CCO", "repeat");
  assert.equal(S.candidates.length, 1);
  assert.equal(S.candidates[0].step, 1);
  assert.match(elements.get("candidateCompare").innerHTML, /#1/);
});

test("substitution requires a fresh single-site detection", async () => {
  const { context, E, S, elements } = setup();
  let calls = 0;
  context.DesignApi.substitute = async () => { calls += 1; return { success: true, new_smiles: "CCO" }; };
  context.DesignApi.calcProperties = async () => ({ success: false, error: "not expected" });
  const frame = context.document.getElementById("ketcher-frame");
  frame.contentWindow.ketcher.getSmiles = async () => "CC[*]";
  S.smiles = "CC[*]";
  S.selectedFrag = { smi: "[*]O", label: "O" };
  S.detectedSiteCount = null;
  await E.execSubstitute();
  assert.equal(calls, 0);
});

test("property failure after substitution is not recorded as a successful candidate", async () => {
  const { context, E, S, elements } = setup();
  context.DesignApi.substitute = async () => ({ success: true, new_smiles: "CCO" });
  context.DesignApi.calcProperties = async () => ({ success: false, error: "property tool unavailable" });
  const frame = context.document.getElementById("ketcher-frame");
  frame.contentWindow.ketcher.getSmiles = async () => "CC[*]";
  S.smiles = "CC[*]";
  S.selectedFrag = { smi: "[*]O", label: "O" };
  S.detectedSiteCount = 1;
  await E.execSubstitute();
  assert.equal(S.candidates.length, 0);
  assert.equal(S.history.length, 0);
});

test("old property success cannot overwrite a newer molecule", async () => {
  const { P, requests, S } = setup();
  S.smiles = "CC";
  const first = P.calcProps("CC");
  S.smiles = "CCO";
  const second = P.calcProps("CCO");
  requests[1]({ success: true, properties: { mw: 46 } });
  await second;
  requests[0]({ success: true, properties: { mw: 30 } });
  assert.equal((await first).stale, true);
  assert.equal(S.curProps.mw, 46);
});

test("clear invalidates properties, goals, history and in-flight responses", async () => {
  const { P, E, requests, S } = setup();
  S.smiles = "CCO";
  S.curProps = { mw: 46 };
  S.curGoals = { items: [1] };
  S.history.push({ smi: "CCO" });
  const pending = P.calcProps("CCO");
  await E.clearCanvas();
  requests[0]({ success: true, properties: { mw: 46 } });
  assert.equal((await pending).stale, true);
  assert.equal(S.curProps, null);
  assert.equal(S.curGoals, null);
  assert.equal(S.history.length, 0);
});

test("restore clears prior properties immediately and binds refreshed values to SMILES", async () => {
  const { E, requests, S } = setup();
  S.smiles = "CC";
  S.curProps = { mw: 30 };
  const restore = E.restoreCandidate("CCO");
  assert.equal(S.curProps, null);
  for (let i = 0; i < 10 && !requests.length; i++) await Promise.resolve();
  requests[0]({ success: true, properties: { mw: 46 } });
  await restore;
  assert.equal(S.propsSmiles, "CCO");
  assert.equal(S.curProps.mw, 46);
});

test("unavailable status overrides a legacy numeric placeholder", async () => {
  const { P, S, requests, elements } = setup();
  S.smiles = "CCO";
  const pending = P.calcProps("CCO");
  requests[0]({ success: true, properties: { sa_score: 0 }, property_status: { sa_score: "unavailable" } });
  await pending;
  assert.equal(elements.get("pSAS").textContent, "—");
});

test("clearing while substitution runs never restores the obsolete product", async () => {
  const { context, E, S, elements } = setup();
  let resolve;
  context.DesignApi.substitute = () => new Promise(r => { resolve = r; });
  context.document.getElementById("ketcher-frame").contentWindow.ketcher.getSmiles = async () => "CC*";
  S.smiles = "CC*";
  S.selectedFrag = { smi: "*O", label: "O" };
  S.detectedSiteCount = 1;
  const pending = E.execSubstitute();
  for (let i = 0; i < 10 && !resolve; i++) await Promise.resolve();
  await E.clearCanvas();
  resolve({ success: true, new_smiles: "CCO" });
  await pending;
  assert.equal(S.smiles, "");
  assert.equal(S.candidates.length, 0);
  assert.equal(S.history.length, 0);
});
