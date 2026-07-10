"use strict";

(function (root) {
  function toText(value) {
    return value === null || value === undefined ? "" : String(value);
  }

  function escapeHtml(value) {
    return toText(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/'/g, "&#039;")
      .replace(/"/g, "&quot;");
  }

  function escapeAttr(value) {
    return escapeHtml(value).replace(/`/g, "&#096;");
  }

  function escapeJsString(value) {
    return toText(value)
      .replace(/\\/g, "\\\\")
      .replace(/'/g, "\\'")
      .replace(/"/g, '\\"')
      .replace(/\r/g, "\\r")
      .replace(/\n/g, "\\n")
      .replace(/\u2028/g, "\\u2028")
      .replace(/\u2029/g, "\\u2029")
      .replace(/</g, "\\x3C")
      .replace(/>/g, "\\x3E")
      .replace(/&/g, "\\x26");
  }

  function escapeInlineJsString(value) {
    return escapeAttr(escapeJsString(value));
  }

  function escapeCssIdent(value) {
    const raw = toText(value);
    if (root.CSS && typeof root.CSS.escape === "function") {
      return root.CSS.escape(raw);
    }
    return raw
      .replace(/\\/g, "\\\\")
      .replace(/"/g, '\\"')
      .replace(/\r/g, "\\D ")
      .replace(/\n/g, "\\A ")
      .replace(/\f/g, "\\C ");
  }

  function safeUrl(value) {
    const raw = toText(value).trim();
    if (!raw) return "#";

    if (
      raw.startsWith("/") ||
      raw.startsWith("#") ||
      raw.startsWith("./") ||
      raw.startsWith("../")
    ) {
      return escapeAttr(raw);
    }

    if (/^(https?:|mailto:)/i.test(raw) && typeof URL === "undefined") {
      return escapeAttr(raw);
    }

    try {
      const base =
        root.window && root.window.location && root.window.location.origin
          ? root.window.location.origin
          : "http://localhost";
      const parsed = new URL(raw, base);
      if (["http:", "https:", "mailto:"].includes(parsed.protocol)) {
        return escapeAttr(raw);
      }
    } catch (_) {
      return "#";
    }
    return "#";
  }

  function setTrustedHtml(element, html) {
    if (!element) return;
    element.innerHTML = toText(html);
  }

  function trustedTemplate(strings) {
    let output = "";
    for (let i = 0; i < strings.length; i += 1) {
      output += strings[i];
      if (i + 1 < arguments.length) {
        output += escapeHtml(arguments[i + 1]);
      }
    }
    return output;
  }

  const api = {
    escapeHtml,
    escapeAttr,
    escapeJsString,
    escapeInlineJsString,
    escapeCssIdent,
    safeUrl,
    setTrustedHtml,
    trustedTemplate,
  };

  root.MedChatSafeRender = api;
  if (root.window) {
    root.window.MedChatSafeRender = api;
  }
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : window);
