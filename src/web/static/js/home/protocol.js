"use strict";

// Pure WebSocket protocol boundary. Connection ownership, state transitions,
// and rendering remain in home/main.js.
window.HomeProtocol = {
  parseMessage(data, maxLength) {
    if (typeof data !== "string") {
      return {
        ok: false,
        code: 1002,
        reason: "invalid message data",
        userMessage: "收到的消息格式无效，连接已关闭。",
      };
    }
    if (data.length > maxLength) {
      return {
        ok: false,
        code: 1009,
        reason: "message too large",
        userMessage: "收到的消息过大，已安全忽略。",
      };
    }
    if (data.trim() === "") {
      return {
        ok: false,
        code: 1002,
        reason: "empty message",
        userMessage: "收到空消息，连接已关闭。",
      };
    }
    let message;
    try {
      message = JSON.parse(data);
    } catch (_) {
      return {
        ok: false,
        code: 1002,
        reason: "invalid message JSON",
        userMessage: "收到的消息格式无效，连接已关闭。",
      };
    }
    if (!message || typeof message !== "object" || Array.isArray(message)) {
      return {
        ok: false,
        code: 1002,
        reason: "invalid message shape",
        userMessage: "收到的消息格式无效，连接已关闭。",
      };
    }
    if (!message.type) {
      return {
        ok: false,
        code: 1002,
        reason: "missing message type",
        userMessage: "收到的消息缺少类型，连接已关闭。",
      };
    }
    return { ok: true, message };
  },
};
