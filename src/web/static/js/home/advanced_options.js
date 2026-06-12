"use strict";

window.HomeAdvancedOptions = (function () {
  const advancedConfig = {
    ragCount: HomeConfig.advancedDefaults.ragCount,
    temperature: HomeConfig.advancedDefaults.temperature,
    molCount: HomeConfig.advancedDefaults.moleculeCount,
  };

  function save() {
    try {
      localStorage.setItem(
        HomeConfig.storageKeys.advancedOptions,
        JSON.stringify(advancedConfig),
      );
    } catch (error) {
      console.error("❌ 保存高级选项失败:", error);
    }
  }

  function load() {
    try {
      const saved = localStorage.getItem(HomeConfig.storageKeys.advancedOptions);
      if (!saved) return;

      const loaded = JSON.parse(saved);
      advancedConfig.ragCount =
        loaded.ragCount || HomeConfig.advancedDefaults.ragCount;
      advancedConfig.temperature =
        loaded.temperature || HomeConfig.advancedDefaults.temperature;
      advancedConfig.molCount =
        loaded.molCount || HomeConfig.advancedDefaults.moleculeCount;
    } catch (error) {
      console.error("❌ 加载高级选项失败:", error);
    }
  }

  function getConfig() {
    return { ...advancedConfig };
  }

  function init() {
    const moreBtn = document.querySelector(".index_con .right .more");
    const panel = document.getElementById("advancedOptionsPanel");
    const overlay = document.getElementById("advancedOptionsOverlay");
    const closeBtn = document.getElementById("closeAdvancedPanel");
    const resetBtn = document.getElementById("resetAdvancedOptions");

    const ragCountSlider = document.getElementById("ragCountSlider");
    const ragCountValue = document.getElementById("ragCountValue");
    const ragSliderFill = document.getElementById("ragSliderFill");

    const temperatureSlider = document.getElementById("temperatureSlider");
    const temperatureValue = document.getElementById("temperatureValue");
    const tempSliderFill = document.getElementById("tempSliderFill");

    const molCountSlider = document.getElementById("molCountSlider");
    const molCountValue = document.getElementById("molCountValue");
    const molSliderFill = document.getElementById("molSliderFill");

    if (!moreBtn || !panel || !overlay) {
      return;
    }

    load();

    function updateTrackFill(slider, fill) {
      if (!slider) return;
      const min = parseFloat(slider.min) || 0;
      const max = parseFloat(slider.max) || 100;
      const value = parseFloat(slider.value);
      const percent = ((value - min) / (max - min)) * 100;
      slider.style.background = `linear-gradient(90deg, #111111 0%, #111111 ${percent}%, #e7e7e7 ${percent}%, #e7e7e7 100%)`;
      if (fill) fill.style.width = percent + "%";
    }

    function updateSliderValues() {
      if (ragCountSlider) {
        ragCountSlider.value = advancedConfig.ragCount;
        if (ragCountValue) ragCountValue.innerHTML = advancedConfig.ragCount;
        updateTrackFill(ragCountSlider, ragSliderFill);
      }

      if (temperatureSlider) {
        temperatureSlider.value = advancedConfig.temperature;
        if (temperatureValue) {
          temperatureValue.innerHTML = advancedConfig.temperature.toFixed(1);
        }
        updateTrackFill(temperatureSlider, tempSliderFill);
      }

      if (molCountSlider) {
        molCountSlider.value = advancedConfig.molCount;
        if (molCountValue) molCountValue.innerHTML = advancedConfig.molCount;
        updateTrackFill(molCountSlider, molSliderFill);
      }
    }

    function notify(message, type) {
      if (
        window.HomeChatRenderer &&
        typeof window.HomeChatRenderer.showNotification === "function"
      ) {
        window.HomeChatRenderer.showNotification(message, type);
        return;
      }
      console.log(type || "info", message);
    }

    function closePanel() {
      panel.classList.remove("show");
      overlay.classList.remove("show");
      setTimeout(function () {
        panel.style.display = "none";
        overlay.style.display = "none";
      }, 300);
    }

    moreBtn.addEventListener("click", function () {
      panel.style.display = "block";
      overlay.style.display = "block";

      requestAnimationFrame(function () {
        panel.classList.add("show");
        overlay.classList.add("show");
      });

      updateSliderValues();
    });

    if (closeBtn) closeBtn.addEventListener("click", closePanel);
    overlay.addEventListener("click", closePanel);

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && panel.style.display === "block") {
        closePanel();
      }
    });

    if (ragCountSlider) {
      ragCountSlider.addEventListener("input", function (e) {
        const value = parseInt(e.target.value, 10);
        advancedConfig.ragCount = value;
        if (ragCountValue) ragCountValue.innerHTML = value;
        updateTrackFill(ragCountSlider, ragSliderFill);
        save();
      });
    }

    if (temperatureSlider) {
      temperatureSlider.addEventListener("input", function (e) {
        const value = parseFloat(e.target.value);
        advancedConfig.temperature = value;
        if (temperatureValue) temperatureValue.innerHTML = value.toFixed(1);
        updateTrackFill(temperatureSlider, tempSliderFill);
        save();
      });
    }

    if (molCountSlider) {
      molCountSlider.addEventListener("input", function (e) {
        const value = parseInt(e.target.value, 10);
        advancedConfig.molCount = value;
        if (molCountValue) molCountValue.innerHTML = value;
        updateTrackFill(molCountSlider, molSliderFill);
        save();
      });
    }

    if (resetBtn) {
      resetBtn.addEventListener("click", function () {
        advancedConfig.ragCount = HomeConfig.advancedDefaults.ragCount;
        advancedConfig.temperature = HomeConfig.advancedDefaults.temperature;
        advancedConfig.molCount = HomeConfig.advancedDefaults.moleculeCount;
        updateSliderValues();
        save();
        notify("✅ 已恢复默认设置", "success");
      });
    }

    updateSliderValues();
  }

  return {
    init: init,
    save: save,
    load: load,
    getConfig: getConfig,
  };
})();
