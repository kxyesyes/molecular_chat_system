/* Synthetic transport fixtures only; these are not scientific predictions. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const read = file => fs.readFileSync(path.join(__dirname, '..', file), 'utf8');

class Element {
  constructor(tag = 'div') {
    this.tagName = tag; this.children = []; this.attributes = {}; this.events = {};
    this.hidden = false; this.disabled = false; this.value = ''; this._text = '';
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(' '); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text = ''; this.children = children; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  addEventListener(name, callback) { this.events[name] = callback; }
  focus() { this.focused = true; }
  async dispatch(name) { return this.events[name]?.({preventDefault() {}}); }
}

const source = read('src/web/static/js/admet.js');
const statusSource = read('src/web/static/js/shared/status.js');
const template = read('src/web/templates/admet.html');
const styles = read('src/web/static/css/admet.css');
assert(template.includes('/static/js/shared/status.js'), 'load the shared status contract before page rendering');
assert(!source.includes('const stateText ='), 'status labels belong to the shared status module');
assert(!source.includes('innerHTML'));
assert(!/on(?:click|submit)=/.test(template));
assert(!template.includes('/kermt-admet'));
assert(template.includes('/static/css/subpage_header.css'), 'reuse the shared subpage header');
assert(template.includes('class="header"'), 'use the shared tool-page header');
assert(template.includes('class="logo-section"'), 'use the shared brand layout');
assert(template.includes('class="back-btn"'), 'use the shared back button');
assert(!template.includes('console-header'), 'remove the page-specific header shell');
assert(!template.includes('evidence-sidebar'), 'remove the oversized right evidence column');
assert(!template.includes('evidence-disclosure'), 'do not show provenance in the primary workspace');
assert(template.includes('id="evidence-panel"'), 'retain a non-visible evidence sink for the renderer contract');
assert(!template.includes('input-footnote'), 'remove non-essential input footnote');
assert(!template.includes('results-footer'), 'remove non-essential results footer');
assert(!template.includes('page-footer'), 'remove non-essential page footer');
assert(template.includes('class="empty-workflow"'), 'show a meaningful initial workflow instead of empty space');
assert(template.includes('id="smiles-count"'), 'show the SMILES input count');
assert(template.includes('class="example-chip"'), 'provide compact molecule examples');
assert(template.includes('id="metric-cards"'), 'reserve a data-backed summary area');
assert(template.includes('id="endpoint-tabs"'), 'provide endpoint category navigation');
assert(template.includes('id="completed-at"'), 'show the actual completion timestamp');
assert(template.includes('id="rerun-admet"'), 'allow rerunning from the result header');
assert(template.includes('id="structure-preview"'), 'show a real RDKit structure preview when available');
assert(template.includes('class="input-mode-tabs"'), 'provide SMILES and file input modes');
assert(template.includes('id="structure-file"'), 'provide a real file input');
assert(template.includes('id="file-help"'), 'explain supported upload formats');
assert(styles.includes('align-items: stretch'), 'keep both workspace columns at the same row height');
assert(template.includes('/static/css/admet.css?v=20261005-38874c3'), 'bust stale ADMET stylesheet caches after layout changes');
assert(template.includes('/static/js/admet.js?v=20261005-38874c3'), 'bust stale ADMET script caches after behavior changes');
assert(!template.includes('置信度'), 'do not imply a confidence score that the API does not return');
assert(styles.includes('max(460px, calc(100vh - 260px))'), 'fill the initial workspace with meaningful content');
assert(!styles.includes('min-height: 620px'), 'do not force a large empty result panel');
assert(!source.includes('#182421'), 'ADMET page should not keep the standalone dark theme');
assert(!/setTimeout\([^]*2500/.test(source), 'no fake prediction delay');
assert(!template.includes('res-caco2'), 'no legacy hardcoded endpoint cards');

function createUI(fetchImpl) {
  const elements = Object.fromEntries([...template.matchAll(/id="([^"]+)"/g)].map(([, id]) => [id, new Element()]));
  const document = { getElementById: id => elements[id], createElement: tag => new Element(tag) };
  const sandbox = { document, window: {}, fetch: fetchImpl, console, AbortController, setTimeout, clearTimeout };
  vm.runInNewContext(statusSource, sandbox, { filename: 'shared/status.js' });
  vm.runInNewContext(source, sandbox, { filename: 'admet.js' });
  return { elements, ui: sandbox.window.MedChatADMET };
}
const response = () => ({
  success: true, status: 'succeeded', message: 'fixture complete', warnings: ['fixture warning'],
  provenance: { tool_name: 'admet_predictor', model_name: 'ADMET-AI', model_version: '1.4.0', demo_mode: false, fallback_used: false },
  data: [{ molecule_id: 'molecule-001', smiles: 'CCO', status: 'succeeded', warnings: ['row warning'], admet: {
    prediction_method: 'admet_ai', model_name: 'ADMET-AI', model_version: '1.4.0',
    weights_id: 'sha256:' + 'a'.repeat(64), demo_mode: false, fallback_used: false,
    endpoints: {
      molecular_weight: { value: 46.07, name: 'Molecular weight', category: 'Physicochemical', unit: 'g/mol', task_type: 'regression', source: 'rdkit_calculation' },
      hERG: { value: 0.21, name: 'hERG', category: 'Toxicity', unit: 'probability', task_type: 'classification', source: 'admet_ai_model' },
      future: { value: 0, name: 'Novel endpoint', category: 'Future category', unit: 'unknown', task_type: 'unknown', source: 'admet_ai_model' },
    }, risk_endpoint_ids: [], risk_count: 0, total_endpoints: 1,
  }}],
});

async function main() {
  const { ui, elements } = createUI();
  assert(ui, 'export renderer for executable tests');
  assert(!elements['endpoint-groups'].textContent.includes('46.07'), 'initial state has no results');
  ui.renderResponse(response());
  let text = elements['endpoint-groups'].textContent;
  assert(text.includes('46.07'), 'the first endpoint category is visible by default');
  assert(text.includes('RDKit'));
  assert(elements['metric-cards'].textContent.includes('预测指标'));
  assert(elements['metric-cards'].textContent.includes('风险项'));
  assert(elements['metric-cards'].textContent.includes('1'), 'summary must use the actual endpoint count');
  assert(elements['endpoint-tabs'].textContent.includes('物化性质'));
  assert(elements['endpoint-tabs'].textContent.includes('毒性'));
  assert(elements['completed-at'].textContent !== '尚未运行');
  const toxicityTab = elements['endpoint-tabs'].children.find(child => child.textContent.includes('毒性'));
  await toxicityTab.events.click();
  text = elements['endpoint-groups'].textContent;
  assert(text.includes('0.21') && text.includes('模型预测'));
  const otherTab = elements['endpoint-tabs'].children.find(child => child.textContent.includes('其他端点'));
  await otherTab.events.click();
  text = elements['endpoint-groups'].textContent;
  assert(text.includes('Novel endpoint'));
  assert(!text.includes('安全'));
  assert(elements['evidence-panel'].textContent.includes('sha256:'));
  assert(elements['evidence-panel'].textContent.includes('fixture warning'));
  assert(elements['evidence-panel'].textContent.includes('row warning'));
  const evidence = response();
  evidence.data[0].admet.backend_version = 'fixture-backend-version';
  evidence.quality = { assessed: 'fixture-quality' };
  evidence.provenance.input_summary = 'fixture-input-summary';
  ui.renderResponse(evidence);
  for (const value of ['fixture-backend-version', 'fixture-quality', 'fixture-input-summary']) {
    assert(elements['evidence-panel'].textContent.includes(value), 'retain returned evidence: ' + value);
  }

  for (const field of ['demo_mode', 'fallback_used']) {
    const bad = response(); bad.data[0].admet[field] = true;
    ui.renderResponse(bad);
    assert(!elements['endpoint-groups'].textContent.includes('46.07'), 'demo/fallback cannot produce trusted endpoint cards');
    assert(elements['run-status'].textContent.includes('证据'));
    assert.notEqual(elements['status-badge'].textContent, '评估完成', 'untrusted evidence is not a green success');
  }
  const absent = response(); delete absent.data[0].admet.weights_id;
  ui.renderResponse(absent);
  assert(!elements['endpoint-groups'].textContent.includes('46.07'));

  const invalid = response(); invalid.data[0].admet.endpoints.hERG.value = NaN;
  ui.renderResponse(invalid);
  assert(!elements['endpoint-groups'].textContent.includes('NaN'));
  const invalidToxicityTab = elements['endpoint-tabs'].children.find(child => child.textContent.includes('毒性'));
  await invalidToxicityTab.events.click();
  assert(elements['endpoint-groups'].textContent.includes('未计算'));

  const hostile = response();
  hostile.data[0].admet.endpoints.hERG.name = '<img src=x onerror=alert(1)>';
  ui.renderResponse(hostile);
  const hostileToxicityTab = elements['endpoint-tabs'].children.find(child => child.textContent.includes('毒性'));
  await hostileToxicityTab.events.click();
  assert(elements['endpoint-groups'].textContent.includes('<img src=x onerror=alert(1)>'));
  const allChildren = el => [el, ...el.children.flatMap(allChildren)];
  assert(!allChildren(elements['endpoint-groups']).some(el => el.tagName === 'img'));

  const partial = response(); partial.success = false; partial.status = 'partial';
  partial.data.push({ molecule_id: 'molecule-002', smiles: 'CC(C)((', status: 'failed', error: 'Invalid SMILES', warnings: [] });
  ui.renderResponse(partial);
  assert(elements['run-status'].textContent.includes('部分完成'));
  assert(elements['endpoint-groups'].textContent.includes('46.07'));
  assert(elements['endpoint-groups'].textContent.includes('Invalid SMILES'));

  for (const status of ['failed', 'unavailable', 'invalid_input']) {
    const failure = response(); failure.status = status; failure.success = false; failure.message = 'Unavailable or invalid';
    ui.renderResponse(failure);
    assert(!elements['endpoint-groups'].textContent.includes('46.07'), 'failure must clear old and unexpected numeric results');
  }
  const emptyFailure = { success: false, status: 'unavailable', message: 'ADMET backend unavailable', data: [], warnings: [] };
  ui.renderResponse(emptyFailure);
  assert.equal(elements['empty-state'].hidden, true, 'failure state must not keep the empty placeholder');
  assert(elements['endpoint-groups'].textContent.includes('ADMET backend unavailable'));
  ui.renderResponse({ success: true, status: 'succeeded', data: [] });
  assert(!elements['run-status'].textContent.includes('评估完成'), 'empty success is not a computed result');

  const calls = [];
  let resolve;
  const deferred = new Promise(r => { resolve = r; });
  const app = createUI((...args) => { calls.push(args); return deferred; });
  app.elements['smiles-input'].value = ' CCO ';
  const pending = app.elements['admet-form'].dispatch('submit');
  await app.elements['admet-form'].dispatch('submit');
  assert.equal(calls.length, 1, 'in-flight guard prevents duplicate requests');
  assert.equal(calls[0][0], '/api/admet/predict');
  assert.equal(JSON.parse(calls[0][1].body).smiles, 'CCO');
  assert(app.elements['run-admet'].disabled);
  assert(app.elements['reset-admet'].disabled, 'reset must not race a pending result');
  resolve({ ok: true, json: async () => response() });
  await pending;
  assert.equal(app.elements['run-admet'].disabled, false);
  assert.equal(app.elements['reset-admet'].disabled, false);
  assert(app.elements['endpoint-groups'].textContent.includes('46.07'));
  await app.elements['reset-admet'].dispatch('click');
  assert.equal(app.elements['smiles-input'].value, '');
  assert.equal(app.elements['endpoint-groups'].textContent, '');
  await app.elements['admet-form'].dispatch('submit');
  assert.equal(calls.length, 1, 'empty input must not call API');
  await app.elements['load-example'].dispatch('click');
  assert(app.elements['smiles-input'].value.length > 0);
  assert.equal(app.elements['structure-preview'].hidden, false, 'example loading prepares the real structure preview');
  assert(app.elements['structure-preview-image'].attributes.src.includes('/api/utils/smiles_to_image?'), 'preview uses the existing RDKit image endpoint');
  assert.equal(calls.length, 1, 'loading an example does not run prediction');
  assert.equal(app.ui.extractUploadedSmiles('aspirin.smi', 'CCO aspirin\n'), 'CCO', 'read the first SMILES from a text structure file');
  assert.equal(app.ui.extractUploadedSmiles('molecules.csv', 'name,smiles\nethanol,CCO\n'), 'CCO', 'read the SMILES column from CSV uploads');

  for (const [httpStatus, status, message] of [
    [503, 'unavailable', 'Model weights unavailable'],
    [500, 'failed', 'ADMET execution failed'],
  ]) {
    const failure = { success: false, status, message, data: [], warnings: ['fixture diagnostic'], provenance: { tool_name: 'admet_predictor' } };
    const failedApp = createUI(async () => ({ok: false, status: httpStatus, json: async () => failure}));
    failedApp.elements['smiles-input'].value = 'CCO';
    await failedApp.ui.submit();
    assert(failedApp.elements['run-status'].textContent.includes(message), 'retain structured HTTP error reason');
    assert(failedApp.elements['evidence-panel'].textContent.includes('fixture diagnostic'));
    assert.equal(failedApp.elements['run-admet'].disabled, false);
  }
  for (const [httpStatus, expected] of [[422, '输入无效'], [429, '繁忙']]) {
    const failedApp = createUI(async () => ({ok: false, status: httpStatus, json: async () => ({detail: 'not for display'})}));
    failedApp.elements['smiles-input'].value = 'CCO';
    await failedApp.ui.submit();
    assert(failedApp.elements['run-status'].textContent.includes(expected));
  }
  for (const [httpStatus, code, message] of [
    [422, 'INVALID_SMILES', '结构无效，请检查输入'],
    [429, 'ADMET_BUSY', '服务繁忙，请稍后重试'],
  ]) {
    const failedApp = createUI(async () => ({
      ok: false,
      status: httpStatus,
      json: async () => ({detail: {code, message: `${message} token=frontend-secret`}}),
    }));
    failedApp.elements['smiles-input'].value = 'CCO';
    await failedApp.ui.submit();
    assert(failedApp.elements['run-status'].textContent.includes(code));
    assert(failedApp.elements['run-status'].textContent.includes(message));
    assert(!failedApp.elements['run-status'].textContent.includes('frontend-secret'));
  }

  for (const implementation of [
    async () => { throw new Error('network secret-marker'); },
    async () => ({ok: false, status: 503, json: async () => ({detail: 'backend secret-marker'})}),
    async () => ({ok: true, json: async () => {throw new Error('bad JSON');}}),
  ]) {
    const badApp = createUI(implementation);
    badApp.elements['smiles-input'].value = 'CCO';
    await badApp.elements['admet-form'].dispatch('submit');
    assert.equal(badApp.elements['run-admet'].disabled, false);
    assert(!badApp.elements['endpoint-groups'].textContent.includes('46.07'));
    assert(!badApp.elements['run-status'].textContent.includes('secret-marker'));
    assert(badApp.elements['run-status'].textContent.includes('失败'));
  }
  console.log('ADMET frontend: rendering, truth-state, XSS and request lifecycle checks passed');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
