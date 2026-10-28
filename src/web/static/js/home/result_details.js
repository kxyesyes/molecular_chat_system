"use strict";

/* Standalone, read-only snapshot viewer. No science execution or status writes.
 * HomeResultDetails.mount(container, {traceId, reference, load?}) -> closed <details>.
 *   Replaces only this module's previous instance in container. load receives
 *   {traceId, reference, signal} and must return {view_id, revision, byte_length,
 *   payload}, just like read(). Both paths validate and detach the returned data.
 * HomeResultDetails.read({traceId, reference, fetcher?, signal?}) -> frozen data.
 *   Uses HomeConfig.resultDetails.baseUrl; requires a streaming Fetch response.
 *   Scientific numbers that cannot round-trip retain their JSON lexeme in an
 *   internal frozen numeric scalar, NOT a string. Pass read() data to mount();
 *   these display-only scalars deliberately reject ordinary JSON serialization.
 * HomeResultDetails.abortAll() aborts ONLY current reads (e.g. WS disconnect).
 *   Loaded history stays intact; cancelled instances can retry/reopen the SAME
 *   immutable reference. Late completions cannot update any instance.
 * HomeResultDetails.dispose(container) aborts/removes only its owned disclosure.
 *   Call before re-render/deletion. MutationObserver also cleans up detachment.
 * Errors expose only localized message/code: network, not_found, invalid,
 * too_large, aborted, auth. No exception, server-message or filesystem-path passthrough.
 */
(function () {
  const MAX_BYTES = 4 * 1024 * 1024, ENVELOPE_BYTES = 64 * 1024;
  const MAX_DEPTH = 34, MAX_NODES = 65536, PAGE_SIZE = 12, TEXT_PAGE = 2048;
  const instances = new WeakMap(), inflight = new Set();
  const numericLexemes = new WeakMap();
  const numericText = value => value && typeof value === "object" ? numericLexemes.get(value) : undefined;
  const own = (object, key) => Object.prototype.hasOwnProperty.call(object, key);
  const STATUS = Object.freeze({completed: "已完成", partial: "部分完成", failed: "失败",
    rejected: "未执行", cancelled: "已取消", timeout: "超时", unavailable: "不可用",
    not_calculated: "未计算", waiting_for_input: "等待输入"});
  const TOOL = Object.freeze({property_calculator: "分子性质计算", drug_likeness_assessment: "类药性评估",
    admet_predictor: "ADMET 预测", activity_predictor: "活性预测", target_database_search: "靶点检索",
    reverse_target_predictor: "反向寻靶", llm_molecular_generator: "分子生成", candidate_ranker: "候选排序",
    rag_search: "知识库检索", molecular_docking: "分子对接", vina_docking: "分子对接"});
  const FIELD = Object.freeze({status: "状态", success: "成功", data: "计算结果", results: "结果",
    tool_name: "工具标识", tool_result_sequence: "计算步骤", error: "错误", warnings: "提示",
    quality: "结果质量", provenance: "来源", artifacts: "产物", step_id: "步骤标识",
    display_redacted: "已脱敏", smiles: "SMILES", molecular_weight: "分子量", logp: "LogP",
    tpsa: "极性表面积", hbd: "氢键供体", hba: "氢键受体", value: "值", units: "单位", unit: "单位",
    target: "靶点", target_name: "靶点名称", pic50: "pIC50", pIC50: "pIC50", score: "评分",
    binding_energy: "结合能", model_version: "模型版本", source: "来源", message: "信息",
    code: "代码", trace_id: "运行标识", view_id: "快照标识", revision: "版本", byte_length: "字节数",
    format: "格式", endpoint: "预测端点", exclusion_reason: "排除原因", disagreement: "预测分歧"});
  const MESSAGES = Object.freeze({network: "网络异常，未能加载明细。", not_found: "明细不存在或已不可用。",
    auth: "会话验证失败，请重新登录后重试。",
    invalid: "明细格式无效，无法显示。", too_large: "明细过大，无法显示。", aborted: "明细加载已取消。"});
  class DetailError extends Error {
    constructor(code) {super(MESSAGES[code]); this.name = "ResultDetailsError"; this.code = code;}
  }
  const fail = code => {throw new DetailError(code);};
  const safeError = error => error instanceof DetailError ? error : new DetailError("network");
  const label = key => own(FIELD, key) ? FIELD[key] : key;
  function record(value) {
    if (!value || typeof value !== "object" || Array.isArray(value) || numericText(value) !== undefined) return false;
    const proto = Object.getPrototypeOf(value);
    return proto === null || Object.getPrototypeOf(proto) === null;
  }
  function exact(value, fields) {
    if (!record(value) || Reflect.ownKeys(value).length !== fields.length) fail("invalid");
    for (const key of fields) {
      const descriptor = Object.getOwnPropertyDescriptor(value, key);
      if (!descriptor || !own(descriptor, "value") || !descriptor.enumerable) fail("invalid");
    }
  }
  function binding(traceId, reference) {
    exact(reference, ["view_id", "revision", "byte_length"]);
    if (typeof traceId !== "string" || !/^[a-f0-9]{32}$/.test(traceId) || traceId.length !== 32
      || typeof reference.view_id !== "string" || reference.view_id.length !== 32
      || !/^[a-f0-9]{12}4[a-f0-9]{3}[89ab][a-f0-9]{15}$/.test(reference.view_id)
      || typeof reference.revision !== "string" || reference.revision.length !== 64
      || !/^[a-f0-9]{64}$/.test(reference.revision) || !Number.isSafeInteger(reference.byte_length)
      || reference.byte_length < 1 || reference.byte_length > MAX_BYTES) fail("invalid");
    return Object.freeze({view_id: reference.view_id, revision: reference.revision, byte_length: reference.byte_length});
  }

  // Count actual JSON UTF-8 bytes without first allocating a giant encoded string.
  function stringBytes(value, remaining) {
    let bytes = 2;
    for (let i = 0; i < value.length; i++) {
      const c = value.charCodeAt(i);
      if (c >= 0xd800 && c <= 0xdbff) {
        const next = value.charCodeAt(++i);
        if (!(next >= 0xdc00 && next <= 0xdfff)) fail("invalid");
        bytes += 4;
      } else if (c >= 0xdc00 && c <= 0xdfff) fail("invalid");
      else bytes += c === 34 || c === 92 ? 2 : c < 32 ? ([8, 9, 10, 12, 13].includes(c) ? 2 : 6)
        : c < 128 ? 1 : c < 2048 ? 2 : 3;
      if (bytes > remaining) fail("too_large");
    }
    return bytes;
  }
  function boundTree(value, maxDepth, maxNodes, maxBytes) {
    const stack = [{value, depth: 0}], active = new Set();
    let nodes = 0, bytes = 0;
    while (stack.length) {
      const frame = stack.pop(), item = frame.value;
      if (frame.exit) {active.delete(item); continue;}
      if (++nodes > maxNodes || frame.depth > maxDepth) fail("too_large");
      if (item === null) bytes += 4;
      else if (numericText(item) !== undefined) bytes += numericText(item).length;
      else if (typeof item === "string") bytes += stringBytes(item, maxBytes - bytes);
      else if (typeof item === "boolean") bytes += item ? 4 : 5;
      else if (typeof item === "number") {
        if (!Number.isFinite(item)) fail("invalid");
        bytes += String(item).length;
      } else if (Array.isArray(item) || record(item)) {
        if (active.has(item)) fail("invalid");
        active.add(item); stack.push({value: item, exit: true});
        const array = Array.isArray(item), keys = Reflect.ownKeys(item).filter(key => !array || key !== "length");
        if (array && keys.length !== item.length) fail("invalid");
        if (nodes + keys.length * (array ? 1 : 2) > maxNodes) fail("too_large");
        bytes += 2 + Math.max(0, keys.length - 1);
        keys.forEach((key, index) => {
          if (typeof key !== "string" || (array && key !== String(index))) fail("invalid");
          const descriptor = Object.getOwnPropertyDescriptor(item, key);
          if (!own(descriptor, "value") || !descriptor.enumerable) fail("invalid");
          if (!array) {nodes++; bytes += stringBytes(key, maxBytes - bytes) + 1;}
          stack.push({value: descriptor.value, depth: frame.depth + 1});
        });
      } else fail("invalid");
      if (bytes > maxBytes) fail("too_large");
    }
  }
  function snapshot(value, traceId, reference) {
    boundTree(value, MAX_DEPTH + 1, MAX_NODES + 16, reference.byte_length + ENVELOPE_BYTES);
    exact(value, ["view_id", "revision", "byte_length", "payload"]);
    if (["view_id", "revision", "byte_length"].some(key => value[key] !== reference[key])) fail("invalid");
    const p = value.payload;
    exact(p, ["format", "trace_id", "status", "tool_result_sequence", "error", "warnings", "display_redacted"]);
    if (p.format !== "summary_details_v1" || p.trace_id !== traceId || typeof p.status !== "string"
      || !own(STATUS, p.status) || !Array.isArray(p.tool_result_sequence) || !p.tool_result_sequence.every(record)
      || (p.error !== null && !record(p.error)) || !Array.isArray(p.warnings)
      || !p.warnings.every(w => typeof w === "string") || typeof p.display_redacted !== "boolean") fail("invalid");
    boundTree(p, MAX_DEPTH, MAX_NODES, MAX_BYTES);
    // The validated depth is bounded. Copy containers without reserializing
    // numeric scalars or invoking __proto__ setters; atoms are already frozen.
    function detach(item) {
      if (!item || typeof item !== "object" || numericText(item) !== undefined) return item;
      const copy = Array.isArray(item) ? [] : {};
      for (const key of Object.keys(item)) Object.defineProperty(copy, key,
        {value: detach(item[key]), enumerable: true});
      return Object.freeze(copy);
    }
    return detach(value);
  }

  function parseNumber(raw) {
    const value = Number(raw), integer = !/[.eE]/.test(raw);
    if (integer) {
      // Match the server's finite native integer contract (at most 4096 bits).
      // Bound decimal length before BigInt allocation; giant integers are not
      // nonfinite floats just because converting them to Number overflows.
      const digits = raw[0] === "-" ? raw.slice(1) : raw;
      if (digits.length > 1234 || (digits.length === 1234 && BigInt(digits) >= (1n << 4096n))) fail("invalid");
    } else if (!Number.isFinite(value)) fail("invalid");
    if ((!integer || Number.isSafeInteger(value)) && JSON.stringify(value) === raw) return value;
    const scalar = Object.create(null);
    Object.defineProperty(scalar, "toJSON", {value() {
      throw new TypeError("Lossless numeric scalar requires explicit text rendering");
    }});
    numericLexemes.set(scalar, raw);
    return Object.freeze(scalar);
  }

  // Parse bounded JSON without ever routing scientific numeric tokens through
  // JSON.parse. Keys also count toward budgets, and duplicates fail closed.
  // Only individual quoted strings use the native parser for escape validation.
  function parseBounded(text) {
    let index = 0, nodes = 0;
    const number = /-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/y;
    const space = () => {while (index < text.length && /[ \t\r\n]/.test(text[index])) index++;};
    const count = depth => {if (++nodes > MAX_NODES + 64 || depth > MAX_DEPTH + 2) fail("too_large");};
    function quoted() {
      const start = index++;
      while (index < text.length && text[index] !== '"') {
        if (text[index] === "\\") index++;
        index++;
      }
      if (index >= text.length) fail("invalid");
      return JSON.parse(text.slice(start, ++index));
    }
    function value(depth) {
      space(); count(depth);
      const char = text[index];
      if (char === '"') return quoted();
      if (char === "{" || char === "[") {
        const array = char === "[", close = array ? "]" : "}", result = array ? [] : {}, keys = new Set();
        index++; space();
        if (text[index] === close) {index++; return result;}
        for (;;) {
          if (array) result.push(value(depth + 1));
          else {
            space(); count(depth + 1);
            if (text[index] !== '"') fail("invalid");
            const key = quoted();
            if (keys.has(key)) fail("invalid");
            keys.add(key); space();
            if (text[index++] !== ":") fail("invalid");
            Object.defineProperty(result, key, {value: value(depth + 1), enumerable: true});
          }
          space();
          if (text[index] === close) {index++; return result;}
          if (text[index++] !== ",") fail("invalid");
        }
      }
      for (const [token, primitive] of [["true", true], ["false", false], ["null", null]]) {
        if (text.startsWith(token, index)) {index += token.length; return primitive;}
      }
      number.lastIndex = index;
      const match = number.exec(text);
      if (!match) fail("invalid");
      index = number.lastIndex;
      return parseNumber(match[0]);
    }
    const result = value(0); space();
    if (index !== text.length) fail("invalid");
    return result;
  }
  function checkAbort(signal) {if (signal?.aborted) fail("aborted");}
  function abortable(promise, signal) {
    if (!signal) return Promise.resolve(promise);
    return new Promise((resolve, reject) => {
      const settle = (handler, value) => {signal.removeEventListener("abort", aborted); handler(value);};
      const aborted = () => settle(reject, new DetailError("aborted"));
      signal.addEventListener("abort", aborted, {once: true});
      // Attach handlers even if already aborted, so a late rejection is consumed.
      Promise.resolve(promise).then(value => settle(resolve, value), error => settle(reject, error));
      if (signal.aborted) aborted();
    });
  }
  async function read({traceId, reference, fetcher, signal} = {}) {
    try {
      const ref = binding(traceId, reference);
      checkAbort(signal);
      const base = window.HomeConfig?.resultDetails?.baseUrl;
      // Only the configured root-relative endpoint is supported. Never follow
      // redirects, absolute/protocol-relative URLs, queries or encoded path tricks.
      if (base !== "/api/agent/runs") fail("invalid");
      const url = `${base}/${traceId}/result-details/${ref.view_id}?revision=${ref.revision}`;
      const fetchResponse = fetcher || window.fetch?.bind(window);
      if (typeof fetchResponse !== "function") fail("network");
      const response = await abortable(fetchResponse(url, {method: "GET", credentials: "same-origin",
        cache: "no-store", redirect: "error", headers: {Accept: "application/json"}, signal}), signal);
      checkAbort(signal);
      if (!response || response.redirected || ["opaque", "opaqueredirect"].includes(response.type)) fail("invalid");
      if (response.url) {
        const origin = window.location?.origin;
        if (!origin || response.url !== new URL(url, origin).href) fail("invalid");
      }
      if (!response.ok) fail(response.status === 401 ? "auth" : response.status === 404 ? "not_found" : "network");
      if (typeof response.body?.getReader !== "function") fail("invalid");
      const reader = response.body.getReader(), decoder = new TextDecoder("utf-8", {fatal: true});
      const parts = []; let size = 0, finished = false, part = "";
      try {
        for (;;) {
          checkAbort(signal);
          const {done, value} = await abortable(reader.read(), signal);
          checkAbort(signal);
          if (done) {finished = true; break;}
          if (!(value instanceof Uint8Array) || value.byteLength === 0) fail("invalid");
          size += value.byteLength;
          if (size > ref.byte_length + ENVELOPE_BYTES || size > MAX_BYTES + ENVELOPE_BYTES) fail("too_large");
          try {part += decoder.decode(value, {stream: true});} catch (_) {fail("invalid");}
          // Tiny chunks cannot create millions of separately retained strings.
          if (part.length >= 16384) {parts.push(part); part = "";}
        }
        let envelope;
        try {parts.push(part + decoder.decode()); envelope = parseBounded(parts.join(""));}
        catch (error) {throw error instanceof DetailError ? error : new DetailError("invalid");}
        if (!record(envelope) || envelope.success !== true || !own(envelope, "data")) fail("invalid");
        return snapshot(envelope.data, traceId, ref);
      } finally {
        if (!finished) {try {Promise.resolve(reader.cancel()).catch(() => {});} catch (_) { /* best effort */ }}
        try {reader.releaseLock();} catch (_) { /* a cancelled read may still be settling */ }
      }
    } catch (error) {throw safeError(error);}
  }

  function element(state, tag, text, className) {
    const node = state.container.ownerDocument.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  function current(state) {
    if (state.disposed || instances.get(state.container) !== state) return false;
    if (state.root.parentNode !== state.container) {dispose(state.container); return false;}
    if (state.root.isConnected) {state.wasConnected = true; return true;}
    if (state.wasConnected) dispose(state.container);
    return false;
  }
  function moreButton(parent, state, remaining, action) {
    const button = element(state, "button", `显示更多（剩余 ${remaining}）`);
    button.type = "button";
    button.addEventListener("click", () => {if (current(state)) {button.remove(); action();}});
    parent.appendChild(button);
  }
  function scalar(parent, value, state, key) {
    let text = numericText(value) ?? (value === null ? "无" : typeof value === "boolean" ? (value ? "是" : "否") : String(value));
    if (key === "status" && typeof value === "string") {
      text = own(STATUS, value) ? STATUS[value] : value === "succeeded" ? "已完成" : value;
    }
    const pre = element(state, "pre", "", "home-result-details__value"); parent.appendChild(pre);
    let offset = 0;
    const page = () => {
      // Avoid splitting a Unicode surrogate pair at a display page boundary.
      let end = Math.min(text.length, offset + TEXT_PAGE);
      if (end < text.length && /[\uD800-\uDBFF]/.test(text[end - 1])) end--;
      pre.textContent += text.slice(offset, end); offset = end;
      if (offset < text.length) moreButton(parent, state, text.length - offset, page);
    };
    page();
  }
  function disclosure(parent, title, state, populate) {
    const details = element(state, "details"); details.open = false;
    details.appendChild(element(state, "summary", title)); parent.appendChild(details);
    let loaded = false;
    details.addEventListener("toggle", event => {
      if (event.target !== details || !details.open || loaded || !current(state)) return;
      loaded = true;
      const body = element(state, "div", undefined, "home-result-details__section");
      details.appendChild(body); populate(body);
    });
  }
  function valueNode(parent, value, state, key = "") {
    if (!value || typeof value !== "object" || numericText(value) !== undefined) {scalar(parent, value, state, key); return;}
    const count = Object.keys(value).length;
    if (!count) {scalar(parent, Array.isArray(value) ? "[]" : "{}", state); return;}
    disclosure(parent, `${own(FIELD, key) ? FIELD[key] : "展开"}（${count}）`, state,
      body => collection(body, value, state, key));
  }
  function fieldName(state, tag, key) {
    const node = element(state, tag), text = label(key);
    if (text.length > TEXT_PAGE) scalar(node, text, state);
    else node.textContent = text;
    return node;
  }
  function collection(parent, value, state, key) {
    const array = Array.isArray(value), keys = Object.keys(value);
    let columns = null;
    if (array && key !== "tool_result_sequence" && record(value[0])) {
      const first = Object.keys(value[0]);
      if (first.length > 0 && first.length <= 8 && value.every(row => record(row)
        && Object.keys(row).length === first.length && first.every(k => own(row, k)
          && (row[k] === null || typeof row[k] !== "object" || numericText(row[k]) !== undefined)))) columns = first;
    }
    const list = element(state, columns ? "div" : "dl", undefined, columns ? "home-result-details__table" : "home-result-details__fields");
    parent.appendChild(list);
    let body = list;
    if (columns) {
      list.tabIndex = 0; list.setAttribute("role", "region");
      list.setAttribute("aria-label", own(FIELD, key) ? FIELD[key] : "明细表格");
      const table = element(state, "table"), head = element(state, "thead"), row = element(state, "tr");
      columns.forEach(column => {const th = fieldName(state, "th", column); th.setAttribute("scope", "col"); row.appendChild(th);});
      head.appendChild(row); table.appendChild(head); body = element(state, "tbody"); table.appendChild(body); list.appendChild(table);
    }
    let offset = 0;
    const page = () => {
      const end = Math.min(keys.length, offset + PAGE_SIZE);
      for (; offset < end; offset++) {
        const field = keys[offset], item = value[field];
        if (columns) {
          const row = element(state, "tr");
          columns.forEach(column => {const cell = element(state, "td"); scalar(cell, item[column], state, column); row.appendChild(cell);});
          body.appendChild(row);
        } else {
          const name = array ? String(offset + 1) : field, dd = element(state, "dd");
          list.appendChild(fieldName(state, "dt", name)); list.appendChild(dd);
          if (array && key === "tool_result_sequence" && record(item)) {
            const title = typeof item.tool_name === "string" && own(TOOL, item.tool_name)
              ? TOOL[item.tool_name] : `计算步骤 ${offset + 1}`;
            disclosure(dd, title, state, content => collection(content, item, state));
          } else valueNode(dd, item, state, array ? "" : field);
        }
      }
      if (offset < keys.length) moreButton(parent, state, keys.length - offset, page);
    };
    page();
  }
  function renderData(state, data) {
    state.body.replaceChildren();
    const p = data.payload;
    collection(state.body, {status: p.status, tool_result_sequence: p.tool_result_sequence,
      error: p.error, warnings: p.warnings, display_redacted: p.display_redacted}, state);
    disclosure(state.body, "技术标识", state, body => collection(body, {trace_id: p.trace_id, format: p.format,
      view_id: data.view_id, revision: data.revision, byte_length: data.byte_length}, state));
  }
  function renderError(state, error) {
    state.body.replaceChildren(); state.body.setAttribute("aria-busy", "false");
    const message = element(state, "p", safeError(error).message); message.setAttribute("role", "status");
    state.body.appendChild(message);
    const retry = element(state, "button", "重试"); retry.type = "button";
    retry.addEventListener("click", () => start(state)); state.body.appendChild(retry);
  }
  function start(state) {
    if (!current(state) || state.loaded) return Promise.resolve();
    if (state.request) return state.request.promise;
    const request = {controller: new AbortController()}; state.request = request; inflight.add(state);
    state.body.replaceChildren(element(state, "p", "正在加载…")); state.body.setAttribute("aria-busy", "true");
    request.promise = (async () => {
      try {
        const value = await abortable(Promise.resolve().then(() => {
          checkAbort(request.controller.signal);
          if (state.bindingError) throw state.bindingError;
          return state.load({traceId: state.traceId, reference: state.reference, signal: request.controller.signal});
        }), request.controller.signal);
        if (state.request !== request || !current(state)) return;
        const data = snapshot(value, state.traceId, state.reference);
        renderData(state, data); state.loaded = true;
      } catch (error) {
        if (state.request === request && current(state)) renderError(state, error);
      } finally {
        if (state.request === request) {
          state.request = null; inflight.delete(state); state.body.setAttribute("aria-busy", "false");
        }
      }
    })();
    return request.promise;
  }
  function cancel(state) {
    const request = state.request;
    if (!request) return false;
    state.request = null; inflight.delete(state); request.controller.abort(); return true;
  }
  function dispose(container) {
    const state = instances.get(container);
    if (!state) return;
    state.disposed = true; cancel(state); state.observer?.disconnect();
    state.root.removeEventListener("toggle", state.toggle); state.root.remove(); instances.delete(container);
  }
  function abortAll() {
    for (const state of [...inflight]) {
      if (cancel(state) && current(state)) renderError(state, new DetailError("aborted"));
    }
  }
  function mount(container, {traceId, reference, load = read} = {}) {
    dispose(container);
    const state = {container, traceId, load, loaded: false, disposed: false, request: null};
    try {state.reference = binding(traceId, reference); if (typeof load !== "function") fail("invalid");}
    catch (_) {state.bindingError = new DetailError("invalid");}
    state.root = element(state, "details", undefined, "home-result-details"); state.root.open = false;
    state.root.appendChild(element(state, "summary", "查看明细"));
    state.body = element(state, "div", undefined, "home-result-details__body"); state.root.appendChild(state.body);
    state.toggle = event => {if (event.target === state.root && state.root.open) return start(state);};
    state.root.addEventListener("toggle", state.toggle); container.appendChild(state.root); instances.set(container, state);
    state.wasConnected = state.root.isConnected;
    const Observer = window.MutationObserver;
    if (Observer && container.ownerDocument.documentElement) {
      state.observer = new Observer(() => current(state));
      state.observer.observe(container.ownerDocument.documentElement, {childList: true, subtree: true});
    }
    return state.root;
  }
  window.HomeResultDetails = Object.freeze({mount, dispose, read, abortAll});
})();
