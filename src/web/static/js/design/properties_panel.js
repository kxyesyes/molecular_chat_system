/* ═══════════════════════════════════════════════════════════
   分子设计模块 – 属性面板
   calcProps / renderProps / Ro5 / Goals / Advice / clearUI
   ═══════════════════════════════════════════════════════════ */
"use strict";

var PropertiesPanel = (function () {
  var S = DesignState;
  var Api = DesignApi;
  var UI = DesignUI;

  /* ── 清空属性 UI ── */
  function clearPropsUI() {
    ["pLogP", "pMW", "pQED", "pTPSA", "pSAS"].forEach(function (id) {
      var el = document.getElementById(id);
      el.textContent = "-";
      el.classList.add("ph");
    });
    ["dLogP", "dMW", "dQED", "dTPSA", "dSAS"].forEach(function (id) {
      document.getElementById(id).style.display = "none";
    });
    ["bLogP", "bMW", "bQED", "bTPSA", "bSAS"].forEach(function (id) {
      var b = document.getElementById(id);
      b.style.width = "0%";
      b.classList.remove("good", "warn", "danger");
    });
    document.getElementById("ro5Grid").innerHTML =
      '<div class="ro5-item"><div class="ro5-dot"></div>MW ≤ 500</div>' +
      '<div class="ro5-item"><div class="ro5-dot"></div>LogP ≤ 5</div>' +
      '<div class="ro5-item"><div class="ro5-dot"></div>HBD ≤ 5</div>' +
      '<div class="ro5-item"><div class="ro5-dot"></div>HBA ≤ 10</div>' +
      '<div class="ro5-item"><div class="ro5-dot"></div>RotB ≤ 10</div>' +
      '<div class="ro5-item"><div class="ro5-dot"></div>TPSA ≤ 140</div>';
    document.getElementById("goalList").innerHTML =
      '<div class="goal-item"><span>QED ≥ 0.7</span> <span class="goal-status pend">待计算</span></div>' +
      '<div class="goal-item"><span>MW ≤ 500 Da</span> <span class="goal-status pend">待计算</span></div>' +
      '<div class="goal-item"><span>LogP ≤ 5</span> <span class="goal-status pend">待计算</span></div>' +
      '<div class="goal-item"><span>SA Score ≤ 3.5</span> <span class="goal-status pend">待计算</span></div>';
    var adviceEl = document.getElementById("aiAdvice");
    if (adviceEl) {
      adviceEl.textContent = "绘制分子后，系统将自动给出优化建议...";
    }
  }

  /* ── 计算属性 ── */
  async function calcProps(smi) {
    if (!smi) return;
    try {
      var d = await Api.calcProperties(smi);
      if (d.success) {
        S.prevProps = S.curProps;
        S.curProps = d.properties;
        renderProps(d.properties, S.prevProps);
        renderRo5(d.properties);
        renderGoals(d.properties);
        renderAdvice(d.properties);
      } else {
        S.curProps = null;
        clearPropsUI();
        UI.toast(d.error || "属性计算失败", "error");
      }
    } catch (e) {
      console.error("calcProps error:", e);
      S.curProps = null;
      clearPropsUI();
      UI.toast("属性计算请求失败", "error");
    }
  }

  /* ── 渲染属性值、delta 箭头、进度条 ── */
  function renderProps(p, prev) {
    function setP(
      vid,
      val,
      unit,
      prevV,
      bid,
      maxV,
      warnV,
      deltaId,
      lowerBetter,
    ) {
      var el = document.getElementById(vid);
      el.textContent = val.toFixed(2) + unit;
      el.classList.remove("ph", "warn", "danger");
      // 数值颜色
      if (warnV != null) {
        if (lowerBetter) {
          if (val > warnV * 1.2) el.classList.add("danger");
          else if (val > warnV) el.classList.add("warn");
        } else {
          if (val < warnV * 0.6) el.classList.add("danger");
          else if (val < warnV) el.classList.add("warn");
        }
      }
      // delta 箭头
      var del = document.getElementById(deltaId);
      if (prev && prevV !== undefined) {
        var d = val - prevV;
        if (Math.abs(d) > 0.001) {
          del.textContent = (d > 0 ? "↑" : "↓") + Math.abs(d).toFixed(2);
          del.className = "prop-delta " + (d > 0 ? "up" : "down");
          del.style.display = "";
        } else {
          del.style.display = "none";
        }
      }
      // 进度条
      var bar = document.getElementById(bid);
      if (bar) {
        bar.style.width = Math.min((val / maxV) * 100, 100) + "%";
        bar.classList.remove("good", "warn", "danger");
        if (warnV != null) {
          if (lowerBetter) {
            bar.classList.add(
              val <= warnV ? "good" : val <= warnV * 1.2 ? "warn" : "danger",
            );
          } else {
            bar.classList.add(
              val >= warnV ? "good" : val >= warnV * 0.6 ? "warn" : "danger",
            );
          }
        } else {
          bar.classList.add("good");
        }
      }
    }
    setP("pLogP", p.logp ?? 0, "", prev?.logp, "bLogP", 10, 5, "dLogP", true);
    setP("pMW", p.mw ?? 0, "", prev?.mw, "bMW", 600, 500, "dMW", true);
    setP("pQED", p.qed ?? 0, "", prev?.qed, "bQED", 1, 0.7, "dQED", false);
    setP(
      "pTPSA",
      p.tpsa ?? 0,
      "",
      prev?.tpsa,
      "bTPSA",
      200,
      140,
      "dTPSA",
      true,
    );
    if (p.sa_score !== undefined) {
      setP(
        "pSAS",
        p.sa_score,
        "",
        prev?.sa_score,
        "bSAS",
        10,
        3.5,
        "dSAS",
        true,
      );
    }
  }

  /* ── Lipinski Ro5 ── */
  function renderRo5(p) {
    var rules = [
      { l: "MW ≤ 500", p: (p.mw ?? 999) <= 500 },
      { l: "LogP ≤ 5", p: (p.logp ?? 99) <= 5 },
      { l: "HBD ≤ 5", p: (p.hbd ?? 99) <= 5 },
      { l: "HBA ≤ 10", p: (p.hba ?? 99) <= 10 },
      { l: "RotB ≤ 10", p: (p.rotbonds ?? 99) <= 10 },
      { l: "TPSA ≤ 140", p: (p.tpsa ?? 999) <= 140 },
    ];
    document.getElementById("ro5Grid").innerHTML = rules
      .map(function (r) {
        return (
          '<div class="ro5-item"><div class="ro5-dot ' +
          (r.p ? "pass" : "fail") +
          '"></div>' +
          r.l +
          "</div>"
        );
      })
      .join("");
  }

  /* ── 优化目标 ── */
  function renderGoals(p) {
    var goals = [
      { l: "QED ≥ 0.7", p: (p.qed ?? 0) >= 0.7 },
      { l: "MW ≤ 500 Da", p: (p.mw ?? 999) <= 500 },
      { l: "LogP ≤ 5", p: (p.logp ?? 99) <= 5 },
      { l: "SA Score ≤ 3.5", p: (p.sa_score ?? 99) <= 3.5 },
    ];
    document.getElementById("goalList").innerHTML = goals
      .map(function (g) {
        var status = g.p ? "pass" : "fail";
        var text = g.p ? "达成" : "未达成";
        return (
          '<div class="goal-item"><span>' +
          g.l +
          '</span> <span class="goal-status ' +
          status +
          '">' +
          text +
          "</span></div>"
        );
      })
      .join("");
  }

  /* ── AI 建议 ── */
  function renderAdvice(p) {
    var issues = [];
    if ((p.mw ?? 0) > 500) issues.push("分子量偏大");
    if ((p.logp ?? 0) > 5) issues.push("LogP过高");
    if ((p.qed ?? 0) < 0.5) issues.push("QED偏低");
    var adviceEl = document.getElementById("aiAdvice");
    if (adviceEl) {
      adviceEl.innerHTML =
        issues.length === 0 ? "✅ 属性良好" : "⚠️ 建议：" + issues.join(", ");
    }
  }

  return {
    clearPropsUI: clearPropsUI,
    calcProps: calcProps,
    renderProps: renderProps,
    renderRo5: renderRo5,
    renderGoals: renderGoals,
    renderAdvice: renderAdvice,
  };
})();
