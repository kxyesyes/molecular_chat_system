/* Live ScientificReport@1: detached display data, never ACK/result authority. */
(function (root) {
  "use strict";
  const trusted = new WeakSet();
  const trustedRows = new WeakSet(), trustedSources = new WeakSet();
  const descriptors = ["molecular_weight", "logp", "tpsa", "qed"];
  const positive = s => s === "available" || s === "partial";
  const states = ["available", "partial", "not_provided", "unavailable", "invalid", "ambiguous"];
  const reasons = ["none", "missing_source", "source_unavailable", "source_failed", "source_mismatch", "input_unverifiable",
    "alignment_missing", "row_missing", "invalid_value", "ambiguous_source", "stale_source", "candidate_not_displayed", "partial_source", "unsupported_binding"];
  const check = ok => { if (!ok) throw Error("Invalid scientific report"); };
  const exact = (o, fields) => check(o && !Array.isArray(o) && typeof o === "object" &&
    Object.keys(o).sort().join(" ") === fields.split(" ").sort().join(" "));
  const text = (v, max = 128, nullable = false) => check((nullable && v === null) || typeof v === "string" && v.trim() && v.length <= max);
  const digest = (v, nullable = false) => check((nullable && v === null) || typeof v === "string" && /^[a-f0-9]{64}$/.test(v));
  const count = (v, nullable = false) => check((nullable && v === null) || Number.isSafeInteger(v) && v >= 0);
  const unit = (v, nullable = false) => check((nullable && v === null) || typeof v === "number" && Number.isFinite(v) && v >= 0 && v <= 1);
  const list = (v, max) => check(Array.isArray(v) && v.length <= max);
  const missing = v => {list(v, 2); check(new Set(v).size === v.length && v.every(x => x === "admet" || x === "activity"));};
  const pairKey = (oid, cid) => JSON.stringify([oid, cid]);
  function sameReference(a, b) {
    return a && b && a.trace_id === b.trace_id && a.presentation_id === b.presentation_id && a.revision === b.revision &&
      JSON.stringify(a.ordered_keys) === JSON.stringify(b.ordered_keys);
  }
  function cloneBounded(value) {
    let nodes = 0, chars = 0;
    const active = new Set();
    function copy(v, depth) {
      check(++nodes <= 8192 && depth <= 12);
      if (v === null || typeof v === "boolean") return v;
      if (typeof v === "number") {check(Number.isFinite(v)); return v;}
      if (typeof v === "string") {
        chars += v.length; check(chars <= 131072);
        check(!/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?:^|[^\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(v));
        return v;
      }
      check(v && typeof v === "object" && !active.has(v));
      const array = Array.isArray(v);
      check(Object.getPrototypeOf(v) === (array ? Array.prototype : Object.prototype));
      const fields = Object.getOwnPropertyDescriptors(v), names = Reflect.ownKeys(fields);
      check(names.length <= 8192);
      if (array) check(names.length === v.length + 1 && v.length <= 8192);
      active.add(v);
      const out = array ? [] : {};
      for (const k of names) {
        if (array && k === "length") continue;
        check(typeof k === "string" && !["__proto__", "constructor", "prototype"].includes(k));
        const d = fields[k]; check("value" in d && d.enumerable);
        if (array) check(/^(0|[1-9][0-9]*)$/.test(k) && Number(k) < v.length);
        copy(k, depth + 1);
        out[k] = copy(d.value, depth + 1);
      }
      active.delete(v);
      return out;
    }
    const out = copy(value, 0);
    // UTF-8 byte budget; only detached exact builtins reach serialization.
    check(unescape(encodeURIComponent(JSON.stringify(out))).length <= 131072);
    return out;
  }
  function normalize(payload) {
    try {
      const p = cloneBounded(payload);
      exact(p, "type schema_version trace_id source_version projection_id run_status target sources generations collections property_rows steps ranking warnings omitted");
      check(p.type === "scientific_report" && p.schema_version === "1" && ["completed", "partial"].includes(p.run_status));
      text(p.trace_id); digest(p.source_version); digest(p.projection_id);
      exact(p.target, "label origin"); text(p.target.label, 128, true);
      check(p.target.origin === (p.target.label === null ? "not_provided" : "workflow_plan"));
      for (const [k, n] of Object.entries({sources:24, generations:8, collections:8, property_rows:32, steps:32, warnings:32})) list(p[k], n);
      const sources = new Map(), keys = new Set(), views = new Set();
      for (const s of p.sources) {
        exact(s, "observation_id step_id tool_name tool_version evidence_id status input_digest data_digest observation_digest output_digest_origin model_name model_version method backend_version");
        for (const k of ["observation_id", "step_id", "tool_name", "tool_version", "evidence_id"]) text(s[k]);
        for (const k of ["input_digest", "data_digest", "observation_digest"]) digest(s[k]);
        for (const k of ["model_name", "model_version", "method", "backend_version"]) text(s[k], 128, true);
        check(["succeeded", "partial"].includes(s.status) && ["recorded", "checkpoint_snapshot"].includes(s.output_digest_origin));
        check(!sources.has(s.observation_id)); sources.set(s.observation_id, s);
      }
      for (const c of p.collections) {
        exact(c, "reference generator_observation_id"); const r = c.reference;
        exact(r, "trace_id presentation_id revision ordered_keys");
        check(r.trace_id === p.trace_id); text(r.presentation_id); digest(r.revision);
        check(!views.has(r.presentation_id)); views.add(r.presentation_id);
        check(sources.get(c.generator_observation_id)?.tool_name === "llm_molecular_generator");
        list(r.ordered_keys, 32); check(r.ordered_keys.length > 0);
        for (const pair of r.ordered_keys) {
          list(pair, 2); check(pair.length === 2 && pair[0] === c.generator_observation_id); text(pair[1]);
          const key = pairKey(...pair); check(!keys.has(key)); keys.add(key);
        }
      }
      const generations = new Set();
      for (const g of p.generations) {
        const fields = "requested_count valid_count unique_count invalid_count duplicate_count displayed_count";
        exact(g, "source_observation_id status " + fields);
        check(["succeeded", "partial", "unknown"].includes(g.status));
        if (g.status === "unknown") check(g.source_observation_id === null && fields.split(" ").every(k => g[k] === null));
        else {
          check(sources.get(g.source_observation_id)?.tool_name === "llm_molecular_generator" && sources.get(g.source_observation_id).status === g.status);
          check(!generations.has(g.source_observation_id)); generations.add(g.source_observation_id);
          fields.split(" ").forEach(k => count(g[k]));
          check(g.displayed_count === [...keys].filter(k => JSON.parse(k)[0] === g.source_observation_id).length && g.displayed_count <= g.unique_count && g.unique_count <= g.valid_count);
        }
      }
      const rows = new Set();
      for (const r of p.property_rows) {
        exact(r, "generator_observation_id candidate_id canonical_smiles source_observation_id source_row_index row_digest state reason_code values");
        const key = pairKey(r.generator_observation_id, r.candidate_id);
        check(keys.has(key) && !rows.has(key)); rows.add(key); text(r.canonical_smiles, 8192);
        check(states.includes(r.state) && reasons.includes(r.reason_code)); exact(r.values, descriptors.join(" "));
        for (const [k, v] of Object.entries(r.values)) if (v !== null) {
          check(typeof v === "number" && Number.isFinite(v));
          check(k !== "molecular_weight" || v > 0); check(k !== "tpsa" || v >= 0); if (k === "qed") unit(v);
        }
        if (positive(r.state)) {
          check(sources.get(r.source_observation_id)?.tool_name === "property_calculator");
          count(r.source_row_index); digest(r.row_digest); check(Object.values(r.values).some(v => v !== null));
          if (r.state === "available") check(sources.get(r.source_observation_id).status === "succeeded" && Object.values(r.values).every(v => v !== null));
        } else check(Object.values(r.values).every(v => v === null) && r.source_observation_id === null && r.source_row_index === null && r.row_digest === null);
      }
      for (const s of p.steps) {
        exact(s, "step_id tool_name status source_observation_id reason_code message"); text(s.step_id); text(s.tool_name); text(s.message, 256);
        check(["succeeded", "partial", "failed", "rejected", "cancelled", "skipped", "unknown"].includes(s.status) && reasons.includes(s.reason_code));
        check(s.source_observation_id === null || sources.has(s.source_observation_id));
      }
      const rank = p.ranking;
      exact(rank, "state reason_code source_observation_id generator_observation_id requested_top_n ranked_candidate_count top_candidates unrankable_candidates");
      check(states.includes(rank.state) && reasons.includes(rank.reason_code)); list(rank.top_candidates, 32); list(rank.unrankable_candidates, 32);
      if (!positive(rank.state)) check(!rank.top_candidates.length && !rank.unrankable_candidates.length &&
        ["source_observation_id", "generator_observation_id", "requested_top_n", "ranked_candidate_count"].every(k => rank[k] === null));
      else {
        check(sources.get(rank.source_observation_id)?.tool_name === "candidate_ranker"); count(rank.requested_top_n); count(rank.ranked_candidate_count);
        check(rank.requested_top_n > 0 && rank.top_candidates.length <= Math.min(rank.requested_top_n, rank.ranked_candidate_count));
        const ranked = new Set();
        for (const r of [...rank.top_candidates, ...rank.unrankable_candidates]) {
          const key = pairKey(rank.generator_observation_id, r.candidate_id);
          check(keys.has(key) && !ranked.has(key)); ranked.add(key); text(r.canonical_smiles, 8192);
        }
        for (const r of rank.unrankable_candidates) {exact(r, "candidate_id canonical_smiles reason"); text(r.reason, 256);}
        for (const r of rank.top_candidates) {
          exact(r, "candidate_id canonical_smiles score missing_evidence ranking_evidence"); unit(r.score); missing(r.missing_evidence);
          const e = r.ranking_evidence; exact(e, "property_score admet_score activity_score weights_used missing_evidence");
          unit(e.property_score); unit(e.admet_score, true); unit(e.activity_score, true); missing(e.missing_evidence);
          check(JSON.stringify(e.missing_evidence) === JSON.stringify(r.missing_evidence)); exact(e.weights_used, "properties admet activity");
          for (const [k, v] of Object.entries(e.weights_used)) unit(v, k !== "properties");
        }
      }
      p.warnings.forEach(w => text(w, 256)); exact(p.omitted, "generations steps property_rows ranking_rows warnings"); Object.values(p.omitted).forEach(v => count(v));
      function freeze(v) {if (v && typeof v === "object") {Object.values(v).forEach(freeze); Object.freeze(v);}}
      freeze(p); trusted.add(p); p.property_rows.forEach(r => trustedRows.add(r)); p.sources.forEach(s => trustedSources.add(s)); return p;
    } catch (_) {return null;}
  }
  function createLifecycle() {
    let active = false, trace = null, value = null, conflicted = false;
    const clear = () => {active = false; trace = value = null; conflicted = false;};
    return {clear, startRequest() {clear(); active = true;},
      observeTrace(id) {if (!active || typeof id !== "string" || !id || id.length > 128) return false;
        if (trace && trace !== id) {value = null; conflicted = true; return false;} trace = id; return true;},
      enqueue(raw) {const p = normalize(raw); if (!active || conflicted || !p || p.trace_id !== trace) return false;
        if (value) {if (value.projection_id !== p.projection_id || JSON.stringify(value) !== JSON.stringify(p)) {value = null; conflicted = true;} return false;}
        value = p; return true;},
      take(id) {const p = active && !conflicted && id === trace ? value : null; clear(); return p;}};
  }
  const labels = {completed:"已完成", succeeded:"成功", partial:"部分完成／证据不完整", failed:"失败", rejected:"已拒绝", cancelled:"已取消", skipped:"已跳过", unknown:"未知",
    available:"已验证来源", not_provided:"未提供独立证据", unavailable:"不可用", invalid:"证据无效", ambiguous:"来源不唯一"};
  function line(parent, tag, text) {const el = document.createElement(tag); el.textContent = text; parent.appendChild(el); return el;}
  function renderReport(container, report) {
    if (!container || !trusted.has(report)) return {mounted:false};
    const box = document.createElement("section"); box.className = "scientific-evidence-report";
    box.style.cssText = "margin:16px 0;padding:16px;border:1px solid #cbd5e1;border-radius:8px;overflow-wrap:anywhere";
    line(box, "h3", `科研证据报告 · ${report.target.label || "靶点未提供"} · ${labels[report.run_status]}`);
    report.generations.forEach(g => line(box, "p", g.status === "unknown" ? "生成数量：未知" :
      `生成：请求 ${g.requested_count}，有效 ${g.valid_count}，去重后 ${g.unique_count}，无效 ${g.invalid_count}，重复 ${g.duplicate_count}，展示 ${g.displayed_count}`));
    report.steps.forEach(s => line(box, "p", `${s.tool_name}：${labels[s.status]}；${s.message}`));
    const rank = report.ranking;
    line(box, "h4", positive(rank.state) ? `实际 Top-${rank.requested_top_n}：展示 ${rank.top_candidates.length} / 可排序 ${rank.ranked_candidate_count}（${labels[rank.state]}）` : `候选排序：${labels[rank.state]}`);
    rank.top_candidates.forEach((r, i) => line(box, "p", `${i + 1}. ${r.candidate_id} · 优先级分数 ${r.score}；缺失证据：${r.missing_evidence.join("、") || "无"}`));
    rank.unrankable_candidates.forEach(r => line(box, "p", `${r.candidate_id}：不可排序（${r.reason}）`));
    report.sources.forEach(s => line(box, "p", `来源 ${s.tool_name}@${s.tool_version} · ${s.model_name || s.method || "模型未提供"} · 版本 ${s.model_version || s.backend_version || "未提供"} · ${s.evidence_id} · ${s.output_digest_origin}`));
    report.warnings.forEach(w => line(box, "p", w));
    if (Object.values(report.omitted).some(v => v > 0)) line(box, "p", `展示截断：${JSON.stringify(report.omitted)}`);
    container.appendChild(box); return {mounted:true};
  }
  function findPropertyRow(report, reference, generatorId, candidate) {
    if (!trusted.has(report) || !report.collections.some(c => c.generator_observation_id === generatorId && sameReference(c.reference, reference))) return null;
    return report.property_rows.find(r => r.generator_observation_id === generatorId && r.candidate_id === candidate.candidate_id && r.canonical_smiles === candidate.canonical_smiles) || null;
  }
  function renderProperties(container, row, source) {
    if (!container || !trustedRows.has(row) || !trustedSources.has(source) || !positive(row.state) || source.observation_id !== row.source_observation_id || source.tool_name !== "property_calculator") return false;
    const box = document.createElement("div");
    line(box, "strong", `独立 RDKit 描述符 · ${labels[row.state]}`);
    const names = ["分子量", "LogP", "TPSA", "QED"];
    descriptors.forEach((k, i) => line(box, "div", `${names[i]}：${row.values[k] === null ? "未提供" : row.values[k]}`));
    line(box, "small", `来源 ${source.evidence_id} · 行 ${row.source_row_index} · 非实验验证`);
    container.replaceChildren(box); return true;
  }
  root.HomeEvidenceReport = {normalize, createLifecycle, renderReport, findPropertyRow, renderProperties};
})(window);
