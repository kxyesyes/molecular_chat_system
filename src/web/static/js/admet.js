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
  const GROUP_ORDER = ["物化性质", "药物相似性", "吸收", "分布", "代谢", "排泄", "毒性", "其他端点"];
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
    const tabs = get("endpoint-tabs");
    clear(tabs);
    if (tabs) tabs.hidden = true;
    const error = document.createElement("p");
    error.className = "error-note";
    error.textContent = message;
    if (groups) groups.append(error);
  }
  function formatCount(value) {
    return Number.isInteger(value) && value >= 0 ? String(value) : "未计算";
  }
  function summaryRows(rows) {
    return rows.filter(row => row && row.status === "succeeded" && trustedAdmet(row.admet));
  }
  function appendMetric(root, label, value, detail, tone) {
    const card = document.createElement("article");
    card.className = "metric-card metric-" + tone;
    appendText(card, "span", label, "metric-label");
    appendText(card, "strong", value, "metric-value");
    appendText(card, "span", detail, "metric-detail");
    root.append(card);
  }
  function renderMetrics(response, rows) {
    const root = get("metric-cards");
    clear(root);
    if (!root) return;
    const trustedRows = summaryRows(rows);
    const status = normalizedStatus(response || {});
    const first = trustedRows[0]?.admet || {};
    const totalEndpoints = trustedRows.reduce((sum, row) => {
      const count = row.admet && row.admet.total_endpoints;
      return sum + (Number.isInteger(count) && count >= 0 ? count : endpointEntries(row.admet).length);
    }, 0);
    const riskCount = trustedRows.reduce((sum, row) => {
      const count = row.admet && row.admet.risk_count;
      return sum + (Number.isInteger(count) && count >= 0 ? count : 0);
    }, 0);
    const hasCounts = trustedRows.length > 0;
    appendMetric(root, "综合评估", stateText[status] || "未计算", response?.message || "依据本次工具状态", status === "succeeded" ? "success" : status === "partial" ? "warning" : "neutral");
    appendMetric(root, "预测指标", hasCounts ? formatCount(totalEndpoints) : "未计算", hasCounts ? "工具返回的端点数量" : "尚未形成可信结果", "primary");
    appendMetric(root, "风险项", hasCounts ? formatCount(riskCount) : "未计算", hasCounts ? "工具标记的风险端点" : "未形成风险判断", riskCount > 0 ? "danger" : "success");
    const model = first.model_name || first.model_version || first.prediction_method;
    appendMetric(root, "模型状态", model || (status === "unavailable" ? "不可用" : "未提供"), first.model_version ? "版本 " + first.model_version : "来源见结果记录", model ? "primary" : "neutral");
  }
  function renderOverview(response, rows) {
    const overview = get("admet-overview");
    clear(overview);
    renderMetrics(response, rows);
    if (!overview) return;
    const successCount = rows.filter(row => row && row.status === "succeeded").length;
    if (rows.length && successCount !== rows.length) appendText(overview, "p", "已保留成功记录，并明确显示未完成步骤。");
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
  function groupedEndpoints(rows) {
    const groups = new Map();
    rows.forEach(row => {
      if (!row || row.status !== "succeeded" || !trustedAdmet(row.admet)) return;
      endpointEntries(row.admet).forEach(([id, endpoint]) => {
        const group = GROUP_LABELS[endpoint.category] || DEFAULT_GROUP;
        if (!groups.has(group)) groups.set(group, []);
        groups.get(group).push({ row, id, endpoint });
      });
    });
    return groups;
  }
  function renderTabs(rows, activeGroup) {
    const tabs = get("endpoint-tabs");
    clear(tabs);
    if (!tabs) return "";
    const groups = groupedEndpoints(rows);
    const names = [...groups.keys()].sort((a, b) => {
      const ai = GROUP_ORDER.indexOf(a); const bi = GROUP_ORDER.indexOf(b);
      return (ai < 0 ? 99 : ai) - (bi < 0 ? 99 : bi);
    });
    if (!names.length) { tabs.hidden = true; return ""; }
    tabs.hidden = false;
    const selected = names.includes(activeGroup) ? activeGroup : names[0];
    names.forEach(name => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "endpoint-tab" + (name === selected ? " is-active" : "");
      button.setAttribute("aria-selected", name === selected ? "true" : "false");
      button.textContent = name + " " + groups.get(name).length;
      button.addEventListener("click", () => {
        renderTabs(rows, name);
        renderEndpointGroups(rows, name);
      });
      tabs.append(button);
    });
    return selected;
  }
  function renderEndpointGroups(rows, activeGroup) {
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
        if (activeGroup && groupName !== activeGroup) return;
        const section = document.createElement("section");
        section.className = "endpoint-section";
        section.setAttribute("data-category", groupName);
        appendText(section, "h4", groupName);
        const scroll = document.createElement("div");
        scroll.className = "table-scroll";
        const table = document.createElement("table");
        const head = document.createElement("thead");
        const headRow = document.createElement("tr");
        ["指标名称", "预测值", "风险标记", "来源"].forEach(value => appendText(headRow, "th", value));
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
          const riskCell = document.createElement("td");
          const isRisk = Array.isArray(admet.risk_endpoint_ids) && admet.risk_endpoint_ids.includes(id);
          appendText(riskCell, "span", isRisk ? "风险项" : "未标记", isRisk ? "endpoint-risk" : "endpoint-clear");
          const sourceCell = document.createElement("td");
          appendText(sourceCell, "span", sourceLabel(endpoint), "endpoint-source");
          tr.append(nameCell, valueCell, riskCell, sourceCell);
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
    const completedAt = get("completed-at");
    if (completedAt) completedAt.textContent = new Date().toLocaleString("zh-CN", { hour12: false });
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
    activeGroup = renderTabs(displayRows, activeGroup);
    renderEndpointGroups(displayRows, activeGroup);
    const evidenceInsufficient = displayRows.some(row => row && row.status === "succeeded" && !trustedAdmet(row.admet));
    const message = evidenceInsufficient ? "证据不足，未展示为真实模型结果。" : response?.message || stateText[status];
    const displayStatus = evidenceInsufficient ? "partial" : status;
    setStatus(displayStatus, displayStatus === "succeeded" ? message : stateText[displayStatus] + "：" + message);
  }
  let inFlight = false;
  let activeGroup = "";
  function updateSmilesCount() {
    const input = get("smiles-input");
    const counter = get("smiles-count");
    if (input && counter) counter.textContent = String(input.value.length) + "/" + String(input.maxLength || 8192);
  }
  function updatePreview(smiles) {
    const preview = get("structure-preview");
    const image = get("structure-preview-image");
    const message = get("structure-preview-message");
    if (!preview || !image) return;
    const value = String(smiles || "").trim();
    if (!value) { preview.hidden = true; return; }
    image.setAttribute("src", "/api/utils/smiles_to_image?smiles=" + encodeURIComponent(value) + "&width=300&height=180");
    image.addEventListener("error", () => {
      preview.hidden = false;
      image.hidden = true;
      if (message) { message.hidden = false; message.textContent = "无法生成结构预览，请检查 SMILES。"; }
    }, { once: true });
    image.addEventListener("load", () => {
      image.hidden = false;
      if (message) message.hidden = true;
    }, { once: true });
    preview.hidden = false;
  }
  function setInFlight(value) {
    inFlight = value;
    ["run-admet", "rerun-admet", "reset-admet", "load-example", "load-example-caffeine", "load-example-ibuprofen", "smiles-input"].forEach(id => {
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
    clear(get("metric-cards"));
    clear(get("endpoint-tabs"));
    const tabs = get("endpoint-tabs");
    if (tabs) tabs.hidden = true;
    clear(get("evidence-panel"));
    const empty = get("empty-state");
    if (empty) empty.hidden = true;
    activeGroup = "";
    updatePreview(value);
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
    updatePreview("");
    const completedAt = get("completed-at");
    if (completedAt) completedAt.textContent = "尚未运行";
    const evidence = get("evidence-panel");
    clear(evidence);
    if (evidence) appendText(evidence, "p", "尚无运行记录。完成调用后，这里会保留工具、模型版本、权重标识和警告。", "evidence-empty");
    updateSmilesCount();
    setStatus("not_calculated", "等待输入分子结构。提交后将在此显示实际工具结果。");
  }
  function loadExample(smiles) {
    if (inFlight) return;
    const input = get("smiles-input");
    if (input) { input.value = smiles || EXAMPLE_SMILES; updateSmilesCount(); updatePreview(input.value); input.focus(); }
  }
  function init() {
    const form = get("admet-form");
    const example = get("load-example");
    const caffeine = get("load-example-caffeine");
    const ibuprofen = get("load-example-ibuprofen");
    const resetButton = get("reset-admet");
    const rerunButton = get("rerun-admet");
    const input = get("smiles-input");
    if (form) form.addEventListener("submit", submit);
    if (example) example.addEventListener("click", () => loadExample(EXAMPLE_SMILES));
    if (caffeine) caffeine.addEventListener("click", () => loadExample("Cn1c(=O)c2c(ncn2C)n(C)c1=O"));
    if (ibuprofen) ibuprofen.addEventListener("click", () => loadExample("CC(C)Cc1ccc(cc1)[C@@H](C)C(=O)O"));
    if (resetButton) resetButton.addEventListener("click", reset);
    if (rerunButton) rerunButton.addEventListener("click", () => submit());
    if (input) { input.addEventListener("input", updateSmilesCount); updateSmilesCount(); }
  }
  const api = { init, renderResponse, submit, reset };
  if (typeof window !== "undefined") {
    window.MedChatADMET = api;
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, { once: true });
    else init();
  }
}());
