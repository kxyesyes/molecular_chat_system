# Homepage Centered Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-layout the homepage into a centered workbench that keeps the existing top header, collapses the left navigation by default, and makes the composer the primary visual focus.

**Architecture:** Keep the current homepage entry points and chat-mode behavior, but wrap the homepage content in a new centered shell and drive the layout with a final override block in `index.html`. Preserve the existing theme-switcher and advanced-options logic in `script.js`, and add a small smoke test that guards the new DOM structure and default collapsed sidebar state.

**Tech Stack:** Static HTML/CSS in `src/web/templates/index.html`, vanilla JavaScript in `src/web/static/js/script.js`, Node.js for smoke tests and syntax checks.

---

### Task 1: Add a structural smoke test and homepage shell markup

**Files:**
- Create: `tests/ui/homepage_layout_smoke.test.js`
- Modify: `src/web/templates/index.html`

- [ ] **Step 1: Write the failing smoke test**

Create `tests/ui/homepage_layout_smoke.test.js` with these assertions:

```js
const fs = require("fs");
const path = require("path");
const assert = require("assert");

const html = fs.readFileSync(
  path.join(__dirname, "..", "..", "src", "web", "templates", "index.html"),
  "utf8"
);

function expectMatch(pattern, message) {
  assert(pattern.test(html), message);
}

expectMatch(/class="home-shell"/, "expected .home-shell wrapper in homepage markup");
expectMatch(/class="top home-toolbar"/, "expected .home-toolbar class on the homepage toolbar");
expectMatch(/class="mass home-intro"/, "expected .home-intro class on the homepage title block");
expectMatch(/class="fill home-composer"/, "expected .home-composer class on the homepage input block");
expectMatch(/class="tool home-toolbelt"/, "expected .home-toolbelt class on the homepage controls");
expectMatch(/class="icon_list home-quick-actions"/, "expected .home-quick-actions class on the quick action list");

console.log("homepage layout smoke test: structure assertions passed");
```

- [ ] **Step 2: Run the smoke test to verify it fails**

Run:

```powershell
node tests/ui/homepage_layout_smoke.test.js
```

Expected: FAIL with an assertion such as `expected .home-shell wrapper in homepage markup`.

- [ ] **Step 3: Add the centered workbench wrapper classes in the homepage markup**

In `src/web/templates/index.html`, wrap the homepage content inside `.right` with a semantic shell and add the new structural classes without changing the top header:

```html
<div class="right">
  <div class="btn_list">
    <img id="sidebarToggle" src="/static/images/l11.png" title="收起/展开侧边栏" alt="" />
    <img id="newSessionBtn" src="/static/images/l12.png" title="开启新对话" alt="" />
    <img id="clearHistoryBtn" src="/static/images/l13.png" title="彻底清空当前历史" alt="" />
  </div>

  <section class="home-shell">
    <div class="top home-toolbar">
      <div class="theme-switcher">
        <span class="theme-switcher-label">首页主题</span>
        <div class="theme-options">
          <button type="button" class="theme-chip active" data-theme="rose">粉雾</button>
          <button type="button" class="theme-chip" data-theme="amber">琥珀</button>
          <button type="button" class="theme-chip" data-theme="mint">薄荷</button>
        </div>
      </div>
      <img id="exportDataBtn" src="/static/images/r2.png" title="导出数据" alt="" />
      <img id="helpBtn" src="/static/images/r3.png" title="帮助文档" alt="" />
    </div>

    <div class="mass home-intro" style="text-align: center">
      <div class="hero-mark">
        <img src="/static/images/logo3.png" alt="" />
        <h2>MedChat</h2>
      </div>
      <p>专业的 AI 药物设计平台</p>
    </div>

    <div class="fill home-composer">
      <input
        type="text"
        placeholder="请输入您的问题，例如：'分析 CCO 的分子性质' 或 '生成符合条件的分子'..."
      />
      <div class="btn">➤</div>
      <div class="pic">
        <img src="/static/images/r7.png" alt="" />
        <img src="/static/images/r8.png" alt="" />
      </div>
    </div>

    <div class="tool home-toolbelt">
      <div class="chose">
        <div class="item">
          <div class="icon"></div>
          <span>RAG 检索</span>
        </div>
        <div class="item">
          <div class="icon"></div>
          <span>智能工具</span>
        </div>
      </div>
      <div class="more">
        <img src="/static/images/r11.png" alt="" />
        <span>高级选项</span>
      </div>
    </div>

    <div class="icon_list home-quick-actions">
      <div class="list">
        <div class="item">
          <img src="/static/images/r6.png" alt="" />
          <span>分析乙醇性质</span>
        </div>
        <div class="item">
          <img src="/static/images/r5.png" alt="" />
          <span>生成类药分子</span>
        </div>
        <div class="item" onclick="window.location.href='/kermt-admet'">
          <img src="/static/images/r4.png" alt="" />
          <span>ADMET 预测</span>
        </div>
      </div>
    </div>
  </section>
```

- [ ] **Step 4: Re-run the smoke test to verify the structure passes**

Run:

```powershell
node tests/ui/homepage_layout_smoke.test.js
```

Expected: PASS with `homepage layout smoke test: structure assertions passed`.

- [ ] **Step 5: Checkpoint the task**

Run:

```powershell
git rev-parse --is-inside-work-tree
```

If the command prints `true`, run:

```powershell
git add tests/ui/homepage_layout_smoke.test.js src/web/templates/index.html
git commit -m "feat: add centered homepage shell structure"
```

If the command errors with `fatal: not a git repository`, run:

```powershell
New-Item -ItemType Directory -Force 'scratch/checkpoints/homepage-task-1' | Out-Null
Copy-Item 'tests/ui/homepage_layout_smoke.test.js' 'scratch/checkpoints/homepage-task-1/'
Copy-Item 'src/web/templates/index.html' 'scratch/checkpoints/homepage-task-1/'
```

Expected: either a git commit is created, or the checkpoint copies exist in `scratch/checkpoints/homepage-task-1/`.

### Task 2: Default the homepage sidebar to collapsed and preserve the toggle behavior

**Files:**
- Modify: `tests/ui/homepage_layout_smoke.test.js`
- Modify: `src/web/static/js/script.js`
- Modify: `src/web/templates/index.html`

- [ ] **Step 1: Extend the smoke test with sidebar state assertions**

Append these assertions to `tests/ui/homepage_layout_smoke.test.js`:

```js
const script = fs.readFileSync(
  path.join(__dirname, "..", "..", "src", "web", "static", "js", "script.js"),
  "utf8"
);

function expectScriptMatch(pattern, message) {
  assert(pattern.test(script), message);
}

expectMatch(/class="index_con sidebar-collapsed"/, "expected .index_con to start collapsed");
expectScriptMatch(/function initSidebarState\(\)/, "expected initSidebarState helper");
expectScriptMatch(/setSidebarCollapsed\(true\);/, "expected homepage sidebar to initialize as collapsed");
expectScriptMatch(/function setSidebarCollapsed\(collapsed\)/, "expected reusable sidebar state setter");
```

- [ ] **Step 2: Run the smoke test to verify the new assertions fail**

Run:

```powershell
node tests/ui/homepage_layout_smoke.test.js
```

Expected: FAIL on the missing `sidebar-collapsed` class or `initSidebarState` helper.

- [ ] **Step 3: Implement a reusable collapsed-state helper and initialize it on page load**

In `src/web/templates/index.html`, change the main container opening tag to:

```html
<div class="index_con sidebar-collapsed">
```

In `src/web/static/js/script.js`, add a helper pair and call them from `init()`:

```js
function init() {
  console.log("=== 初始化分子聊天系统 ===");
  // ...existing DOM lookup...
  createChatContainer();
  initThemeSwitcher();
  initSidebarState();
  updateConnectionStatus("connecting");
  connectWebSocket();
  bindEvents();
  updateToggleStates();
  addEnhancedStyles();
  initAdvancedOptions();
}

function setSidebarCollapsed(collapsed) {
  const mainContainer = document.querySelector(".index_con");
  const leftSidebar = document.querySelector(".index_con .left");
  if (!mainContainer || !leftSidebar) return;

  mainContainer.classList.toggle("sidebar-collapsed", collapsed);
  leftSidebar.style.width = collapsed ? "0" : "280px";
  leftSidebar.style.opacity = collapsed ? "0" : "1";
  leftSidebar.style.pointerEvents = collapsed ? "none" : "auto";

  if (elements.sidebarToggle) {
    elements.sidebarToggle.style.transform = collapsed ? "rotate(180deg)" : "rotate(0deg)";
    elements.sidebarToggle.setAttribute("aria-pressed", String(collapsed));
  }
}

function initSidebarState() {
  setSidebarCollapsed(true);
}

function toggleSidebar() {
  const mainContainer = document.querySelector(".index_con");
  if (!mainContainer) return;
  const collapsed = mainContainer.classList.contains("sidebar-collapsed");
  setSidebarCollapsed(!collapsed);
}
```

- [ ] **Step 4: Re-run the smoke test and the JavaScript syntax check**

Run:

```powershell
node tests/ui/homepage_layout_smoke.test.js
node --check src/web/static/js/script.js
```

Expected:
- `homepage layout smoke test: structure assertions passed`
- no output from `node --check`

- [ ] **Step 5: Checkpoint the task**

If git is available:

```powershell
git add tests/ui/homepage_layout_smoke.test.js src/web/static/js/script.js src/web/templates/index.html
git commit -m "feat: collapse homepage sidebar by default"
```

If git is unavailable:

```powershell
New-Item -ItemType Directory -Force 'scratch/checkpoints/homepage-task-2' | Out-Null
Copy-Item 'tests/ui/homepage_layout_smoke.test.js' 'scratch/checkpoints/homepage-task-2/'
Copy-Item 'src/web/static/js/script.js' 'scratch/checkpoints/homepage-task-2/'
Copy-Item 'src/web/templates/index.html' 'scratch/checkpoints/homepage-task-2/'
```

Expected: a commit or checkpoint copy exists for Task 2.

### Task 3: Rebuild the homepage as a centered workbench and make the composer the visual focal point

**Files:**
- Modify: `src/web/templates/index.html`

- [ ] **Step 1: Add a final CSS override block for the centered workbench layout**

Append this override block near the end of the `<style>` section in `src/web/templates/index.html`, after the existing homepage rules so it wins the cascade:

```css
      /* Homepage Centered Workbench */
      .index_con {
        background:
          linear-gradient(90deg, rgba(255, 255, 255, 0.92) 0, rgba(255, 255, 255, 0.92) 72px, transparent 144px),
          var(--page-bg);
      }

      .index_con.sidebar-collapsed .left {
        width: 0 !important;
        opacity: 0 !important;
        pointer-events: none !important;
        padding: 0 !important;
        border-right: 0 !important;
        box-shadow: none !important;
      }

      .index_con .right {
        align-items: stretch;
        padding: 28px 40px 56px;
        background-image:
          radial-gradient(circle at 18% 20%, rgba(255, 255, 255, 0.88), transparent 24%),
          radial-gradient(circle at 82% 16%, var(--accent-soft), transparent 20%),
          linear-gradient(180deg, rgba(255, 255, 255, 0.62), rgba(255, 255, 255, 0.78)),
          url(/static/images/r1.png);
        background-size: auto, auto, auto, cover;
        background-position: center, center, center, center;
      }

      .home-shell {
        width: min(1360px, calc(100vw - 112px));
        margin: 0 auto;
        display: flex;
        flex-direction: column;
        align-items: center;
      }

      .home-toolbar {
        width: auto !important;
        min-width: 440px;
        margin: 24px auto 0 !important;
        padding: 10px 14px;
        justify-content: center;
        gap: 14px;
        background: rgba(255, 255, 255, 0.76);
        border: 1px solid rgba(255, 255, 255, 0.78);
        box-shadow: 0 18px 34px rgba(15, 23, 42, 0.06);
      }

      .home-intro {
        margin-top: 48px !important;
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 18px;
      }

      .home-intro .hero-mark {
        display: flex;
        align-items: center;
        gap: 18px;
        justify-content: center;
      }

      .home-intro img {
        width: 68px !important;
      }

      .home-intro h2 {
        margin-top: 0 !important;
        font-size: 64px !important;
        letter-spacing: -0.04em;
      }

      .home-intro p {
        margin-top: 0 !important;
        font-size: 18px !important;
        color: var(--text-soft);
      }

      .home-composer {
        width: min(980px, 100%);
        margin: 34px auto 0 !important;
        padding: 0 !important;
        gap: 14px;
      }

      .home-composer input {
        height: 68px !important;
        border-radius: 22px !important;
        padding: 0 28px !important;
        background: rgba(255, 255, 255, 0.94) !important;
        box-shadow: 0 20px 40px rgba(15, 23, 42, 0.08);
      }

      .home-composer .btn {
        width: 60px !important;
        height: 60px !important;
        box-shadow: 0 16px 28px var(--accent-shadow);
      }

      .home-composer .pic {
        gap: 10px !important;
      }

      .home-toolbelt {
        width: min(980px, 100%);
        margin: 18px auto 0 !important;
        padding: 0 !important;
      }

      .home-quick-actions {
        width: min(1120px, 100%);
        margin-top: 52px !important;
        text-align: center;
      }

      .home-quick-actions .list {
        margin-top: 0 !important;
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 18px;
      }

      .home-quick-actions .item {
        width: 100% !important;
        height: auto !important;
        min-height: 92px;
        margin: 0 !important;
        padding: 24px 28px;
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 16px;
        line-height: 1;
        border-radius: 24px;
        background: rgba(255, 255, 255, 0.82);
        box-shadow: 0 18px 34px rgba(15, 23, 42, 0.06);
      }

      .home-quick-actions .item span {
        margin-left: 0 !important;
        font-size: 18px !important;
      }
```

- [ ] **Step 2: Tighten the theme-switcher and toolbelt composition**

Add these supporting rules in the same override block so the theme switcher stays visible but no longer reads as a full-width banner:

```css
      .theme-switcher {
        padding: 8px 10px !important;
        border-radius: 999px !important;
        gap: 10px;
      }

      .theme-switcher-label {
        font-size: 12px !important;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: var(--text-soft);
      }

      .theme-options {
        gap: 8px !important;
      }

      .theme-chip {
        min-width: 64px;
        padding: 8px 14px !important;
      }

      .home-toolbelt .chose {
        display: flex;
        align-items: center;
        gap: 22px;
      }

      .home-toolbelt .item {
        margin-right: 0 !important;
      }

      .home-toolbelt .more {
        width: auto !important;
        padding: 0 18px;
        display: inline-flex;
        align-items: center;
        gap: 10px;
      }
```

- [ ] **Step 3: Add large-screen and narrow-screen guardrails**

Finish the override section with responsive rules that keep the centered workbench stable on 2560×1600 while remaining usable on narrower screens:

```css
      @media (max-width: 1440px) {
        .home-shell {
          width: min(1180px, calc(100vw - 88px));
        }

        .home-intro h2 {
          font-size: 56px !important;
        }
      }

      @media (max-width: 1100px) {
        .home-toolbar {
          min-width: 0;
          width: 100% !important;
          justify-content: space-between;
        }

        .home-quick-actions .list {
          grid-template-columns: 1fr;
        }

        .home-toolbelt {
          flex-direction: column;
          gap: 16px;
        }
      }
```

- [ ] **Step 4: Run the smoke test and JavaScript syntax check again**

Run:

```powershell
node tests/ui/homepage_layout_smoke.test.js
node --check src/web/static/js/script.js
```

Expected:
- smoke test passes
- `node --check` stays silent

- [ ] **Step 5: Checkpoint the task**

If git is available:

```powershell
git add src/web/templates/index.html
git commit -m "feat: center homepage workbench layout"
```

If git is unavailable:

```powershell
New-Item -ItemType Directory -Force 'scratch/checkpoints/homepage-task-3' | Out-Null
Copy-Item 'src/web/templates/index.html' 'scratch/checkpoints/homepage-task-3/'
```

Expected: a commit or checkpoint copy exists for Task 3.

### Task 4: Preserve homepage-to-chat transitions and finish the regression sweep

**Files:**
- Modify: `tests/ui/homepage_layout_smoke.test.js`
- Modify: `src/web/static/js/script.js`

- [ ] **Step 1: Extend the smoke test to guard homepage section visibility helpers**

Append these assertions to `tests/ui/homepage_layout_smoke.test.js`:

```js
expectScriptMatch(/function setHomeSectionsVisible\(visible\)/, "expected reusable home section visibility helper");
expectScriptMatch(/setHomeSectionsVisible\(true\);/, "expected reset path to restore homepage sections");
expectScriptMatch(/setHomeSectionsVisible\(false\);/, "expected chat entry path to hide homepage sections");
```

- [ ] **Step 2: Run the smoke test to verify the helper assertions fail**

Run:

```powershell
node tests/ui/homepage_layout_smoke.test.js
```

Expected: FAIL on the missing `setHomeSectionsVisible` helper.

- [ ] **Step 3: Replace the duplicated show/hide logic with a reusable helper**

In `src/web/static/js/script.js`, add a helper and reuse it in both the “reset to home” path and the “enter chat mode” path:

```js
function setHomeSectionsVisible(visible) {
  const sections = document.querySelectorAll(".home-intro, .home-quick-actions");
  sections.forEach((section) => {
    section.style.display = visible ? "" : "none";
  });
}

function resetToHome() {
  if (chatMode) {
    currentMessages = [];
    chatMode = false;
    setHomeSectionsVisible(true);

    if (elements.chatContainer) {
      elements.chatContainer.style.display = "none";
    }

    document.querySelector(".index_con .right").classList.remove("chat-mode");
    showNotification("已重置对话环境", "success");
  } else {
    showNotification("当前已经在首页", "info");
  }
}
```

Replace the current chat-entry hide block with:

```js
setHomeSectionsVisible(false);

if (elements.chatContainer) {
  elements.chatContainer.style.display = "block";
}

document.querySelector(".index_con .right").classList.add("chat-mode");
```

- [ ] **Step 4: Run the full regression commands and a manual browser sweep**

Run:

```powershell
node tests/ui/homepage_layout_smoke.test.js
node --check src/web/static/js/script.js
```

Expected:
- `homepage layout smoke test: structure assertions passed`
- no output from `node --check`

Then perform this manual browser sweep:

1. Load the homepage and confirm the left navigation starts collapsed.
2. Expand and collapse the left navigation with the sidebar toggle.
3. Confirm the homepage visual order is `主题切换 -> 标题区 -> 输入区 -> 开关区 -> 快捷入口`.
4. Click a quick action and verify chat mode hides the intro and quick actions.
5. Click “新对话” and verify the intro and quick actions return in the centered layout.
6. Switch all three themes and confirm only the emphasis layers change.
7. Open “高级选项” and verify the modal still opens and closes correctly.

- [ ] **Step 5: Checkpoint the task**

If git is available:

```powershell
git add tests/ui/homepage_layout_smoke.test.js src/web/static/js/script.js
git commit -m "fix: preserve homepage transitions after centered relayout"
```

If git is unavailable:

```powershell
New-Item -ItemType Directory -Force 'scratch/checkpoints/homepage-task-4' | Out-Null
Copy-Item 'tests/ui/homepage_layout_smoke.test.js' 'scratch/checkpoints/homepage-task-4/'
Copy-Item 'src/web/static/js/script.js' 'scratch/checkpoints/homepage-task-4/'
```

Expected: a commit or checkpoint copy exists for Task 4.
