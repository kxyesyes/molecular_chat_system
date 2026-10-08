const fs = require("fs");

const main = fs.readFileSync("src/web/static/js/home/main.js", "utf8");
const renderer = fs.readFileSync("src/web/static/js/home/chat_renderer.js", "utf8");

if (/function\s+addSystemMessage\s*\(/.test(main)) {
  throw new Error("main.js must delegate system messages to HomeChatRenderer");
}
if (/function\s+showNotification\s*\(/.test(main)) {
  throw new Error("main.js must delegate notifications to HomeChatRenderer");
}
if (!/function\s+addSystemMessage\s*\(/.test(renderer)) {
  throw new Error("chat_renderer.js must keep the system-message implementation");
}
if (!/function\s+showNotification\s*\(/.test(renderer)) {
  throw new Error("chat_renderer.js must keep the notification implementation");
}

console.log("Homepage renderer boundary checks passed");
