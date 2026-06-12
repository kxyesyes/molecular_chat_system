# 2026-04-23 优化主页布局实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 通过调整主页面右侧容器的垂直布局逻辑（由居中改为顶部偏移）并增加交互组件间的间距，解决布局重心过高和输入框下方空白感的问题。

**Architecture:** 
1. 将 `.index_con .right` 的 `justify-content` 从 `center` 改为 `flex-start`。
2. 通过 `padding-top: 18vh` 手动定位重心。
3. 增加 `.index_con .right .tool` 的 `margin-top` 以优化局部平衡。

**Tech Stack:** HTML5, CSS3

---

### Task 1: 修改主页内部样式 (index.html)

**Files:**
- Modify: `src/web/templates/index.html`

- [ ] **Step 1: 修改主容器对齐方式和顶部偏移**

在 `index.html` 的 `<style>` 标签中找到 `.index_con .right` 的定义：

```css
/* 修改前 */
.index_con .right {
    flex: 1;
    background-image: url(/static/images/r1.png);
    background-size: cover;
    background-position: center;
    position: relative;
    min-height: calc(100vh - 60px);
    display: flex;
    flex-direction: column;
    justify-content: center; /* 这里需要修改 */
}

/* 修改后 */
.index_con .right {
    flex: 1;
    background-image: url(/static/images/r1.png);
    background-size: cover;
    background-position: center;
    position: relative;
    min-height: calc(100vh - 60px);
    display: flex;
    flex-direction: column;
    justify-content: flex-start; /* 改为顶部对齐 */
    padding-top: 18vh; /* 整体下移重心 */
}
```

- [ ] **Step 2: 增加输入框下方控制栏间距**

找到 `.index_con .right .tool` 的定义：

```css
/* 修改前 */
.index_con .right .tool {
    display: flex;
    margin-top: 2vh; /* 这里需要增加 */
    justify-content: space-between;
    /* ... 其他属性 ... */
}

/* 修改后 */
.index_con .right .tool {
    display: flex;
    margin-top: 6vh; /* 增加间距，使空白区域看起来像刻意留白 */
    justify-content: space-between;
    /* ... 其他属性 ... */
}
```

- [ ] **Step 3: 调整侧边栏间距以匹配高度**

找到 `.index_con .left .data` 的定义：

```css
/* 修改前 */
.index_con .left .data {
    margin-top: 6vh;
}

/* 修改后 */
.index_con .left .data {
    margin-top: 10vh; /* 适当下移，呼应右侧重心 */
}
```

- [ ] **Step 4: 确认并保存**

---

### Task 2: 同步修改外部样式表 (style.css)

**Files:**
- Modify: `src/web/static/css/style.css`

- [ ] **Step 1: 同步主容器样式**

```css
/* 修改 .index_con .right */
.index_con .right {
    flex: 1;
    background-image: url(/static/images/r1.png);
    background-size: 100% 100%;
    position: relative;
    min-height: calc(100vh - 60px);
    display: flex;
    flex-direction: column;
    justify-content: flex-start; /* 修改点 */
    padding-top: 18vh; /* 修改点 */
}
```

- [ ] **Step 2: 同步工具栏间距**

```css
/* 修改 .index_con .right .tool */
.index_con .right .tool {
    display: flex;
    margin-top: 6vh; /* 修改点 */
    justify-content: space-between;
    align-items: center;
    padding: 0 36px;
    margin-bottom: auto;
}
```

- [ ] **Step 3: 同步侧边栏间距**

```css
/* 修改 .index_con .left .data */
.index_con .left .data {
  margin-top: 10vh; /* 修改点 */
}
```

- [ ] **Step 4: 保存并提交**

---

### Task 3: 视觉验证

- [ ] **Step 1: 启动/刷新应用**
确保 `python main.py` 正在运行。

- [ ] **Step 2: 浏览器检查**
访问 `http://localhost:8080`，确认主页重心是否平稳下移，且输入框下方的间距是否看起来更自然。
