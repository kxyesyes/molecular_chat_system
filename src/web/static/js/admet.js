(function () {
  "use strict";

  const EXAMPLE_SMILES = "CC(=O)Oc1ccccc1C(=O)O";
  const GROUP_LABELS = {
    Physicochemical: "物化性质",
    Absorption: "吸收",
    Distribution: "分布",
    Metabolism: "代谢",
    Excretion: "排泄",
    Toxicity: "毒性",
    Druglikeness: "药物相似性",
  };
  const DEFAULT_GROUP = "其他端点";
  const stateText = {
    succeeded: "评估完成",
    partial: "部分完成",
    failed: "评估失败",
    unavailable: "工具不可用",
    timeout: "计算超时",
    busy: "服务繁忙",
    invalid_input: "输入无效",
    not_calculated: "尚未运行",
    running: "执行中",
  };

  function get(id) { return document.getElementById(id); }
  function text(value, empty) {
    if (empty === undefined) empty = "未提供";
    if (value === null || value === undefined || value === "") return empty;
    if (typeof value === "number" && !Number.isFinite(value)) return "未计算";
    if (typeof value === "boolean") return value ? "是" : "否";
    if (typeof value === "object") return JSON.stringify(value);
    return String(value);
  }
  function appendText(parent, tag, value, className) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    element.textContent = text(value);
    parent.append(element);
    return element;
  }
  function clear(element) { if (element) element.replaceChildren(); }
  function normalizedStatus(response) {
    const status = response && typeof response.status === "string" ? response.status : "failed";
    return stateText[status] ? status : "failed";
  }
  function setStatus(status, message) {
    const badge = get("status-badge");
    const runStatus = get("run-status");
    const results = get("results-panel");
    const safeStatus = stateText[status] ? status : "failed";
    if (badge) {
      badge.className = "status-badge state-" + safeStatus;
      badge.textContent = stateText[safeStatus];
    }
    if (runStatus) runStatus.textContent = message || stateText[safeStatus];
    if (results) {
      results.setAttribute("aria-busy", safeStatus === "running" ? "true" : "false");
      results.setAttribute("data-state", safeStatus);
    }
  }
  function setError(message) {
    const groups = get("endpoint-groups");
    clear(groups);
    const error = document.createElement("p");
    error.className = "error-note";
    error.textContent = message;
    if (groups) groups.append(error);
  }
  function renderOverview(response, rows) {
    const overview = get("admet-overview");
    clear(overview);
    if (!overview) return;
    if (response && response.message) appendText(overview, "p", response.message);
    if (rows.length) {
      const successCount = rows.filter(row => row && row.status === "succeeded").length;
      appendText(overview, "p", "返回 " + successCount + "/" + rows.length + " 个分子记录。");
    }
  }
  function endpointEntries(admet) {
    if (!admet || typeof admet !== "object" || !admet.endpoints || typeof admet.endpoints !== "object") return [];
    return Object.entries(admet.endpoints).filter(([, endpoint]) => endpoint && typeof endpoint === "object");
  }
  function diagnosticText(value, empty) {
    if (value && typeof value === "object") {
      return value.message || value.code || empty;
    }
    return value || empty;
  }
  function trustedAdmet(admet) {
    if (!admet || typeof admet !== "object") return false;
    if (!admet.prediction_method || admet.demo_mode !== false || admet.fallback_used !== false) return false;
    if (admet.prediction_method === "admet_ai" && !admet.weights_id) return false;
    return endpointEntries(admet).length > 0;
  }
  function sourceLabel(endpoint) {
    return endpoint && endpoint.source === "rdkit_calculation" ? "RDKit 计算" : "模型预测";
  }
  function renderEndpointGroups(rows) {
    const root = get("endpoint-groups");
    clear(root);
    if (!root) return;
    rows.forEach(row => {
      if (!row || typeof row !== "object") return;
      const molecule = document.createElement("article");
      molecule.className = "molecule-result";
      const heading = document.createElement("div");
      heading.className = "molecule-heading";
      appendText(heading, "h3", row.molecule_id || "未命名分子");
      appendText(heading, "span", row.status || "未提供", "status-badge state-" + (row.status === "succeeded" ? "success" : "failed"));
      molecule.append(heading);
      if (row.smiles) appendText(molecule, "code", row.smiles, "smiles-record");
      if (row.status !== "succeeded") {
        const error = document.createElement("p");
        error.className = "error-note";
        error.textContent = diagnosticText(row.error, "该分子未形成可用 ADMET 结果。");
        molecule.append(error);
        (Array.isArray(row.warnings) ? row.warnings : []).forEach(warning => appendText(molecule, "p", warning, "error-note"));
        root.append(molecule);
        return;
      }
      const admet = row.admet;
      if (!trustedAdmet(admet)) {
        const warning = document.createElement("p");
        warning.className = "error-note";
        warning.textContent = "缺少真实模型 provenance，未展示 ADMET 数值。";
        molecule.append(warning);
        root.append(molecule);
        return;
      }
      const grouped = new Map();
      endpointEntries(admet).forEach(([id, endpoint]) => {
        const group = GROUP_LABELS[endpoint.category] || DEFAULT_GROUP;
        if (!grouped.has(group)) grouped.set(group, []);
        grouped.get(group).push([id, endpoint]);
      });
      grouped.forEach((entries, groupName) => {
        const section = document.createElement("section");
        section.className = "endpoint-section";
        appendText(section, "h4", groupName);
        const scroll = document.createElement("div");
        scroll.className = "table-scroll";
        const table = document.createElement("table");
        const head = document.createElement("thead");
        const headRow = document.createElement("tr");
        ["端点", "返回值", "来源"].forEach(value => appendText(headRow, "th", value));
        head.append(headRow);
        table.append(head);
        const body = document.createElement("tbody");
        entries.forEach(([id, endpoint]) => {
          const tr = document.createElement("tr");
          const nameCell = document.createElement("td");
          appendText(nameCell, "span", endpoint.name || id);
          appendText(nameCell, "span", id, "endpoint-id");
          const valueCell = document.createElement("td");
          appendText(valueCell, "span", endpoint.value, "endpoint-value");
          appendText(valueCell, "span", endpoint.unit, "endpoint-unit");
          const sourceCell = document.createElement("td");
          appendText(sourceCell, "span", sourceLabel(endpoint), "endpoint-source");
          if (Array.isArray(admet.risk_endpoint_ids) && admet.risk_endpoint_ids.includes(id)) appendText(sourceCell, "span", "筛选提示", "endpoint-risk");
          tr.append(nameCell, valueCell, sourceCell);
          body.append(tr);
        });
        table.append(body);
        scroll.append(table);
        section.append(scroll);
        molecule.append(section);
      });
      root.append(molecule);
    });
  }
  function renderEvidence(response, rows) {
    const panel = get("evidence-panel");
    clear(panel);
    if (!panel) return;
    const admet = rows.find(row => row && row.admet)?.admet || {};
    const provenance = response && response.provenance && typeof response.provenance === "object" ? response.provenance : {};
    const record = document.createElement("dl");
    record.className = "evidence-record";
    [
      ["工具", provenance.tool_name || "admet_predictor"],
      ["模型", admet.model_name || provenance.model_name],
      ["模型版本", admet.model_version || provenance.model_version],
      ["权重标识", admet.weights_id],
      ["预测方法", admet.prediction_method],
      ["后端版本", admet.backend_version || provenance.backend_version],
      ["demo mode", admet.demo_mode],
      ["fallback", admet.fallback_used],
    ].forEach(([name, value]) => { appendText(record, "dt", name); appendText(record, "dd", value); });
    panel.append(record);
    const isUntrusted = admet.demo_mode !== false || admet.fallback_used !== false || (admet.prediction_method === "admet_ai" && !admet.weights_id);
    if (isUntrusted && (admet.prediction_method || response.status === "succeeded")) appendText(panel, "p", "证据不足，未展示为真实模型结果。", "error-note");
    const warnings = [];
    if (Array.isArray(response.warnings)) warnings.push(...response.warnings);
    rows.forEach(row => { if (Array.isArray(row?.warnings)) warnings.push(...row.warnings); });
    if (warnings.length) {
      const list = document.createElement("ul");
      list.className = "warning-list";
      [...new Set(warnings.map(value => String(value)))].forEach(warning => appendText(list, "li", warning));
      panel.append(list);
    }
    if (response.reasoning) appendText(panel, "p", response.reasoning, "evidence-record");
    const details = document.createElement("details");
    details.className = "evidence-details";
    appendText(details, "summary", "查看完整来源与质量记录");
    appendText(details, "pre", JSON.stringify({
      provenance: response.provenance || null,
      quality: response.quality || null,
      error: response.error || null,
    }, null, 2));
    panel.append(details);
    if (!Object.keys(provenance).length && !Object.keys(admet).length && !warnings.length) appendText(panel, "p", "本次响应没有提供来源记录。", "error-note");
  }
  function renderResponse(response) {
    const empty = get("empty-state");
    const rawRows = response && Array.isArray(response.data) ? response.data : [];
    const rows = rawRows.filter(row => row && typeof row === "object");
    const status = normalizedStatus(response || {});
    const displayRows = ["failed", "unavailable", "invalid_input"].includes(status)
      ? rows.filter(row => row.status !== "succeeded") : rows;
    renderOverview(response || {}, displayRows);
    renderEvidence(response || {}, displayRows);
    if (empty) empty.hidden = displayRows.length > 0;
    if (status === "succeeded" && displayRows.length === 0) {
      if (empty) empty.hidden = true;
      setStatus("failed", "服务未返回可用分子记录，未形成评估。");
      setError("没有可展示的 ADMET 结果。请检查模型状态后重试。");
      return;
    }
    if (!displayRows.length && ["failed", "unavailable", "invalid_input", "timeout", "busy"].includes(status)) {
      if (empty) empty.hidden = true;
      const message = response?.message || stateText[status];
      setError(message);
      setStatus(status, stateText[status] + "：" + message);
      return;
    }
    renderEndpointGroups(displayRows);
    const evidenceInsufficient = displayRows.some(row => row && row.status === "succeeded" && !trustedAdmet(row.admet));
    const message = evidenceInsufficient ? "证据不足，未展示为真实模型结果。" : response?.message || stateText[status];
    const displayStatus = evidenceInsufficient ? "partial" : status;
    setStatus(displayStatus, displayStatus === "succeeded" ? message : stateText[displayStatus] + "：" + message);
  }
  let inFlight = false;
  function setInFlight(value) {
    inFlight = value;
    ["run-admet", "reset-admet", "load-example", "smiles-input"].forEach(id => {
      const element = get(id);
      if (element) element.disabled = value;
    });
  }
  async function submit(event) {
    if (event) event.preventDefault();
    if (inFlight) return;
    const input = get("smiles-input");
    const value = input ? input.value.trim() : "";
    if (!value) {
      setStatus("invalid_input", "请输入一个完整的 SMILES 结构。");
      setError("缺少 SMILES，未调用 ADMET 工具。");
      return;
    }
    setInFlight(true);
    clear(get("endpoint-groups"));
    clear(get("admet-overview"));
    clear(get("evidence-panel"));
    setStatus("running", "正在调用本地 ADMET 工具，等待结构化结果。");
    try {
      const response = await fetch("/api/admet/predict", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ smiles: value, molecule_id: "molecule-001" }),
      });
      const payload = await response.json();
      if (!response.ok) {
        if (payload && typeof payload === "object" && payload.status) {
          renderResponse(payload);
        } else {
          const detail = payload && payload.detail;
          const structured = detail && typeof detail === "object" ? detail : {};
          const detailText = typeof detail === "string" ? detail : structured.message;
          const code = structured.code || (response.status === 429 ? "ADMET_BUSY" : response.status === 422 ? "INVALID_INPUT" : "ADMET_REQUEST_FAILED");
          const fallbackMessage = response.status === 429 ? "服务繁忙，请稍后重试。" : response.status === 422 ? "输入无效，请检查 SMILES。" : "请求失败，未形成 ADMET 结果。";
          const message = detailText || fallbackMessage;
          const safeMessage = String(message).replace(/\b(?:sk-|api[_-]?key|token|secret|password)\S*/gi, "[已隐藏]");
          const displayMessage = fallbackMessage + (detailText && safeMessage !== fallbackMessage ? " " + safeMessage : "");
          setStatus(response.status === 422 ? "invalid_input" : "failed", code + "：" + displayMessage);
          setError(code + "：" + displayMessage);
        }
        return;
      }
      renderResponse(payload);
    } catch (_error) {
      setStatus("failed", "请求失败，未形成 ADMET 结果。");
      setError("无法连接 ADMET 服务。请检查服务状态后重试。");
      clear(get("admet-overview"));
      clear(get("evidence-panel"));
    } finally {
      setInFlight(false);
    }
  }
  function reset() {
    if (inFlight) return;
    const input = get("smiles-input");
    if (input) input.value = "";
    clear(get("endpoint-groups"));
    clear(get("admet-overview"));
    const empty = get("empty-state");
    if (empty) empty.hidden = false;
    const evidence = get("evidence-panel");
    clear(evidence);
    if (evidence) appendText(evidence, "p", "尚无运行记录。完成调用后，这里会保留工具、模型版本、权重标识和警告。", "evidence-empty");
    setStatus("not_calculated", "等待输入分子结构。提交后将在此显示实际工具结果。");
  }
  function loadExample() {
    if (inFlight) return;
    const input = get("smiles-input");
    if (input) { input.value = EXAMPLE_SMILES; input.focus(); }
  }
  function init() {
    const form = get("admet-form");
    const example = get("load-example");
    const resetButton = get("reset-admet");
    if (form) form.addEventListener("submit", submit);
    if (example) example.addEventListener("click", loadExample);
    if (resetButton) resetButton.addEventListener("click", reset);
  }
  const api = { init, renderResponse, submit, reset };
  if (typeof window !== "undefined") {
    window.MedChatADMET = api;
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, { once: true });
    else init();
  }
}());
