// Standalone Chromium regression. Run with Playwright on Node's module path:
// node --test tests/ui_resilience_browser.cjs
const { after, before, test } = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const fs = require("node:fs");
const { chromium } = require("playwright");

const web = path.join(__dirname, "../src/web");
const widths = [320, 375, 430, 768, 1280];
let browser;
before(async () => { browser = await chromium.launch({ channel: "chrome" }); });
after(async () => { if (browser) await browser.close(); });

async function pageFor(t, pathname, api) {
  const page = await browser.newPage({ viewport: { width: 320, height: 812 }, isMobile: true });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  t.after(async () => {
    await page.context().close();
    assert.deepEqual(errors, []);
  });
  // Use the shipped pages and assets, with isolated responses for failures and
  // model data. No requests reach configured scientific or model services.
  await page.routeWebSocket("**/*", () => {});
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
  await page.goto("http://medchat.test" + pathname);
  return page;
}

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

test("model list reports failure and its controls fit a long model name", async t => {
  let available = false;
  const page = await pageFor(t, "/activity-prediction", (route, url) => {
    if (available && url.pathname === "/api/activity/models") {
      return route.fulfill({ json: { success: true, current_model: "fixture",
        models: [{ model_id: "fixture", name: "test-model-".repeat(20) }] } });
    }
    return route.fulfill({ status: 503, json: { detail: "服务暂不可用" } });
  });
  await page.waitForFunction(() => document.querySelector("#modelSelector").textContent.includes("加载失败"));
  assert(await page.locator("#deleteModelBtn").isHidden());
  available = true;
  await page.reload();
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

test("reloading a running training job restores progress and prevents a second launch", async t => {
  let state = "running";
  const page = await pageFor(t, "/activity-prediction", (route, url) => {
    if (url.pathname === "/api/activity/train/status/fixture") {
      return route.fulfill({json:{success:true,status:{state,epoch:2,progress:50}}});
    }
    return route.fulfill({status:503,json:{detail:"服务暂不可用"}});
  });
  await page.evaluate(() => localStorage.setItem("activeTrainJobId", JSON.stringify({jobId:"fixture", totalEpochs:4})));
  await page.reload();
  await page.waitForFunction(() => document.querySelector("#dock-epoch-text").textContent === "Epoch 2/4");
  assert(await page.locator("#trainBtn").isDisabled());
  await page.locator(".dock-view-btn").click();
  await page.locator('[onclick="toggleDrawerFull()"]').click();
  assert(await page.locator("#train-drawer").evaluate(el => el.classList.contains("fullscreen")));
  await page.locator('[onclick="minimizeDrawer()"]').click();
  state = "failed";
  await page.waitForFunction(() => localStorage.getItem("activeTrainJobId") === null);
  assert(await page.locator("#trainBtn").isEnabled());
});

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
