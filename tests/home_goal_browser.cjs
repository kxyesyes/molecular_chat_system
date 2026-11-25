// Against one running homepage, using its saved model configuration and real tools.
const assert = require("node:assert/strict");
const {chromium} = require("playwright");

async function exercise(browser, run) {
  const page = await browser.newPage();
  const frames = [];
  page.on("websocket", socket => socket.on("framereceived", envelope => {
    frames.push(JSON.parse(envelope.payload));
  }));
  async function waitFor(predicate) {
    const deadline = Date.now() + 180000;
    while (Date.now() < deadline) {
      const match = frames.find(predicate);
      if (match) return match;
      const terminal = frames.find(frame => frame.type === "complete" || frame.type === "error");
      if (terminal) throw Error(JSON.stringify(terminal));
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    throw Error("Timed out waiting for homepage goal event");
  }
  async function submit(question, button) {
    await page.getByRole("textbox", {name: "消息"}).fill(question);
    await page.getByRole("button", {name: button, exact: true}).click();
  }
  try {
    await page.goto("http://127.0.0.1:6001");
    await page.getByRole("button", {name: "按目标分析", exact: true}).click();
    await run({frames, waitFor, submit, page});
  } finally {
    await page.close();
  }
}

async function steering({frames, waitFor, submit, page}) {
  await submit("请先比较 CCO 和 CCN 的分子量和亲脂性，再检查这两个分子的类药规则。", "发送消息");
  const first = await waitFor(f => f.type === "goal_progress" && f.phase === "observed");
  assert.equal(first.tool, "property_calculator");
  assert(!frames.some(f => f.type === "complete"));
  await submit("保留两者刚算出的分子量和 logP，取消类药规则检查。再查 UniProt P06276 的结构记录，给我能核对的实验 PDB 编号。", "更新目标");
  const changed = await waitFor(f => f.type === "goal_updated");
  assert.equal(changed.revision, 2);
  assert.equal(changed.trace_id, first.trace_id);
  const done = await waitFor(f => f.type === "complete");
  assert.equal(done.status, "completed", done.content);
  assert.equal(done.goal_revision, 2);
  assert.deepEqual(frames.filter(f => f.phase === "running").map(f => f.tool),
                   ["property_calculator", "target_database_search"]);
  assert(frames.some(f => f.revision === 2 && f.phase === "reused" && f.tool === "property_calculator"));
  assert.match(done.content, /46\.0/);
  assert.match(done.content, /45\.0/);
  assert.match(done.content, /P06276/);
  assert.match(done.content, /\b[1-9][A-Z0-9]{3}\b/);
  await page.getByText(/UniProt P06276/).last().waitFor();
  console.log(JSON.stringify({test:"steering",trace:first.trace_id,revision:done.goal_revision,
    calls:frames.filter(f=>f.phase==="running").map(f=>f.tool),answer:done.content}));
}

async function condition({frames, waitFor, submit}, smiles, shouldCheck) {
  await submit(`比较 CCO 和 ${smiles} 的分子量。如果两者实际分子量相差不到 2 Da，再核对这两个分子的类药规则；否则只给出已算出的性质。`, "发送消息");
  const done = await waitFor(f => f.type === "complete");
  assert.equal(done.status, "completed", done.content);
  const calls = frames.filter(f => f.phase === "running").map(f => f.tool);
  assert.deepEqual(calls, shouldCheck ? ["property_calculator","drug_likeness_assessment"] : ["property_calculator"]);
  assert.match(done.content, /CCO/);
  assert(done.content.includes(smiles));
  assert.equal(done.content.includes("Lipinski"), shouldCheck);
  console.log(JSON.stringify({test:"condition",smiles,calls,trace:done.trace_id,answer:done.content}));
}

async function finishedFollowups({frames, waitFor, submit, page}) {
  for (const [question, expectedCalls, expectedText] of [
    ["比较 CCO 和 CCN 的分子量", ["property_calculator"], /46\.07/],
    ["第二个比分子量最大的那个轻多少", [], /0\.99 Da/],
    ["只给较轻的那个查类药规则", ["drug_likeness_assessment"], /CCN：Lipinski/],
  ]) {
    frames.length = 0;
    await submit(question, "发送消息");
    const done = await waitFor(f => f.type === "complete");
    assert.equal(done.status, "completed", done.content);
    const calls = frames.filter(f => f.phase === "running").map(f => f.tool);
    assert.deepEqual(calls, expectedCalls);
    assert.match(done.content, expectedText);
    if (calls.includes("property_calculator")) {
      assert.match(done.content, /CCO：分子量 46\.07 Da[^\n]*\nCCN：分子量 45\.08 Da/);
    }
    if (!calls.length) {
      assert.match(done.content, /CCN.*比 CCO.*轻 0\.99 Da/);
      assert(frames.some(f => f.phase === "reused" && f.tool === "property_calculator"));
    }
    await page.getByText(expectedText).last().waitFor();
    console.log(JSON.stringify({test:"completed_followup", trace:done.trace_id, calls, answer:done.content}));
  }
}

async function emptySession({frames, waitFor, submit, page}) {
  await submit("第二个呢", "发送消息");
  const done = await waitFor(f => f.type === "complete");
  assert.match(done.content, /请补充/);
  assert.doesNotMatch(done.content, /CCO|CCN|0\.99/);
  assert(!frames.some(f => f.phase === "running" || f.phase === "reused"));
  await page.getByText(/请补充/).last().waitFor();
  console.log(JSON.stringify({test:"empty_session", trace:done.trace_id, answer:done.content}));
}

async function candidateQuestion({frames, waitFor, submit, page}) {
  async function ask(question, status, expectedTools, expectedText) {
    frames.length = 0;
    await submit(question, "发送消息");
    const done = await waitFor(f => f.type === "complete");
    assert.equal(done.status, status, done.content);
    assert.deepEqual(frames.filter(f => f.phase === "running").map(f => f.tool), expectedTools);
    assert.match(done.content, expectedText);
    await page.getByText(expectedText).last().waitFor();
    console.log(JSON.stringify({test:"candidate_question", trace:done.trace_id, answer:done.content}));
  }
  const choose = "只给我选的那个分子查类药规则，先让我选择候选。";
  await ask("按顺序比较 CCO、CCN、CO 的分子量", "completed", ["property_calculator"], /CCO：分子量 46\.07/);
  await ask(choose, "waiting_for_input", [], /1\. CCO、2\. CCN、3\. CO/);
  await exercise(page.context().browser(), emptySession);
  await ask("第二个", "completed", ["drug_likeness_assessment"], /CCN：Lipinski/);
  await ask("它比最重的轻多少", "completed", [], /CCN.*比 CCO.*轻 0\.99 Da/);
  await ask(choose, "waiting_for_input", [], /2\. CCN/);
  await ask("换一批，按顺序比较 CO 和 CCO 的分子量", "completed", ["property_calculator"], /CO：分子量 32\.04/);
  await ask("这批第二个比最轻的重多少", "completed", [], /CCO.*比 CO.*重 14\.03 Da/);
  await ask(choose, "waiting_for_input", [], /1\. CO、2\. CCO/);
  await ask("取消", "cancelled", [], /已取消/);
  await ask("第二个", "not_calculated", [], /请补充/);
}

(async () => {
  const browser = await chromium.launch({channel:"chrome"});
  try {
    if (process.argv.includes("--clarification")) {
      await exercise(browser, candidateQuestion);
      return;
    }
    if (!process.argv.includes("--followups")) {
      await exercise(browser, steering);
      await exercise(browser, context => condition(context, "CCN", true));
      await exercise(browser, context => condition(context, "CCCCCC", false));
    }
    await exercise(browser, finishedFollowups);
    await exercise(browser, emptySession);
    await exercise(browser, candidateQuestion);
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error.message); process.exitCode = 1;});
