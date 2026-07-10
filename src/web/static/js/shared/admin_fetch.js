"use strict";

(function (root) {
  const STORAGE_KEY = "medchat_admin_token";
  const HEADER_NAME = "X-MedChat-Admin-Token";
  const nativeFetch = root.fetch ? root.fetch.bind(root) : null;

  function getToken() {
    try {
      return (root.sessionStorage && root.sessionStorage.getItem(STORAGE_KEY)) || "";
    } catch (_) {
      return "";
    }
  }

  function setToken(value) {
    const token = String(value || "").trim();
    if (!token) return "";
    try {
      if (root.sessionStorage) root.sessionStorage.setItem(STORAGE_KEY, token);
    } catch (_) {
      return token;
    }
    return token;
  }

  function clearToken() {
    try {
      if (root.sessionStorage) root.sessionStorage.removeItem(STORAGE_KEY);
    } catch (_) {
      // Session storage may be unavailable in hardened browser contexts.
    }
  }

  function requestToken() {
    if (typeof root.prompt !== "function") return "";
    const supplied = root.prompt("请输入 MedChat 管理员令牌。令牌仅保存在当前浏览器会话中：");
    return setToken(supplied);
  }

  async function adminFetch(input, init) {
    if (!nativeFetch) throw new Error("fetch is unavailable");
    const token = getToken() || requestToken();
    const options = Object.assign({}, init || {});
    const headers = new Headers(options.headers || {});
    if (token) headers.set(HEADER_NAME, token);
    options.headers = headers;

    const response = await nativeFetch(input, options);
    if (response.status === 401 || response.status === 403) clearToken();
    return response;
  }

  root.MedChatAdminAuth = {
    fetch: adminFetch,
    getToken,
    setToken,
    clearToken,
    requestToken,
  };
})(typeof window !== "undefined" ? window : globalThis);
