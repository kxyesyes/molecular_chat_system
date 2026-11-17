"use strict";

window.HomeWorkspace = (() => {
  const base = "/api/workspace";
  const titles = {upload: "上传文档", projects: "我的项目", history: "分析记录"};
  const states = {succeeded: "已完成", completed: "已完成", running: "进行中", pending: "待执行",
    failed: "失败", partial: "部分完成", rejected: "已拒绝", canceled: "已取消", timed_out: "超时"};
  const types = {activity_prediction: "活性预测", admet: "ADMET 评估", docking: "分子对接",
    reverse_target: "反向寻靶", agent_workflow: "分析任务"};
  const id = name => document.getElementById("workspace" + name);
  let mode, selected, chip, actions, items, status, select, dialog, allowedToAttach, onSelection;
  let busy = false;
  function node(tag, text) {
    const el = document.createElement(tag);
    if (text != null) el.textContent = text;
    return el;
  }
  async function request(path, options) {
    const response = await fetch(path, options);
    const value = await response.json();
    if (!response.ok || value?.success === false) {
      throw new Error(typeof value.detail === "string" ? value.detail : value.message || "请求失败，请重试");
    }
    return value;
  }
  function json(method, value) {
    return {method, headers: {"Content-Type":"application/json"}, body: JSON.stringify(value)};
  }
  function run(action) {
    return async event => {
      event?.preventDefault();
      if (busy) return;
      busy = true;
      actions.disabled = true;
      status.textContent = "加载中…";
      try { status.textContent = await action(event) || ""; }
      catch (err) { status.textContent = err.message || "加载失败，请重试"; }
      finally { busy = false; actions.disabled = false; }
    };
  }
  function button(text, action) {
    const el = node("button", text);
    el.type = "button"; el.onclick = run(action);
    return el;
  }
  function row(label) {
    const el = node("div");
    el.className = "workspace-row";
    el.append(node("strong", label)); items.append(el);
    return el;
  }
  function section(title, count) {
    items.append(node("h3", title));
    if (!count) items.append(node("p", "暂无记录"));
  }
  function questionRow(q) {
    const details = node("details");
    details.className = "workspace-row";
    details.append(node("summary", q.question), node("small", q.name), node("p", q.answer));
    items.append(details);
  }
  function recordRow(kind, key, title, info, canAssign) {
    const el = row(title);
    if (info) el.append(node("small", info));
    const content = node("div");
    const encoded = encodeURIComponent(key);
    const path = kind === "run" ? "/api/agent/runs/" + encoded : "/api/tasks/" + encoded;
    const details = button("查看记录", async () => {
      if (content.childElementCount) {
        content.hidden = !content.hidden;
      } else {
        const response = await request(kind === "run" ? path + "/events" : path);
        if (kind === "run") {
          const reference = await request(base + "/runs/" + encoded + "/reference");
          if (reference) window.HomeResultDetails.mount(content, {traceId:key, reference});
          else content.append(node("p", "此记录未保存回答正文或结果明细。"));
          const events = response.data.events || [];
          for (const e of events) content.append(node("p", e.message || e.event));
          if (!events.length) content.append(node("p", "尚无运行事件"));
        } else {
          const task = response.data;
          content.append(node("p", states[task.status] || task.status));
          if (task.result) content.append(node("pre", typeof task.result === "string" ? task.result : JSON.stringify(task.result, null, 2)));
          if (task.error) content.append(node("p", task.error));
        }
      }
      details.textContent = content.hidden ? "查看记录" : "收起记录";
    });
    el.append(details);
    if (canAssign && select.value) el.append(button("归入当前项目", async () => {
      await request(base + "/projects/" + select.value + "/bookmarks", json("POST", {kind,record:key}));
      return "已归入 " + select.selectedOptions[0].textContent;
    }));
    el.append(content);
  }
  function displayTime(value) {
    if (!value) return "";
    const date = new Date(typeof value === "number" ? value * 1000 : value);
    return Number.isNaN(date.valueOf()) ? "" : date.toLocaleString();
  }
  async function renderHistory() {
    const sources = await Promise.allSettled([request(base + "/questions"), request(base + "/runs"),
      request("/api/tasks?limit=100")]);
    ["文档问答","首页分析","后台任务"].forEach((title, i) => {
      const source = sources[i];
      if (source.status === "rejected") {
        items.append(node("h3", title), node("p", source.reason.message)); return;
      }
      const records = i === 2 ? source.value.data : source.value;
      section(title, records.length);
      for (const record of records) {
        if (i === 0) questionRow(record);
        else {
          const label = i === 1 ? record.title || "首页分析" : types[record.task_type] || record.task_type;
          const info = [states[record.status] || record.status, displayTime(record.created_at)].filter(Boolean).join(" · ");
          recordRow(i === 1 ? "run" : "task", record.trace_id || record.task_id, label, info, true);
        }
      }
    });
  }
  function setDocument(doc) {
    selected = doc;
    chip.hidden = !doc;
    chip.firstChild.textContent = doc ? "提问文档 · " + doc.name : "";
    onSelection();
  }
  async function render() {
    if (selected && selected.project !== select.value) setDocument(null);
    items.replaceChildren();
    id("Upload").hidden = mode === "history" || !select.value;
    id("ProjectForm").hidden = mode === "history" && !!select.value;
    id("ProjectForm").querySelector("[value=rename]").disabled = !select.value;
    if (mode === "history") { await renderHistory(); return; }
    if (!select.value) { items.append(node("p", "先新建项目以保存文档和分析。")); return; }
    const project = await request(base + "/projects/" + select.value);
    section("项目文档", project.documents.length);
    for (const doc of project.documents) {
      const el = row(doc.name);
      const download = node("a","下载原件");
      download.href = base + "/documents/" + encodeURIComponent(doc.id); download.download = doc.name;
      download.onclick = run(async () => {
        status.textContent = "正在下载…";
        const response = await fetch(download.href);
        if (!response.ok) {
          const error = await response.json();
          throw new Error(error.detail || "文档下载失败");
        }
        const link = node("a");
        link.href = URL.createObjectURL(await response.blob());
        link.download = doc.name;
        link.click();
        URL.revokeObjectURL(link.href);
        return "已开始下载";
      });
      el.append(download, button("用此文档提问", () => {
        if (!allowedToAttach()) throw new Error("请先结束当前聊天任务");
        setDocument({...doc, project:project.id}); dialog.close();
        document.querySelector(".fill textarea").focus();
      }));
    }
    if (mode === "projects") {
      section("已归档分析", project.bookmarks.length + project.questions.length);
      for (const b of project.bookmarks) recordRow(b.kind, b.record, types[b.title] || b.title || (b.kind === "run" ? "首页分析" : "后台任务"), "", false);
      project.questions.forEach(questionRow);
    }
  }
  async function reload(preferred) {
    const projects = await request(base + "/projects");
    select.replaceChildren();
    for (const p of projects) select.add(new Option(p.name,p.id));
    if (projects.some(p => p.id === preferred)) select.value = preferred;
    if (select.value) sessionStorage.setItem("homeWorkspaceProject",select.value);
    await render();
  }
  function init({closeSidebar, canAttach, selectionChanged}) {
    dialog = id("Dialog"); actions = id("Actions"); items = id("Items"); status = id("Status"); select = id("Project");
    allowedToAttach = canAttach; onSelection = selectionChanged;
    chip = node("div"); chip.className = "workspace-attachment"; chip.hidden = true;
    const remove = node("button","移除文档"); remove.type = "button"; remove.onclick = () => setDocument(null);
    chip.append(node("span"), remove);
    document.querySelector(".chat-composer").prepend(chip);
    for (const entry of document.querySelectorAll("[data-workspace]")) {
      entry.onclick = run(async () => {
        closeSidebar();
        mode = entry.dataset.workspace;
        id("Title").textContent = titles[mode]; items.replaceChildren(); dialog.showModal();
        await reload(sessionStorage.getItem("homeWorkspaceProject"));
      });
    }
    id("Close").onclick = () => dialog.close();
    select.onchange = run(async () => { sessionStorage.setItem("homeWorkspaceProject",select.value); await render(); });
    id("ProjectForm").onsubmit = run(async event => {
      const name = id("Name").value.trim();
      if (!name) throw new Error("请输入项目名称");
      const rename = event.submitter?.value === "rename";
      const result = await request(base + "/projects" + (rename ? "/" + select.value : ""),
        json(rename ? "PATCH" : "POST", {name}));
      id("Name").value = ""; await reload(result.id);
      return rename ? "已重命名" : "已新建项目";
    });
    id("Upload").onsubmit = run(async () => {
      const file = id("File").files[0];
      if (!file) throw new Error("请选择文档");
      if (file.size > 1048576) throw new Error("文档上限为 1 MiB");
      const form = new FormData(); form.append("file",file);
      status.textContent = "正在上传…";
      await request(base + "/projects/" + select.value + "/documents", {method:"POST",body:form});
      id("File").value = ""; await render(); return "文档已上传";
    });
  }
  return {init, get selected() {return selected;}, clear() {if (selected) setDocument(null);},
    ask(doc, question) { return request(base + "/documents/" + encodeURIComponent(doc.id) + "/questions", json("POST",{question})); }};
})();
