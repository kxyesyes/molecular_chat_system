const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const context = { window: {} };
vm.runInNewContext(
  fs.readFileSync("src/web/static/js/home/protocol.js", "utf8"),
  context,
  { filename: "home/protocol.js" },
);

const protocol = context.window.HomeProtocol;
assert(protocol, "HomeProtocol must be exported");

assert.strictEqual(JSON.stringify(protocol.parseMessage('{"type":"pong"}', 1024)), JSON.stringify({
  ok: true,
  message: { type: "pong" },
}));
assert.strictEqual(JSON.stringify(protocol.parseMessage("", 1024)), JSON.stringify({
  ok: false,
  code: 1002,
  reason: "empty message",
  userMessage: "收到空消息，连接已关闭。",
}));
assert.strictEqual(JSON.stringify(protocol.parseMessage("not-json", 1024)), JSON.stringify({
  ok: false,
  code: 1002,
  reason: "invalid message JSON",
  userMessage: "收到的消息格式无效，连接已关闭。",
}));
assert.strictEqual(JSON.stringify(protocol.parseMessage('{"message":"missing type"}', 1024)), JSON.stringify({
  ok: false,
  code: 1002,
  reason: "missing message type",
  userMessage: "收到的消息缺少类型，连接已关闭。",
}));
assert.strictEqual(JSON.stringify(protocol.parseMessage("123456", 4)), JSON.stringify({
  ok: false,
  code: 1009,
  reason: "message too large",
  userMessage: "收到的消息过大，已安全忽略。",
}));

console.log("Home WebSocket protocol checks passed");
