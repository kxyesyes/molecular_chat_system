"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const root = path.join(__dirname, "../src/web");
const template = fs.readFileSync(path.join(root, "templates/index.html"), "utf8");
const source = fs.readFileSync(path.join(root, "static/js/home/main.js"), "utf8");

// Native textarea semantics preserve pasted line separators; input[type=text]
// strips them before our send handler can read the value.
const composer = template.match(/<div class="fill">([\s\S]*?)<div class="btn">/);
assert(composer, "home composer must exist");
assert(/<textarea\b[^>]*rows="3"[^>]*><\/textarea>/.test(composer[1]),
  "composer must use a native three-row textarea to preserve multiline SMILES");
assert(!/<input\b/.test(composer[1]), "composer must not sanitize away pasted newlines");
assert(source.includes('document.querySelector(".fill textarea")'), "events must bind to the textarea");

// Execute the real binding section, with only the send boundary replaced.
const start = source.indexOf("    // 输入框回车发送");
const end = source.indexOf("    // RAG开关", start);
assert(start >= 0 && end > start);
const listeners = {};
const input = {value: "比较以下两个分子的性质：\nCCO\nCCN", addEventListener(type, fn) {listeners[type] = fn;}};
const sent = [];
new Function("elements", "sendMessage", "console", source.slice(start, end))(
  {input}, () => sent.push(input.value), {log() {}, warn() {}});
const handler = listeners.keydown || listeners.keypress;
assert.equal(typeof handler, "function", "composer must bind keyboard sending");
let prevented = false;
handler({key: "Enter", shiftKey: true, preventDefault() {prevented = true;}});
assert.equal(sent.length, 0, "Shift+Enter must not send");
assert.equal(prevented, false, "Shift+Enter must allow the native textarea newline");
handler({key: "a", shiftKey: false, preventDefault() {prevented = true;}});
assert.equal(sent.length, 0, "typing must not send");
handler({key: "Enter", shiftKey: false, preventDefault() {prevented = true;}});
assert.equal(prevented, true, "Enter sends without inserting another newline");
assert.deepEqual(sent, ["比较以下两个分子的性质：\nCCO\nCCN"], "send must retain both molecule boundaries");
console.log("home multiline composer markup and keyboard contracts passed (browser paste/geometry checked separately)");
