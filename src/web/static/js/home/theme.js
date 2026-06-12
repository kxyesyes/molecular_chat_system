"use strict";

window.HomeTheme = (function () {
  function apply(theme, persist) {
    const nextTheme = HomeConfig.themes.includes(theme) ? theme : "medium";

    // 设置到 html 标签以支持全局 CSS 变量切换
    document.documentElement.setAttribute("data-theme", nextTheme);

    if (
      HomeState.elements.themeButtons &&
      HomeState.elements.themeButtons.length
    ) {
      HomeState.elements.themeButtons.forEach(function (button) {
        button.classList.toggle("active", button.dataset.theme === nextTheme);
      });
    }

    if (persist !== false) {
      localStorage.setItem(HomeConfig.storageKeys.theme, nextTheme);
    }
  }

  function init() {
    const savedTheme =
      localStorage.getItem(HomeConfig.storageKeys.theme) || "medium";

    apply(savedTheme, false);

    if (
      !HomeState.elements.themeButtons ||
      !HomeState.elements.themeButtons.length
    ) {
      return;
    }

    HomeState.elements.themeButtons.forEach(function (button) {
      button.addEventListener("click", function () {
        apply(button.dataset.theme);
      });
    });
  }

  return {
    apply: apply,
    init: init,
  };
})();
