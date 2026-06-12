# 活性预测 UI 优化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 优化活性预测页面的视觉体验，引入 ECharts 动态图表（仪表盘、直方图）与 GSAP/CSS 动画（交错入场、数字滚动），使风格与主页统一。

**Architecture:** 将内联 CSS 和 JS 提取到独立文件，引入 ECharts 库处理数据可视化，使用 CSS Keyframes 与 Staggered 逻辑处理动效。

**Tech Stack:** HTML/CSS/JS, ECharts 5.x, Flexbox/Grid.

---

### Task 1: 外部资源引入与文件重构

**Files:**
- Modify: `src/web/templates/activity_prediction.html`
- Create: `src/web/static/css/activity_prediction_v2.css`
- Create: `src/web/static/js/activity_prediction_v2.js`

- [ ] **Step 1: 引入 ECharts CDN**
在 `activity_prediction.html` 的 `<head>` 中添加：
```html
<script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
```

- [ ] **Step 2: 提取并链接样式与脚本**
将现有 1400 行 HTML 中的 `<style>` 块移动至 `activity_prediction_v2.css`，将 `<script>` 块移动至 `activity_prediction_v2.js`，并在 HTML 中正确链接。

- [ ] **Step 3: 提交重构代码**
```bash
git add src/web/templates/activity_prediction.html src/web/static/css/activity_prediction_v2.css src/web/static/js/activity_prediction_v2.js
git commit -m "style: refactor activity prediction page file structure and add ECharts"
```

---

### Task 2: 动效系统实现 (Animations)

**Files:**
- Modify: `src/web/static/css/activity_prediction_v2.css`
- Modify: `src/web/static/js/activity_prediction_v2.js`

- [ ] **Step 1: 定义 Staggered CSS 动画**
在 CSS 中添加：
```css
@keyframes slideUpFade {
  from { opacity: 0; transform: translateY(20px); }
  to { opacity: 1; transform: translateY(0); }
}
.stagger-item { opacity: 0; animation: slideUpFade 0.5s ease forwards; }
```

- [ ] **Step 2: 实现数字滚动工具类**
在 JS 中添加：
```javascript
function animateNumber(id, endValue, duration = 1000) {
    const el = document.getElementById(id);
    let startTimestamp = null;
    const step = (timestamp) => {
        if (!startTimestamp) startTimestamp = timestamp;
        const progress = Math.min((timestamp - startTimestamp) / duration, 1);
        el.innerText = (progress * endValue).toFixed(3);
        if (progress < 1) window.requestAnimationFrame(step);
    };
    window.requestAnimationFrame(step);
}
```

- [ ] **Step 3: 提交动效基础代码**
```bash
git commit -m "feat: add animation primitives and number scroller"
```

---

### Task 3: 单分子可视化 (Activity Gauge)

**Files:**
- Modify: `src/web/templates/activity_prediction.html`
- Modify: `src/web/static/js/activity_prediction_v2.js`

- [ ] **Step 1: 添加仪表盘容器**
在 `single-panel` 的结果展示区上方添加：
```html
<div id="singleGauge" style="width: 100%; height: 300px; display: none;"></div>
```

- [ ] **Step 2: 初始化 ECharts 仪表盘**
在 `handlePredict` 的回调中，根据结果初始化仪表盘：
```javascript
function renderGauge(score) {
    const chart = echarts.init(document.getElementById('singleGauge'));
    document.getElementById('singleGauge').style.display = 'block';
    const option = {
        series: [{
            type: 'gauge',
            progress: { show: true, width: 18 },
            axisLine: { lineStyle: { width: 18 } },
            pointer: { icon: 'path://M12.8,4.8l1.5-1.5c0.2-0.2,0.2-0.5,0-0.7s-0.5-0.2-0.7,0l-1.5,1.5c-0.2,0.2-0.2,0.5,0,0.7S12.6,5,12.8,4.8z', length: '70%', width: 8 },
            axisTick: { show: false },
            splitLine: { show: false },
            detail: { valueAnimation: true, formatter: '{value}', fontSize: 30 },
            data: [{ value: score.toFixed(3) }]
        }]
    };
    chart.setOption(option);
}
```

- [ ] **Step 3: 测试并提交**
```bash
git commit -m "feat: implement interactive gauge chart for single prediction"
```

---

### Task 4: 批量可视化 (Activity Histogram)

**Files:**
- Modify: `src/web/templates/activity_prediction.html`
- Modify: `src/web/static/js/activity_prediction_v2.js`

- [ ] **Step 1: 添加直方图容器**
在 `results` 容器的表格上方添加：
```html
<div id="batchHistogram" style="width: 100%; height: 250px; display: none; margin-bottom: 20px;"></div>
```

- [ ] **Step 2: 实现直方图逻辑**
统计返回数据中的分值区间并渲染：
```javascript
function renderHistogram(dataList) {
    const scores = dataList.map(item => Number(item.activity_score));
    const chart = echarts.init(document.getElementById('batchHistogram'));
    document.getElementById('batchHistogram').style.display = 'block';
    // 统计区间数据逻辑...
    const option = {
        xAxis: { type: 'category', data: ['0.0-0.2', '0.2-0.4', '0.4-0.6', '0.6-0.8', '0.8-1.0'] },
        yAxis: { type: 'value' },
        series: [{ data: [/* counts */], type: 'bar', itemStyle: { color: '#4f46e5' } }]
    };
    chart.setOption(option);
}
```

- [ ] **Step 3: 提交批量可视化**
```bash
git commit -m "feat: add batch activity distribution histogram"
```

---

### Task 5: 样式润色与最终集成

**Files:**
- Modify: `src/web/static/css/activity_prediction_v2.css`

- [ ] **Step 1: 完善交错入场逻辑**
修改 `handlePredict` 中的循环，为每一行添加 `style="animation-delay: ${index * 0.05}s"` 和 `stagger-item` 类。

- [ ] **Step 2: 对齐靛蓝色调**
全局检查并将紫色背景替换为靛蓝渐变，按钮阴影对齐首页风格。

- [ ] **Step 3: 完成并测试**
运行 `python main.py`，进入活性预测页进行完整流程测试（单分子、批量）。
