/* ═══════════════════════════════════════════════════════════
   分子设计模块 – 通用 UI 工具
   toast / modal / 手风琴 / 辅助函数
   ═══════════════════════════════════════════════════════════ */
"use strict";

var DesignUI = (function () {
  /* ── Toast 通知 ── */
  function toast(msg, type) {
    type = type || "info";
    var c = document.getElementById("toastBox");
    var t = document.createElement("div");
    t.className = "toast " + type;
    t.textContent = msg;
    if (type === "success") t.style.background = "#059669";
    else if (type === "error") t.style.background = "#dc2626";
    else t.style.background = "#3b82f6";
    c.appendChild(t);
    setTimeout(function () {
      t.remove();
    }, 2500);
  }

  function setFeedback(msg, type) {
    var el = document.getElementById("designFeedback");
    if (!el) return;
    if (!msg) {
      el.style.display = "none";
      el.className = "design-feedback";
      el.textContent = "";
      return;
    }
    type = type || "info";
    el.style.display = "block";
    el.className = "design-feedback " + type;
    el.textContent = msg;
  }

  /* ── 导入 Modal ── */
  function showImport() {
    document.getElementById("importModal").style.display = "flex";
  }

  function closeImport() {
    document.getElementById("importModal").style.display = "none";
  }

  /* ── 手风琴折叠 ── */
  function toggleAcc(id) {
    var grp = document.getElementById(id);
    var body = grp.querySelector(".acc-body");
    var arrow = grp.querySelector(".acc-arrow");
    var isOpen = grp.classList.contains("open");
    if (isOpen) {
      grp.classList.remove("open");
      body.style.display = "none";
      arrow.textContent = "▸";
    } else {
      grp.classList.add("open");
      body.style.display = "";
      arrow.textContent = "▾";
    }
  }

  /** 统计每组已选筛选器数量，更新徽章 */
  function updateAccBadges() {
    document.querySelectorAll(".acc-group").forEach(function (grp) {
      var chips = grp.querySelectorAll(".tag-chip.active");
      var badge = grp.querySelector(".acc-badge");
      if (badge) {
        badge.textContent = chips.length > 0 ? chips.length : "";
      }
    });
  }

  /* ── 转义单引号 (用于 onclick 属性) ── */
  function esc(s) {
    return s.replace(/\\/g, "\\\\").replace(/'/g, "\\'");
  }

  return {
    toast: toast,
    showImport: showImport,
    closeImport: closeImport,
    toggleAcc: toggleAcc,
    updateAccBadges: updateAccBadges,
    setFeedback: setFeedback,
    esc: esc,
  };
})();
