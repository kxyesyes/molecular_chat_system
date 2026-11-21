// Standalone Chromium regression. Run with Playwright on Node's module path:
// node --test tests/ui_resilience_browser.cjs
const { after, before, test } = require("node:test");
const assert = require("node:assert/strict");
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
    page.getByRole("button", { name: "准备并打开分子对接" }),
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
