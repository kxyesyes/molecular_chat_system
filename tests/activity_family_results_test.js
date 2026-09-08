const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '..');
class Element {
  constructor(tag = 'div') {
    this.tagName = tag; this.children = []; this.style = {}; this._text = '';
    this.classes = new Set();
    this.classList = {add: x => this.classes.add(x), remove: x => this.classes.delete(x)};
  }
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() { return this._text + this.children.map(x => x.textContent).join(' '); }
  set innerHTML(v) { throw new Error('Unsafe HTML assignment: ' + v); }
  append(...nodes) { this.children.push(...nodes); }
  appendChild(n) { this.append(n); }
  replaceChildren(...nodes) { this._text = ''; this.children = nodes; }
}
const ids = Object.fromEntries(['resultsBody', 'resultsHeader', 'results', 'singleSummary',
  'batchHistogram', 'statusStat', 'submitBtn', 'loading'].map(x => [x, new Element()]));
let histogram = 0;
const context = {window: {}, document: {createElement: t => new Element(t), getElementById: id => ids[id]},
  echarts: {getInstanceByDom: () => null},
  ActivityUtils: {animateNumber: () => {}, getTagClass: () => '', setText: () => {}, formatScore: String},
  ActivityModels: {getTaskType: () => 'regression',
    getRangeConfig: () => ({min: 0, max: 10, lowUpper: 5, highUpper: 7}), getSelectedMeta: () => null},
  ActivityCharts: {renderHistogram: () => histogram++}, alert: () => {throw new Error('Unexpected alert');}};
vm.createContext(context);
function load(file) { vm.runInContext(fs.readFileSync(path.join(root, file), 'utf8'), context); }
load('src/web/static/js/activity_prediction/results_renderer.js');
const renderer = context.window.ActivityResults;
context.ActivityResults = renderer;
const row = {smiles: 'CCO', requested_target: 'PDE5A', family_id: 'pde-family', status: 'passed', success: true,
  activity_class: '无活性', activity_probability: 0, predicted_pIC50: 4.321,
  errors: {}, warnings: [], provenance: {bundle_id: 'synthetic', models: {
    classification: {model_id: 'fixture-classifier'}, regression: {model_id: 'fixture-regressor'}
  }}};
renderer.renderPredictionResults({results: [row], status: 'passed'}, {isSingleRequest: true});
assert.match(ids.resultsBody.textContent, /4\.321/);
assert.match(ids.resultsBody.textContent, /无活性/);
assert.match(ids.resultsBody.textContent, /0\.0%/);
assert.match(ids.resultsHeader.textContent, /活性概率/);
assert.equal(ids.singleSummary.style.display, 'none', 'do not show legacy gauge for family values');
assert.equal(histogram, 0);
ids.activityTarget = new Element();
ids.activityTarget.value = 'BuChE';
assert.match(ids.resultsBody.textContent, /PDE5A/);
assert.match(ids.resultsBody.textContent, /synthetic/);
assert.match(ids.resultsBody.textContent, /fixture-classifier/);
assert.match(ids.resultsBody.textContent, /fixture-regressor/);
assert.ok(!ids.resultsBody.textContent.includes('BuChE'));
const hostile = '<img src=x onerror=alert(1)>';
renderer.renderPredictionResults({results: [{...row, success: false, status: 'partial',
  predicted_pIC50: null, warnings: [hostile], errors: {regression: 'unavailable'}}]}, {});
assert.match(ids.resultsBody.textContent, /部分完成/);
assert.match(ids.resultsBody.textContent, /无活性/);
assert.ok(ids.resultsBody.textContent.includes(hostile));
assert.ok(!ids.resultsBody.textContent.includes('4.321'));
assert.equal(ids.resultsBody.children.length, 1);
renderer.renderPredictionResults({results: [{...row, success: false, status: 'failed',
  activity_class: null, activity_probability: null, predicted_pIC50: null,
  errors: {input: 'invalid_smiles'}}]}, {});
assert.match(ids.resultsBody.textContent, /失败/);
assert.match(ids.resultsBody.textContent, /invalid_smiles/);
assert.ok(!ids.resultsBody.textContent.includes('0.000'));
assert.ok(ids.results.classes.has('show'), 'failed scientific rows must remain visible');
renderer.renderPredictionResults({results: [{smiles: 'CCO', success: true,
  task_type: 'regression', endpoint: 'pIC50', units: 'pIC50', value: 3.14159}]}, {});
assert.match(ids.resultsBody.textContent, /3\.1416/);
assert.ok(!ids.resultsBody.textContent.includes('0.000'));
renderer.renderPredictionResults({results: [{smiles: 'CCO', success: true,
  task_type: 'classification', endpoint: 'activity', units: 'probability', probability: 0}]}, {});
assert.match(ids.resultsBody.textContent, /0\.0000/);
assert.match(ids.resultsHeader.textContent, /Endpoint/);
assert.equal(histogram, 0, 'task-aware values must not use legacy activity_score histogram');
load('src/web/static/js/activity_prediction/main.js');
context.fetch = async () => ({ok: true, json: async () => ({success: false, status: 'partial',
  results: [{...row, success: false, status: 'partial', predicted_pIC50: null}]})});
(async () => {
  const form = {get: () => '', delete: function (key) {this.deleted = key;}};
  await context.window.ActivityMain.handlePredict('/api/activity/predict', form, 'submitBtn');
  assert.equal(form.deleted, 'target', 'legacy option must omit the target');
  assert.equal(ids.statusStat.textContent, '部分完成');
  assert.equal(ids.submitBtn.disabled, false);
  assert.ok(!ids.loading.classes.has('show'));
  const template = fs.readFileSync(path.join(root, 'src/web/templates/activity_prediction.html'), 'utf8');
  assert.equal((template.match(/name="target"/g) || []).length, 2);
  assert.ok(!template.includes('>Ready</div>'), 'no hardcoded ready before bundle verification');
  console.log('Activity family results DOM and request tests passed');
})().catch(e => {console.error(e); process.exitCode = 1;});
