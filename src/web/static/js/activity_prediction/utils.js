"use strict";

window.ActivityUtils = (function () {
  const DEFAULT_ACTIVITY_RANGE = {
    regression: { min: 4, lowUpper: 5, highUpper: 7, max: 9, label: "pIC50" },
    classification: {
      min: 0,
      lowUpper: 0.33,
      highUpper: 0.66,
      max: 1,
      label: "Score",
    },
  };

  function animateNumber(el, endValue, duration, isInt) {
    if (!el) return;

    const totalDuration = duration || 1200;
    const integerMode = Boolean(isInt);
    let startTimestamp = null;
    const startValue = 0;

    function step(timestamp) {
      if (!startTimestamp) startTimestamp = timestamp;
      const progress = Math.min((timestamp - startTimestamp) / totalDuration, 1);
      const easeProgress =
        progress === 1 ? 1 : 1 - Math.pow(2, -10 * progress);
      const current = easeProgress * (endValue - startValue) + startValue;

      el.textContent = integerMode
        ? Math.floor(current)
        : Number(current).toFixed(3);

      if (progress < 1) {
        window.requestAnimationFrame(step);
      }
    }

    window.requestAnimationFrame(step);
  }

  function setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
  }

  function formatScore(score) {
    return Number(score || 0).toFixed(3);
  }

  function getTagClass(label, score, success, taskType, rangeConfig) {
    if (success === false) return "tag-error";

    const normalized = String(label || "").toLowerCase();
    if (normalized.includes("high") || normalized.includes("active")) {
      return "tag-high";
    }
    if (normalized.includes("medium") || normalized.includes("moderate")) {
      return "tag-medium";
    }
    if (normalized.includes("low") || normalized.includes("inactive")) {
      return "tag-low";
    }

    if (taskType === "classification") {
      if (score >= 0.66) return "tag-high";
      if (score >= 0.33) return "tag-medium";
      return "tag-low";
    }

    const range = rangeConfig || DEFAULT_ACTIVITY_RANGE.regression;
    if (score >= range.highUpper) return "tag-high";
    if (score >= range.lowUpper) return "tag-medium";
    return "tag-low";
  }

  function finalizeHistogramBins(scores, boundaries) {
    const counts = new Array(boundaries.length - 1).fill(0);
    const labels = [];

    boundaries.forEach(function (value, index) {
      if (index === boundaries.length - 1) return;
      labels.push(
        `${boundaries[index].toFixed(1)}-${boundaries[index + 1].toFixed(1)}`,
      );
    });

    scores.forEach(function (score) {
      for (let i = 0; i < boundaries.length - 1; i += 1) {
        const left = boundaries[i];
        const right = boundaries[i + 1];
        const isLast = i === boundaries.length - 2;

        if ((score >= left && score < right) || (isLast && score === right)) {
          counts[i] += 1;
          break;
        }
      }
    });

    return { labels: labels, counts: counts };
  }

  function buildHistogramBins(scores, taskType) {
    if (!scores.length) return { labels: [], counts: [] };

    if (taskType === "classification") {
      return finalizeHistogramBins(scores, [0, 0.2, 0.4, 0.6, 0.8, 1]);
    }

    const minScore = Math.min.apply(null, scores);
    const maxScore = Math.max.apply(null, scores);
    const start = Math.floor(minScore * 2) / 2;
    const rawEnd = Math.ceil(maxScore * 2) / 2;
    const end = rawEnd > start ? rawEnd : start + 1;
    const binCount = Math.min(6, Math.max(4, Math.ceil(Math.sqrt(scores.length))));
    const step = (end - start) / binCount;
    const boundaries = Array.from({ length: binCount + 1 }, function (_, index) {
      return Number((start + step * index).toFixed(2));
    });

    boundaries[boundaries.length - 1] = Number(end.toFixed(2));
    return finalizeHistogramBins(scores, boundaries);
  }

  return {
    DEFAULT_ACTIVITY_RANGE: DEFAULT_ACTIVITY_RANGE,
    animateNumber: animateNumber,
    setText: setText,
    formatScore: formatScore,
    getTagClass: getTagClass,
    finalizeHistogramBins: finalizeHistogramBins,
    buildHistogramBins: buildHistogramBins,
  };
})();
