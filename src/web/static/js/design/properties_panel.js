"use strict";

var PropertiesPanel = (function () {
  var S = DesignState;
  var Api = DesignApi;
  var UI = DesignUI;
  var MISSING = "—";

  function clearPropsUI() {
    S.curProps = null;
    S.prevProps = null;
    S.curGoals = null;
    S.propsSmiles = "";
    ["pLogP", "pMW", "pQED", "pTPSA", "pSAS"].forEach(function (id) {
      var el = document.getElementById(id);
      if (!el) return;
      el.textContent = MISSING;
      el.className = "ph";
    });
    ["dLogP", "dMW", "dQED", "dTPSA", "dSAS"].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.textContent = "";
    });
    var ro5 = document.getElementById("ro5Grid");
    if (ro5) ro5.innerHTML = ["MW ≤ 500", "LogP ≤ 5", "HBD ≤ 5", "HBA ≤ 10", "RotB ≤ 10", "TPSA ≤ 140"].map(function (label) {
      return '<div class="ro5-item"><span class="ro5-dot"></span>' + label + ' · ' + MISSING + '</div>';
    }).join("");
    var goals = document.getElementById("goalList");
    if (goals) goals.innerHTML = '<div class="empty-inline">未指定优化目标</div>';
  }

  async function calcProps(smi, referenceSmiles, requestToken) {
    if (!smi) return { stale: true };
    var token = requestToken == null ? ++S.propsRequestSeq : requestToken;
    try {
      var d = await Api.calcProperties(smi, {
        command: S.optimizationCommand || "",
        reference_smiles: referenceSmiles || "",
      });
      // A late response is data for an old molecule, never for the current UI.
      if (token !== S.propsRequestSeq || smi !== S.smiles) return { stale: true };
      if (d.success) {
        S.prevProps = S.curProps;
        S.curProps = Object.assign({}, d.properties || {});
        var propertyStatus = d.property_status || {};
        Object.keys(propertyStatus).forEach(function (key) {
          if (propertyStatus[key] === "unavailable" || propertyStatus[key] === "failed") S.curProps[key] = null;
        });
        S.propsSmiles = smi;
        S.curGoals = d.goals || null;
        renderProps(S.curProps, S.prevProps);
        renderRo5(S.curProps);
        renderGoals(d.goals || {});
        var status = document.getElementById("designStatus");
        if (status) status.textContent = "已计算";
        var caption = document.getElementById("resultCaption");
        if (caption) caption.textContent = "性质来自 RDKit；不可用项不会被填充为数值。";
      } else {
        S.curProps = null;
        S.propsSmiles = "";
        clearPropsUI();
        UI.toast(d.error || "属性计算失败", "error");
      }
      return d;
    } catch (e) {
      if (token !== S.propsRequestSeq || smi !== S.smiles) return { stale: true };
      S.curProps = null;
      S.propsSmiles = "";
      clearPropsUI();
      UI.toast("属性计算请求失败", "error");
      return { success: false, error: e.message };
    }
  }

  function numeric(value) {
    if (value == null || typeof value === "boolean" || (typeof value === "string" && !value.trim())) return null;
    var n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  function renderProps(p, prev) {
    p = p || {};
    function setP(vid, value, prevValue, deltaId, warnValue, lowerBetter) {
      var el = document.getElementById(vid);
      var n = numeric(value);
      if (!el) return;
      el.className = "";
      if (n === null) {
        el.textContent = MISSING;
        el.className = "ph";
        var missingDelta = document.getElementById(deltaId);
        if (missingDelta) missingDelta.textContent = "";
        return;
      }
      el.textContent = n.toFixed(2);
      if (warnValue != null) {
        if (lowerBetter && n > warnValue * 1.2) el.className = "danger";
        else if (lowerBetter && n > warnValue) el.className = "warn";
        else if (!lowerBetter && n < warnValue * .6) el.className = "danger";
        else if (!lowerBetter && n < warnValue) el.className = "warn";
      }
      var previous = numeric(prevValue);
      var delta = previous === null ? null : n - previous;
      var deltaEl = document.getElementById(deltaId);
      if (deltaEl) deltaEl.textContent = delta === null || Math.abs(delta) <= .001 ? "" : (delta >= 0 ? "+" : "") + delta.toFixed(2);
    }
    setP("pLogP", p.logp, prev && prev.logp, "dLogP", 5, true);
    setP("pMW", p.mw, prev && prev.mw, "dMW", 500, true);
    setP("pQED", p.qed, prev && prev.qed, "dQED", .7, false);
    setP("pTPSA", p.tpsa, prev && prev.tpsa, "dTPSA", 140, true);
    setP("pSAS", p.sa_score, prev && prev.sa_score, "dSAS", 3.5, true);
  }

  function renderRo5(p) {
    p = p || {};
    var rules = [{ l: "MW ≤ 500", v: p.mw, ok: function (v) { return v <= 500; } }, { l: "LogP ≤ 5", v: p.logp, ok: function (v) { return v <= 5; } }, { l: "HBD ≤ 5", v: p.hbd, ok: function (v) { return v <= 5; } }, { l: "HBA ≤ 10", v: p.hba, ok: function (v) { return v <= 10; } }, { l: "RotB ≤ 10", v: p.rotbonds, ok: function (v) { return v <= 10; } }, { l: "TPSA ≤ 140", v: p.tpsa, ok: function (v) { return v <= 140; } }];
    var el = document.getElementById("ro5Grid");
    if (!el) return;
    el.innerHTML = rules.map(function (r) { var n = numeric(r.v); return '<div class="ro5-item"><span class="ro5-dot ' + (n === null ? "" : r.ok(n) ? "pass" : "fail") + '"></span>' + r.l + ' · ' + (n === null ? MISSING : (r.ok(n) ? "通过" : "超限")) + '</div>'; }).join("");
  }

  function renderGoals(result) {
    var el = document.getElementById("goalList");
    if (!el) return;
    var items = result && Array.isArray(result.items) ? result.items : [];
    if (!items.length) { el.innerHTML = '<div class="empty-inline">未指定优化目标</div>'; return; }
    el.innerHTML = items.map(function (item) {
      var status = item.passed === true ? "pass" : item.passed === false ? "fail" : "pend";
      var text = item.passed === true ? "达成" : item.passed === false ? "未达成" : "待比较";
      return '<div class="goal-item"><span>' + item.label + '</span><span class="goal-status ' + status + '">' + text + '</span></div>';
    }).join("");
  }

  return { numeric: numeric, clearPropsUI: clearPropsUI, calcProps: calcProps, renderProps: renderProps, renderRo5: renderRo5, renderGoals: renderGoals };
})();
