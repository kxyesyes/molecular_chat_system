const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const sandbox = {window: {}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,
  '../src/web/static/js/home/formatters.js'), 'utf8'), sandbox);
const render = sandbox.window.HomeFormatters.formatContent;

const explanation = '校准状态未知，需要查看相关文献或模型评估文档。';
const explanationHtml = render(explanation);
assert.ok(explanationHtml.includes(explanation));
assert.ok(!explanationHtml.includes('文献数据检索结果'));
assert.ok(!explanationHtml.includes('文献检索结果'));

// A textual result already carries its own content; a keyword is not proof
// that this turn performed a search and must not synthesize that assertion.
const report = '文献数据搜索结果\n\n未检索到匹配记录。';
const reportHtml = render(report);
assert.ok(reportHtml.includes('文献数据搜索结果'));
assert.ok(reportHtml.includes('未检索到匹配记录。'));
assert.ok(!reportHtml.includes('文献数据检索结果'));

console.log('literature presentation: passed');
