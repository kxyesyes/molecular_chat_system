"use strict";

const assert = require("assert");

const values = new Map();
let captured = null;
global.window = {
  sessionStorage: {
    getItem: (key) => values.get(key) || null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  },
  prompt: () => "unit-admin-token",
  fetch: async (input, init) => {
    captured = { input, init };
    return { status: 200 };
  },
};

require("../src/web/static/js/shared/admin_fetch.js");

(async function () {
  await window.MedChatAdminAuth.fetch("/api/llm/config");
  assert.strictEqual(captured.input, "/api/llm/config");
  assert.strictEqual(
    captured.init.headers.get("X-MedChat-Admin-Token"),
    "unit-admin-token",
  );
  assert.strictEqual(
    window.sessionStorage.getItem("medchat_admin_token"),
    "unit-admin-token",
  );
  console.log("Admin fetch session-token checks passed");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
