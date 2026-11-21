// Standalone Chromium regression. Run with Playwright on Node's module path:
// node --test tests/ui_resilience_browser.cjs
const { after, before, test } = require("node:test");
const assert = require("node:assert/strict");
const { createHash } = require("node:crypto");
const path = require("node:path");
const fs = require("node:fs");
const { chromium } = require("playwright");
const candidateEvent = require("./fixtures/molecule_candidate_event.cjs");

const web = path.join(__dirname, "../src/web");
const widths = [320, 375, 430, 768, 1280];
let browser;
before(async () => { browser = await chromium.launch({ channel: "chrome" }); });
after(async () => { if (browser) await browser.close(); });

async function pageFor(t, pathname, api, onSocket = () => {}) {
  const page = await browser.newPage({ viewport: { width: 320, height: 812 }, isMobile: true });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  t.after(async () => {
    await page.context().close();
    assert.deepEqual(errors, []);
  });
  // Use the shipped pages and assets, with isolated responses for failures and
  // model data. No requests reach configured scientific or model services.
  await page.routeWebSocket("**/*", onSocket);
  await page.route("**/*", route => {
    const url = new URL(route.request().url());
    if (url.hostname !== "medchat.test") return route.abort();
    if (url.pathname.startsWith("/api/")) {
      return api ? api(route, url) : route.fulfill({ status: 503, json: { detail: "服务暂不可用" } });
    }
    let file;
    if (url.pathname.startsWith("/static/")) file = path.join(web, url.pathname);
    else if (url.pathname === "/cadd_interactive_radial.html") {
      file = path.join(web, "static/knowledge/cadd_interactive_radial.html");
    } else {
      const name = url.pathname.slice(1).replaceAll("-", "_") || "index";
      file = path.join(web, "templates", name + ".html");
    }
    return fs.existsSync(file) && fs.statSync(file).isFile()
      ? route.fulfill({ path: file }) : route.fulfill({ status: 404, body: "" });
  });
  await page.goto(new URL(pathname, "http://medchat.test").href);
  return page;
}

test("molecule carousels keep batch numbering, references and last-card clipboard actions", {timeout: 20000}, async t => {
  const origin = "https://medchat.test";
  const chains = Array.from({length: 16}, (_, i) => "C".repeat(i + 1));
  const first = candidateEvent(chains);
  const second = candidateEvent(chains.map(s => s + "O"), "22222222-2222-4222-8222-222222222222", "second");
  const overflow = candidateEvent(["COF"], "33333333-3333-4333-8333-333333333333");
  const retrieved = ["CCCl", "CCBr", "CCF"];
  const confirmed = [];
  const followup = Promise.withResolvers();
  const page = await pageFor(t, origin, (route, url) => {
    if (url.pathname.endsWith("/references/confirm")) {
      confirmed.push(route.request().postDataJSON());
      return route.fulfill({json: {success: true, data: {confirmed: true}}});
    }
    return route.fulfill({status: 404, json: {}});
  }, socket => socket.onMessage(raw => {
    const message = JSON.parse(raw);
    const send = frame => socket.send(JSON.stringify(frame));
    if (message.type === "ping") send({type: "connection_ready"});
    if (message.type !== "chat") return;
    if (message.message === "继续分析所选候选") { followup.resolve(message); return; }
    [first, second, overflow].forEach(send);
    send({type: "message", message: "生成候选"});
    send({type: "rag_info", molecules: retrieved.map(smiles => ({smiles}))});
  }));
  page.setDefaultTimeout(5000);
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"], {origin});
  const ask = async question => {
    await page.getByRole("textbox", {name: "消息"}).fill(question);
    await page.getByRole("button", {name: "发送消息"}).click();
  };
  await page.getByText(/传输已连接/).first().waitFor({state: "attached"});
  await ask("显示两批候选");
  await page.getByText("存在多个候选集合，请点击具体卡片选择要继续计算的分子。").waitFor();
  assert.deepEqual(confirmed.map(r => r.presentation_id).sort(), [first, second].map(e => e.reference.presentation_id));
  const grids = page.locator(".molecules-carousel");
  const generated = grids.locator(".molecule-card");
  assert.equal(await grids.count(), 2);
  assert.deepEqual(await generated.getByText(/^候选 \d+$/).allTextContents(),
    Array.from({length: 32}, (_, i) => "候选 " + (i + 1)));
  assert.deepEqual(await generated.evaluateAll(cards => cards.map(c => [c.dataset.observationId, c.dataset.candidateId])),
    [...first.reference.ordered_keys, ...second.reference.ordered_keys]);

  async function copyLast(grid, expected) {
    assert.ok(await grid.evaluate(el => {
      el.scrollTo({left: el.scrollWidth, behavior: "instant"});
      return el.scrollLeft > 0 && el.scrollWidth - el.clientWidth - el.scrollLeft < 2;
    }));
    const last = grid.locator(".molecule-card").last();
    await last.getByRole("button", {name: "复制 SMILES"}).click();
    await page.waitForFunction(async value => await navigator.clipboard.readText() === value, expected);
    return last;
  }
  const rag = page.locator(".rag-molecules-carousel");
  assert.equal(await rag.locator(".molecule-card").count(), retrieved.length);
  await copyLast(rag, retrieved.at(-1));
  const last = await copyLast(grids.last(), second.candidate_set.candidates.at(-1).canonical_smiles);
  await last.getByRole("button", {name: "选择此分子"}).click();
  await last.getByRole("button", {name: "已选择"}).waitFor();
  await ask("继续分析所选候选");
  const outgoing = await followup.promise;
  const {ordered_keys, ...pointer} = second.reference;
  const [observation_id, candidate_id] = ordered_keys.at(-1);
  assert.deepEqual(outgoing.reference, pointer);
  assert.deepEqual(outgoing.selection, {observation_id, candidate_id});
});

test("same-name uploads remain distinguishable when selecting, asking and reviewing history", async t => {
  const name = "测定记录 <br> (复核).csv";
  const older = {id:"doc-old", name, upload_number:null};
  const newer = {id:"doc-new", name, upload_number:2};
  const docs = [older];
  const question = {name, project_name:"组一", upload_number:2, question:"哪一批？", answer:"夏季"};
  const asked = [];
  const page = await pageFor(t, "/", (route, url) => {
    const reply = json => route.fulfill({json});
    const base = "/api/workspace";
    if (url.pathname === base + "/projects") return reply([{id:"p", name:"组一"}]);
    if (url.pathname === base + "/projects/p") return reply({id:"p", documents:docs, questions:[question], bookmarks:[]});
    if (url.pathname === base + "/documents/doc-new/questions") {
      asked.push(route.request().postDataJSON().question);
      return reply({answer:"夏季"});
    }
    if (url.pathname === base + "/questions") return reply([question]);
    if (url.pathname === base + "/runs") return reply([]);
    if (url.pathname === "/api/tasks") return reply({data:[]});
    return route.fulfill({status:404, json:{}});
  });
  page.setDefaultTimeout(3000);
  const dialog = page.locator("#workspaceDialog");
  const chip = page.locator(".workspace-attachment");
  async function open(title) {
    await page.locator("#openSidebarBtn").click();
    await page.getByRole("button",{name:title, exact:true}).first().click();
    await dialog.waitFor();
  }
  await open("我的项目");
  await dialog.getByRole("button",{name:"用此文档提问"}).click();
  assert.equal(await chip.locator("span").innerText(), "提问文档 · " + name);
  older.upload_number = 1;
  docs.unshift(newer);
  await open("我的项目");
  const label = name + "（第 2 次上传）";
  const row = dialog.locator(".workspace-row").filter({has: page.getByText(label,{exact:true})}).first();
  await row.waitFor();
  assert.equal(await row.locator("strong").textContent(), label);
  assert.equal(await row.locator("strong br").count(), 0);
  assert.match(await chip.locator("span").innerText(), /第 1 次上传/);
  const download = row.getByRole("link",{name:"下载原件"});
  assert.equal(await download.getAttribute("download"), name);
  assert.match(await download.getAttribute("href"), /\/doc-new$/);
  await row.getByRole("button",{name:"用此文档提问"}).click();
  assert.equal(await chip.locator("span").innerText(), "提问文档 · " + label);
  await page.getByRole("textbox",{name:"消息"}).fill(question.question);
  await page.getByRole("button",{name:"发送消息"}).click();
  await page.locator(".assistant-message").getByText("夏季").waitFor();
  assert.deepEqual(asked, [question.question]);
  await page.getByText(label + " · " + question.question,{exact:true}).waitFor();
  await open("分析记录");
  await dialog.getByText(question.question,{exact:true}).click();
  await dialog.getByText("组一 · " + label,{exact:true}).waitFor();
});

test("sidebar project, document question, and analysis archive work at phone width", async t => {
  const base = "/api/workspace";
  let project, document, savedQuestion, bookmarked = false;
  let questionRequests = 0;
  let downloadRequests = 0;
  const task = { task_id: "t1", task_type: "activity_prediction", status: "succeeded", result: {message: "已完成测试"} };
  const page = await pageFor(t, "/", async (route, url) => {
    const path = url.pathname, method = route.request().method();
    const json = value => route.fulfill({json:value});
    if (path === base + "/projects" && method === "POST") {
      project = {id:"p1", name:route.request().postDataJSON().name}; return json(project);
    }
    if (path === base + "/projects") return json(project ? [project] : []);
    if (path.endsWith("/documents")) { document = {id:"d1",name:"study.txt"}; return json(document); }
    if (path.endsWith("/documents/d1")) {
      if (!downloadRequests++) return route.fulfill({status:503, json:{detail:"下载暂不可用"}});
      return route.fulfill({contentType:"application/octet-stream", body:"测试正文"});
    }
    if (path.endsWith("/documents/d1/questions")) {
      questionRequests++;
      if (questionRequests === 1) {
        await new Promise(resolve => setTimeout(resolve, 200));
        return route.fulfill({status:503,json:{detail:"问答暂不可用"}});
      }
      savedQuestion = {question:route.request().postDataJSON().question,answer:"来自文档的答案",name:"study.txt",project:"p1"};
      return json(savedQuestion);
    }
    if (path.endsWith("/bookmarks")) { bookmarked = true; return json({project:"p1"}); }
    if (path === base + "/projects/p1") return json({...project, documents:document?[document]:[],
      questions:savedQuestion?[savedQuestion]:[], bookmarks:bookmarked?[{kind:"task",record:"t1"}]:[]});
    if (path === base + "/questions") return json(savedQuestion?[savedQuestion]:[]);
    if (path === base + "/runs") return json([]);
    if (path === "/api/tasks") return json({success:true,data:[task]});
    if (path === "/api/tasks/t1") return json({success:true,data:task});
    return route.fulfill({status:503,json:{detail:"测试服务不可用"}});
  });
  page.setDefaultTimeout(3000);
  const dialog = page.locator("#workspaceDialog");
  const legacyFiles = JSON.stringify([{id:"legacy-1", name:"previous.txt", size:12, lastModified:1}]);
  await page.evaluate(value => localStorage.setItem("medchatManagedFiles", value), legacyFiles);
  await page.reload();
  async function open(label) {
    const entry = page.getByRole("button",{name:label,exact:true}).first();
    await page.locator("#openSidebarBtn").click();
    await entry.click();
    await dialog.waitFor();
  }
  await open("上传文档");
  await dialog.getByLabel("项目名称").fill("研究资料");
  await dialog.getByRole("button",{name:"新建项目"}).click();
  await dialog.getByLabel("选择文档").setInputFiles({name:"study.txt",mimeType:"text/plain",buffer:Buffer.from("测试正文")});
  await dialog.getByRole("button",{name:"上传",exact:true}).click();
  const download = dialog.getByRole("link",{name:"下载原件"});
  await download.click();
  await dialog.getByText("下载暂不可用").waitFor();
  const saved = page.waitForEvent("download");
  await download.click();
  assert.equal((await saved).suggestedFilename(), "study.txt");
  await dialog.getByRole("button",{name:"用此文档提问"}).click();
  await page.getByRole("textbox",{name:"消息"}).fill("请概括正文");
  await page.getByRole("button",{name:"发送消息"}).dblclick();
  await page.locator(".error-message").getByText(/问答暂不可用/).waitFor();
  assert.equal(await page.getByRole("textbox",{name:"消息"}).inputValue(), "请概括正文");
  assert.equal(questionRequests, 1);
  await page.getByRole("button",{name:"发送消息"}).click();
  await page.locator(".assistant-message").getByText(/来自文档的答案/).waitFor();
  await page.reload();
  await open("分析记录");
  await dialog.getByText("请概括正文",{exact:true}).waitFor();
  await dialog.getByRole("button",{name:"归入当前项目"}).click();
  await page.waitForFunction(() => document.querySelector("#workspaceStatus").textContent.includes("已归入"));
  await dialog.getByRole("button",{name:"关闭"}).click();
  await open("我的项目");
  await dialog.getByRole("button",{name:"查看记录"}).click();
  await dialog.getByText(/已完成测试/).waitFor();
  assert.equal(await page.evaluate(() => localStorage.getItem("medchatManagedFiles")), legacyFiles);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 320);
});

test("project changes detach the previous document even when file names match", async t => {
  const projects = [{id:"p1", name:"项目一"}, {id:"p2", name:"项目二"}];
  const page = await pageFor(t, "/", (route, url) => {
    if (url.pathname === "/api/workspace/projects") {
      if (route.request().method() === "POST") {
        const project = {id:"p3", name:route.request().postDataJSON().name};
        projects.push(project);
        return route.fulfill({json:project});
      }
      return route.fulfill({json:projects});
    }
    const project = projects.find(p => url.pathname === "/api/workspace/projects/" + p.id);
    if (project) return route.fulfill({json:{...project, questions:[], bookmarks:[],
      documents:project.id === "p3" ? [] : [{id:project.id + "-doc", name:"报告.txt"}]}});
    return route.fulfill({json:[]});
  });
  const dialog = page.locator("#workspaceDialog");
  async function openProjects() {
    await page.locator("#openSidebarBtn").click();
    await page.getByRole("button", {name:"我的项目", exact:true}).click();
    await dialog.getByRole("button", {name:"用此文档提问"}).waitFor();
  }
  const attach = dialog.getByRole("button", {name:"用此文档提问"});
  await openProjects();
  await attach.click();
  await openProjects();
  assert.equal(await page.evaluate(() => HomeWorkspace.selected.id), "p1-doc");
  await dialog.getByLabel("当前项目").selectOption("p2");
  await page.waitForFunction(() => !document.querySelector("#workspaceActions").disabled);
  assert.equal(await page.locator(".workspace-attachment").isHidden(), true);
  assert.equal(await page.evaluate(() => Boolean(HomeWorkspace.selected)), false);
  await attach.click();
  assert.equal(await page.evaluate(() => HomeWorkspace.selected.id), "p2-doc");
  await openProjects();
  await dialog.getByLabel("项目名称").fill("项目三");
  await dialog.getByRole("button", {name:"新建项目"}).click();
  await dialog.getByText("已新建项目", {exact:true}).waitFor();
  assert.equal(await page.locator(".workspace-attachment").isHidden(), true);
  assert.equal(await page.evaluate(() => Boolean(HomeWorkspace.selected)), false);
});

test("history explains an analysis without an archived result and still shows its events", async t => {
  const page = await pageFor(t, "/", (route, url) => {
    if (url.pathname === "/api/workspace/runs") return route.fulfill({json:[
      {trace_id:"r1", title:"历史分析", status:"completed"}]});
    if (url.pathname.endsWith("/reference")) return route.fulfill({contentType:"application/json",body:"null"});
    if (url.pathname.endsWith("/events")) return route.fulfill({json:{data:{events:[{message:"执行结束"}]}}});
    if (url.pathname === "/api/tasks") return route.fulfill({json:{data:[]}});
    return route.fulfill({json:[]});
  });
  await page.locator("#openSidebarBtn").click();
  await page.getByRole("button", {name:"分析记录",exact:true}).click();
  await page.getByRole("button", {name:"查看记录"}).click();
  await page.getByText("此记录未保存回答正文或结果明细。").waitFor({timeout:1500});
  assert(await page.getByText("执行结束").isVisible());
});

test("homepage retains zero temperature and opens one knowledge graph per click", async t => {
  const page = await pageFor(t, "/");
  const more = page.locator(".index_con .right .more");
  await more.click();
  const slider = page.locator("#temperatureSlider");
  await slider.focus();
  await slider.press("Home");
  await page.reload();
  await more.click();
  assert.equal(await slider.inputValue(), "0");
  await page.locator("#closeAdvancedPanel").click();
  await page.evaluate(() => {
    window.openedGraphUrls = [];
    window.open = url => { window.openedGraphUrls.push(url); };
  });
  await page.locator(".knowledge-entry").click();
  assert.deepEqual(await page.evaluate(() => window.openedGraphUrls), ["./cadd_interactive_radial.html"]);
});

test("docking history remains reachable at phone and desktop widths", async t => {
  const page = await pageFor(t, "/molecular-docking");
  for (const width of widths) {
    await page.setViewportSize({ width, height: 812 });
    await page.locator("#history-btn").click();
    await page.waitForSelector("#history-panel.open");
    await page.locator(".history-panel-close").click();
    await page.waitForSelector("#history-panel.open", { state: "detached" });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), width);
  }
});

test("docking refresh, offline retry and history takeover follow one submitted job", async t => {
  const task = {task_id: "dock-a", task_type: "docking", phase: "vina_running", status: "running", progress: 0.2};
  const statusPath = "/api/tasks/" + task.task_id;
  let submissions = 0, offline = false, unavailable = false;
  const page = await pageFor(t, "/molecular-docking", (route, url) => {
    if (url.pathname === "/api/docking/tasks" && route.request().method() === "POST") {
      submissions++;
      return route.fulfill({json: {success: true, data: task}});
    }
    if (url.pathname === statusPath) {
      if (offline) return route.continue();
      if (unavailable) return route.fulfill({status: 503, json: {message: "fixture unavailable"}});
      return route.fulfill({json: {success: true, data: task}});
    }
    if (url.pathname === "/api/tasks") return route.fulfill({json: {data: [task]}});
    if (url.pathname === "/api/docking/history") return route.fulfill({json: {success: true, history: []}});
    if (url.pathname === "/api/docking/cavities") return route.fulfill({json: {cavities: []}});
    return route.fulfill({status: 404, json: {}});
  });
  page.setDefaultTimeout(8000);
  await page.locator("#protein-file").setInputFiles({name: "receptor.pdb", mimeType: "text/plain",
    buffer: Buffer.from("ATOM      1  CA  ALA A   1       1.000   0.000   0.000  1.00  0.00           C\nEND\n")});
  await page.locator("#smiles-input").fill("CCO");
  await page.locator("#center_x").fill("1");
  await page.locator("#start-btn").click();
  const progress = percent => page.locator(`#docking-progress[data-progress="${percent}%"]`).waitFor({state: "attached"});
  await progress(20);
  assert.equal(submissions, 1);

  offline = true;
  await page.context().setOffline(true);
  const failedRead = page.waitForEvent("requestfailed", request => new URL(request.url()).pathname === statusPath);
  await page.reload();
  assert.match((await failedRead).failure().errorText, /INTERNET_DISCONNECTED/);
  await page.getByText("连接中断，等待重连…").waitFor();
  assert(await page.locator("#start-btn").isDisabled());
  assert.equal(await page.evaluate(() => sessionStorage.getItem("medchat.docking.active_task_id")), task.task_id);
  task.progress = 0.4;
  offline = false;
  await page.context().setOffline(false);
  await progress(40);
  // A failed status read unlocks the page. Reopen the same job from history.
  unavailable = true;
  await page.locator("#results-content").getByText("fixture unavailable").waitFor();
  unavailable = false;
  task.progress = 0.65;
  task.status = "cancel_requested";
  await page.locator("#history-btn").click();
  await page.getByRole("button", {name: "恢复结果"}).click();
  await progress(65);
  await page.getByText("正在取消…", {exact: true}).waitFor();
  assert(await page.locator("#cancel-docking-btn").isDisabled());
  task.status = "canceled";
  await page.locator("#results-content").getByText("对接任务已取消").waitFor();
  assert(await page.locator("#start-btn").isEnabled());
  assert.equal(await page.evaluate(() => sessionStorage.getItem("medchat.docking.active_task_id")), null);
  assert.equal(new URL(page.url()).searchParams.get("docking_task"), null);
  assert.equal(submissions, 1);
});

test("a late docking history response cannot replace the newer task or restart polling", {timeout: 20000}, async t => {
  const old = {task_id: "old-task", task_type: "docking", status: "running", progress: 0.11};
  const current = {...old, task_id: "current-task", progress: 0.73, phase: "vina_running"};
  const oldRead = Promise.withResolvers();
  const releaseOld = Promise.withResolvers();
  const writes = [];
  let oldReads = 0;
  const page = await pageFor(t, "/molecular-docking", async (route, url) => {
    if (route.request().method() !== "GET") writes.push(url.pathname);
    if (url.pathname === "/api/tasks") return route.fulfill({json: {data: [old, current]}});
    if (url.pathname === "/api/docking/history") return route.fulfill({json: {success: true, history: []}});
    if (url.pathname === "/api/tasks/old-task") {
      oldReads++;
      oldRead.resolve();
      await releaseOld.promise;
      return route.fulfill({json: {data: old}});
    }
    if (url.pathname === "/api/tasks/current-task") return route.fulfill({json: {data: current}});
    return route.fulfill({status: 404, json: {}});
  });
  page.setDefaultTimeout(8000);
  await page.locator("#history-btn").click();
  const card = id => page.locator(".history-card").filter({hasText: id}).getByRole("button", {name: "恢复结果"});
  await card(old.task_id).click();
  await oldRead.promise;
  await card(current.task_id).click();
  await page.locator('#docking-progress[data-progress="73%"]').waitFor({state: "attached"});
  const received = page.waitForResponse(response => new URL(response.url()).pathname === "/api/tasks/old-task");
  releaseOld.resolve();
  await (await received).finished();
  current.progress = 0.78;
  await page.locator('#docking-progress[data-progress="78%"]').waitFor({state: "attached"});
  assert.equal(new URL(page.url()).searchParams.get("docking_task"), current.task_id);
  current.status = "timed_out";
  await page.locator("#results-content").getByText("状态：timed_out").waitFor();
  assert.equal(oldReads, 1);
  assert.deepEqual(writes, []);
});

test("docking history reads legacy poses and restores the durable task's own receptor and best pose", async t => {
  const task = {task_id: "saved-dock", task_type: "docking", status: "running", progress: 0.5,
    result: {data: {result_job_id: "original-worker-job", total_poses: 2, best_pose: {pose: 2, binding_energy: -7}}}};
  const legacy = {job_id: "legacy-dock", has_result: true, status: "completed", pose_count: 2};
  const poses = "REMARK VINA RESULT: -6.0 0.0 0.0\nREMARK VINA RESULT: -7.0 0.0 0.0\n";
  const receptor = "ATOM      1  CA  ALA A   1       2.000   0.000   0.000  1.00  0.00           C\nEND\n";
  const ligand = "saved-dock pose 2\n  test\n\n  1  0  0  0  0  0            999 V2000\n    4.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\nM  END\n";
  const reads = [];
  const page = await pageFor(t, "/molecular-docking", (route, url) => {
    reads.push([route.request().method(), url.pathname + url.search]);
    switch (url.pathname) {
      case "/api/tasks": return route.fulfill({json: {data: [task]}});
      case "/api/docking/history": return route.fulfill({json: {success: true, history: [legacy]}});
      case "/api/tasks/saved-dock": return route.fulfill({json: {data: task}});
      case "/api/docking/result/legacy-dock":
      case "/api/docking/result/saved-dock": return route.fulfill({body: poses});
      case "/api/docking/result/saved-dock/summary":
        return route.fulfill({json: {results: [{pose: 1, binding_energy: -6}, {pose: 2, binding_energy: -7}]}});
      case "/api/docking/tasks/saved-dock/receptor":
        return route.fulfill({body: receptor, headers: {"X-Receptor-Format": "pdb"}});
      case "/api/docking/pose_sdf/saved-dock":
        if (url.search === "?pose=2") return route.fulfill({body: ligand});
    }
    return route.fulfill({status: 404, json: {}});
  });
  page.setDefaultTimeout(8000);
  await page.locator("#history-btn").click();
  await page.getByRole("button", {name: /加载结果/}).click();
  await page.locator("#results-content").getByText("Pose 2", {exact: true}).waitFor();
  assert(await page.locator("#pose-select").isDisabled());
  assert.equal(await page.locator("#results-content").getByRole("button", {name: "查看 Pose 2"}).count(), 0);

  await page.locator("#history-btn").click();
  await page.getByRole("button", {name: "恢复结果"}).click();
  await page.locator('#docking-progress[data-progress="50%"]').waitFor({state: "attached"});
  task.status = "succeeded";
  await page.waitForFunction(expected => AppState.dockedLigandData === expected, ligand);
  assert.equal(await page.evaluate(() => receptorForView(AppState.currentJobId).data), receptor);
  assert.equal(await page.locator("#pose-select").inputValue(), "2");
  assert(await page.locator("#pose-select").isEnabled());
  assert(reads.every(([method, path]) => method === "GET" && !path.includes("original-worker-job")));
});

test("model list reports failure and its controls fit a long model name", async t => {
  let available = false;
  const page = await pageFor(t, "/activity-prediction", (route, url) => {
    if (available && url.pathname === "/api/activity/models") {
      return route.fulfill({ json: { success: true, current_model: "fixture",
        models: [
          { model_id: "first", name: "同名模型" },
          { model_id: "fixture", name: "test-model-".repeat(20) },
        ] } });
    }
    return route.fulfill({ status: 503, json: { detail: "服务暂不可用" } });
  });
  await page.waitForFunction(() => document.querySelector("#modelSelector").textContent.includes("加载失败"));
  assert(await page.locator("#deleteModelBtn").isHidden());
  available = true;
  await page.reload();
  await page.locator('#modelSelector option[value="fixture"]').waitFor({state:"attached"});
  assert.equal(await page.locator("#modelSelector").inputValue(), "fixture");
  for (const width of widths) {
    await page.setViewportSize({ width, height: 812 });
    await page.locator("#modelInfoBtn").click();
    for (const selector of ["#modelInfoBtn", "#modelSelector", "#deleteModelBtn", "#modelInfoPopover", ".back-btn"]) {
      const box = await page.locator(selector).boundingBox();
      assert(box && box.x >= -1 && box.x + box.width <= width + 1, selector + " at " + width);
    }
    await page.locator("#modelInfoBtn").click();
    assert(await page.locator("#modelInfoPopover").isHidden());
  }
});

test("knowledge graph keeps topic details available on mobile when its CDN is offline", async t => {
  const page = await pageFor(t, "/cadd_interactive_radial.html");
  assert.equal(await page.evaluate(() => innerWidth), 320);
  assert.match(await page.locator("#mynetwork").innerText(), /加载失败/);
  assert(await page.locator("#reset-btn").isDisabled());
  const topic = page.locator(".catalog-topic").first();
  if (!await topic.isVisible()) await page.locator("#catalog-toggle").click();
  await topic.click();
  assert(await page.locator("#detail-modal").isVisible());
  await page.locator("#detail-close").click();
  assert(await page.locator("#detail-modal").isHidden());
});

test("a submitted training job survives refresh and offline retries with one POST", async t => {
  let submissions = 0;
  let offline = false;
  const statusPath = "/api/activity/train/status/fixture";
  const failedReads = [];
  let state = "running";
  const page = await pageFor(t, "/activity-prediction", (route, url) => {
    if (url.pathname === "/api/activity/train" && route.request().method() === "POST") {
      submissions++;
      return route.fulfill({json: {success: true, job_id: "fixture"}});
    }
    if (url.pathname === statusPath && route.request().method() === "GET") {
      if (offline) return route.continue();
      return route.fulfill({json:{success:true,status:{state,epoch:2,progress:50}}});
    }
    return route.fulfill({status:503,json:{detail:"服务暂不可用"}});
  });
  page.on("requestfailed", request => {
    if (new URL(request.url()).pathname === statusPath) failedReads.push(request.failure().errorText);
  });
  await page.locator('[onclick*="switchTab"][onclick*="train"]').click();
  await page.locator("#trainFile").setInputFiles({
    name: "training.csv", mimeType: "text/csv", buffer: Buffer.from("smiles,label\nCCO,1\n"),
  });
  await page.locator('[onclick="confirmPreflight()"]').click();
  await page.locator("#epochs").fill("4");
  await page.locator("#trainBtn").click();
  await page.locator("#confirmLaunchBtn").click();
  await page.waitForFunction(() => JSON.parse(localStorage.getItem("activeTrainJobId"))?.jobId === "fixture");
  const saved = await page.evaluate(() => localStorage.getItem("activeTrainJobId"));
  assert.equal(submissions, 1);
  offline = true;
  await page.context().setOffline(true);
  await page.reload();
  await page.getByText("状态暂不可用，正在重试").waitFor();
  await page.waitForEvent("requestfailed", request => new URL(request.url()).pathname === statusPath);
  assert(failedReads.length >= 2 && failedReads.every(error => error.includes("INTERNET_DISCONNECTED")));
  assert.equal(await page.evaluate(() => localStorage.getItem("activeTrainJobId")), saved);
  assert(await page.locator("#trainBtn").isDisabled());
  await page.context().setOffline(false);
  offline = false;
  await page.waitForFunction(() => document.querySelector("#dock-epoch-text").textContent === "Epoch 2/4");
  assert(await page.locator("#trainBtn").isDisabled());
  await page.locator(".dock-view-btn").click();
  await page.locator('[onclick="toggleDrawerFull()"]').click();
  assert(await page.locator("#train-drawer").evaluate(el => el.classList.contains("fullscreen")));
  await page.locator('[onclick="minimizeDrawer()"]').click();
  state = "failed";
  await page.waitForFunction(() => localStorage.getItem("activeTrainJobId") === null);
  assert(await page.locator("#trainBtn").isEnabled());
  assert.equal(submissions, 1);
});

for (const firstRead of [true, false]) {
  for (const [end, refreshLabel, pollLabel] of [
    ["completed", "Idle", "Model Ready"],
    ["failed", "Idle", "Failed"],
    ["canceled", "训练已取消", "训练已取消"],
    ["missing", "Idle", "Idle"],
  ]) {
    test(`training ${firstRead ? "refresh" : "poll"} handles ${end} with the existing model selected`, async t => {
      const statusReads = [];
      let modelReads = 0;
      const writes = [];
      const model = {model_id: "saved-model", name: "已保存模型", task_type: "regression", target: "label"};
      const page = await pageFor(t, "/activity-prediction", (route, url) => {
        if (route.request().method() !== "GET") writes.push(url.pathname);
        if (url.pathname === "/api/activity/models") {
          modelReads++;
          return route.fulfill({json: {success: true, models: [model], current_model: model.model_id}});
        }
        if (url.pathname === "/api/activity/train/status/legacy-job") {
          const state = firstRead || statusReads.length ? end : "running";
          statusReads.push(state);
          if (state === "missing") return route.fulfill({status: 404, json: {}});
          return route.fulfill({json: {success: true, status: {
            state, epoch: 2, best_epoch: 2, logs: ["epoch 2"], error: "fixture error",
          }}});
        }
        return route.fulfill({status: 503, json: {}});
      });
      await page.locator('#modelSelector option[value="saved-model"]').waitFor({state: "attached"});
      await page.evaluate(() => localStorage.setItem("activeTrainJobId", JSON.stringify({jobId: "legacy-job", totalEpochs: 4})));
      await page.reload();
      await page.waitForFunction(() => localStorage.getItem("activeTrainJobId") === null);
      const completedLive = !firstRead && end === "completed";
      assert.equal(await page.locator("#trainBtn").textContent(), "🚀 重新训练");
      assert(await page.locator("#trainBtn").isEnabled());
      assert.equal(await page.locator("#train-dock").isVisible(), !firstRead || end === "canceled");
      assert.equal(await page.locator("#statusStat").textContent(), firstRead ? refreshLabel : pollLabel);
      assert.equal(await page.locator("#trainResultArea").evaluate(el => getComputedStyle(el).display !== "none"), completedLive);
      if (completedLive) assert.equal(await page.locator("#resBestEpoch").textContent(), "2");
      if (end === "canceled") assert.equal(await page.locator("#d-status").textContent(), "训练已取消");
      if (!firstRead && end === "missing") assert.equal(await page.locator("#dock-status-text").textContent(), "任务不存在");
      if (!firstRead && end === "failed") assert.match(await page.locator("#trainLog").textContent(), /epoch 2\s+\[Error\] fixture error/);
      assert.deepEqual(statusReads, firstRead ? [end] : ["running", end]);
      assert.equal(modelReads, completedLive ? 3 : 2);
      assert.equal(await page.locator("#modelSelector").inputValue(), model.model_id);
      assert.deepEqual(writes, []);
    });
  }
}

test("training file preflight, confirmation and failed submission stay usable at 320px", async t => {
  let posts = 0;
  const page = await pageFor(t, "/activity-prediction", route => {
    if (route.request().method() === "POST") posts++;
    return route.fulfill({status:503, json:{detail:"测试服务暂不可用"}});
  });
  const dialogs = [];
  page.on("dialog", async dialog => { dialogs.push(dialog.message()); await dialog.dismiss(); });
  await page.locator('[onclick*="switchTab"][onclick*="train"]').click();
  const file = page.locator("#trainFile");
  await file.setInputFiles({name:"empty.csv", mimeType:"text/csv", buffer:Buffer.from("")});
  await page.getByText(/文件行数不足/).waitFor();
  assert.equal(posts, 0);
  await file.setInputFiles({name:"fixture.csv", mimeType:"text/csv", buffer:Buffer.from("smiles,label\nCCO,1\n")});
  await page.locator('[onclick="confirmPreflight()"]').click();
  assert.equal(await page.locator("#targetColumn").inputValue(), "label");
  const launch = page.locator("#trainBtn");
  await launch.click();
  await page.locator('#launch-modal [onclick="closeLaunchModal()"]').last().click();
  assert(await page.locator("#launch-modal").isHidden());
  await launch.click();
  await page.locator("#confirmLaunchBtn").click();
  await page.waitForFunction(() => !document.querySelector("#trainBtn").disabled);
  assert.equal(posts, 1);
  assert.match(dialogs.at(-1), /训练错误/);
});

test("structure actions accept one request per double click and recover after failure", async t => {
  const target = { target_id: 1, gene_symbol: "TEST" };
  const structure = { id: 2, file_format: "cif" };
  let preflights = 0;
  let detailRequests = 0;
  const page = await pageFor(t, "/target-search", async (route, url) => {
    if (url.pathname.endsWith("/search")) {
      return route.fulfill({ json: { query: "TEST", results: [target] } });
    }
    if (url.pathname.endsWith("/targets/1")) {
      detailRequests++;
      return route.fulfill({ json: { ...target, structures: [structure] } });
    }
    if (url.pathname.endsWith("/preflight")) {
      preflights++;
      await new Promise(resolve => setTimeout(resolve, 250));
    }
    return route.fulfill({ status: 503, json: { detail: "测试结构不可用" } });
  });
  await page.getByRole("button", { name: "下载 PDB" }).waitFor();
  for (const button of [
    page.locator("#targetResults").getByRole("button", { name: "下载推荐" }),
    page.locator("#targetDetail").getByRole("button", { name: "下载推荐" }),
    page.getByRole("button", { name: "下载 PDB" }),
    page.getByRole("button", { name: "下载 mmCIF" }),
  ]) {
    const before = preflights;
    await button.dblclick();
    assert(await button.isDisabled());
    await page.waitForFunction(() => document.querySelector("#targetMessage").textContent.includes("测试结构不可用"));
    assert(await button.isEnabled());
    assert.equal(preflights, before + 1);
  }
  assert.equal(detailRequests, 2);
});

test("PDB handoff retries offline, prepares the selected receptor once and preserves direct links", {timeout: 30000}, async t => {
  const target = {target_id: 1, gene_symbol: "TEST"};
  const structures = [{id: 21, structure_id: "1AAA"}, {id: 32, structure_id: "2BBB"}];
  const pdb = id => `HEADER    RECEPTOR ${id}\nATOM      1  CA  ALA A   1       ${id === 21 ? "1" : "2"}.000   0.000   0.000  1.00  0.00           C\nEND\n`;
  const reads = [], creates = [];
  const prepared = Promise.withResolvers();
  let offline = false;
  const page = await pageFor(t, "https://medchat.test/target-search", async (route, url) => {
    if (url.pathname.endsWith("/search")) return route.fulfill({json: {query: "TEST", results: [target]}});
    if (url.pathname.endsWith("/targets/1")) return route.fulfill({json: {...target, structures}});
    if (["/api/docking/tasks", "/api/docking/batch_submit"].includes(url.pathname) && route.request().method() === "POST") {
      creates.push(url.pathname);
    }
    const match = url.pathname.match(/^\/api\/target-db\/structures\/(\d+)\/(.+)$/);
    if (match) {
      const structure = structures.find(s => s.id === Number(match[1]));
      reads.push([route.request().method(), structure.id, match[2], url.searchParams.get("format")]);
      const preflight = {...structure, can_download: true, cache_status: "available", docking_grade: "A"};
      if (match[2] === "send-to-docking") {
        if (offline) return route.continue();
        await prepared.promise;
        return route.fulfill({json: {structure_db_id: structure.id, file_format: "pdb", preflight}});
      }
      if (match[2] === "preflight") return route.fulfill({json: preflight});
      if (match[2] === "download") return route.fulfill({body: pdb(structure.id)});
    }
    if (url.pathname === "/api/docking/cavities") return route.fulfill({json: {cavities: []}});
    return route.fulfill({status: 404, json: {}});
  });
  page.setDefaultTimeout(6000);
  async function verifyReceptor(structure) {
    const expected = pdb(structure.id);
    await page.waitForFunction(text => AppState.currentProteinData === text, expected);
    const file = await page.locator("#protein-file").evaluate(async input => {
      const receptor = input.files[0];
      const digest = await crypto.subtle.digest("SHA-256", await receptor.arrayBuffer());
      return {name: receptor.name, hash: Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("")};
    });
    assert.deepEqual(file, {name: `target-${structure.structure_id}.pdb`, hash: createHash("sha256").update(expected).digest("hex")});
  }
  const launch = page.locator(".structure-item").filter({hasText: "2BBB"}).getByRole("button", {name: "准备并打开分子对接"});
  await launch.waitFor();
  offline = true;
  await page.context().setOffline(true);
  const failed = page.waitForEvent("requestfailed", request => request.url().includes("/send-to-docking"));
  await launch.click();
  assert.match((await failed).failure().errorText, /INTERNET_DISCONNECTED/);
  await page.waitForFunction(() => document.querySelector("#targetMessage").classList.contains("error"));
  assert(await launch.isEnabled());
  assert.equal(new URL(page.url()).pathname, "/target-search");
  offline = false;
  await page.context().setOffline(false);
  try {
    await launch.dblclick();
    assert(await launch.isDisabled());
  } finally {
    prepared.resolve();
  }
  await page.waitForURL("**/molecular-docking*");
  await verifyReceptor(structures[1]);
  const prepare = ["POST", 32, "send-to-docking", "pdb"];
  assert.deepEqual(reads, [prepare, prepare, ["GET", 32, "preflight", "pdb"], ["GET", 32, "download", "pdb"]]);
  await page.reload();
  assert.equal(reads.length, 4);
  for (const key of ["target_structure", "target_structure_id"]) {
    await page.goto(`https://medchat.test/molecular-docking?${key}=21&keep=yes#viewer`);
    await verifyReceptor(structures[0]);
    assert.equal(new URL(page.url()).search + new URL(page.url()).hash, "?keep=yes#viewer");
  }
  assert.deepEqual(reads.slice(4), Array(2).fill([["GET", 21, "preflight", "pdb"], ["GET", 21, "download", "pdb"]]).flat());
  assert.deepEqual(creates, []);
});

test("a failed receptor link survives refresh until its file has been read", async t => {
  const link = "https://medchat.test/molecular-docking?target_structure_id=32&keep=yes#viewer";
  const pdb = "HEADER    2BBB\nEND\n";
  const submissions = [];
  let failDownload = true;
  const page = await pageFor(t, link, (route, url) => {
    if (url.pathname === "/api/target-db/structures/32/preflight") return route.fulfill({json: {can_download: true, structure_id: "2BBB"}});
    if (url.pathname === "/api/target-db/structures/32/download") {
      return failDownload ? route.abort("internetdisconnected") : route.fulfill({body: pdb});
    }
    if (url.pathname === "/api/docking/cavities") return route.fulfill({json: {cavities: []}});
    if (route.request().method() === "POST") submissions.push(url.pathname);
    return route.fulfill({status: 404, json: {}});
  });
  await page.getByText(/靶点结构未能自动载入/).waitFor();
  assert.equal(page.url(), link);
  failDownload = false;
  await page.addInitScript(() => {
    const text = File.prototype.text;
    File.prototype.text = async function () {
      if (this.name === "target-2BBB.pdb") {
        await new Promise((resolve, reject) => {
          window.finishRead = resolve;
          window.failRead = () => reject(new Error("read failed"));
        });
      }
      return text.call(this);
    };
  });
  await page.reload();
  await page.waitForFunction(() => !!window.failRead);
  assert.equal(await page.getByText(/已加载 2BBB/).count(), 0);
  assert.equal(page.url(), link);
  await page.evaluate(() => window.failRead());
  await page.getByText(/靶点结构未能自动载入/).waitFor();
  assert.equal(page.url(), link);
  await page.reload();
  await page.waitForFunction(() => !!window.finishRead);
  await page.evaluate(() => window.finishRead());
  await page.getByText(/已加载 2BBB/).waitFor();
  assert.equal(await page.evaluate(() => AppState.currentProteinData), pdb);
  assert.equal(new URL(page.url()).search + new URL(page.url()).hash, "?keep=yes#viewer");
  await page.reload();
  assert.deepEqual(submissions, []);
});

test("manual receptor selection supersedes a slow demo and preserves the demo ligand and cavity workflow on retry", async t => {
  const samples = path.resolve(__dirname, "../data/samples");
  const receptorName = "PDE4A_apo_6FG5.pdb";
  const ligandName = "roflumilast.pdbqt";
  const manual = "HEADER    MANUAL\nEND\n";
  const cavities = [];
  const submits = [];
  const page = await pageFor(t, "https://medchat.test/molecular-docking", (route, url) => {
    if (url.pathname.endsWith("/demo/pde4a-apo")) return route.fulfill({path: path.join(samples, receptorName)});
    if (url.pathname.endsWith("/demo/roflumilast.pdbqt")) return route.fulfill({path: path.join(samples, ligandName)});
    if (url.pathname === "/api/docking/cavities") {
      const form = route.request().postDataBuffer().toString();
      cavities.push(form.includes(receptorName) ? receptorName : "manual.pdb");
      return route.fulfill({json: {cavities: [{volume: 100, center: [3, 4, 5], size: [16, 18, 20]}]}});
    }
    if (route.request().method() === "POST") submits.push(url.pathname);
    return route.fulfill({status: 404, json: {}});
  });
  await page.evaluate(name => {
    const text = File.prototype.text;
    File.prototype.text = async function () {
      if (this.name === name && !window.releaseDemoRead) {
        await new Promise(resolve => { window.releaseDemoRead = resolve; });
      }
      return text.call(this);
    };
  }, receptorName);
  await page.locator("#center_x").fill("12");
  await page.locator("#smiles-input").fill("CCO");
  await page.locator("#docking-demo-btn").click();
  await page.waitForFunction(() => !!window.releaseDemoRead);
  await page.locator("#protein-file").setInputFiles({name: "manual.pdb", mimeType: "chemical/x-pdb", buffer: Buffer.from(manual)});
  await page.waitForFunction(text => AppState.currentProteinData === text, manual);
  await page.evaluate(() => window.releaseDemoRead());
  await page.waitForFunction(() => !document.getElementById("docking-demo-btn").disabled);
  assert.equal(await page.evaluate(() => AppState.currentProteinData), manual);
  assert.equal(await page.locator("#protein-file").evaluate(input => input.files[0].name), "manual.pdb");
  assert.equal(await page.locator("#ligand-file").evaluate(input => input.files.length), 0);
  assert.equal(await page.locator("#smiles-input").inputValue(), "CCO");
  assert.equal(await page.locator("#center_x").inputValue(), "12");
  assert.equal(await page.getByText(/已载入曼氏血吸虫/).count(), 0);

  await page.locator("#docking-demo-btn").click();
  await page.getByText(/已载入曼氏血吸虫/).waitFor();
  for (const [selector, name] of [["#protein-file", receptorName], ["#ligand-file", ligandName]]) {
    const actual = await page.locator(selector).evaluate(async input => {
      const file = input.files[0];
      const hash = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
      return [file.name, Array.from(new Uint8Array(hash), b => b.toString(16).padStart(2, "0")).join("")];
    });
    assert.deepEqual(actual, [name, createHash("sha256").update(fs.readFileSync(path.join(samples, name))).digest("hex")]);
  }
  assert.equal(await page.evaluate(() => AppState.currentProteinData), fs.readFileSync(path.join(samples, receptorName), "utf8"));
  assert.equal(await page.locator("#smiles-input").inputValue(), "");
  assert.deepEqual(await page.evaluate(() => ["exhaustiveness", "num_modes", "energy_range"].map(id => document.getElementById(id).value)), ["24", "20", "3"]);
  await page.locator("#cavity-choice").selectOption("0");
  assert.deepEqual(await page.evaluate(() => ["center_x", "center_y", "center_z", "size_x", "size_y", "size_z"].map(id => Number(document.getElementById(id).value))), [3, 4, 5, 16, 18, 20]);
  assert.deepEqual(cavities, ["manual.pdb", receptorName]);
  assert.deepEqual(submits, []);
});

async function reverseTargetCsv(page) {
  const [download] = await Promise.all([page.waitForEvent("download"), page.locator("#exportBtn").click()]);
  const chunks = [];
  for await (const chunk of await download.createReadStream()) chunks.push(chunk);
  return Buffer.concat(chunks);
}

for (const first of ["single", "batch"]) {
  for (const [late, state] of [["success", "results"], ["failure", "loading"], ["success", "error"], ["failure", "error"]]) {
    test(`reverse target ignores late ${first} ${late} while the current tab shows ${state}`, async t => {
      const requests = [];
      const gates = {single: Promise.withResolvers(), batch: Promise.withResolvers()};
      const page = await pageFor(t, "https://medchat.test/reverse-target", async (route, url) => {
        const mode = {predict_3d: "single", batch_predict: "batch"}[url.pathname.split("/").pop()];
        if (!mode) return route.fulfill({status: 404, json: {}});
        requests.push(mode);
        await gates[mode].promise;
        if (mode === first && late === "failure") return route.fulfill({status: 503, json: {detail: "obsolete failure"}});
        if (mode !== first && state === "error") return route.fulfill({status: 503, json: {detail: "current failure"}});
        const row = {target_name: mode + "-row", final_similarity: 0};
        return route.fulfill({json: {results: mode === "single" ? [row] : [{success: true, query_smiles: "CCN", targets: [row]}]}});
      });
      await page.setViewportSize({width: 1280, height: 900});
      async function submit(mode) {
        await page.locator(`.tab[data-tab="${mode}"]`).click();
        if (mode === "single") await page.locator("#smiles").fill("CCO");
        else await page.locator("#batchFile").setInputFiles({name: "input.csv", mimeType: "text/csv", buffer: Buffer.from("smiles\nCCN\n")});
        const request = page.waitForRequest(r => r.method() === "POST");
        await page.locator(mode === "single" ? "#submitBtn" : "#batchSubmitBtn").click();
        await request;
      }
      const current = first === "single" ? "batch" : "single";
      await submit(first);
      await submit(current);
      // Let the old finally block run while the newer request is loading,
      // or after its results are already visible.
      if (state === "error") {
        gates[current].resolve();
        await page.getByText("current failure", {exact: true}).waitFor();
      } else if (state === "results") {
        gates[current].resolve();
        await page.getByText(current + "-row", {exact: true}).waitFor();
      }
      const oldResponse = page.waitForResponse(r => r.url().endsWith(first === "single" ? "/predict_3d" : "/batch_predict"));
      gates[first].resolve();
      await (await oldResponse).finished();
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      assert.deepEqual(requests, [first, current]);
      assert.equal(await page.locator(".tab.active").getAttribute("data-tab"), current);
      if (state === "error") {
        assert(await page.locator("#error").isVisible());
        assert.equal(await page.locator("#errorText").textContent(), "current failure");
        assert.equal(await page.locator("#results").isVisible(), false);
        assert.equal(await page.locator("#loading").isVisible(), false);
        return;
      }
      assert.equal(await page.locator("#error").isVisible(), false);
      if (state === "loading") {
        assert(await page.locator("#loading").isVisible());
        assert.equal(await page.locator("#results").isVisible(), false);
        gates[current].resolve();
        await page.getByText(current + "-row", {exact: true}).waitFor();
      }
      assert.equal(await page.locator("#loading").isVisible(), false);
      assert.match(await page.locator("#resultsBody").innerText(), new RegExp(current + "-row"));
      assert.match((await reverseTargetCsv(page)).toString("utf8"), new RegExp(current + "-row"));
    });
  }
}

test("reverse target keeps ranks, details, CSV bytes and uploaded inputs with their own query and session", async t => {
  const header = "row_index,original_row_index,query_smiles,status,error,pharm_refinement_status,pharm_error,target_name,organism,molecule_chembl_id,standard_type,standard_relation,standard_value,standard_units,measurement_status,similar_count,morgan_similarity,maccs_similarity,final_similarity,final_3d_score,alignment_score,alignment_rmsd,alignment_coverage";
  const columns = header.split(",");
  function expectedCsv(records) {
    const lines = records.map(row => columns.map(key => '"' + String(row[key] ?? "").replaceAll('"', '""') + '"').join(","));
    return Buffer.from("\ufeff" + [header, ...lines].join("\n"));
  }
  const ranked = Array.from({length: 12}, (_, i) => ({target_name: "Rank " + (i + 1), final_similarity: (12 - i) / 12}));
  const refined = ranked.slice(0, 10).map(row => ({...row, pharm_refinement_status: "refined", final_3d_score: 0.5}));
  const fallback = ranked.slice(10).map(row => ({...row, pharm_refinement_status: "failed", pharm_error: "test fallback"}));
  const batchTarget = {target_name: 'Example, "quoted"', standard_value: 0, standard_units: "μM", standard_relation: "<", final_similarity: 0};
  const rows = [
    {row_index: 1, original_row_index: 2, query_smiles: "CCN", success: true, targets: [batchTarget]},
    {row_index: 2, original_row_index: 4, query_smiles: "CCN", success: true, targets: [], status: "no_match", error: "no match"},
    {row_index: 3, original_row_index: 5, query_smiles: "bad", success: false, status: "failed", error: 'bad, "input"'},
  ];
  const input = Buffer.from("smiles\r\nCCN\r\n\r\nCCN\r\nbad\r\n");
  const details = [], posts = [], uploads = [];
  async function respond(route, url) {
    const endpoint = url.pathname.split("/").pop();
    if (route.request().method() === "POST") posts.push(endpoint);
    if (endpoint === "predict") return route.fulfill({json: {results: ranked}});
    if (endpoint === "predict_3d") return route.fulfill({json: {results: refined, fallback_results: fallback, prefilter_threshold: 0.3}});
    if (endpoint === "batch_predict") {
      const request = route.request();
      const multipart = new Response(request.postDataBuffer(), {headers: {"content-type": request.headers()["content-type"]}});
      uploads.push(Buffer.from(await (await multipart.formData()).get("file").arrayBuffer()));
      return route.fulfill({json: {results: rows}});
    }
    if (endpoint === "similar_molecules") {
      details.push(Object.fromEntries(url.searchParams));
      return route.fulfill({json: {results: []}});
    }
    return route.fulfill({status: 404, json: {}});
  }
  const url = "https://medchat.test/reverse-target";
  const single = await pageFor(t, url, respond);
  const other = await pageFor(t, url, respond);
  for (const page of [single, other]) await page.setViewportSize({width: 1280, height: 900});
  await other.evaluate(() => { RT_CONFIG.IS_PHARM_3D_MODE = false; });
  async function checkSingle(page, smiles, records) {
    await page.locator("#smiles").fill(smiles);
    await page.locator("#submitBtn").click();
    await page.locator("#results.show").waitFor();
    const titles = page.locator("#resultsBody .rt-target-title");
    assert.deepEqual(await titles.allTextContents(), records.slice(0, 10).map(r => r.target_name));
    await page.getByRole("button", {name: "下一页"}).click();
    assert.deepEqual(await titles.allTextContents(), records.slice(10).map(r => r.target_name));
    assert.deepEqual(await page.locator(".rt-rank-cell").allTextContents(), ["#11", "#12"]);
    // Editing the form must not change details or exports for the displayed result.
    await page.locator("#smiles").fill("CCC");
    await page.locator("#threshold").fill("0.9");
    await page.locator("#humanOnly").uncheck();
    await page.locator("#resultsBody button").first().click();
    await page.getByText("暂无更多详细数据").waitFor();
    await page.locator("#similarModal .modal-close").click();
    const csv = await reverseTargetCsv(page);
    assert.deepEqual(csv, expectedCsv(records.map(row => ({...row, query_smiles: smiles, status: "completed"}))));
    return csv;
  }
  await checkSingle(single, "CO", refined.concat(fallback));
  assert.match(await single.locator("#resultsBody").innerText(), /2D fallback/);
  const otherCsv = await checkSingle(other, "CCO", ranked);
  await single.locator('.tab[data-tab="batch"]').click();
  await single.locator("#batchFile").setInputFiles({name: "duplicates.csv", mimeType: "text/csv", buffer: input});
  await single.locator("#batchThreshold").fill("0");
  await single.locator("#batchHumanOnly").uncheck();
  await single.locator("#batchSubmitBtn").click();
  await single.locator("#results.show").waitFor();
  await single.locator("#batchThreshold").fill("0.8");
  await single.locator("#resultsBody button").first().click();
  await single.getByText("暂无更多详细数据").waitFor();
  await single.locator("#similarModal .modal-close").click();
  assert.deepEqual(details, [
    {smiles: "CO", target_name: "Rank 11", threshold: "0.3", limit: "50", organism_filter: "Human"},
    {smiles: "CCO", target_name: "Rank 11", threshold: "0.5", limit: "50", organism_filter: "Human"},
    {smiles: "CCN", target_name: batchTarget.target_name, threshold: "0", limit: "50"},
  ]);
  assert.deepEqual(await reverseTargetCsv(single), expectedCsv([{...rows[0], ...batchTarget, status: "completed"}, rows[1], rows[2]]));
  assert.deepEqual(uploads, [input]);
  const fileHash = await single.locator("#batchFile").evaluate(async el => {
    const hash = await crypto.subtle.digest("SHA-256", await el.files[0].arrayBuffer());
    return Array.from(new Uint8Array(hash), b => b.toString(16).padStart(2, "0")).join("");
  });
  assert.equal(fileHash, createHash("sha256").update(input).digest("hex"));
  assert.deepEqual(await reverseTargetCsv(other), otherCsv);
  assert.deepEqual(posts, ["predict_3d", "predict", "batch_predict"]);
});

test("ADMET ignores pending, unreadable and superseded file selections", async t => {
  const posts = [];
  const page = await pageFor(t, "https://medchat.test/admet", (route, url) => {
    if (url.pathname === "/api/admet/predict") {
      posts.push(route.request().postDataJSON().smiles);
      return route.fulfill({json: {status: "unavailable", data: []}});
    }
    return route.fulfill({status: 404, body: ""});
  });
  await page.evaluate(() => {
    const read = File.prototype.text;
    File.prototype.text = async function () {
      if (this.name === "slow.smi") {
        await new Promise((resolve, reject) => {
          window.finishFile = fail => fail ? reject(new Error("old file error")) : resolve();
        });
      }
      return read.call(this);
    };
  });
  const file = page.locator("#structure-file");
  const choose = (name, data) => file.setInputFiles({name, mimeType: "text/plain", buffer: Buffer.from(data)});
  const checkBlocked = async () => {
    // Exercise the form handler even if the submit control is disabled.
    await page.evaluate(async () => {
      document.querySelector("#admet-form").requestSubmit();
      document.querySelector("#rerun-admet").click();
      await window.MedChatADMET.submit();
    });
    assert.equal(await page.locator("#run-admet").isDisabled(), true);
    assert.equal(await page.locator("#rerun-admet").isDisabled(), true);
    assert.equal(await page.locator("#structure-preview").isVisible(), false);
    assert.deepEqual(posts, []);
  };
  async function startSlow() {
    await page.evaluate(() => { delete window.finishFile; });
    await choose("slow.smi", "CN old");
    await page.waitForFunction(() => !!window.finishFile);
  }
  async function release(fail) {
    await page.evaluate(async fail => {
      window.finishFile(fail);
      await new Promise(r => setTimeout(r, 0));
    }, fail);
  }
  const preview = page.locator("#structure-preview-image");
  const previewSmiles = async () => new URL(await preview.getAttribute("src"), page.url()).searchParams.get("smiles");
  await page.locator("#smiles-input").fill("CCO");
  await page.locator("#file-mode").click();
  await startSlow();
  await checkBlocked();
  await choose("empty.csv", "smiles\n");
  await page.getByText("文件中没有可读取的 SMILES", {exact: true}).first().waitFor();
  await checkBlocked();
  await release(false);
  await checkBlocked();

  await startSlow();
  await choose("current.smi", "CCN second");
  await page.waitForFunction(() => !document.getElementById("run-admet").disabled);
  await release(true);
  assert.equal(await previewSmiles(), "CCN");
  assert.equal(await page.locator("#file-name").textContent(), "current.smi");
  assert.match(await page.locator("#file-status").textContent(), /已读取/);

  await startSlow();
  await page.locator("#smiles-mode").click();
  await release(false);
  assert.equal(await page.locator("#smiles-input").inputValue(), "CCO");
  assert.equal(await previewSmiles(), "CCO");

  await page.evaluate(() => { delete window.finishFile; });
  await page.locator("#file-mode").click();
  await page.waitForFunction(() => !!window.finishFile);
  await page.locator("#reset-admet").click();
  await release(false);
  assert.equal(await page.locator("#smiles-input").inputValue(), "");
  assert.equal(await file.evaluate(el => el.files.length), 0);
  assert.equal(await page.locator("#structure-preview").isVisible(), false);
  assert.equal(await page.locator("#results-panel").getAttribute("data-state"), "not_calculated");
  assert.deepEqual(posts, []);
});

test("ADMET evaluates the active input once and keeps file bytes, model evidence and sessions separate", async t => {
  const raw = Buffer.from('\ufeffname,canonical_smiles\r\n"first, ""quoted""",CCN\r\nsecond,CN\r\n');
  async function openSession(tag) {
    const posts = [], previews = [];
    const page = await pageFor(t, "https://medchat.test/admet", (route, url) => {
      if (url.pathname === "/api/utils/smiles_to_image") {
        previews.push(url.searchParams.get("smiles"));
        return route.fulfill({contentType: "image/svg+xml", body: '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>'});
      }
      if (url.pathname !== "/api/admet/predict") return route.fulfill({status: 404});
      const payload = route.request().postDataJSON();
      posts.push(payload);
      // Synthetic transport values for checking the renderer, not model output.
      const provenance = {tool_name: "admet_predictor", model_name: tag, model_version: "fixture-v1"};
      return route.fulfill({json: {status: "succeeded", provenance, data: [{
        ...payload, status: "succeeded", admet: {prediction_method: "admet_ai", model_name: tag,
          model_version: "fixture-v1", weights_id: "fixture-weights-" + tag, demo_mode: false, fallback_used: false,
          endpoints: {fixture: {value: 0, unit: "fixture-unit", source: "admet_ai_model"}}},
      }]}});
    });
    return {page, posts, previews};
  }
  async function evaluate(session, button, smiles, expectedCount) {
    const {page, posts, previews} = session;
    await page.locator(button).dblclick({delay: 120});
    await page.waitForFunction(s => document.querySelector(".smiles-record")?.textContent === s &&
      document.getElementById("results-panel").dataset.state === "succeeded", smiles);
    assert.equal(posts.length, expectedCount);
    assert.deepEqual(posts.at(-1), {smiles, molecule_id: "molecule-001"});
    await page.waitForFunction(expected => {
      const image = document.getElementById("structure-preview-image");
      return image.currentSrc && image.naturalWidth > 0 && !image.hidden &&
        new URL(image.currentSrc).searchParams.get("smiles") === expected;
    }, smiles);
    assert(previews.includes(smiles));
    assert.equal(await page.locator(".endpoint-value").textContent(), "0");
    assert.equal(await page.locator(".endpoint-unit").textContent(), "fixture-unit");
    assert.equal(await page.locator(".endpoint-source").textContent(), "模型预测");
  }
  const a = await openSession("session-A"), b = await openSession("session-B");
  await a.page.locator("#smiles-input").fill("CO");
  await a.page.locator("#file-mode").click();
  await a.page.locator("#structure-file").setInputFiles({name: "input.csv", mimeType: "text/csv", buffer: raw});
  await a.page.getByText(/已读取第一个结构/).waitFor();
  const bytes = await a.page.locator("#structure-file").evaluate(async el => {
    const digest = await crypto.subtle.digest("SHA-256", await el.files[0].arrayBuffer());
    return [el.files[0].name, Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, "0")).join("")];
  });
  assert.deepEqual(bytes, ["input.csv", createHash("sha256").update(raw).digest("hex")]);
  await evaluate(a, "#run-admet", "CCN", 1);
  const evidence = await a.page.locator("#evidence-panel").textContent();
  assert.match(evidence, /session-A/);
  assert.match(evidence, /fixture-v1/);
  assert.match(evidence, /fixture-weights-session-A/);

  await b.page.locator("#smiles-input").fill("CCC");
  await evaluate(b, "#rerun-admet", "CCC", 1);
  assert.match(await b.page.locator("#evidence-panel").textContent(), /fixture-weights-session-B/);
  assert.equal(await a.page.locator("#evidence-panel").textContent(), evidence);
  await a.page.locator("#smiles-mode").click();
  assert.equal(await a.page.locator("#smiles-input").inputValue(), "CO");
  await evaluate(a, "#rerun-admet", "CO", 2);
  await a.page.locator("#file-mode").click();
  await evaluate(a, "#rerun-admet", "CCN", 3);
  assert.equal(await b.page.locator(".smiles-record").textContent(), "CCC");
});
