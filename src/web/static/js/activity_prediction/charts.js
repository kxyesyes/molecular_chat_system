"use strict";

window.ActivityCharts = (function () {
  let metricsChart = null;
  let metricEpochs = [];
  let metricHistory = {
    trainLoss: [],
    valLoss: [],
    primary: [],
  };
  let metricDescriptor = { key: "r2", label: "R2" };

  function getMetricDescriptor(metrics) {
    const source = metrics || {};
    if (source.r2 !== undefined) return { key: "r2", label: "R2" };
    if (source.auc_pr !== undefined) return { key: "auc_pr", label: "AUC-PR" };
    if (source.accuracy !== undefined) return { key: "accuracy", label: "Accuracy" };
    return { key: "value", label: "Metric" };
  }

  function buildMetricSummary(metrics) {
    const source = metrics || {};
    if (source.r2 !== undefined) {
      return `R2=${Number(source.r2).toFixed(3)} RMSE=${Number(source.rmse).toFixed(3)}`;
    }
    if (source.auc_pr !== undefined) {
      return `AUC-PR=${Number(source.auc_pr).toFixed(3)} ACC=${Number(source.accuracy).toFixed(3)}`;
    }
    return null;
  }

  function buildMetricsChartOption() {
    const primaryLabel = metricDescriptor.label || "Metric";
    const bestEpoch = metricDescriptor.bestEpoch || 0;
    const bestValue = metricDescriptor.bestValue;
    const bestPoint =
      bestEpoch && bestValue !== undefined
        ? [
            {
              coord: [bestEpoch, Number(bestValue.toFixed(4))],
              value: `Best @ ${bestEpoch}`,
            },
          ]
        : [];

    return {
      backgroundColor: "transparent",
      color: ["#94a3b8", "#f59e0b", "#4f46e5"],
      legend: {
        type: "scroll",
        top: 0,
        left: 16,
        right: 96,
        data: ["Train Loss", "Val Loss", primaryLabel],
        itemWidth: 10,
        itemHeight: 10,
        itemGap: 18,
        pageIconColor: "#94a3b8",
        pageIconInactiveColor: "#cbd5e1",
        pageTextStyle: { color: "#94a3b8", fontSize: 10 },
        textStyle: { color: "#64748b", fontSize: 11 },
      },
      grid: { top: 58, right: 56, bottom: 24, left: 52, containLabel: true },
      tooltip: {
        trigger: "axis",
        backgroundColor: "rgba(15, 23, 42, 0.92)",
        borderWidth: 0,
        textStyle: { color: "#f8fafc" },
        axisPointer: {
          lineStyle: { color: "rgba(79, 70, 229, 0.22)", width: 1 },
        },
        valueFormatter: function (value) {
          if (value === null || value === undefined) return "--";
          return Number(value).toFixed(4);
        },
      },
      xAxis: {
        type: "category",
        data: metricEpochs,
        boundaryGap: false,
        axisLabel: { color: "#64748b", fontSize: 11, margin: 12 },
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { show: false },
      },
      yAxis: [
        {
          type: "value",
          name: "Loss",
          nameTextStyle: {
            color: "#64748b",
            fontSize: 11,
            padding: [0, 0, 8, 0],
          },
          scale: true,
          axisLabel: { color: "#64748b", fontSize: 11, margin: 12 },
          axisLine: { show: false },
          axisTick: { show: false },
          splitLine: { lineStyle: { color: "#eef2f7" } },
        },
        {
          type: "value",
          name: primaryLabel,
          nameTextStyle: {
            color: "#4338ca",
            fontSize: 11,
            padding: [0, 0, 8, 0],
          },
          scale: true,
          position: "right",
          axisLabel: { color: "#4338ca", fontSize: 11, margin: 12 },
          axisLine: { show: false },
          axisTick: { show: false },
          splitLine: { show: false },
        },
      ],
      series: [
        {
          name: "Train Loss",
          type: "line",
          yAxisIndex: 0,
          color: "#94a3b8",
          data: metricHistory.trainLoss,
          smooth: true,
          showSymbol: false,
          symbol: "none",
          itemStyle: { color: "#94a3b8" },
          lineStyle: { color: "#94a3b8", width: 2, type: "dashed" },
        },
        {
          name: "Val Loss",
          type: "line",
          yAxisIndex: 0,
          color: "#f59e0b",
          data: metricHistory.valLoss,
          smooth: true,
          showSymbol: false,
          symbol: "none",
          itemStyle: { color: "#f59e0b" },
          lineStyle: { color: "#f59e0b", width: 2.2 },
        },
        {
          name: primaryLabel,
          type: "line",
          yAxisIndex: 1,
          color: "#4f46e5",
          data: metricHistory.primary,
          smooth: true,
          showSymbol: false,
          symbol: "none",
          itemStyle: { color: "#4f46e5" },
          lineStyle: { color: "#4f46e5", width: 2.6 },
          areaStyle: {
            color: {
              type: "linear",
              x: 0,
              y: 0,
              x2: 0,
              y2: 1,
              colorStops: [
                { offset: 0, color: "rgba(79,70,229,0.15)" },
                { offset: 1, color: "rgba(79,70,229,0)" },
              ],
            },
          },
          markPoint: bestPoint.length
            ? {
                symbol: "circle",
                symbolSize: 12,
                label: {
                  show: true,
                  position: "top",
                  color: "#4338ca",
                  fontSize: 11,
                  formatter: function (params) {
                    return params.data.value;
                  },
                },
                itemStyle: {
                  color: "#4f46e5",
                  borderColor: "#ffffff",
                  borderWidth: 2,
                },
                data: bestPoint,
              }
            : undefined,
        },
      ],
    };
  }

  function resetMetricsChartState(taskType) {
    metricEpochs = [];
    metricHistory = { trainLoss: [], valLoss: [], primary: [] };
    metricDescriptor = {
      key: taskType === "classification" ? "auc_pr" : "r2",
      label: taskType === "classification" ? "AUC-PR" : "R2",
    };

    if (metricsChart) {
      metricsChart.setOption(buildMetricsChartOption(), true);
    }
  }

  function initMetricsChart() {
    const dom = document.getElementById("metricsChart");
    if (!dom) return;

    if (!metricsChart) {
      metricsChart = echarts.init(dom);
      window.addEventListener("resize", function () {
        if (metricsChart) metricsChart.resize();
      });
    }

    metricsChart.setOption(buildMetricsChartOption(), true);
    metricsChart.resize();
  }

  function updateMetricsChart(epoch, metrics, bestEpoch, bestMetrics) {
    const source = metrics || {};
    const descriptor = getMetricDescriptor(source);
    metricDescriptor = {
      key: descriptor.key,
      label: descriptor.label,
      bestEpoch: bestEpoch || 0,
      bestValue:
        bestMetrics && bestMetrics[descriptor.key] !== undefined
          ? bestMetrics[descriptor.key]
          : undefined,
    };

    metricEpochs.push(epoch);
    metricHistory.trainLoss.push(
      source.train_loss !== undefined ? Number(source.train_loss.toFixed(4)) : null,
    );
    metricHistory.valLoss.push(
      source.val_loss !== undefined ? Number(source.val_loss.toFixed(4)) : null,
    );
    metricHistory.primary.push(
      source[descriptor.key] !== undefined
        ? Number(source[descriptor.key].toFixed(4))
        : null,
    );

    if (metricsChart) {
      metricsChart.setOption(buildMetricsChartOption(), true);
    }
  }

  function renderHistogram(dataList) {
    const chartDom = document.getElementById("batchHistogram");
    if (!chartDom) return;

    const scores = dataList
      .filter(function (item) {
        return item && item.success !== false;
      })
      .map(function (item) {
        return Number(item.activity_score || 0);
      })
      .filter(function (value) {
        return Number.isFinite(value);
      });

    if (!scores.length) {
      chartDom.style.display = "none";
      return;
    }

    chartDom.style.display = "block";
    const chart = echarts.getInstanceByDom(chartDom) || echarts.init(chartDom);
    chart.clear();

    if (!chartDom.dataset.resizeBound) {
      window.addEventListener("resize", function () {
        const instance = echarts.getInstanceByDom(chartDom);
        if (instance) instance.resize();
      });
      chartDom.dataset.resizeBound = "true";
    }

    const taskType = ActivityModels.getTaskType();
    const histogram = ActivityUtils.buildHistogramBins(scores, taskType);
    const taskLabel = taskType === "classification" ? "Score" : "Activity";

    chart.setOption({
      title: {
        text: `${taskLabel} Distribution`,
        left: "center",
        textStyle: { color: "#475569", fontSize: 14, fontWeight: 700 },
      },
      tooltip: { trigger: "axis" },
      grid: { top: 48, right: 20, bottom: 36, left: 48 },
      xAxis: {
        type: "category",
        data: histogram.labels,
        axisLabel: { color: "#64748b", fontSize: 11 },
        axisLine: { lineStyle: { color: "#cbd5e1" } },
      },
      yAxis: {
        type: "value",
        name: "Count",
        nameTextStyle: { color: "#64748b" },
        axisLabel: { color: "#64748b" },
        splitLine: { lineStyle: { color: "#e2e8f0" } },
      },
      series: [
        {
          name: "Count",
          data: histogram.counts,
          type: "bar",
          barWidth: "58%",
          itemStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: "#4f46e5" },
              { offset: 1, color: "#60a5fa" },
            ]),
            borderRadius: [8, 8, 0, 0],
          },
        },
      ],
    });
  }

  return {
    getMetricDescriptor: getMetricDescriptor,
    buildMetricSummary: buildMetricSummary,
    initMetricsChart: initMetricsChart,
    updateMetricsChart: updateMetricsChart,
    renderHistogram: renderHistogram,
    resetMetricsChartState: resetMetricsChartState,
  };
})();
