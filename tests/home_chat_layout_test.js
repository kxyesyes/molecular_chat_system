"use strict";

// Static ownership contracts only. Real viewport geometry and keyboard scrolling
// must also be checked in the browser at 390/768/1280px.
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const root = path.join(__dirname, "../src/web");
const template = fs.readFileSync(path.join(root, "templates/index.html"), "utf8");
const href = '/static/css/home_chat_layout.css?v=20261026-responsive-v1';
assert(template.includes(href), "home must load the scoped responsive layout stylesheet");
assert(template.indexOf(href) > template.lastIndexOf("</style>"), "layout overrides must follow existing inline styles");
const css = fs.readFileSync(path.join(root, "static/css/home_chat_layout.css"), "utf8");
function rule(selector, source = css) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = source.match(new RegExp(escaped + "\\s*\\{([^}]+)\\}"));
  assert(match, "missing scoped rule: " + selector);
  return match[1];
}
for (const selector of [".index_con .right", ".chat-messages", ".chat-messages .message-wrapper",
  ".chat-messages .message-container", ".chat-messages .message-container > .message-box"]) {
  assert(/min-width:\s*0\s*;/.test(rule(selector)), selector + " must be allowed to shrink");
}
assert(/max-width:\s*70%/.test(rule(".chat-messages .message-container")), "retain desktop shell width");
assert(/max-width:\s*100%/.test(rule(".chat-messages .message-container > .message-box")), "bound the bubble");
const mobile = css.slice(css.indexOf("@media (max-width: 768px)"));
assert(/max-width:\s*100%/.test(rule(".chat-messages .message-container", mobile)), "mobile shell may use the full row");
assert(/padding-bottom:\s*calc\([^;]+env\(safe-area-inset-bottom\)/.test(rule(".index_con .right", mobile)),
  "reserve mobile space for the fixed multi-row composer and safe area");
const scroll = rule(".home-comparison-table-scroll");
assert(/max-width:\s*100%/.test(scroll) && /min-width:\s*0\s*;/.test(scroll));
assert(/overflow-x:\s*auto/.test(scroll), "wide tables must scroll locally");
assert(!/overflow(?:-x)?:\s*(hidden|clip)|font-size\s*:|text-overflow\s*:/.test(css),
  "containment cannot clip evidence or reduce existing typography");
assert(!css.includes(".home-result-details"), "existing detail-table styles remain independently owned");
assert(/display:\s*flex/.test(rule(".chat-composer .decision-runtime-controls")));
assert(/flex-wrap:\s*wrap/.test(rule(".chat-composer .decision-runtime-controls")));
assert(/display:\s*none/.test(rule(".chat-composer .decision-runtime-controls[hidden]")), "flex must not override hidden mode");
assert(/outline:/.test(rule(".home-comparison-table-scroll:focus-visible")));
assert(css.includes("button:disabled") && css.includes("button:focus-visible"));
console.log("home chat layout contracts passed (not browser geometry proof)");
