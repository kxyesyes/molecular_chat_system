"use strict";

const assert = require("node:assert/strict");
const { test } = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function loadPolicy(origin = "https://medchat.example") {
  const context = vm.createContext({ window: { location: { origin } }, URL });
  for (const file of ["shared/safe_render.js", "home/formatters.js"]) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, "../src/web/static/js", file), "utf8"), context);
  }
  return context.window;
}

for (const value of [
  "/\\evil.example/path", "\\\\evil.example/path", "/\t/evil.example/path",
  "https://medchat.example@evil.example/", "https://user:pass@medchat.example/",
  "https://www.rcsb.org.evil.example/", "https://www.rcsb.org:8443/",
  "http://www.rcsb.org/", "https://evil.example/", "javascript:alert(1)",
  "data:text/html,<script>alert(1)</script>", "//www.rcsb.org/",
  "https://www.uniprot.org.evil.example/uniprotkb/Q6P988/entry",
  "https://evil.uniprot.org/uniprotkb/Q6P988/entry",
  "http://www.uniprot.org/uniprotkb/Q6P988/entry",
  "https://www.uniprot.org:8443/uniprotkb/Q6P988/entry",
  "https://user@www.uniprot.org/uniprotkb/Q6P988/entry",
  "https://www.uniprot.org/redirect?url=https://evil.example",
  "https://www.uniprot.org/uniprotkb/Q6P988/entry?redirect=https://evil.example",
  "https://www.uniprot.org/uniprotkb/Q6P988/entry?",
  "https://www.uniprot.org/uniprotkb/Q6P988/entry#",
  "https://www.uniprot.org/uniprotkb/x/../Q6P988/entry",
  "https://www.uniprot.org/uniprotkb/%2e/Q6P988/entry",
]) {
  test(`chat URL rejects ${JSON.stringify(value)}`, () => {
    assert.equal(loadPolicy().MedChatSafeRender.safeChatUrl(value), "#");
  });
}

for (const value of ["/target-search", "./report", "../report", "#results",
  "https://medchat.example/report", "https://www.rcsb.org/structure/1ABC",
  "https://www.uniprot.org/uniprotkb/Q6P988/entry",
  "https://www.uniprot.org/uniprotkb/A0A024RBG1/entry",
  "https://pubmed.ncbi.nlm.nih.gov/123/", "https://doi.org/10.1234/example"]) {
  test(`chat URL preserves approved ${value}`, () => {
    assert.equal(loadPolicy().MedChatSafeRender.safeChatUrl(value), value);
  });
}

test("production formatter rejects external destinations and retains safe label text", () => {
  const page = loadPolicy();
  const html = page.HomeFormatters.formatContent('[paper](https://evil.example/) <img src=x onerror=alert(1)>');
  assert.ok(!html.includes('href="https://evil.example/"'));
  assert.ok(html.includes('href="#"'));
  assert.ok(!html.includes('<img'));
  assert.ok(html.includes('&lt;img'));
});

test("ranked structure formatter preserves official UniProt entry links", () => {
  const url = "https://www.uniprot.org/uniprotkb/Q6P988/entry";
  const html = loadPolicy().HomeFormatters.formatContent(`[UniProt Q6P988](${url})`);
  assert.ok(html.includes(`href="${url}"`));
});
