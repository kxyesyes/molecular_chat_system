const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const events = [];
class FakeSocket {
  constructor(url) {
    this.url = url;
    this.readyState = FakeSocket.CONNECTING;
    FakeSocket.instance = this;
  }
  close(code, reason) {
    this.closeArgs = [code, reason];
    this.readyState = FakeSocket.CLOSED;
  }
}
FakeSocket.CONNECTING = 0;
FakeSocket.OPEN = 1;
FakeSocket.CLOSED = 3;

const context = {
  window: { WebSocket: FakeSocket },
  setTimeout,
  clearTimeout,
};
vm.runInNewContext(
  fs.readFileSync("src/web/static/js/home/connection.js", "utf8"),
  context,
  { filename: "home/connection.js" },
);

const socket = context.window.HomeConnection.create({
  url: "ws://localhost/ws",
  timeoutMs: 1000,
  onOpen: value => events.push(["open", value]),
  onMessage: value => events.push(["message", value]),
  onError: value => events.push(["error", value]),
  onClose: value => events.push(["close", value]),
});

assert.strictEqual(socket.url, "ws://localhost/ws");
assert.strictEqual(typeof socket.onopen, "function");
assert.strictEqual(typeof socket.onmessage, "function");
assert.strictEqual(typeof socket.onerror, "function");
assert.strictEqual(typeof socket.onclose, "function");
socket.onopen();
socket.onmessage({ data: "payload" });
socket.onerror("error");
socket.onclose({ code: 1000 });
assert.deepStrictEqual(events.map(item => item[0]), ["open", "message", "error", "close"]);

console.log("Home WebSocket connection checks passed");
