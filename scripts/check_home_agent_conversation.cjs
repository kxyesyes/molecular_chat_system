// Run against a local MedChat verification server using its own AGENT_STATE_DB.
const assert = require("node:assert/strict");
const { DatabaseSync } = require("node:sqlite");
const { test } = require("node:test");
const { chromium } = require("playwright");

async function conversation(browser, executions, verifyModel = false) {
  const context = await browser.newContext();
  const page = await context.newPage();
  const [socket] = await Promise.all([
    page.waitForEvent("websocket", ws => new URL(ws.url()).pathname === "/ws"),
    page.goto("http://127.0.0.1:6001/"),
  ]);
  if (verifyModel) {
    const config = await page.evaluate(async () => {
      const { config } = await (await fetch("/api/llm/config")).json();
      return { provider: config.provider, model: config.model_name };
    });
    assert.equal(config.provider, "openai_compatible");
    await page.locator("#settingsBtn").click();
    const [response] = await Promise.all([
      page.waitForResponse(r => new URL(r.url()).pathname === "/api/llm/test", { timeout: 120000 }),
      page.locator("#testLlmConfig").click(),
    ]);
    const probe = await response.json();
    assert.ok(probe.success && probe.model === config.model, "首页模型连接测试未通过");
    await page.locator("#closeLlmSettings").click();
  }

  return async (question, tools = [], status = "completed", { stopAfterAcceptance = false } = {}) => {
    const answers = page.locator(".assistant-message.complete");
    const index = await answers.count();
    await page.getByRole("textbox", { name: "消息" }).fill(question);
    const stop = stopAfterAcceptance && socket.waitForEvent("framereceived", {
      predicate: event => JSON.parse(event.payload).type === "request_accepted",
    }).then(() => page.getByRole("button", { name: "停止", exact: true }).click());
    const [frame] = await Promise.all([
      socket.waitForEvent("framereceived", {
        predicate: event => JSON.parse(event.payload).type === "complete",
        timeout: 180000,
      }),
      page.getByRole("button", { name: "发送消息", exact: true }).click(),
      stop,
    ]);
    const done = JSON.parse(frame.payload);
    assert.ok(done.trace_id, question);
    assert.ok([].concat(status).includes(done.status),
      question + " returned " + done.status + " (" + (done.error?.code || done.content || done.trace_id) + ")");
    const calls = executions.all(done.trace_id);
    assert.deepEqual(calls.map(row => row.tool_name), tools, question);
    const answer = answers.nth(index);
    await answer.waitFor();
    return {
      text: (await answer.innerText()).replace(/\s+/g, " ").trim(),
      outputs: calls.map(row => JSON.parse(row.output_json)),
      traceId: done.trace_id,
      answer,
    };
  };
}

async function verificationBrowser(t) {
  assert.ok(process.env.AGENT_STATE_DB, "请指定验收服务使用的 AGENT_STATE_DB");
  const db = new DatabaseSync(process.env.AGENT_STATE_DB, { readOnly: true });
  t.after(() => db.close());
  const executions = db.prepare("SELECT tool_name, output_json FROM tool_executions WHERE trace_id=? ORDER BY created_at, id");
  const browser = await chromium.launch({ channel: "chrome" });
  t.after(() => browser.close());
  return { browser, executions };
}

// Opt in only when the local generation model and MedChat can run together.
test("首页生成两个候选，选中第二个后复述和重算都使用同一结构", {
  skip: process.env.MEDCHAT_VERIFY_HOME_GENERATION !== "1",
}, async t => {
  const { browser, executions } = await verificationBrowser(t);
  const ask = await conversation(browser, executions, true);
  const generated = await ask("请生成两个类药候选分子。", ["llm_molecular_generator"]);
  const batch = generated.outputs[0].data;
  assert.equal(batch.requested_count, 2);
  assert.equal(batch.unique_count, 2);
  assert.equal(batch.candidates.length, 2);
  const cards = generated.answer.page().locator(".molecule-card");
  await cards.nth(1).waitFor();
  assert.deepEqual(await cards.evaluateAll(nodes => nodes.map(n => n.dataset.candidateId)),
    batch.candidates.map(row => row.candidate_id));
  for (const [i, row] of batch.candidates.entries()) {
    assert.equal(row.metadata.model, "gmm-llama:latest");
    assert.equal(row.validation.valid, true);
    await cards.nth(i).getByText("显示 SMILES", { exact: true }).click();
    assert.ok(await cards.nth(i).getByText(row.canonical_smiles, { exact: true }).isVisible());
  }
  const chosen = cards.nth(1).getByRole("button", { name: "选择此分子" });
  await chosen.click();
  assert.equal(await cards.nth(1).getByRole("button", {name: "已选择"}).getAttribute("aria-pressed"), "true");
  const result = await ask("请计算这个分子的分子量。", ["property_calculator"]);
  const [row] = result.outputs[0].data;
  assert.equal(result.outputs[0].data.length, 1);
  assert.equal(row.smiles, batch.candidates[1].canonical_smiles);
  const weight = row.properties.molecular_weight.toFixed(2);
  assert.ok(result.text.includes(weight));
  const recalled = await ask("它刚才的分子量是多少？");
  assert.ok(recalled.text.includes(weight));
  const repeated = await ask("请把这个分子的分子量重新算一次。", ["property_calculator"]);
  assert.deepEqual(repeated.outputs[0].data.map(r => [r.smiles, r.properties.molecular_weight]),
    [[row.smiles, row.properties.molecular_weight]]);
  assert.ok(repeated.text.includes(weight));
});

test("停止知识咨询后可在原会话重试并继续计算分子性质", async t => {
  const { browser, executions } = await verificationBrowser(t);
  const ask = await conversation(browser, executions, true);
  const question = "请用通俗的中文解释分配系数 logP 在药物研发中的用途，不用举具体分子的例子。";
  const stopped = await ask(question, [], "cancelled", { stopAfterAcceptance: true });
  assert.match(stopped.text, /已取消/);
  const retried = await ask(question);
  assert.notEqual(stopped.traceId, retried.traceId);
  assert.match(retried.text, /logP|分配系数/i);
  assert.match(retried.text, /亲[脂油]|脂溶|疏水|(?:油|有机相).{0,12}水/);
  const result = await ask("请计算 CCO 的分子量。", ["property_calculator"]);
  const [row] = result.outputs[0].data;
  assert.equal(row.smiles, "CCO");
  assert.ok(result.text.includes(row.properties.molecular_weight.toFixed(2)));
});

test("性质与 ADMET 的组合比较逐行对应两个工具的原始数据", async t => {
  const { browser, executions } = await verificationBrowser(t);
  const ask = await conversation(browser, executions, true);
  const { outputs: [properties, admet], answer } = await ask(
    "请比较 CCO 和 CCN 的分子量、logP 和 ADMET，并列出各自的 hERG 预测值。",
    ["property_calculator", "admet_predictor"]);
  const structures = ["CCO", "CCN"];
  for (const output of [properties, admet]) {
    assert.deepEqual(output.data.map(row => row.smiles), structures);
  }
  const tables = await answer.locator("table").evaluateAll(nodes => nodes.map(table =>
    Array.from(table.rows, row => Array.from(row.cells, cell => cell.innerText.trim()))));
  const propertyTable = tables.find(([header]) => header.includes("分子量 (Da)") && header.includes("logP"));
  assert.ok(propertyTable, "回答中缺少分子性质比较表");
  const [header, ...rows] = propertyTable;
  assert.deepEqual(rows.map(row => row[header.indexOf("SMILES")]), structures);
  for (const [i, row] of rows.entries()) {
    for (const [label, key] of [["分子量 (Da)", "molecular_weight"], ["logP", "logp"]]) {
      const cell = row[header.indexOf(label)];
      assert.ok(cell && Number.isFinite(properties.data[i].properties[key]));
      assert.equal(Number(cell), properties.data[i].properties[key]);
    }
  }
  const endpointTable = tables.find(([labels]) => labels[0] === "端点" && labels.includes("CCN"));
  assert.ok(endpointTable, "回答中缺少按分子比较的 ADMET 端点表");
  assert.deepEqual(endpointTable[0].slice(1), structures);
  const hERG = endpointTable.find(row => row[0] === "hERG");
  assert.ok(hERG, "端点表缺少用户指定的 hERG");
  for (const [i, row] of admet.data.entries()) {
    const endpoint = row.admet.endpoints.hERG;
    assert.equal(endpoint.source, "admet_ai_model");
    assert.equal(endpoint.task_type, "classification");
    assert.ok(Number.isFinite(endpoint.value));
    const displayed = Number.parseFloat(hERG[i + 1]);
    assert.equal(displayed, Number(endpoint.value.toPrecision(4)));
  }
});

test("澄清、纠正及越界后按各会话自己的表格和问题作答", async t => {
  const { browser, executions } = await verificationBrowser(t);
  const activity = await conversation(browser, executions, true);
  const descriptors = await conversation(browser, executions);
  const batch = await activity("预测 CCO 和 CCN 对 BuChE 的活性。", ["activity_predictor"], "partial");
  const order = Array.from(batch.text.matchAll(/SMILES[：:]\s*(\S+)/g), m => m[1]);
  assert.deepEqual(order, ["CCO", "CCN"]);
  const brief = "这两种估计怎么对不上，你能用一句话说说是怎么回事吗？";
  const clarify = await activity(brief, [], "not_calculated");
  assert.match(clarify.text, /序号或 SMILES/);
  const unavailable = await activity("CCC", [], "not_calculated");
  assert.match(unavailable.text, /缺少所选分子/);

  async function checkRow(query, index) {
    const row = batch.outputs[0].data.find(r => r.smiles === order[index]);
    const answer = await activity(query, [], row.classification_regression_consistent ? "completed" : "partial");
    assert.ok(answer.text.includes(row.smiles) && !answer.text.includes(order[1 - index]), query);
    assert.ok(answer.text.includes(row.predicted_pIC50.toPrecision(4)), query);
    assert.ok(answer.text.includes(row.classification_regression_consistent ? "标签一致" : "标签不一致，需复核"), query);
    assert.doesNotMatch(answer.text, /指标\s+结果/);
  }
  const second = "帮我看一下第二行。";
  const first = "不好意思，其实我想看的是第一行。";
  const beyond = "那第三行呢？";
  await checkRow(second, 1);
  const weights = await descriptors("查查 CCN 和 CCO 分别有多重，看看哪个更重？", ["property_calculator"]);
  const properties = weights.outputs[0].data;
  assert.deepEqual(properties.map(r => r.smiles), [...order].reverse());
  async function checkWeight(query, index) {
    const answer = await descriptors(query);
    assert.ok(answer.text.includes(properties[index].smiles));
    assert.ok(answer.text.includes(properties[index].properties.molecular_weight.toFixed(2) + " Da"));
  }
  await checkWeight(second, 1);
  const summary = await descriptors("给没看到屏幕的同事写一句摘要，带上刚才选中行的 SMILES 和分子量，保留两位小数。");
  assert.ok(summary.text.includes("CCO") && summary.text.includes("46.07") && !summary.text.includes("CCN"), summary.text);
  await checkRow(first, 0);
  await checkWeight(first, 0);
  for (const ask of [checkRow, checkWeight]) {
    await ask("刚才那两个里，另一个呢？", 1);
    await ask("另一个的情况呢？", 0);
  }
  for (const ask of [activity, descriptors]) {
    const missing = await ask(beyond, [], "not_calculated");
    assert.match(missing.text, /序号/);
  }
  await checkRow("下一行", 1);
  await checkWeight("最后一行", 1);
  await checkRow("不是第二行，是第一行。", 0);
  await checkWeight("上一行", 0);
  const triple = await descriptors("比较 CCC、CCO 和 CCN 的分子量、LogP、HBA 和 HBD。", ["property_calculator"]);
  const structures = triple.outputs[0].data.map(row => row.smiles);
  assert.deepEqual(structures, ["CCC", "CCO", "CCN"]);
  assert.ok((await descriptors("第二个的 logP 多少？")).text.includes("CCO"));
  const ambiguous = await descriptors("那另一个呢？", [], "not_calculated");
  assert.match(ambiguous.text, /3 个分子.*序号/);
  assert.ok((await descriptors("那它呢？")).text.includes("CCO"));
  const pickTwo = await descriptors("根据刚才的结果，分别判断这两个分子是否符合 Lipinski 五规则。", [], "not_calculated");
  assert.match(pickTwo.text, /3 个分子/);
  const invalidPair = await descriptors("第三行和最后一行", [], "not_calculated");
  assert.match(invalidPair.text, /不同/);
  const pair = await descriptors("最后一行和第一行。");
  assert.ok(pair.text.indexOf("CCN") >= 0 && pair.text.indexOf("CCN") < pair.text.indexOf("CCC"));
  assert.ok(!pair.text.includes("CCO"));
  assert.match(pair.text, /四项均未超过阈值/);
  const logp = await descriptors("第二个的 logP 多少？");
  const currentLogp = triple.outputs[0].data.find(r => r.smiles === "CCC").properties.logp.toFixed(3);
  assert.ok(logp.text.includes("CCC") && logp.text.includes(currentLogp), logp.text);
  const short = await descriptors("请用一句话概括刚才选中行的 SMILES 和 logP，保留三位小数。");
  assert.ok(short.text.includes("CCC") && short.text.includes(currentLogp), short.text);
  const other = await descriptors("另一个分子的 logP 多少？");
  const otherLogp = triple.outputs[0].data.find(r => r.smiles === "CCN").properties.logp.toFixed(3);
  assert.ok(other.text.includes("CCN") && other.text.includes(otherLogp) && !other.text.includes("CCC"), other.text);
  const next = await activity("预测 CCC 和 CCO 对 BuChE 的活性。", ["activity_predictor"], ["partial", "completed"]);
  const nextOrder = Array.from(next.text.matchAll(/SMILES[：:]\s*(\S+)/g), m => m[1]);
  assert.deepEqual(nextOrder, ["CCC", "CCO"]);
  assert.match((await activity(brief, [], "not_calculated")).text, /序号或 SMILES/);
  for (const [query, smiles] of [["OCC", "CCO"], ["上一行", "CCC"]]) {
    const row = next.outputs[0].data.find(r => r.smiles === smiles);
    const answer = await activity(query, [], row.classification_regression_consistent ? "completed" : "partial");
    assert.ok(answer.text.includes(smiles) && answer.text.includes(row.predicted_pIC50.toPrecision(4)));
    assert.match(answer.text, /标签/);
  }
});

test("首页模型解释后仍沿用当前分子、单位和同次靶点记录", async t => {
  const { browser, executions } = await verificationBrowser(t);
  const activity = await conversation(browser, executions, true);
  const target = await conversation(browser, executions);

  const batch = await activity("预测 CCO 和 CCN 对 BuChE 的活性。", ["activity_predictor"], "partial");
  const displayed = Array.from(batch.text.matchAll(/SMILES[：:]\s*(\S+)/g), m => m[1]);
  assert.deepEqual([...displayed].sort(), ["CCN", "CCO"]);
  const rows = displayed.map(smiles => batch.outputs[0].data.find(row => row.smiles === smiles));
  assert.ok(rows.every(row => row && Number.isFinite(row.predicted_pIC50)));
  async function checkConversion(question, row, unit = "μM") {
    const status = row.classification_regression_consistent ? "completed" : "partial";
    const answer = await activity(question, [], status);
    assert.ok(answer.text.includes(`分子 ${row.smiles} 对 BuChE`), question);
    const amount = answer.text.match(/换算约为 ([\d.eE+-]+) (微摩尔（μM）|μM|nM)/);
    assert.ok(amount, question);
    assert.ok(amount[2].includes(unit), question);
    const exponent = unit === "nM" ? 9 : 6;
    assert.equal(Number(amount[1]), Number((10 ** (exponent - row.predicted_pIC50)).toPrecision(4)));
    assert.equal(answer.text.includes("需复核"), !row.classification_regression_consistent);
    assert.ok(rows.every(other => other === row || !answer.text.includes(other.smiles)), question);
  }
  const selected = rows[1];
  const clarification = await activity("这个 pIC50 换算成多少微摩尔？", [], "not_calculated");
  assert.match(clarification.text, /序号/);
  await checkConversion("2", selected);
  const classification = await activity("第二个分子的分类与回归是否一致？", [],
    selected.classification_regression_consistent ? "completed" : "partial");
  assert.ok(classification.text.includes(selected.smiles));
  await checkConversion("这个 pIC50 换算成多少微摩尔？", selected);
  const explanation = await activity("请用自己的话解释刚才选中的那个分子，写出它的 SMILES，并说说两项预测为什么需要复核。");
  assert.ok(explanation.text.includes(selected.smiles) && /复核|不一致/.test(explanation.text), explanation.text);
  const values = await activity("不要调用任何工具。请复述刚才选中的分子的 SMILES、活性概率和预测 pIC50，两项数值都保留四位有效数字。",
    [], selected.classification_regression_consistent ? "completed" : "partial");
  assert.ok(values.text.includes(selected.smiles));
  assert.ok(!values.text.includes(rows[0].smiles), values.text);
  assert.equal(values.text.includes("需复核"), !selected.classification_regression_consistent);
  assert.ok(values.text.includes(selected.predicted_pIC50.toPrecision(4)), values.text);
  assert.ok(values.text.includes(selected.activity_probability.toPrecision(4)) ||
    values.text.includes((selected.activity_probability * 100).toPrecision(4) + "%"), values.text);
  await checkConversion("那它呢？", selected);
  await checkConversion("那第一个呢？", rows[0]);
  await checkConversion(selected.smiles, selected);
  await checkConversion("我指的是第一个", rows[0]);
  await checkConversion(selected.smiles, selected);
  await checkConversion("能把刚才那个微摩尔数值帮我换算成 nM 吗？", selected, "nM");
  await checkConversion("剩下那个呢？", rows[0], "nM");
  await checkConversion("那另一个呢？", selected, "nM");
  const brief = "这两种估计怎么对不上，你能用一句话说说是怎么回事吗？";
  const disagreement = await activity(brief, [], "partial");
  assert.ok(disagreement.text.includes(selected.smiles) && !disagreement.text.includes(rows[0].smiles));
  assert.ok(disagreement.text.includes(selected.predicted_pIC50.toPrecision(4)));
  assert.match(disagreement.text, /标签不一致，需复核/);
  await checkConversion("那第一个呢？", rows[0], "nM");
  const premise = await activity(brief);
  assert.ok(rows[0].classification_regression_consistent);
  assert.ok(premise.text.includes(rows[0].smiles) && !premise.text.includes(selected.smiles), premise.text);
  assert.match(premise.text, /标签一致/);
  assert.ok(premise.text.includes(rows[0].predicted_pIC50.toPrecision(4)));
  const weights = await activity("麻烦你先查一下 CCO 和 CCN 各有多重，再告诉我哪个更重？", ["property_calculator"]);
  const measured = weights.outputs[0].data;
  assert.deepEqual(measured.map(row => row.smiles), ["CCO", "CCN"]);
  for (const row of measured) {
    assert.ok(weights.text.includes(row.smiles) && weights.text.includes(row.properties.molecular_weight.toFixed(2)));
  }
  assert.ok(measured[0].properties.molecular_weight > measured[1].properties.molecular_weight);
  assert.match(weights.text, /第一个结构较高/);
  const weight = await activity("请计算 CCO 的分子量。", ["property_calculator"]);
  const propertyRow = weight.outputs[0].data[0];
  assert.equal(propertyRow.smiles, "CCO");
  assert.ok(Number.isFinite(propertyRow.properties.molecular_weight));

  const lookup = await target("请查询人 EGFR（UniProt P00533）的蛋白功能和实验结构。", ["target_database_search"]);
  const record = lookup.outputs[0].data[0];
  assert.equal(record.uniprot_id, "P00533");
  const structures = record.recommended_structures.filter(row =>
    row.structure_type === "experimental" && Number.isFinite(row.resolution));
  assert.ok(structures.length && record.function);
  const best = Math.min(...structures.map(row => row.resolution));
  const discussion = await target("请用通俗的话解释刚才这个分辨率意味着什么，以及为什么有共晶配体还不能直接说受体已经准备好了。");
  assert.match(discussion.text, /分辨率/);
  const resolution = await target("刚才列出的结构中，哪个分辨率最高？");
  assert.ok(resolution.text.includes(best + " Å"));
  assert.ok(structures.some(row => row.resolution === best && resolution.text.includes(row.structure_id)));
  const annotation = await target("这个靶点的生物学功能是什么？");
  assert.ok(annotation.text.includes(record.function.split(/(?<=\.)\s+/)[0].slice(0, 70)));
  const isolated = await target("这个 pIC50 换算成多少 nM？", [], "not_calculated");
  assert.match(isolated.text, /无法确认/);
  const named = "回到刚才那批 BuChE 活性预测，第二个分子的 pIC50 换算成多少 μM？只使用已有结果。";
  const foreign = await target(named, [], "not_calculated");
  assert.match(foreign.text, /找不到/);
  await activity("请查询人 EGFR（UniProt P00533）的蛋白功能和实验结构。", ["target_database_search"]);
  await checkConversion(named, rows[1]);
  await checkConversion("那第一个呢？", rows[0]);
  const editor = await conversation(browser, executions);
  const copy = await editor("请帮我为对话界面写三句中文测试台词。第一句，用户已有微摩尔结果，想把同一个值改成纳摩尔，越口语越好。第二句，让助手把乙醇 CCO 和乙胺 CCN 的分子量算出来并指出哪个更重。第三句，用户只想用一句话听懂两项预测互相矛盾意味着什么。只给三句台词，不回答台词中的问题。");
  assert.match(copy.text, /纳摩尔|nM/i);
  assert.match(copy.text, /乙醇|CCO/);
  assert.match(copy.text, /乙胺|CCN/);
  assert.match(copy.text, /预测|矛盾|冲突|不一致|对不上/);
  assert.doesNotMatch(copy.text, /46\.07|45\.08/);
});
