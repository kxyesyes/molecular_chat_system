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
    if (raw.startsWith("//")) return "#";

    if (
      (raw.startsWith("/") && !raw.startsWith("//")) ||
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

  function safeExternalUrl(value) {
    const raw = toText(value).trim();
    if (!raw || /[\\\u0000-\u001f\u007f]/.test(raw)) return "#";
    try {
      const parsed = new URL(raw);
      const trustedHosts = [
        "ebi.ac.uk",
        "rcsb.org",
        "pubmed.ncbi.nlm.nih.gov",
        "doi.org",
      ];
      const trustedHost = trustedHosts.some(
        (host) => parsed.hostname === host || parsed.hostname.endsWith(`.${host}`),
      );
      if (
        parsed.protocol !== "https:" ||
        parsed.username ||
        parsed.password ||
        parsed.port ||
        !trustedHost
      ) {
        return "#";
      }
      return escapeAttr(raw);
    } catch (_) {
      return "#";
    }
  }

  function safeChatUrl(value) {
    const raw = toText(value).trim();
    if (!raw || raw.startsWith("//") || /[\\\u0000-\u001f\u007f]/.test(raw)) return "#";

    try {
      const base =
        root.window && root.window.location && root.window.location.origin
          ? root.window.location.origin
          : "http://localhost";
      const parsed = new URL(raw, base);
      if (parsed.username || parsed.password) return "#";
      if (parsed.origin === new URL(base).origin && ["http:", "https:"].includes(parsed.protocol)) {
        return escapeAttr(raw);
      }
    } catch (_) {
      return "#";
    }
    return safeExternalUrl(raw);
  }

  function setTrustedHtml(element, html) {
    if (!element) return;
    element.innerHTML = toText(html);
  }

  function appendSafeSvg(element, value) {
    if (!element || typeof DOMParser === "undefined") return false;
    const source = toText(value).trim();
    if (!source) return false;
    const parsed = new DOMParser().parseFromString(source, "image/svg+xml");
    const root = parsed.documentElement;
    if (!root || root.nodeName.toLowerCase() !== "svg" || parsed.querySelector("parsererror")) {
      return false;
    }
    const allowedTags = new Set([
      "svg", "g", "path", "line", "circle", "rect", "polygon", "polyline",
      "text", "tspan", "defs", "title", "desc",
    ]);
    const allowedAttributes = new Set([
      "viewbox", "width", "height", "xmlns", "fill", "stroke", "stroke-width",
      "stroke-linecap", "stroke-linejoin", "stroke-dasharray", "d", "x", "y",
      "x1", "x2", "y1", "y2", "cx", "cy", "r", "points", "transform", "class",
      "font-size", "font-family", "text-anchor", "dominant-baseline",
    ]);
    const clone = document.importNode(root, true);
    const nodes = [clone, ...clone.querySelectorAll("*")];
    for (const node of nodes) {
      if (!allowedTags.has(node.nodeName.toLowerCase())) return false;
      for (const attribute of [...node.attributes]) {
        const name = attribute.name.toLowerCase();
        if (!allowedAttributes.has(name) || name.startsWith("on") || /(?:javascript|data:|url\s*\()/i.test(attribute.value)) {
          node.removeAttribute(attribute.name);
        }
      }
    }
    element.replaceChildren(clone);
    return true;
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
    safeExternalUrl,
    safeChatUrl,
    setTrustedHtml,
    appendSafeSvg,
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
