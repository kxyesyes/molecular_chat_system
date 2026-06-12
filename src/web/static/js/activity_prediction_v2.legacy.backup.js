function animateNumber(el, endValue, duration = 1200, isInt = false) {
  if (!el) return;
  let startTimestamp = null;
  const startValue = 0;
  const step = (timestamp) => {
    if (!startTimestamp) startTimestamp = timestamp;
    const progress = Math.min((timestamp - startTimestamp) / duration, 1);
    // Ease out expo
    const easeProgress = progress === 1 ? 1 : 1 - Math.pow(2, -10 * progress);
    const current = easeProgress * (endValue - startValue) + startValue;
    el.textContent = isInt ? Math.floor(current) : current.toFixed(3);
    if (progress < 1) {
      window.requestAnimationFrame(step);
    }
  };
  window.requestAnimationFrame(step);
}

function fillExample() {
  const input = document.getElementById("smilesInput");
  input.value = "CC(=O)OC1=CC=CC=C1C(=O)O";
  input.focus();
}

function switchTab(mode, tabEl) {
  document
    .querySelectorAll(".tab")
    .forEach((t) => t.classList.remove("active"));
  if (tabEl) tabEl.classList.add("active");

  const map = ["single", "batch", "train"];
  map.forEach((m) => {
    const panel = document.getElementById(`${m}-panel`);
    if (panel) panel.style.display = m === mode ? "block" : "none";
  });

  const results = document.getElementById("results");
  if (results) results.classList.remove("show");
}

function setStatus(text) {
  const el = document.getElementById("statusStat");
  if (el) el.textContent = text;
}

function bindFileDisplay(inputId, infoId, prefix) {
  const input = document.getElementById(inputId);
  const info = document.getElementById(infoId);
  if (!input || !info) return;
  input.addEventListener("change", () => {
    if (!input.files || !input.files[0]) {
      info.textContent = "未选择文件";
      return;
    }
    const f = input.files[0];
    info.textContent = `${prefix}: ${f.name} (${(f.size / 1024).toFixed(1)} KB)`;
  });
}

bindFileDisplay("batchFile", "fileInfo", "批量文件");
// trainFile is handled by preflight listener below

const DEFAULT_ACTIVITY_RANGE = {
  regression: { min: 4, lowUpper: 5, highUpper: 7, max: 9, label: "pIC50" },
  classification: { min: 0, lowUpper: 0.33, highUpper: 0.66, max: 1, label: "Score" },
};

function getSelectedModelMeta() {
  const selector = document.getElementById("modelSelector");
  if (!selector) return null;
  return _allModels.find((model) => model.weights_file === selector.value) || null;
}

function getCurrentTaskType() {
  return getSelectedModelMeta()?.task_type || "regression";
}

function getRangeConfig() {
  return DEFAULT_ACTIVITY_RANGE[getCurrentTaskType()] || DEFAULT_ACTIVITY_RANGE.regression;
}

function getTagClass(label, score, success = true) {
  if (!success) return "tag-error";
  const normalized = String(label || "").toLowerCase();

  if (normalized.includes("high") || normalized.includes("active")) return "tag-high";
  if (normalized.includes("medium") || normalized.includes("moderate")) return "tag-medium";
  if (normalized.includes("low") || normalized.includes("inactive")) return "tag-low";

  if (getCurrentTaskType() === "classification") {
    if (score >= 0.66) return "tag-high";
    if (score >= 0.33) return "tag-medium";
    return "tag-low";
  }

  const range = getRangeConfig();
  if (score >= range.highUpper) return "tag-high";
  if (score >= range.lowUpper) return "tag-medium";
  return "tag-low";
}

function updateTaskTypeStat() {
  const el = document.getElementById("taskTypeStat");
  if (!el) return;
  el.textContent = getCurrentTaskType() === "classification" ? "分类" : "回归";
}

function resetResultViews() {
  const singleSummary = document.getElementById("singleSummary");
  const batchHistEl = document.getElementById("batchHistogram");

  if (singleSummary) singleSummary.style.display = "none";
  if (batchHistEl) batchHistEl.style.display = "none";

  if (batchHistEl) {
    const chart = echarts.getInstanceByDom(batchHistEl);
    if (chart) chart.clear();
  }
}

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function formatScore(score) {
  return Number(score || 0).toFixed(3);
}

function finalizeHistogramBins(scores, boundaries) {
  const counts = new Array(boundaries.length - 1).fill(0);
  const labels = [];

  boundaries.forEach((value, index) => {
    if (index === boundaries.length - 1) return;
    labels.push(`${boundaries[index].toFixed(1)}-${boundaries[index + 1].toFixed(1)}`);
  });

  scores.forEach((score) => {
    for (let i = 0; i < boundaries.length - 1; i++) {
      const left = boundaries[i];
      const right = boundaries[i + 1];
      const isLast = i === boundaries.length - 2;
      if ((score >= left && score < right) || (isLast && score === right)) {
        counts[i] += 1;
        break;
      }
    }
  });

  return { labels, counts };
}

function buildHistogramBins(scores) {
  if (!scores.length) return { labels: [], counts: [] };

  if (getCurrentTaskType() === "classification") {
    return finalizeHistogramBins(scores, [0, 0.2, 0.4, 0.6, 0.8, 1]);
  }

  const minScore = Math.min(...scores);
  const maxScore = Math.max(...scores);
  const start = Math.floor(minScore * 2) / 2;
  const rawEnd = Math.ceil(maxScore * 2) / 2;
  const end = rawEnd > start ? rawEnd : start + 1;
  const binCount = Math.min(6, Math.max(4, Math.ceil(Math.sqrt(scores.length))));
  const step = (end - start) / binCount;
  const boundaries = Array.from({ length: binCount + 1 }, (_, index) =>
    Number((start + step * index).toFixed(2))
  );

  boundaries[boundaries.length - 1] = Number(end.toFixed(2));
  return finalizeHistogramBins(scores, boundaries);
}

function renderSingleSummary(item) {
  const score = Number(item.activity_score || 0);
  const conf = Number(item.confidence || 0);
  const label = item.class || "Unknown";
  const taskType = getCurrentTaskType();
  const model = getSelectedModelMeta();
  const range = getRangeConfig();
  const markerPct = Math.max(
    0,
    Math.min(100, ((score - range.min) / (range.max - range.min || 1)) * 100)
  );

  setText("singleConfidenceValue", `${(conf * 100).toFixed(1)}%`);
  setText("singleClassValue", label);
  setText("singleTaskTypeValue", taskType === "classification" ? "分类" : "回归");
  setText(
    "singleModelMeta",
    `${model?.target || range.label} / ${model?.name || "当前模型"}`
  );
  setText(
    "activityBandSubtitle",
    taskType === "classification"
      ? "标记线展示当前预测置信度在概率区间中的位置。"
      : "标记线展示当前预测活性值在低、中、高区间中的位置。"
  );
  setText("activityBandRange", `${range.min.toFixed(1)} - ${range.max.toFixed(1)}`);
  setText("activityBandMarkerLabel", formatScore(score));
  setText("bandTickStart", range.min.toFixed(1));
  setText("bandTickLow", range.lowUpper.toFixed(1));
  setText("bandTickHigh", range.highUpper.toFixed(1));
  setText("bandTickEnd", range.max.toFixed(1));

  const scoreEl = document.getElementById("singleScoreValue");
  if (scoreEl) {
    scoreEl.textContent = "0.000";
    animateNumber(scoreEl, score);
  }

  const classTag = document.getElementById("singleClassTag");
  if (classTag) {
    classTag.className = `tag ${getTagClass(label, score)}`;
    classTag.textContent = label;
  }

  const marker = document.getElementById("activityBandMarker");
  if (marker) marker.style.left = `${markerPct}%`;

  const summary = document.getElementById("singleSummary");
  if (summary) summary.style.display = "block";
}

async function handlePredict(url, formData, btnId) {
  const btn = document.getElementById(btnId);
  const loading = document.getElementById("loading");
  const results = document.getElementById("results");
  const tbody = document.getElementById("resultsBody");
  const isSingleRequest = url.includes("/api/activity/predict") && !url.includes("batch");

  btn.disabled = true;
  loading.classList.add("show");
  results.classList.remove("show");
  tbody.innerHTML = "";
  resetResultViews();
  setStatus("Predicting");

  try {
    const res = await fetch(url, { method: "POST", body: formData });
    const data = await res.json();

    if (!data.success || !Array.isArray(data.results)) {
      throw new Error(data.error || "预测失败");
    }

    const successfulResults = data.results.filter((item) => item && item.success !== false);

    data.results.forEach((item, index) => {
      const score = Number(item.activity_score || 0);
      const conf = Number(item.confidence || 0);
      const cls = item.class || (item.success === false ? "Failed" : "Unknown");

      const rowId = `row-score-${index}`;
      const row = document.createElement("tr");
      row.className = "stagger-item";
      row.style.animationDelay = `${index * 0.05}s`;
      if (item.success === false) {
        row.innerHTML = `
          <td style="max-width: 360px; word-break: break-all; font-family: Consolas, monospace;">${item.smiles || "-"}</td>
          <td style="font-weight:700; min-width: 80px; color: #b91c1c;">${item.error || "Error"}</td>
          <td><span class="tag tag-error">Failed</span></td>
          <td>--</td>
        `;
      } else {
        row.innerHTML = `
          <td style="max-width: 360px; word-break: break-all; font-family: Consolas, monospace;">${item.smiles || "-"}</td>
          <td id="${rowId}" style="font-weight:700; min-width: 80px;">0.000</td>
          <td><span class="tag ${getTagClass(cls, score)}">${cls}</span></td>
          <td>${(conf * 100).toFixed(1)}%</td>
        `;
      }
      tbody.appendChild(row);
      
      if (item.success !== false) {
        animateNumber(document.getElementById(rowId), score);
      }
    });

    if (!successfulResults.length) {
      throw new Error("No valid prediction results returned.");
    }

    if (isSingleRequest && successfulResults.length === 1) {
        renderSingleSummary(successfulResults[0]);
    } else {
        renderHistogram(successfulResults);
    }

    results.classList.add("show");
    setStatus("Done");
  } catch (e) {
    alert("请求失败: " + e.message);
    setStatus("Error");
  } finally {
    btn.disabled = false;
    loading.classList.remove("show");
  }
}

function _renderHistogramLegacy(dataList) {
    const chartDom = document.getElementById('batchHistogram');
    chartDom.style.display = 'block';
    const myChart = echarts.init(chartDom);

    const scores = dataList.map(item => Number(item.activity_score || 0));
    
    // Define buckets
    const bins = [0, 0.2, 0.4, 0.6, 0.8, 1.0];
    const counts = new Array(bins.length - 1).fill(0);
    
    scores.forEach(s => {
        for (let i = 0; i < bins.length - 1; i++) {
            if (s >= bins[i] && s < bins[i+1]) {
                counts[i]++;
                break;
            }
            if (s === 1.0 && i === bins.length - 2) {
                counts[i]++;
            }
        }
    });

    const option = {
        title: { text: '活性分布直方图', left: 'center', textStyle: { color: '#8fa0d4', fontSize: 14 } },
        tooltip: { trigger: 'axis' },
        xAxis: {
            type: 'category',
            data: ['0.0-0.2', '0.2-0.4', '0.4-0.6', '0.6-0.8', '0.8-1.0'],
            axisLabel: { color: '#8fa0d4' },
            axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } }
        },
        yAxis: {
            type: 'value',
            name: '分子数量',
            axisLabel: { color: '#8fa0d4' },
            splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } }
        },
        series: [{
            name: '数量',
            data: counts,
            type: 'bar',
            barWidth: '60%',
            itemStyle: {
                color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                    { offset: 0, color: '#6d4bff' },
                    { offset: 1, color: '#00d4ff' }
                ]),
                borderRadius: [4, 4, 0, 0]
            },
            emphasis: { itemStyle: { filter: 'brightness(1.2)' } }
        }]
    };

    myChart.setOption(option);
    window.addEventListener('resize', () => myChart.resize());
}

function _renderSingleGaugeLegacy(score) {
    const chartDom = document.getElementById('singleGauge');
    chartDom.style.display = 'block';
    const myChart = echarts.init(chartDom);
    
    const option = {
        series: [{
            type: 'gauge',
            startAngle: 180,
            endAngle: 0,
            min: 0,
            max: 1,
            splitNumber: 10,
            itemStyle: {
                color: {
                    type: 'linear',
                    x: 0, y: 0, x2: 1, y2: 0,
                    colorStops: [
                        { offset: 0, color: '#ef4444' }, // Inactive
                        { offset: 0.5, color: '#f59e0b' }, // Moderate
                        { offset: 1, color: '#22c55e' }   // Active
                    ]
                }
            },
            progress: {
                show: true,
                width: 14
            },
            pointer: {
                show: true,
                length: '65%',
                width: 6,
                itemStyle: { color: '#fff' }
            },
            axisLine: { lineStyle: { width: 14, color: [[1, 'rgba(255,255,255,0.08)']] } },
            axisTick: { show: false },
            splitLine: { show: false },
            axisLabel: { show: false },
            anchor: { show: false },
            title: { show: true, offsetCenter: [0, '25%'], color: '#8fa0d4', fontSize: 14 },
            detail: {
                valueAnimation: true,
                offsetCenter: [0, '-10%'],
                fontSize: 40,
                fontWeight: 'bold',
                color: '#fff',
                formatter: function (value) {
                    return value.toFixed(3);
                }
            },
            data: [{ value: score, name: '活性评分 (Activity Score)' }]
        }]
    };

    myChart.setOption(option);
    window.addEventListener('resize', () => myChart.resize());
}

function renderHistogram(dataList) {
    const chartDom = document.getElementById('batchHistogram');
    if (!chartDom) return;

    const scores = dataList
        .filter((item) => item && item.success !== false)
        .map((item) => Number(item.activity_score || 0))
        .filter((value) => Number.isFinite(value));

    if (!scores.length) {
        chartDom.style.display = 'none';
        return;
    }

    chartDom.style.display = 'block';
    const chart = echarts.getInstanceByDom(chartDom) || echarts.init(chartDom);
    chart.clear();
    if (!chartDom.dataset.resizeBound) {
        window.addEventListener('resize', () => {
            const instance = echarts.getInstanceByDom(chartDom);
            if (instance) instance.resize();
        });
        chartDom.dataset.resizeBound = 'true';
    }

    const { labels, counts } = buildHistogramBins(scores);
    const taskLabel = getCurrentTaskType() === "classification" ? "Score" : "Activity";

    chart.setOption({
        title: {
            text: `${taskLabel} Distribution`,
            left: 'center',
            textStyle: { color: '#475569', fontSize: 14, fontWeight: 700 }
        },
        tooltip: { trigger: 'axis' },
        grid: { top: 48, right: 20, bottom: 36, left: 48 },
        xAxis: {
            type: 'category',
            data: labels,
            axisLabel: { color: '#64748b', fontSize: 11 },
            axisLine: { lineStyle: { color: '#cbd5e1' } }
        },
        yAxis: {
            type: 'value',
            name: 'Count',
            nameTextStyle: { color: '#64748b' },
            axisLabel: { color: '#64748b' },
            splitLine: { lineStyle: { color: '#e2e8f0' } }
        },
        series: [{
            name: 'Count',
            data: counts,
            type: 'bar',
            barWidth: '58%',
            itemStyle: {
                color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                    { offset: 0, color: '#4f46e5' },
                    { offset: 1, color: '#60a5fa' }
                ]),
                borderRadius: [8, 8, 0, 0]
            }
        }]
    });
}


// ─────────────────────────────────────────
// Preflight State
// ─────────────────────────────────────────
let _preflightData = null; // { headers, rows }

document.getElementById('trainFile').addEventListener('change', function () {
  const file = this.files?.[0];
  if (!file) return;
  document.getElementById('trainFileInfo').textContent = `${file.name}  (${(file.size/1024).toFixed(1)} KB)`;
  
  const reader = new FileReader();
  reader.onload = (e) => {
    const text = e.target.result;
    const lines = text.trim().split('\n').filter(Boolean);
    if (lines.length < 2) {
      setTrainHint('⚠️ 文件行数不足，请检查格式。', 'warn');
      return;
    }
    const sep = lines[0].includes('\t') ? '\t' : ',';
    const headers = lines[0].split(sep).map(h => h.trim().replace(/"/g, ''));
    const rows = lines.slice(1, 6).map(l => l.split(sep).map(c => c.trim().replace(/"/g, '')));
    
    _preflightData = { headers, rows, sep, total: lines.length - 1 };
    openPreflight();
  };
  reader.readAsText(file);
});

function setTrainHint(msg, type = 'info') {
  const el = document.getElementById('trainHint');
  if (!el) return;
  const colors = { info: 'var(--muted)', warn: '#f59e0b', ok: 'var(--success)' };
  el.textContent = msg;
  el.style.color = colors[type] || 'var(--muted)';
}

// ─────────────────────────────────────────
// Preflight Modal
// ─────────────────────────────────────────
function openPreflight() {
  if (!_preflightData) return;
  const { headers, rows, total } = _preflightData;
  const smilesGuess = document.getElementById('smilesColumn').value.trim().toLowerCase();
  const labelGuess  = document.getElementById('targetColumn').value.trim().toLowerCase();

  // Meta info
  document.getElementById('preflight-meta').innerHTML = `
    <div class="preflight-meta-item"><span>行数</span>${total}</div>
    <div class="preflight-meta-item"><span>列数</span>${headers.length}</div>
    <div class="preflight-meta-item"><span>分隔符</span>${_preflightData.sep === '\t' ? 'Tab(\\t)' : 'Comma(,)'}</div>
  `;

  // Column list
  const colsHtml = headers.map(h => {
    const hl = h.toLowerCase();
    let badgeClass = 'other', badgeText = 'COL';
    if (hl === smilesGuess || hl === 'smiles' || hl === 'canonical_smiles') {
      badgeClass = 'smiles'; badgeText = 'SMILES';
    } else if (hl === labelGuess || hl === 'pic50' || hl === 'label' || hl === 'activity') {
      badgeClass = 'label'; badgeText = 'LABEL';
    }
    const cls = (badgeClass !== 'other') ? 'preflight-col-item highlight' : 'preflight-col-item';
    return `<div class="${cls}"><span class="col-badge ${badgeClass}">${badgeText}</span>${h}</div>`;
  }).join('');
  document.getElementById('preflight-cols').innerHTML = colsHtml;

  // Preview table
  const thHtml = headers.map(h => `<th>${h}</th>`).join('');
  const tbodyHtml = rows.map(r => `<tr>${r.map(c => `<td>${c}</td>`).join('')}</tr>`).join('');
  document.getElementById('preflight-preview').innerHTML =
    `<table class="preflight-table"><thead><tr>${thHtml}</tr></thead><tbody>${tbodyHtml}</tbody></table>`;

  const el = document.getElementById('preflight-modal');
  el.style.display = 'flex';
}

function closePreflight() {
  const el = document.getElementById('preflight-modal');
  el.style.display = 'none';
}

function confirmPreflight() {
  // Auto-fill detected column names into form fields
  if (_preflightData) {
    const { headers } = _preflightData;
    const smilesEl = document.getElementById('smilesColumn');
    const labelEl  = document.getElementById('targetColumn');
    const smilesMatch = headers.find(h => ['smiles','canonical_smiles','molecule'].includes(h.toLowerCase()));
    const labelMatch  = headers.find(h => ['pic50','label','activity','target','value'].includes(h.toLowerCase()));
    if (smilesMatch && !smilesEl.value) smilesEl.value = smilesMatch;
    if (labelMatch  && !labelEl.value)  labelEl.value  = labelMatch;
    setTrainHint(`✅ 解析成功：${_preflightData.total} 条分子, ${_preflightData.headers.length} 列`, 'ok');
  }
  closePreflight();
}

// ─────────────────────────────────────────
// Launch Summary Modal
// ─────────────────────────────────────────
function showLaunchModal() {
  const file = document.getElementById('trainFile').files?.[0];
  if (!file) { alert('请先上传训练数据集。'); return; }
  const target = document.getElementById('targetColumn').value.trim();
  if (!target) { alert('请填写目标列名。'); return; }

  const task      = document.getElementById('trainTaskType').value;
  const epochs    = document.getElementById('epochs').value;
  const lr        = document.getElementById('learningRate').value;
  const bs        = document.getElementById('batchSize').value;
  const dr        = document.getElementById('dropout').value;
  const split     = document.getElementById('splitStrategy').value;
  
  // Advanced parameters
  const layers    = document.getElementById('trainLayers').value;
  const hidden    = document.getElementById('trainHidden').value;
  const decay     = document.getElementById('trainWeightDecay').value;
  const patience  = document.getElementById('trainPatience').value;
  const loss      = document.getElementById('trainLoss').value;
  const scheduler = document.getElementById('trainScheduler').value;

  const noLabel = !target;
  const warnHtml = noLabel
    ? `<div class="summary-warning">⚠️ 目标列名未填写，训练将无法识别标签列。</div>`
    : '';

  document.getElementById('launch-summary').innerHTML = `
    <div class="summary-grid">
      <div class="summary-item"><div class="summary-label">数据文件</div><div class="summary-value">${file.name}</div></div>
      <div class="summary-item"><div class="summary-label">目标列 (Label)</div><div class="summary-value">${target}</div></div>
      <div class="summary-item"><div class="summary-label">任务类型</div><div class="summary-value">${task === 'regression' ? '回归 Regression' : '分类 Classification'}</div></div>
      <div class="summary-item"><div class="summary-label">划分策略</div><div class="summary-value">${split}</div></div>
      <div class="summary-item"><div class="summary-label">Epochs</div><div class="summary-value">${epochs}</div></div>
      <div class="summary-item"><div class="summary-label">Learning Rate</div><div class="summary-value">${lr}</div></div>
      <div class="summary-item"><div class="summary-label">Batch Size</div><div class="summary-value">${bs}</div></div>
      <div class="summary-item"><div class="summary-label">Dropout</div><div class="summary-value">${dr}</div></div>
      <div class="summary-item"><div class="summary-label">Layers</div><div class="summary-value">${layers}</div></div>
      <div class="summary-item"><div class="summary-label">Hidden Size</div><div class="summary-value">${hidden}</div></div>
      <div class="summary-item"><div class="summary-label">Weight Decay</div><div class="summary-value">${decay}</div></div>
      <div class="summary-item"><div class="summary-label">Patience</div><div class="summary-value">${patience}</div></div>
      <div class="summary-item"><div class="summary-label">Loss Fn</div><div class="summary-value">${loss}</div></div>
      <div class="summary-item"><div class="summary-label">Scheduler</div><div class="summary-value">${scheduler}</div></div>
    </div>
    ${warnHtml}
  `;
  document.getElementById('launch-modal').style.display = 'flex';
}

function closeLaunchModal() {
  document.getElementById('launch-modal').style.display = 'none';
}

function confirmLaunch() {
  closeLaunchModal();
  startTraining();
}

// ─────────────────────────────────────────
// Bottom Drawer
// ─────────────────────────────────────────
let _metricsChart = null;
let _metricEpochs = [];
let _metricHistory = {
  trainLoss: [],
  valLoss: [],
  primary: [],
};
let _metricDescriptor = { key: "r2", label: "R²" };
let _metricValues = _metricHistory.primary;

function openDrawer() {
  const dock   = document.getElementById('train-dock');
  const drawer = document.getElementById('train-drawer');
  dock.style.display = 'none';
  drawer.style.display = 'flex';
  requestAnimationFrame(() => {
    drawer.style.transform = 'translateY(0)';
  });
  initMetricsChart();
}

function minimizeDrawer() {
  const drawer = document.getElementById('train-drawer');
  const dock   = document.getElementById('train-dock');
  drawer.style.transform = 'translateY(100%)';
  setTimeout(() => { drawer.style.display = 'none'; }, 400);
  dock.style.display = 'flex';
}

function closeDrawer() {
  const drawer = document.getElementById('train-drawer');
  const dock   = document.getElementById('train-dock');
  drawer.style.transform = 'translateY(100%)';
  setTimeout(() => { drawer.style.display = 'none'; drawer.style.transform = ''; }, 400);
  dock.style.display = 'none';
}

function toggleDrawerFull() {
  document.getElementById('train-drawer').classList.toggle('fullscreen');
  if (_metricsChart) setTimeout(() => _metricsChart.resize(), 420);
}

function getMetricDescriptor(metrics = {}) {
  if (metrics.r2 !== undefined) return { key: "r2", label: "R²" };
  if (metrics.auc_pr !== undefined) return { key: "auc_pr", label: "AUC-PR" };
  if (metrics.accuracy !== undefined) return { key: "accuracy", label: "Accuracy" };
  return { key: "value", label: "Metric" };
}

function buildMetricsChartOption() {
  const bestEpoch = _metricDescriptor.bestEpoch || 0;
  const bestValue = _metricDescriptor.bestValue;
  const bestPoint =
    bestEpoch && bestValue !== undefined
      ? [{ coord: [bestEpoch, Number(bestValue.toFixed(4))], value: `Best @ ${bestEpoch}` }]
      : [];

  return {
    backgroundColor: 'transparent',
    legend: {
      top: 0,
      right: 8,
      itemWidth: 12,
      itemHeight: 12,
      textStyle: { color: '#64748b', fontSize: 11 }
    },
    grid: { top: 34, right: 18, bottom: 20, left: 46, containLabel: true },
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(15, 23, 42, 0.92)',
      borderWidth: 0,
      textStyle: { color: '#f8fafc' },
      axisPointer: { lineStyle: { color: 'rgba(79, 70, 229, 0.22)', width: 1 } }
    },
    xAxis: {
      type: 'category',
      data: _metricEpochs,
      boundaryGap: false,
      axisLabel: { color: '#64748b', fontSize: 11, margin: 12 },
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: { show: false }
    },
    yAxis: {
      type: 'value',
      scale: true,
      axisLabel: { color: '#64748b', fontSize: 11, margin: 12 },
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: { lineStyle: { color: '#eef2f7' } }
    },
    series: [
      {
        name: 'Train Loss',
        type: 'line',
        data: _metricHistory.trainLoss,
        smooth: true,
        showSymbol: false,
        symbol: 'none',
        lineStyle: { color: '#94a3b8', width: 2 }
      },
      {
        name: 'Val Loss',
        type: 'line',
        data: _metricHistory.valLoss,
        smooth: true,
        showSymbol: false,
        symbol: 'none',
        lineStyle: { color: '#f59e0b', width: 2.2 }
      },
      {
        name: _metricDescriptor.label,
        type: 'line',
        data: _metricHistory.primary,
        smooth: true,
        showSymbol: false,
        symbol: 'none',
        lineStyle: { color: '#4f46e5', width: 2.6 },
        areaStyle: {
          color: {
            type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(79,70,229,0.15)' },
              { offset: 1, color: 'rgba(79,70,229,0)' }
            ]
          }
        },
        markPoint: bestPoint.length ? {
          symbol: 'circle',
          symbolSize: 12,
          label: {
            show: true,
            position: 'top',
            color: '#4338ca',
            fontSize: 11,
            formatter: ({ data }) => data.value
          },
          itemStyle: { color: '#4f46e5', borderColor: '#ffffff', borderWidth: 2 },
          data: bestPoint
        } : undefined
      }
    ]
  };
}

function getMetricDescriptorV2(metrics = {}) {
  if (metrics.r2 !== undefined) return { key: "r2", label: "R2" };
  if (metrics.auc_pr !== undefined) return { key: "auc_pr", label: "AUC-PR" };
  if (metrics.accuracy !== undefined) return { key: "accuracy", label: "Accuracy" };
  return { key: "value", label: "Metric" };
}

function buildMetricsChartOptionV2() {
  const primaryLabel = _metricDescriptor.label || "Metric";
  const bestEpoch = _metricDescriptor.bestEpoch || 0;
  const bestValue = _metricDescriptor.bestValue;
  const bestPoint =
    bestEpoch && bestValue !== undefined
      ? [{ coord: [bestEpoch, Number(bestValue.toFixed(4))], value: `Best @ ${bestEpoch}` }]
      : [];

  return {
    backgroundColor: 'transparent',
    color: ['#94a3b8', '#f59e0b', '#4f46e5'],
    legend: {
      type: 'scroll',
      top: 0,
      left: 16,
      right: 96,
      data: ['Train Loss', 'Val Loss', primaryLabel],
      itemWidth: 10,
      itemHeight: 10,
      itemGap: 18,
      pageIconColor: '#94a3b8',
      pageIconInactiveColor: '#cbd5e1',
      pageTextStyle: { color: '#94a3b8', fontSize: 10 },
      textStyle: { color: '#64748b', fontSize: 11 }
    },
    grid: { top: 58, right: 56, bottom: 24, left: 52, containLabel: true },
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(15, 23, 42, 0.92)',
      borderWidth: 0,
      textStyle: { color: '#f8fafc' },
      axisPointer: { lineStyle: { color: 'rgba(79, 70, 229, 0.22)', width: 1 } },
      valueFormatter: (value) => value === null || value === undefined ? '--' : Number(value).toFixed(4)
    },
    xAxis: {
      type: 'category',
      data: _metricEpochs,
      boundaryGap: false,
      axisLabel: { color: '#64748b', fontSize: 11, margin: 12 },
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: { show: false }
    },
    yAxis: [
      {
        type: 'value',
        name: 'Loss',
        nameTextStyle: { color: '#64748b', fontSize: 11, padding: [0, 0, 8, 0] },
        scale: true,
        axisLabel: { color: '#64748b', fontSize: 11, margin: 12 },
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { lineStyle: { color: '#eef2f7' } }
      },
      {
        type: 'value',
        name: primaryLabel,
        nameTextStyle: { color: '#4338ca', fontSize: 11, padding: [0, 0, 8, 0] },
        scale: true,
        position: 'right',
        axisLabel: { color: '#4338ca', fontSize: 11, margin: 12 },
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { show: false }
      }
    ],
    series: [
      {
        name: 'Train Loss',
        type: 'line',
        yAxisIndex: 0,
        color: '#94a3b8',
        data: _metricHistory.trainLoss,
        smooth: true,
        showSymbol: false,
        symbol: 'none',
        itemStyle: { color: '#94a3b8' },
        lineStyle: { color: '#94a3b8', width: 2, type: 'dashed' }
      },
      {
        name: 'Val Loss',
        type: 'line',
        yAxisIndex: 0,
        color: '#f59e0b',
        data: _metricHistory.valLoss,
        smooth: true,
        showSymbol: false,
        symbol: 'none',
        itemStyle: { color: '#f59e0b' },
        lineStyle: { color: '#f59e0b', width: 2.2 }
      },
      {
        name: primaryLabel,
        type: 'line',
        yAxisIndex: 1,
        color: '#4f46e5',
        data: _metricHistory.primary,
        smooth: true,
        showSymbol: false,
        symbol: 'none',
        itemStyle: { color: '#4f46e5' },
        lineStyle: { color: '#4f46e5', width: 2.6 },
        areaStyle: {
          color: {
            type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(79,70,229,0.15)' },
              { offset: 1, color: 'rgba(79,70,229,0)' }
            ]
          }
        },
        markPoint: bestPoint.length ? {
          symbol: 'circle',
          symbolSize: 12,
          label: {
            show: true,
            position: 'top',
            color: '#4338ca',
            fontSize: 11,
            formatter: ({ data }) => data.value
          },
          itemStyle: { color: '#4f46e5', borderColor: '#ffffff', borderWidth: 2 },
          data: bestPoint
        } : undefined
      }
    ]
  };
}

function initMetricsChart() {
  const dom = document.getElementById('metricsChart');
  if (!dom) return;
  if (_metricsChart) {
    _metricsChart.setOption(buildMetricsChartOptionV2(), true);
    _metricsChart.resize();
    return;
  }
  _metricsChart = echarts.init(dom);
  _metricsChart.setOption({
    backgroundColor: 'transparent',
    grid: { top: 26, right: 18, bottom: 20, left: 46, containLabel: true },
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(15, 23, 42, 0.92)',
      borderWidth: 0,
      textStyle: { color: '#f8fafc' },
      axisPointer: { lineStyle: { color: 'rgba(79, 70, 229, 0.22)', width: 1 } }
    },
    xAxis: {
      type: 'category',
      data: _metricEpochs,
      boundaryGap: false,
      axisLabel: { color: '#64748b', fontSize: 11, margin: 12 },
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: { show: false }
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: '#64748b', fontSize: 11, margin: 12 },
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: { lineStyle: { color: '#eef2f7' } }
    },
    series: [{
      name: '指标', type: 'line', data: _metricValues, smooth: true,
      symbol: 'none', showSymbol: false, lineStyle: { color: '#4f46e5', width: 2.5 },
      areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: 'rgba(79,70,229,0.15)' }, { offset: 1, color: 'rgba(79,70,229,0)' }] } }
    }]
  });
  _metricsChart.setOption(buildMetricsChartOptionV2(), true);
  window.addEventListener('resize', () => _metricsChart?.resize());
}

function updateMetricsChart(epoch, metrics = {}, bestEpoch = 0, bestMetrics = {}) {
  const descriptor = getMetricDescriptorV2(metrics);
  _metricDescriptor = {
    ...descriptor,
    bestEpoch,
    bestValue: bestMetrics?.[descriptor.key],
  };

  _metricEpochs.push(epoch);
  _metricHistory.trainLoss.push(metrics.train_loss !== undefined ? Number(metrics.train_loss.toFixed(4)) : null);
  _metricHistory.valLoss.push(metrics.val_loss !== undefined ? Number(metrics.val_loss.toFixed(4)) : null);
  const primaryValue = metrics[descriptor.key];
  _metricHistory.primary.push(primaryValue !== undefined ? Number(primaryValue.toFixed(4)) : null);

  if (_metricsChart) {
    _metricsChart.setOption(buildMetricsChartOptionV2(), true);
  }
}

function buildMetricSummary(metrics = {}) {
  if (metrics.r2 !== undefined) {
    return `R²=${Number(metrics.r2).toFixed(3)} RMSE=${Number(metrics.rmse).toFixed(3)}`;
  }
  if (metrics.auc_pr !== undefined) {
    return `AUC-PR=${Number(metrics.auc_pr).toFixed(3)} ACC=${Number(metrics.accuracy).toFixed(3)}`;
  }
  return null;
}

function renderTrainingSummary(status = {}, elapsedSeconds = 0) {
  const area = document.getElementById('trainResultArea');
  if (!area) return;

  const bestMetrics = status.best_metrics || status.metrics || {};
  const descriptor = getMetricDescriptorV2(bestMetrics);
  const secondaryLabel = descriptor.key === 'r2' ? 'RMSE' : 'Accuracy';
  const secondaryValue = descriptor.key === 'r2' ? bestMetrics.rmse : bestMetrics.accuracy;
  const safeTrainLoss = status.train_loss ?? bestMetrics.train_loss;
  const safeValLoss = status.val_loss ?? bestMetrics.val_loss;
  const min = Math.floor(elapsedSeconds / 60);
  const sec = Math.floor(elapsedSeconds % 60);

  area.innerHTML = `
    <div class="metric-grid">
      <div class="metric-card"><div class="metric-name">${descriptor.label}</div><div class="metric-value">${bestMetrics[descriptor.key] !== undefined ? Number(bestMetrics[descriptor.key]).toFixed(4) : '—'}</div></div>
      <div class="metric-card"><div class="metric-name">${secondaryLabel}</div><div class="metric-value">${secondaryValue !== undefined ? Number(secondaryValue).toFixed(4) : '—'}</div></div>
      <div class="metric-card"><div class="metric-name">Best Epoch</div><div class="metric-value">${status.best_epoch || '—'}</div></div>
      <div class="metric-card"><div class="metric-name">Train Loss</div><div class="metric-value">${safeTrainLoss !== undefined && safeTrainLoss !== null ? Number(safeTrainLoss).toFixed(4) : '—'}</div></div>
      <div class="metric-card"><div class="metric-name">Val Loss</div><div class="metric-value">${safeValLoss !== undefined && safeValLoss !== null ? Number(safeValLoss).toFixed(4) : '—'}</div></div>
      <div class="metric-card"><div class="metric-name">训练耗时</div><div class="metric-value">${elapsedSeconds ? `${min}m ${sec}s` : '—'}</div></div>
    </div>
  `;
  area.style.display = 'block';
}

function renderTrainingSummaryV2(status = {}, elapsedSeconds = 0) {
  const area = document.getElementById('trainResultArea');
  if (!area) return;

  const bestMetrics = status.best_metrics || status.metrics || {};
  const descriptor = getMetricDescriptorV2(bestMetrics);
  const secondaryLabel = descriptor.key === 'r2' ? 'RMSE' : 'Accuracy';
  const secondaryValue = descriptor.key === 'r2' ? bestMetrics.rmse : bestMetrics.accuracy;
  const safeTrainLoss = status.train_loss ?? bestMetrics.train_loss;
  const safeValLoss = status.val_loss ?? bestMetrics.val_loss;
  const min = Math.floor(elapsedSeconds / 60);
  const sec = Math.floor(elapsedSeconds % 60);

  area.innerHTML = `
    <div class="metric-grid">
      <div class="metric-card"><div class="metric-name" id="resPrimaryLabel">${descriptor.label}</div><div class="metric-value" id="resR2">${bestMetrics[descriptor.key] !== undefined ? Number(bestMetrics[descriptor.key]).toFixed(4) : '--'}</div></div>
      <div class="metric-card"><div class="metric-name" id="resSecondaryLabel">${secondaryLabel}</div><div class="metric-value" id="resRMSE">${secondaryValue !== undefined ? Number(secondaryValue).toFixed(4) : '--'}</div></div>
      <div class="metric-card"><div class="metric-name">Best Epoch</div><div class="metric-value" id="resBestEpoch">${status.best_epoch || '--'}</div></div>
      <div class="metric-card"><div class="metric-name">Train Loss</div><div class="metric-value" id="resTrainLoss">${safeTrainLoss !== undefined && safeTrainLoss !== null ? Number(safeTrainLoss).toFixed(4) : '--'}</div></div>
      <div class="metric-card"><div class="metric-name">Val Loss</div><div class="metric-value" id="resValLoss">${safeValLoss !== undefined && safeValLoss !== null ? Number(safeValLoss).toFixed(4) : '--'}</div></div>
      <div class="metric-card"><div class="metric-name">训练耗时</div><div class="metric-value" id="resTime">${elapsedSeconds ? `${min}m ${sec}s` : '--'}</div></div>
    </div>
  `;
  area.style.display = 'block';
}

function buildMetricSummaryV2(metrics = {}) {
  if (metrics.r2 !== undefined) {
    return `R2=${Number(metrics.r2).toFixed(3)} RMSE=${Number(metrics.rmse).toFixed(3)}`;
  }
  if (metrics.auc_pr !== undefined) {
    return `AUC-PR=${Number(metrics.auc_pr).toFixed(3)} ACC=${Number(metrics.accuracy).toFixed(3)}`;
  }
  return null;
}

function updateDock(epoch, total, metric, eta, state = 'running') {
  const dot = document.getElementById('dock-dot');
  dot.className = `dock-dot ${state}`;
  document.getElementById('dock-epoch-text').textContent  = `Epoch ${epoch}/${total}`;
  document.getElementById('dock-metric-text').textContent = metric || '—';
  document.getElementById('dock-eta-text').textContent    = eta ? `ETA ${eta}` : 'ETA —';
  document.getElementById('dock-status-text').textContent =
    state === 'running' ? 'Training' : state === 'done' ? 'Done ✓' : 'Error ✗';
}

function updateDrawerStats(epoch, total, metric, eta, state = 'running') {
  document.getElementById('d-epoch').innerHTML = `${epoch} / <span id="d-total">${total}</span>`;
  document.getElementById('d-metric').textContent = metric || '—';
  document.getElementById('d-eta').textContent    = eta || '—';
  const statusEl = document.getElementById('d-status');
  statusEl.textContent = state === 'running' ? 'Running' : state === 'done' ? 'Done ✓' : 'Error ✗';
  statusEl.className = `dstat-value val-${state}`;
}

// ─────────────────────────────────────────
// Config Export / Import
// ─────────────────────────────────────────
function exportConfig() {
  const cfg = {
    task_type:      document.getElementById('trainTaskType').value,
    epochs:         document.getElementById('epochs').value,
    learning_rate:  document.getElementById('learningRate').value,
    batch_size:     document.getElementById('batchSize').value,
    dropout:        document.getElementById('dropout').value,
    target_column:  document.getElementById('targetColumn').value,
    smiles_column:  document.getElementById('smilesColumn').value,
    split_strategy: document.getElementById('splitStrategy').value,
    exported_at:    new Date().toISOString(),
  };
  const blob = new Blob([JSON.stringify(cfg, null, 2)], { type: 'application/json' });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  a.href = url; a.download = 'train_config.json'; a.click();
  URL.revokeObjectURL(url);
}

function importConfig() {
  const input = document.createElement('input');
  input.type = 'file'; input.accept = '.json';
  input.onchange = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      try {
        const cfg = JSON.parse(ev.target.result);
        if (cfg.task_type)      document.getElementById('trainTaskType').value  = cfg.task_type;
        if (cfg.epochs)         document.getElementById('epochs').value          = cfg.epochs;
        if (cfg.learning_rate)  document.getElementById('learningRate').value    = cfg.learning_rate;
        if (cfg.batch_size)     document.getElementById('batchSize').value       = cfg.batch_size;
        if (cfg.dropout)        document.getElementById('dropout').value         = cfg.dropout;
        if (cfg.target_column)  document.getElementById('targetColumn').value    = cfg.target_column;
        if (cfg.smiles_column)  document.getElementById('smilesColumn').value    = cfg.smiles_column;
        setTrainHint('✅ 配置已导入', 'ok');
      } catch { alert('JSON 格式错误，无法解析。'); }
    };
    reader.readAsText(file);
  };
  input.click();
}

// ─────────────────────────────────────────
// startTraining (refactored)
// ─────────────────────────────────────────
async function startTraining() {
  const trainFile    = document.getElementById('trainFile').files?.[0];
  const targetColumn = document.getElementById('targetColumn').value.trim();
  const totalEpochs  = Math.max(1, parseInt(document.getElementById('epochs').value || '50', 10));
  const taskType     = document.getElementById('trainTaskType').value;
  const lr           = document.getElementById('learningRate').value;
  const bs           = document.getElementById('batchSize').value;
  const dr           = document.getElementById('dropout').value;
  
  // New Params
  const layers    = document.getElementById('trainLayers').value;
  const hidden    = document.getElementById('trainHidden').value;
  const decay     = document.getElementById('trainWeightDecay').value;
  const patience  = document.getElementById('trainPatience').value;
  const loss      = document.getElementById('trainLoss').value;
  const scheduler = document.getElementById('trainScheduler').value;

  if (!trainFile || !targetColumn) { alert('请先上传训练数据并填写目标列名。'); return; }

  const trainBtn = document.getElementById('trainBtn');
  trainBtn.disabled = true;
  trainBtn.textContent = '⏳ 提交中...';
  setStatus('Starting Train...');

  // Reset chart history
  _metricEpochs = [];
  _metricHistory = { trainLoss: [], valLoss: [], primary: [] };
  _metricValues = _metricHistory.primary;
  _metricDescriptor = { key: taskType === 'classification' ? 'auc_pr' : 'r2', label: taskType === 'classification' ? 'AUC-PR' : 'R²' };
  if (_metricsChart) {
    _metricsChart.setOption(buildMetricsChartOptionV2(), true);
  }

  _metricDescriptor = getMetricDescriptorV2(taskType === 'classification' ? { auc_pr: 0 } : { r2: 0 });
  if (_metricsChart) {
    _metricsChart.setOption(buildMetricsChartOptionV2(), true);
  }

  // Reset drawer UI
  document.getElementById('drawerProgressBar').style.width = '0%';
  document.getElementById('trainLog').textContent = '[System] 正在上传数据集与训练配置...\n';
  document.getElementById('trainResultArea').style.display = 'none';
  document.getElementById('trainResultArea').innerHTML = '';
  updateDrawerStats(0, totalEpochs, null, null, 'running');

  // Open drawer
  openDrawer();

  const formData = new FormData();
  formData.append('file', trainFile);
  formData.append('target_column', targetColumn);
  formData.append('task_type', taskType);
  formData.append('epochs', totalEpochs);
  formData.append('learning_rate', lr);
  formData.append('batch_size', bs);
  formData.append('dropout', dr);
  formData.append('num_layers', layers);
  formData.append('hidden_size', hidden);
  formData.append('weight_decay', decay);
  formData.append('patience', patience);
  formData.append('loss_metric', loss);
  formData.append('lr_scheduler', scheduler);

  try {
    const response = await fetch('/api/activity/train', { method: 'POST', body: formData });
    const data = await response.json();
    if (!response.ok || !data.success) throw new Error(data.detail || data.error || '启动训练失败');

    const jobId = data.job_id;
    localStorage.setItem('activeTrainJobId', JSON.stringify({ jobId, totalEpochs, taskType }));
    document.getElementById('trainLog').textContent += `[System] 任务已提交, Job ID: ${jobId}\n`;
    trainBtn.textContent = '⏳ 训练中...';
    setStatus('Training');

    const startTime = Date.now();
    const timer = setInterval(async () => {
      try {
        const res = await fetch(`/api/activity/train/status/${jobId}`);
        const sd  = await res.json();
        if (!res.ok || !sd.success) return;
        const js = sd.status;

        const prog = js.progress ?? 0;
        document.getElementById('drawerProgressBar').style.width = `${prog}%`;

        let metricStr = buildMetricSummaryV2(js.metrics);
        if (js.metrics) {
          if (js.metrics.r2 !== undefined)
            metricStr = `R²=${Number(js.metrics.r2).toFixed(3)} RMSE=${Number(js.metrics.rmse).toFixed(3)}`;
          else if (js.metrics.auc_pr !== undefined)
            metricStr = `AUC=${Number(js.metrics.auc_pr).toFixed(3)} ACC=${Number(js.metrics.accuracy).toFixed(3)}`;
        }

        metricStr = buildMetricSummaryV2(js.metrics) || metricStr;

        const elapsed  = (Date.now() - startTime) / 1000;
        const etaSec   = prog > 2 ? Math.round((elapsed / prog) * (100 - prog)) : null;
        const etaStr   = etaSec ? (etaSec > 60 ? `${Math.floor(etaSec/60)}m ${etaSec%60}s` : `${etaSec}s`) : null;

        updateDrawerStats(js.epoch ?? 0, totalEpochs, metricStr, etaStr, 'running');
        updateDock(js.epoch ?? 0, totalEpochs, metricStr, etaStr, 'running');
        if (js.metrics) updateMetricsChart(js.epoch, js.metrics, js.best_epoch, js.best_metrics);

        if (js.logs?.length) {
          document.getElementById('trainLog').textContent = js.logs.join('\n');
          document.getElementById('trainLog').scrollTop = 99999;
        }

        if (js.state === 'completed' || js.state === 'failed') {
          clearInterval(timer);
          localStorage.removeItem('activeTrainJobId');
          trainBtn.disabled = false;
          trainBtn.textContent = '🚀 重新训练';

          if (js.state === 'completed') {
            document.getElementById('trainLog').textContent += '\n[Done] 训练完成!';
            renderTrainingSummaryV2(js, elapsed);
            metricStr = buildMetricSummaryV2(js.best_metrics || js.metrics) || metricStr;
            document.getElementById('resR2').textContent   = js.metrics?.r2?.toFixed(4) ?? js.metrics?.auc_pr?.toFixed(4) ?? '—';
            document.getElementById('resRMSE').textContent = js.metrics?.rmse?.toFixed(4) ?? js.metrics?.accuracy?.toFixed(4) ?? '—';
            const min = Math.floor(elapsed / 60), sec = Math.floor(elapsed % 60);
            document.getElementById('resTime').textContent = `${min}m ${sec}s`;
            updateDrawerStats(totalEpochs, totalEpochs, metricStr, null, 'done');
            updateDock(totalEpochs, totalEpochs, metricStr, null, 'done');
            setStatus('Model Ready');
            loadModels();
          } else {
            document.getElementById('trainLog').textContent += `\n[Error] ${js.error}`;
            updateDrawerStats(js.epoch ?? 0, totalEpochs, null, null, 'error');
            updateDock(js.epoch ?? 0, totalEpochs, null, null, 'error');
            setStatus('Failed');
          }
        }
      } catch (err) { console.error('Polling error:', err); }
    }, 1000);

  } catch (e) {
    alert('训练错误: ' + e.message);
    trainBtn.disabled = false;
    trainBtn.textContent = '🚀 启动训练';
    setStatus('Error');
    closeDrawer();
  }
}

// ─────────────────────────────────────────
// Recover active job on page load
// ─────────────────────────────────────────
(function recoverActiveJob() {
  const saved = localStorage.getItem('activeTrainJobId');
  if (!saved) return;
  try {
    const { jobId, totalEpochs } = JSON.parse(saved);
    if (!jobId) return;
    const dock = document.getElementById('train-dock');
    dock.style.display = 'flex';
    document.getElementById('dock-status-text').textContent = 'Reconnecting...';
    fetch(`/api/activity/train/status/${jobId}`).then(r => r.json()).then(sd => {
      if (!sd.success || sd.status?.state === 'completed' || sd.status?.state === 'failed') {
        localStorage.removeItem('activeTrainJobId');
        dock.style.display = 'none';
      } else {
        updateDock(sd.status.epoch ?? 0, totalEpochs, null, null, 'running');
      }
    }).catch(() => { localStorage.removeItem('activeTrainJobId'); dock.style.display = 'none'; });
  } catch { localStorage.removeItem('activeTrainJobId'); }
})();




document.getElementById("predictForm").addEventListener("submit", (e) => {
  e.preventDefault();
  handlePredict(
    "/api/activity/predict",
    new FormData(e.target),
    "submitBtn",
  );
});

document.getElementById("batchForm").addEventListener("submit", (e) => {
  e.preventDefault();
  handlePredict(
    "/api/activity/batch_predict",
    new FormData(e.target),
    "batchSubmitBtn",
  );
});
let _allModels = [];

async function loadModels() {
  try {
    const res = await fetch("/api/activity/models");
    const data = await res.json();
    if (data.success) {
      _allModels = data.models || [];
      const selector = document.getElementById("modelSelector");
      selector.innerHTML = "";
      
      if (_allModels.length === 0) {
          selector.innerHTML = '<option value="">(尚未训练自定义模型)</option>';
          document.getElementById('deleteModelBtn').style.display = 'none';
          document.getElementById('modelInfoBtn').style.display = 'none';
          updateTaskTypeStat();
          return;
      }
      document.getElementById('deleteModelBtn').style.display = 'flex';
      document.getElementById('modelInfoBtn').style.display = 'block';
      
      _allModels.forEach(model => {
         const opt = document.createElement("option");
         opt.value = model.weights_file;
         opt.setAttribute("data-id", model.model_id);
         opt.textContent = model.name;
         if (data.current_model === model.weights_file) {
             opt.selected = true;
         }
          selector.appendChild(opt);
       });
      updateTaskTypeStat();
      updatePopoverContent();
    }
  } catch (e) {
    console.error("加载模型列表失败", e);
  }
}

async function confirmDeleteModel() {
    const selector = document.getElementById("modelSelector");
    const selectedOpt = selector.options[selector.selectedIndex];
    if (!selectedOpt || !selectedOpt.value) return;

    const modelId = selectedOpt.getAttribute("data-id");
    const modelName = selectedOpt.textContent;

    if (confirm(`⚠️ 确定要物理删除模型 \"${modelName}\" 吗？\n删除后该权重文件将无法找回。`)) {
        try {
            const res = await fetch(`/api/activity/models/${modelId}`, { method: 'DELETE' });
            const data = await res.json();
            if (data.success) {
                alert("✅ 模型已成功删除");
                loadModels(); // 刷新列表
            } else {
                alert("❌ 删除失败: " + data.detail);
            }
        } catch(err) {
            alert("❌ 网络请求失败");
            console.error(err);
        }
    }
}

// ─────────────────────────────────────────
// Model Info Popover Logic
// ─────────────────────────────────────────
function updatePopoverContent() {
  const selector = document.getElementById("modelSelector");
  const selectedFile = selector.value;
  const model = _allModels.find(m => m.weights_file === selectedFile);
  const content = document.getElementById("popoverContent");

  if (!model) {
    content.innerHTML = '<div class="popover-title">未发现模型详情</div>';
    return;
  }

  const dateStr = new Date(model.created_at * 1000).toLocaleString();
  let metricsHtml = '';
  
  if (model.best_metrics) {
    for (const [k, v] of Object.entries(model.best_metrics)) {
      metricsHtml += `
        <div class="popover-metric-row">
          <span class="metric-label">${k.toUpperCase()}</span>
          <span class="metric-highlight">${Number(v).toFixed(4)}</span>
        </div>
      `;
    }
  }

  content.innerHTML = `
    <div class="popover-title">${model.name}</div>
    <div class="popover-metric-row">
      <span class="metric-label">任务类型</span>
      <span class="metric-value">${model.task_type === 'regression' ? '回归' : '分类'}</span>
    </div>
    <div class="popover-metric-row">
      <span class="metric-label">目标列</span>
      <span class="metric-value">${model.target}</span>
    </div>
    <div class="popover-metric-row">
      <span class="metric-label">样本数量</span>
      <span class="metric-value">${model.samples}</span>
    </div>
    <div style="margin: 12px 0 8px; border-top: 1px solid #f1f5f9; padding-top: 8px; font-weight:700; font-size:12px; color:#64748b;">核心指标 · Metrics</div>
    ${metricsHtml || '<div style="font-size:12px; color:var(--muted)">暂无评估数据</div>'}
    <div style="margin-top: 12px; font-size: 11px; color: #94a3b8; text-align: right;">训练于: ${dateStr}</div>
  `;
}

// Toggle Popover
const infoBtn = document.getElementById("modelInfoBtn");
const popover = document.getElementById("modelInfoPopover");

infoBtn?.addEventListener("click", (e) => {
  e.stopPropagation();
  const isShow = popover.style.display === "block";
  popover.style.display = isShow ? "none" : "block";
});

document.addEventListener("click", () => {
  if (popover) popover.style.display = "none";
});

popover?.addEventListener("click", (e) => e.stopPropagation());

document.getElementById("modelSelector")?.addEventListener("change", () => {
  updateTaskTypeStat();
  updatePopoverContent();
});

document.getElementById("modelSelector")?.addEventListener("change", async (e) => {
   const file = e.target.value;
   if (!file) return;
   try {
       const res = await fetch("/api/activity/models/switch", {
           method: "POST",
           headers: { "Content-Type": "application/json" },
           body: JSON.stringify({ model_file: file })
       });
       const data = await res.json();
       if (data.success) {
           alert("✅ 成功切换至模型: " + file);
       } else {
           alert("❌ 切换失败: " + data.detail);
           loadModels(); // re-sync
       }
   } catch(err) {
       console.error(err);
   }
});

// Initialize models list
loadModels();
