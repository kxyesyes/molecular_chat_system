"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const sourcePath = path.join(
  __dirname,
  "..",
  "src",
  "web",
  "static",
  "js",
  "home",
  "task_status.js",
);
const sandbox = { window: {}, Object, Math };
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(sourcePath, "utf8"), sandbox, {
  filename: sourcePath,
});

const status = sandbox.window.HomeTaskStatus;
assert(status, "HomeTaskStatus must be exposed");

for (const event of ["task_completed", "task_partial", "task_rejected", "task_cancelled", "task_failed"]) {
  const pending = {publication_stage: "pending_source_check", answer_released: false};
  const presentation = status.resolve({event, payload: pending});
  assert.strictEqual(presentation.terminal, false, event);
  assert.strictEqual(presentation.progressText, "等待结果验证");
  assert.strictEqual(status.label(event, null, pending), "等待结果验证");
  assert.strictEqual(status.className(presentation.eventType), "is-running");
  for (const payload of [{}, {...pending, answer_released: true}, {...pending, publication_stage: "invalidated"}]) {
    assert.strictEqual(status.resolve({event, payload}).terminal, true);
  }
}

assert.deepStrictEqual(JSON.parse(JSON.stringify(status.resolve({event: "task_partial", progress: 2}))), {
  eventType: "task_partial",
  progressText: "部分完成",
  terminal: true,
});
for (const outcome of ["timeout", "unavailable", "not_calculated"]) {
  const resolved = status.resolve({
    event: "task_failed",
    progress: 1,
    payload: {status: outcome},
  });
  assert.strictEqual(resolved.progressText, {
    timeout: "计算超时",
    unavailable: "工具不可用",
    not_calculated: "尚未计算",
  }[outcome]);
  assert.strictEqual(resolved.terminal, true);
}
assert.deepStrictEqual(JSON.parse(JSON.stringify(status.resolve({event: "tool_started", progress: 0.42}))), {
  eventType: "tool_started",
  progressText: "42%",
  terminal: false,
});
assert.deepStrictEqual(JSON.parse(JSON.stringify(status.resolve({event: "unknown"}))), {
  eventType: "unknown",
  progressText: "执行中",
  terminal: false,
});
assert.strictEqual(status.label("activity_predictor"), "Agent 事件");
assert.strictEqual(status.label("tool_started", "activity_predictor"), "调用 活性预测");
assert.strictEqual(status.label('tool_started', 'candidate_ranker'), '调用 候选排序');
assert.strictEqual(status.label('validation_warning'), '执行提醒');
assert.strictEqual(status.className("task_partial"), "is-warning");
assert.strictEqual(status.className("task_completed"), "is-complete");
assert.strictEqual(status.className("tool_started"), "is-running");
assert.strictEqual(status.message({event:'tool_failed', tool:'admet_predictor', message:'Observation preparation failed'}),
  '结果准备或校验失败，无法采用该结果。');
assert.strictEqual(status.message({event:'task_failed', message:'Decision publication invalidated'}),
  '结果校验未通过，无法交付本次任务结果。');
assert.strictEqual(status.message({event:'validation_warning', tool:'reverse_target_predictor',
  message:'Tool result has validation warnings', payload:{warnings:[
    'Optional step reverse (reverse_target_predictor) failed: 反向寻靶来源正在进行全量科学校验',
    '反向寻靶来源正在进行全量科学校验',
  ]}}), '反向寻靶：步骤未完成；具体原因见失败步骤。');
const activityReason = '未找到与请求物种匹配的已登记活性模型；本次未生成预测值。';
const activityWrapper = 'Optional step web-123 (activity_predictor) failed: 家族活性预测失败；模型预测不等同于实验结论。 ' + activityReason;
assert.strictEqual(status.message({event:'validation_warning', tool:'activity_predictor',
  message:'Tool result has validation warnings', payload:{warnings:[activityReason, activityWrapper]}}),
  '活性预测：步骤未完成；具体原因见失败步骤。');
assert.strictEqual(status.message({event:'validation_warning', tool:'activity_predictor',
  message:'Tool result has validation warnings', payload:{warnings:[activityReason, activityWrapper, '另有适用域风险。']}}),
  '另有适用域风险。');
assert.strictEqual(status.message({event:'validation_warning', tool:'candidate_ranker',
  message:'Tool result has validation warnings', payload:{warnings:[
    'ADMET evidence was absent or unusable for 2 ranked candidate(s)',
    'Activity evidence was absent, demo, fallback, or unusable for 2 ranked candidate(s)']}}),
  '2 个候选缺少可用的 ADMET 证据。；2 个候选缺少可用的活性证据。');
console.log("Home task status checks passed");
