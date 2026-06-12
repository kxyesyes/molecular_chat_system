/* ═══════════════════════════════════════════════════════════
   分子设计模块 – 片段浏览器
   搜索 / 筛选 / 分页 / 片段网格 / 选择
   ═══════════════════════════════════════════════════════════ */
"use strict";

var FragmentBrowser = (function () {
  var S = DesignState;
  var Api = DesignApi;
  var UI = DesignUI;

  /** 加载片段列表 */
  async function loadFrags() {
    var params = { page: S.page, page_size: S.pageSize };
    if (S.search) params.search = S.search;
    S.filters.forEach(function (k) {
      params[k] = "1";
    });

    var activeCount = S.filters.size;
    document.getElementById("fragBadge").textContent =
      activeCount > 0 ? "过滤中 (" + activeCount + ")" : "加载中";

    try {
      var d = await Api.fetchFragments(params);
      if (d.success) {
        renderFragGrid(d.fragments);
        var numEl = document.getElementById("dbCountNum");
        if (numEl) numEl.textContent = d.total;
        document.getElementById("fragBadge").textContent = d.total + " 个";
        var tp = Math.ceil(d.total / S.pageSize);
        document.getElementById("pageInfo").textContent =
          S.page + "/" + Math.max(tp, 1);
        document.getElementById("prevBtn").disabled = S.page <= 1;
        document.getElementById("nextBtn").disabled = S.page >= tp;
      } else {
        document.getElementById("fragBadge").textContent = "加载失败";
        UI.toast("片段库查询失败: " + (d.error || "未知错误"), "error");
      }
    } catch (e) {
      document.getElementById("fragBadge").textContent = "网络错误";
      UI.toast("片段库请求失败: " + e.message, "error");
      console.error("loadFrags error:", e);
    }
  }

  /** 渲染常用基团网格 */
  function renderCommon() {
    document.getElementById("commonGrid").innerHTML = COMMON_FRAGS.map(
      function (f) {
        return (
          '<div class="common-chip" onclick="selFrag(\'' +
          UI.esc(f.s) +
          "','" +
          f.l +
          '\',this)" title="' +
          f.s +
          '">' +
          f.l +
          "</div>"
        );
      },
    ).join("");
  }

  /** 渲染检索到的片段网格 */
  function renderFragGrid(frags) {
    var g = document.getElementById("fragGrid");
    if (!frags || !frags.length) {
      g.innerHTML = '<div style="font-size:12px;padding:10px;">未找到</div>';
      return;
    }
    g.innerHTML = frags
      .map(function (f) {
        var smi = f.fragment_smiles;
        var label = smi.substring(0, 10) + (smi.length > 10 ? "…" : "");
        var imgUrl =
          "/api/utils/smiles_to_image?smiles=" +
          encodeURIComponent(smi) +
          "&width=120&height=60";
        return (
          '<div class="fragment-card" onclick="selFrag(\'' +
          UI.esc(smi) +
          "','" +
          UI.esc(label) +
          "',this)\">" +
          '<img style="height:50px;object-fit:contain;" loading="lazy" src="' +
          imgUrl +
          '">' +
          '<div class="frag-formula">' +
          label +
          "</div></div>"
        );
      })
      .join("");
  }

  /** 搜索输入事件 */
  function onSearch() {
    S.search = document.getElementById("fragSearch").value.trim();
    S.page = 1;
    loadFrags();
  }

  /** 标签筛选切换 */
  function toggleFilter(chip) {
    var k = chip.dataset.k;
    if (S.filters.has(k)) {
      S.filters.delete(k);
      chip.classList.remove("active");
    } else {
      S.filters.add(k);
      chip.classList.add("active");
    }
    UI.updateAccBadges();
    S.page = 1;
    loadFrags();
  }

  /** 分页 */
  function changePage(d) {
    S.page = Math.max(1, S.page + d);
    loadFrags();
  }

  /** 选中某片段 */
  function selFrag(smi, label, el) {
    document
      .querySelectorAll(".fragment-card.selected,.common-chip.selected")
      .forEach(function (c) {
        c.classList.remove("selected");
      });
    el.classList.add("selected");
    S.selectedFrag = { smi: smi, label: label };
    document.getElementById("subBtn").disabled = false;
    UI.toast("已选: " + label, "info");
  }

  return {
    loadFrags: loadFrags,
    renderCommon: renderCommon,
    renderFragGrid: renderFragGrid,
    onSearch: onSearch,
    toggleFilter: toggleFilter,
    changePage: changePage,
    selFrag: selFrag,
  };
})();
