"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

class Node {
  constructor(tagName) {
    this.tagName = tagName;
    this.children = [];
    this.parentNode = null;
    this.className = "";
    this._text = "";
    this.classList = {
      add: (...names) => {
        this.className = [...new Set([
          ...this.className.split(/\s+/).filter(Boolean),
          ...names,
        ])].join(" ");
      },
    };
  }
  appendChild(child) {
    this.children.push(child);
    child.parentNode = this;
    return child;
  }
  set textContent(value) {
    this.children = [];
    this._text = String(value);
  }
  get textContent() {
    return this._text + this.children.map(child => child.textContent).join("");
  }
  querySelector(selector) {
    const wanted = selector.startsWith(".") ? selector.slice(1) : "";
    for (const child of this.children) {
      if ((wanted && child.className.split(/\s+/).includes(wanted)) ||
          (!wanted && child.tagName === selector)) return child;
      const nested = child.querySelector(selector);
      if (nested) return nested;
    }
    return null;
  }
}

const sandbox = {
  window: {
    document: {createElement: tagName => new Node(tagName)},
  },
  Object,
  Set,
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
const modulePath = path.join(
  __dirname, "..", "src", "web", "static", "js", "home", "task_panel.js",
);
vm.runInContext(fs.readFileSync(modulePath, "utf8"), sandbox, {filename: modulePath});

const panelApi = sandbox.window.HomeTaskPanel;
assert(panelApi, "HomeTaskPanel must be exposed");
const container = new Node("section");
const panel = panelApi.create(container);
assert(panel);
assert.strictEqual(panel.className, "agent-task-panel");
assert.strictEqual(panelApi.create(container), panel, "panel creation must be idempotent");

panelApi.reset(panel);
assert(panel.className.includes("is-active"));
assert.strictEqual(panel.querySelector(".agent-task-progress").textContent, "执行中");
assert.strictEqual(panel.querySelector(".agent-task-list").children.length, 0);

const added = panelApi.appendEvent(panel, {
  label: "工具调用",
  message: "<script>alert(1)</script>",
  itemClass: "is-running",
  progressText: "42%",
});
assert.strictEqual(added, true);
const item = panel.querySelector(".agent-task-item");
assert(item.className.includes("is-running"));
assert.strictEqual(item.textContent, "工具调用<script>alert(1)</script>");
assert.strictEqual(item.querySelector("strong").textContent, "工具调用");
assert.strictEqual(item.children[1].children[1].textContent, "<script>alert(1)</script>");
assert.strictEqual(panel.querySelector(".agent-task-progress").textContent, "42%");
console.log("Home task panel rendering checks passed");
