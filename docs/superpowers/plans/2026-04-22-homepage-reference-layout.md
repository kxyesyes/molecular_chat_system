# Homepage Reference Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将首页首屏重排成接近参考图的高保真工作台布局，同时保留现有聊天、模型切换、文件管理、RAG、工具开关与高级选项的交互能力。

**Architecture:** 以 `src/web/templates/index.html` 为主战场，对首页的左侧导航、右侧主舞台、Hero、功能卡片、输入区和底部控制胶囊进行重构；仅对 `home/main.js` 和 `home/state.js` 做最小范围适配，保证旧逻辑仍能驱动新结构。测试采用现有 smoke test 扩展方式，优先验证关键 DOM、脚本引用与核心交互挂点。

**Tech Stack:** Jinja2 模板、内联 CSS、原生 JavaScript（`src/web/static/js/home/*.js`）、Node.js smoke tests

---

## File Structure

- `src/web/templates/index.html`
  - 重排首页首屏 DOM 结构
  - 重写首页核心样式
  - 新增 Hero 装饰层、四张功能卡、输入工具区、底部控制胶囊结构
- `src/web/static/js/home/main.js`
  - 适配新 Hero、卡片、输入区、控制胶囊 DOM
  - 保持已有按钮、发送、聊天态切换与工具层逻辑
- `src/web/static/js/home/state.js`
  - 如新结构需要新增元素引用，统一在这里补充
- `tests/ui/homepage_tool_controls_smoke.test.js`
  - 扩展为首页首屏布局 smoke test，覆盖新结构和关键脚本挂点

### Task 1: 写首页参考图首屏结构的失败 smoke test

**Files:**
- Modify: `tests/ui/homepage_tool_controls_smoke.test.js`
- Test: `tests/ui/homepage_tool_controls_smoke.test.js`

- [ ] **Step 1: 在现有 smoke test 里增加首页参考图结构断言**

在 `tests/ui/homepage_tool_controls_smoke.test.js` 末尾追加下面这段：

```js
[
  'class="hero-shell"',
  'class="hero-copy"',
  'class="hero-visual"',
  'class="feature-grid"',
  'class="feature-card"',
  'class="composer-shell"',
  'class="composer-actions"',
  'class="control-pills"',
  'class="control-pill"',
].forEach((token) => {
  assert(html.includes(token), `Missing homepage reference layout token: ${token}`);
});

[
  "function cacheHomeLayoutElements()",
  "function applyHeroCardContent()",
  "function syncControlPillsState()",
].forEach((token) => {
  assert(main.includes(token), `Missing homepage layout behavior token: ${token}`);
});
```

- [ ] **Step 2: 运行 smoke test，确认它先失败**

Run: `node tests/ui/homepage_tool_controls_smoke.test.js`  
Expected: FAIL with `Missing homepage reference layout token: class="hero-shell"` or another newly added token.

- [ ] **Step 3: 保存红灯 checkpoint**

```powershell
New-Item -ItemType Directory -Force scratch\checkpoints\homepage-reference-layout-task-1-red | Out-Null
Copy-Item tests\ui\homepage_tool_controls_smoke.test.js scratch\checkpoints\homepage-reference-layout-task-1-red -Force
```

### Task 2: 重排右侧主舞台 DOM 结构

**Files:**
- Modify: `src/web/templates/index.html`
- Test: `tests/ui/homepage_tool_controls_smoke.test.js`

- [ ] **Step 1: 将旧的 `mass / icon_list / fill / tool` 页面块重排成参考图式首屏骨架**

把 `src/web/templates/index.html` 中从：

```html
<div class="mass" style="text-align: center">
```

到：

```html
<div class="tool">
```

这一段重排为下面结构：

```html
<div class="hero-shell">
  <div class="hero-copy">
    <div class="hero-brand">
      <img src="/static/images/logo3.png" alt="" />
      <h1>MedChat</h1>
    </div>
    <h2>专业的AI药物设计平台</h2>
    <span class="hero-divider"></span>
    <p>融合先进AI技术与药物化学知识，助力高效发现与设计创新药物分子</p>
  </div>
  <div class="hero-visual" aria-hidden="true">
    <div class="hero-molecule hero-molecule--primary"></div>
    <div class="hero-molecule hero-molecule--secondary"></div>
    <div class="hero-mesh"></div>
  </div>
</div>

<div class="feature-grid icon_list">
  <div class="feature-card item" data-prompt="分析乙醇的理化性质和关键参数">
    <div class="feature-card__icon"><img src="/static/images/r6.png" alt="" /></div>
    <h3>分析乙醇性质</h3>
    <p>分析化合物性质与理化参数</p>
  </div>
  <div class="feature-card item" data-prompt="生成具有类药性的候选分子">
    <div class="feature-card__icon"><img src="/static/images/r5.png" alt="" /></div>
    <h3>生成类药分子</h3>
    <p>基于条件生成类药分子结构</p>
  </div>
  <div
    class="feature-card item"
    data-link="/kermt-admet"
    onclick="window.location.href='/kermt-admet'"
  >
    <div class="feature-card__icon"><img src="/static/images/r4.png" alt="" /></div>
    <h3>ADMET预测</h3>
    <p>预测化合物ADMET性质</p>
  </div>
  <div
    class="feature-card item"
    data-link="/molecular-docking"
    onclick="window.location.href='/molecular-docking'"
  >
    <div class="feature-card__icon"><img src="/static/images/l8.png" alt="" /></div>
    <h3>分子对接</h3>
    <p>分子对接与结合模式分析</p>
  </div>
</div>

<div class="composer-shell fill">
  <input
    type="text"
    placeholder="请输入您的问题，例如：分析CCO的分子性质或生成符合条件的分子..."
  />
  <div class="composer-actions">
    <button type="button" class="composer-icon-btn composer-icon-btn--voice" aria-label="语音输入">
      <img src="/static/images/r7.png" alt="" />
    </button>
    <button type="button" class="composer-icon-btn composer-icon-btn--attach" aria-label="添加附件">
      <img src="/static/images/r8.png" alt="" />
    </button>
    <div class="btn">➤</div>
  </div>
</div>

<div class="control-pills tool">
  <div class="control-pill control-pill--toggle" data-control="rag">
    <div class="control-pill__main">
      <div class="icon"></div>
      <div>
        <strong>RAG检索</strong>
        <span>检索知识库增强回答</span>
      </div>
    </div>
  </div>
  <div class="control-pill control-pill--toggle" data-control="tools">
    <div class="control-pill__main">
      <div class="icon"></div>
      <div>
        <strong>智能工具</strong>
        <span>自动选择合适的分析工具</span>
      </div>
    </div>
  </div>
  <div class="control-pill control-pill--action more">
    <img src="/static/images/r11.png" alt="" />
    <div>
      <strong>高级选项</strong>
      <span>自定义参数与输出设置</span>
    </div>
  </div>
</div>
```

- [ ] **Step 2: 运行 smoke test，确认结构断言转绿**

Run: `node tests/ui/homepage_tool_controls_smoke.test.js`  
Expected: FAIL only on `Missing homepage layout behavior token: function cacheHomeLayoutElements()` or another JS token, and no longer fail on missing DOM structure token.

- [ ] **Step 3: 保存 DOM 重排 checkpoint**

```powershell
New-Item -ItemType Directory -Force scratch\checkpoints\homepage-reference-layout-task-2 | Out-Null
Copy-Item src\web\templates\index.html,tests\ui\homepage_tool_controls_smoke.test.js scratch\checkpoints\homepage-reference-layout-task-2 -Force
```

### Task 3: 重写首页首屏样式

**Files:**
- Modify: `src/web/templates/index.html`
- Test: `tests/ui/homepage_tool_controls_smoke.test.js`

- [ ] **Step 1: 用参考图风格替换旧的 `mass / icon_list / fill / tool` 样式**

在 `src/web/templates/index.html` 中删除或覆盖旧的：

```css
.index_con .right .mass { ... }
.index_con .right .icon_list { ... }
.index_con .right .icon_list .list { ... }
.index_con .right .fill { ... }
.index_con .right .tool { ... }
```

并加入以下新样式：

```css
.index_con .right {
  padding: 92px 56px 42px;
  justify-content: flex-start;
  gap: 28px;
  overflow: hidden;
}

.hero-shell {
  display: grid;
  grid-template-columns: minmax(520px, 1.15fr) minmax(340px, 0.85fr);
  align-items: center;
  gap: 36px;
  min-height: 280px;
}

.hero-copy {
  max-width: 680px;
  position: relative;
  z-index: 2;
}

.hero-brand {
  display: flex;
  align-items: center;
  gap: 18px;
}

.hero-brand img {
  width: 76px;
  height: 76px;
}

.hero-brand h1 {
  font-size: 64px;
  line-height: 1;
  color: #15295b;
  font-weight: 700;
}

.hero-copy h2 {
  margin-top: 18px;
  font-size: 28px;
  color: #20346b;
  font-weight: 700;
}

.hero-divider {
  display: inline-block;
  width: 44px;
  height: 4px;
  border-radius: 999px;
  background: linear-gradient(90deg, #5d5ff6, #8b8dff);
  margin: 18px 0 16px;
}

.hero-copy p {
  font-size: 18px;
  line-height: 1.75;
  color: #7584a1;
  max-width: 620px;
}

.hero-visual {
  position: relative;
  min-height: 320px;
}

.hero-molecule {
  position: absolute;
  inset: 0;
  background-position: center;
  background-size: contain;
  background-repeat: no-repeat;
  opacity: 0.96;
}

.hero-molecule--primary {
  background-image: url(/static/images/r1.png);
  transform: scale(1.04);
}

.hero-molecule--secondary {
  background-image: radial-gradient(circle at 28% 22%, rgba(132, 149, 255, 0.16) 0 8px, transparent 9px),
    radial-gradient(circle at 58% 48%, rgba(132, 149, 255, 0.16) 0 10px, transparent 11px),
    radial-gradient(circle at 78% 30%, rgba(132, 149, 255, 0.16) 0 6px, transparent 7px);
}

.hero-mesh {
  position: absolute;
  right: -8%;
  bottom: -6%;
  width: 78%;
  height: 56%;
  background: radial-gradient(circle at center, rgba(155, 178, 255, 0.34) 0, rgba(155, 178, 255, 0.08) 46%, transparent 72%);
  filter: blur(18px);
}

.feature-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 22px;
}

.feature-card {
  min-height: 208px;
  border-radius: 28px;
  padding: 28px 24px 24px;
  background: rgba(255, 255, 255, 0.74);
  border: 1px solid rgba(221, 229, 244, 0.86);
  box-shadow: 0 20px 44px rgba(48, 72, 122, 0.08);
  backdrop-filter: blur(18px);
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  justify-content: center;
  gap: 14px;
}

.feature-card__icon {
  width: 82px;
  height: 82px;
  border-radius: 999px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.88), rgba(240, 246, 255, 0.96));
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.88), 0 12px 24px rgba(95, 102, 246, 0.08);
}

.feature-card__icon img {
  width: 44px;
  height: 44px;
}

.feature-card h3 {
  font-size: 20px;
  color: #1a2d5d;
  font-weight: 700;
}

.feature-card p {
  font-size: 15px;
  line-height: 1.7;
  color: #7b88a2;
}

.composer-shell {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 18px 22px 18px 28px;
  min-height: 88px;
  border-radius: 28px;
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid rgba(221, 229, 244, 0.92);
  box-shadow: 0 20px 44px rgba(48, 72, 122, 0.08);
}

.composer-shell input {
  flex: 1;
  border: none;
  outline: none;
  background: transparent;
  font-size: 18px;
  color: #33405a;
}

.composer-actions {
  display: flex;
  align-items: center;
  gap: 14px;
}

.composer-icon-btn,
.composer-shell .btn {
  width: 54px;
  height: 54px;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: none;
  background: rgba(245, 248, 255, 0.96);
  box-shadow: 0 12px 24px rgba(48, 72, 122, 0.08);
}

.composer-icon-btn img {
  width: 22px;
  height: 22px;
}

.composer-shell .btn {
  background: linear-gradient(135deg, #5a5cf5, #7c6cff);
  color: #fff;
  font-size: 26px;
}

.control-pills {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, max-content));
  gap: 18px;
  align-items: stretch;
}

.control-pill {
  min-width: 230px;
  padding: 18px 20px;
  border-radius: 24px;
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid rgba(221, 229, 244, 0.92);
  box-shadow: 0 16px 30px rgba(48, 72, 122, 0.07);
}

.control-pill__main,
.control-pill.more {
  display: flex;
  align-items: flex-start;
  gap: 14px;
}

.control-pill strong,
.control-pill.more strong {
  display: block;
  font-size: 18px;
  color: #273a70;
}

.control-pill span,
.control-pill.more span {
  display: block;
  margin-top: 4px;
  font-size: 14px;
  line-height: 1.55;
  color: #8693aa;
}

.control-pill .icon {
  width: 24px;
  height: 24px;
  border-radius: 8px;
  background: linear-gradient(135deg, #6d68ff, #8a7cff);
}

@media (max-width: 1500px) {
  .feature-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .hero-shell {
    grid-template-columns: 1fr;
  }

  .control-pills {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
```

- [ ] **Step 2: 运行 smoke test，确认样式断言通过**

Run: `node tests/ui/homepage_tool_controls_smoke.test.js`  
Expected: FAIL only on `Missing homepage layout behavior token: function cacheHomeLayoutElements()` or another JS token, and no longer fail on missing style/layout token.

- [ ] **Step 3: 保存样式 checkpoint**

```powershell
New-Item -ItemType Directory -Force scratch\checkpoints\homepage-reference-layout-task-3 | Out-Null
Copy-Item src\web\templates\index.html,tests\ui\homepage_tool_controls_smoke.test.js scratch\checkpoints\homepage-reference-layout-task-3 -Force
```

### Task 4: 适配首页脚本到新布局结构

**Files:**
- Modify: `src/web/static/js/home/state.js`
- Modify: `src/web/static/js/home/main.js`
- Test: `tests/ui/homepage_tool_controls_smoke.test.js`

- [ ] **Step 1: 扩展 smoke test，要求首页脚本具备新结构挂点**

在 `tests/ui/homepage_tool_controls_smoke.test.js` 中把现有布局行为断言扩成下面这一段：

```js
[
  "function cacheHomeLayoutElements()",
  "function applyHeroCardContent()",
  "function syncControlPillsState()",
  'document.querySelector(".hero-shell")',
  'document.querySelectorAll(".feature-grid .feature-card")',
  'document.querySelector(".composer-shell")',
  'document.querySelectorAll(".control-pills .control-pill")',
].forEach((token) => {
  assert(main.includes(token), `Missing homepage layout behavior token: ${token}`);
});
```

- [ ] **Step 2: 运行 smoke test，确认脚本断言先失败**

Run: `node tests/ui/homepage_tool_controls_smoke.test.js`  
Expected: FAIL with `Missing homepage layout behavior token: function cacheHomeLayoutElements()` or another new script token.

- [ ] **Step 3: 在 `state.js` 中增加新布局元素引用**

把下面字段加到 `src/web/static/js/home/state.js` 的 `elements` 中：

```js
heroShell: null,
heroCopy: null,
heroVisual: null,
featureCards: null,
composerShell: null,
composerActions: null,
controlPills: null,
```

- [ ] **Step 4: 在 `main.js` 里缓存新布局元素并同步状态**

在 `src/web/static/js/home/main.js` 中加入以下函数：

```js
function cacheHomeLayoutElements() {
  elements.heroShell = document.querySelector(".hero-shell");
  elements.heroCopy = document.querySelector(".hero-copy");
  elements.heroVisual = document.querySelector(".hero-visual");
  elements.featureCards = document.querySelectorAll(".feature-grid .feature-card");
  elements.composerShell = document.querySelector(".composer-shell");
  elements.composerActions = document.querySelector(".composer-actions");
  elements.controlPills = document.querySelectorAll(".control-pills .control-pill");
}

function applyHeroCardContent() {
  if (!elements.featureCards || !elements.featureCards.length) {
    return;
  }

  elements.featureCards.forEach(function (card) {
    const title = card.querySelector("h3");
    if (title) {
      card.setAttribute("data-title", title.textContent.trim());
    }
  });
}

function syncControlPillsState() {
  if (!elements.controlPills || !elements.controlPills.length) {
    return;
  }

  elements.controlPills.forEach(function (pill) {
    const controlType = pill.getAttribute("data-control");
    const icon = pill.querySelector(".icon");

    if (!icon) {
      return;
    }

    if (controlType === "rag") {
      icon.classList.toggle("is-active", ragEnabled);
      pill.classList.toggle("is-active", ragEnabled);
    }

    if (controlType === "tools") {
      icon.classList.toggle("is-active", toolsEnabled);
      pill.classList.toggle("is-active", toolsEnabled);
    }
  });
}
```

并在 `init()` 中补上：

```js
cacheHomeLayoutElements();
applyHeroCardContent();
syncControlPillsState();
```

同时在 `toggleRAG()` 和 `toggleTools()` 中各补一行：

```js
syncControlPillsState();
```

- [ ] **Step 5: 运行 smoke test 和语法检查**

Run: `node tests/ui/homepage_tool_controls_smoke.test.js`  
Expected: PASS

Run: `node --check src/web/static/js/home/state.js`  
Expected: exits with code `0`

Run: `node --check src/web/static/js/home/main.js`  
Expected: exits with code `0`

- [ ] **Step 6: 保存脚本适配 checkpoint**

```powershell
New-Item -ItemType Directory -Force scratch\checkpoints\homepage-reference-layout-task-4 | Out-Null
Copy-Item src\web\static\js\home\state.js,src\web\static\js\home\main.js,tests\ui\homepage_tool_controls_smoke.test.js scratch\checkpoints\homepage-reference-layout-task-4 -Force
```

### Task 5: 修正功能卡、输入区和聊天态切换的兼容逻辑

**Files:**
- Modify: `src/web/static/js/home/main.js`
- Test: `tests/ui/homepage_tool_controls_smoke.test.js`

- [ ] **Step 1: 扩展 smoke test，要求保留聊天态兼容函数**

在 `tests/ui/homepage_tool_controls_smoke.test.js` 中追加下面这段：

```js
[
  "function enterChatMode()",
  'document.querySelector(".mass")',
  'document.querySelector(".icon_list")',
  'document.querySelector(".composer-shell")',
].forEach((token) => {
  assert(main.includes(token), `Missing chat compatibility token: ${token}`);
});
```

- [ ] **Step 2: 运行 smoke test，确认仍为绿灯**

Run: `node tests/ui/homepage_tool_controls_smoke.test.js`  
Expected: PASS

- [ ] **Step 3: 调整 `handleQuickAction()` 与首页卡片的兼容输入**

在 `src/web/static/js/home/main.js` 中，确保 `handleQuickAction()` 支持新卡片上的 `data-prompt`：

```js
function handleQuickAction(event) {
  const target = event.currentTarget;
  const prompt = target.getAttribute("data-prompt");

  if (prompt && elements.input) {
    elements.input.value = prompt;
    sendMessage();
    return;
  }

  const cardText = target.querySelector("h3, span");
  if (cardText && elements.input) {
    elements.input.value = cardText.textContent.trim();
    sendMessage();
  }
}
```

- [ ] **Step 4: 调整聊天态时首页区块的隐藏与恢复**

在 `enterChatMode()`、`resetToHomeState()` 中保留对 `.mass` 和 `.icon_list` 的控制，同时新增对以下容器的兼容：

```js
const heroShell = document.querySelector(".hero-shell");
const featureGrid = document.querySelector(".feature-grid");
const controlPills = document.querySelector(".control-pills");

if (heroShell) heroShell.style.display = "none";
if (featureGrid) featureGrid.style.display = "none";
if (controlPills) controlPills.style.display = "grid";
```

恢复时：

```js
if (heroShell) heroShell.style.display = "";
if (featureGrid) featureGrid.style.display = "";
if (controlPills) controlPills.style.display = "";
```

- [ ] **Step 5: 运行语法检查**

Run: `node --check src/web/static/js/home/main.js`  
Expected: exits with code `0`

- [ ] **Step 6: 保存兼容逻辑 checkpoint**

```powershell
New-Item -ItemType Directory -Force scratch\checkpoints\homepage-reference-layout-task-5 | Out-Null
Copy-Item src\web\static\js\home\main.js,tests\ui\homepage_tool_controls_smoke.test.js scratch\checkpoints\homepage-reference-layout-task-5 -Force
```

### Task 6: 全量校验与交付前验证

**Files:**
- Verify: `src/web/templates/index.html`
- Verify: `src/web/static/js/home/state.js`
- Verify: `src/web/static/js/home/main.js`
- Verify: `tests/ui/homepage_tool_controls_smoke.test.js`

- [ ] **Step 1: 运行自动校验**

Run: `node tests/ui/homepage_tool_controls_smoke.test.js`  
Expected: PASS

Run: `node --check src/web/static/js/home/state.js`  
Expected: exits with code `0`

Run: `node --check src/web/static/js/home/main.js`  
Expected: exits with code `0`

- [ ] **Step 2: 浏览器手工验收首页参考图效果**

手工验证下面这些点：

```text
1. 首页首屏整体结构与参考图在布局、层级和留白上明显接近。
2. 左侧工具导航栏更规整，数据管理按钮与分析工具卡片统一。
3. 右侧 Hero 区中，MedChat 标题、副标题与说明文字层级明确，右侧背景装饰不会压住正文。
4. 四张功能卡完整显示，点击后仍可触发首页原有快捷行为或跳转。
5. 输入框、语音按钮、附件按钮、发送按钮位置与比例接近参考图。
6. `RAG检索 / 智能工具 / 高级选项` 三枚胶囊显示完整且交互正常。
7. 文件管理与帮助按钮仍可打开对应面板。
8. 进入聊天态后，首页 Hero 与功能卡会被正确隐藏，聊天区能正常显示。
```

- [ ] **Step 3: 保存最终 checkpoint**

```powershell
New-Item -ItemType Directory -Force scratch\checkpoints\homepage-reference-layout-final | Out-Null
Copy-Item src\web\templates\index.html,src\web\static\js\home\state.js,src\web\static\js\home\main.js,tests\ui\homepage_tool_controls_smoke.test.js scratch\checkpoints\homepage-reference-layout-final -Force
```
