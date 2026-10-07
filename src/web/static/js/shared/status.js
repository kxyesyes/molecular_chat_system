(function (global) {
  "use strict";

  const labels = Object.freeze({
    succeeded: "评估完成",
    partial: "部分完成",
    failed: "评估失败",
    unavailable: "工具不可用",
    timeout: "计算超时",
    busy: "服务繁忙",
    invalid_input: "输入无效",
    not_calculated: "尚未运行",
    running: "执行中",
  });

  global.MedChatStatus = Object.freeze({
    labels,
    isKnown(value) {
      return typeof value === "string" && Object.prototype.hasOwnProperty.call(labels, value);
    },
    normalize(value, fallback = "failed") {
      return this.isKnown(value) ? value : fallback;
    },
    label(value, fallback = "未计算") {
      return this.isKnown(value) ? labels[value] : fallback;
    },
  });
})(window);
