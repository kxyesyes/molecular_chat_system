// Offline transport/DOM doubles exercise the real page script, not a copied controller.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const directory = path.join(__dirname, '../src/web/static/decision_lab');
const read = (name) => fs.existsSync(path.join(directory, name))
    ? fs.readFileSync(path.join(directory, name), 'utf8') : '';
const source = read('app.js');
const html = read('index.html');
const ids = ['case', 'start', 'query', 'resume', 'disconnect', 'reconnect', 'reset',
    'connection', 'status', 'continuation', 'trace', 'terminal', 'results',
    'warnings', 'events', 'event-count', 'error'];

class Element {
    constructor(tagName = 'div') {
        this.tagName = tagName.toUpperCase();
        this.children = [];
        this.listeners = {};
        this.attributes = {};
        this.value = '';
        this.disabled = false;
        this.text = '';
    }
    set innerHTML(_) { throw new Error('HTML execution sink used'); }
    set outerHTML(_) { throw new Error('HTML execution sink used'); }
    insertAdjacentHTML() { throw new Error('HTML execution sink used'); }
    set textContent(value) { this.text = String(value); this.children = []; }
    get textContent() { return this.text + this.children.map((c) => c.textContent).join(''); }
    appendChild(child) { this.children.push(child); return child; }
    removeChild(child) { this.children.splice(this.children.indexOf(child), 1); }
    get firstChild() { return this.children[0] || null; }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
    emit(name, event = {}) {
        for (const callback of this.listeners[name] || []) callback(event);
        if (this['on' + name]) this['on' + name](event);
    }
    click() { if (!this.disabled) this.emit('click', { preventDefault() {} }); }
}

function harness({ fetchImpl, protocol = 'http:' } = {}) {
    const elements = Object.fromEntries(ids.map((id) => [id, new Element()]));
    elements.case.value = 'chat';
    elements.query.value = 'SMILES: CCN';
    const requests = [];
    const sockets = [];
    const operations = [];
    const timers = new Map();
    let timerId = 0;
    class Socket extends Element {
        static CONNECTING = 0;
        static OPEN = 1;
        static CLOSING = 2;
        static CLOSED = 3;
        constructor(url) {
            super(); this.url = url; this.readyState = 0; this.sent = [];
            sockets.push(this); operations.push('connect');
        }
        open() { this.readyState = 1; this.emit('open'); }
        send(payload) {
            assert.equal(this.readyState, 1, 'send must require an open socket');
            if (this.sendError) throw new Error('send failed');
            this.sent.push(JSON.parse(payload));
        }
        message(data) { this.emit('message', { data: JSON.stringify(data) }); }
        close() { this.readyState = 3; operations.push('close'); this.emit('close'); }
    }
    const document = {
        readyState: 'complete',
        getElementById(id) { assert.ok(id.startsWith('lab-')); return elements[id.slice(4)]; },
        createElement(tag) { return new Element(tag); },
        addEventListener(name, fn) { if (name === 'DOMContentLoaded') fn(); },
    };
    Object.defineProperty(document, 'cookie', { get() { throw new Error('cookie must be server-owned'); },
        set() { throw new Error('cookie must be server-owned'); } });
    const context = {
        AbortController,
        setTimeout(callback) { timers.set(++timerId, callback); return timerId; },
        clearTimeout(id) { timers.delete(id); },
        document, WebSocket: Socket, location: { protocol, host: '127.0.0.1:6012' },
        fetch(url, options) {
            requests.push({ url, options }); operations.push('session');
            return fetchImpl ? fetchImpl(options) : Promise.resolve({ ok: true, json: async () => ({ success: true }) });
        },
    };
    for (const name of ['localStorage', 'sessionStorage']) {
        Object.defineProperty(context, name, { get() { throw new Error('persistent storage forbidden'); } });
    }
    context.window = context;
    vm.runInNewContext(source, context, { filename: 'decision_lab/app.js' });
    return { elements, requests, sockets, operations, timers };
}
const settle = () => new Promise((resolve) => setImmediate(resolve));
for (const stage of ['headers', 'body']) {
    test(`session ${stage} timeout aborts fetch and restores explicit retry`, async () => {
        const pending = new Promise(() => {});
        const h = harness({ fetchImpl: () => stage === 'headers' ? pending
            : Promise.resolve({ ok: true, json: () => pending }) });
        await settle();
        assert.equal(h.elements.reset.disabled, true);
        assert.equal(h.timers.size, 1, 'session creation must have a deadline');
        for (const callback of [...h.timers.values()]) callback();
        await settle();
        assert.equal(h.requests[0].options.signal.aborted, true);
        assert.equal(h.elements.reset.disabled, false);
        assert.match(h.elements.error.textContent, /session_failed/);
        assert.equal(h.sockets.length, 0);
        assert.equal(h.timers.size, 0);
    });
}
async function connected(options) {
    const h = harness(options);
    await settle();
    assert.equal(h.sockets.length, 1, 'page must establish a session then connect');
    h.sockets[0].open();
    return h;
}
function waiting(ws, id = 'continuation-fixture') {
    ws.message({ type: 'agent_result', status: 'waiting_for_input', trace_id: 'trace-fixture',
        final_answer: 'Please supply SMILES.', metadata: { waiting_for_input: true, continuation_id: id } });
    ws.message({ type: 'complete', status: 'waiting_for_input', trace_id: 'trace-fixture',
        content: 'Please supply SMILES.', continuation_id: id });
}

test('HTML contract: isolated scope, self assets, stable IDs and native labels', () => {
    assert.match(html, /isolated acceptance/i);
    assert.match(html, /not production/i);
    assert.match(html, /not a full scientific benchmark/i);
    for (const id of ids) assert.equal((html.match(new RegExp(`id="lab-${id}"`, 'g')) || []).length, 1, id);
    for (const id of ['case', 'query']) assert.match(html, new RegExp(`<label[^>]+for="lab-${id}"`));
    for (const [id, label] of Object.entries({ start: 'Start', resume: 'Resume', disconnect: 'Disconnect', reconnect: 'Reconnect', reset: 'Reset session' })) {
        assert.match(html, new RegExp(`<button[^>]+id="lab-${id}"[^>]*>${label}</button>`));
    }
    const options = Array.from(html.matchAll(/<option value="([^"]+)">([^<]+)<\/option>/g));
    assert.deepEqual(options.map((m) => m[1]), ['chat', 'properties', 'clarify', 'invalid']);
    assert.match(options[0][2], /chat/i);
    assert.match(options[1][2], /CCO.*CCN.*properties/i);
    assert.match(options[2][2], /missing SMILES.*CCN/i);
    assert.ok(options[3][2].includes('CC(C)(('));
    assert.match(html, /value="SMILES: CCN"/);
    assert.match(html, /<script src="\/decision-lab\/app.js" defer><\/script>/);
    assert.match(html, /href="\/decision-lab\/style.css"/);
    assert.doesNotMatch(html, /\son\w+\s*=|\sstyle\s*=|<style\b|https?:\/\/|<iframe\b/i);
    assert.doesNotMatch(source, /innerHTML|outerHTML|insertAdjacentHTML|localStorage|sessionStorage|document\.cookie|eval\s*\(/);
    assert.ok(read('style.css').length > 0, 'page needs its local stylesheet');
    assert.doesNotMatch(read('style.css'), /@import|url\s*\(/i);
});

test('preset labels and clarification guidance expose isolated acceptance obligations', () => {
    assert.match(html, /not (?:a )?general(?:[- ]purpose)? chat/i);
    assert.match(html, /<label[^>]+for="lab-case">Isolated acceptance case<\/label>/);
    const options = Array.from(html.matchAll(/<option value="([^"]+)">([^<]+)<\/option>/g));
    assert.equal(options.length, 4);
    for (const [, id, label] of options) assert.match(label, /isolated acceptance/i, id);
    const hint = html.match(/<p id="resume-hint"[^>]*>([^<]+)<\/p>/)?.[1] || '';
    assert.match(hint, /expected.*SMILES: CCN/i);
    assert.match(hint, /other molecules.*(?:do not|will not) satisfy/i);
    assert.match(hint, /complete/i);
});

test('boot POSTs exactly once, waits for success, then opens isolated WebSocket', async () => {
    let release;
    const h = harness({ fetchImpl: () => new Promise((resolve) => { release = resolve; }) });
    assert.equal(h.requests.length, 1, 'initial session POST is missing');
    assert.equal(h.sockets.length, 0);
    assert.equal(h.elements.start.disabled, true);
    assert.equal(h.elements.reset.disabled, true);
    const request = h.requests[0];
    assert.equal(request.url, '/decision-lab/session');
    assert.equal(request.options.method, 'POST');
    assert.equal(request.options.credentials, 'same-origin');
    assert.equal(request.options.body, undefined);
    release({ ok: true, json: async () => ({ success: true }) });
    await settle();
    assert.equal(h.sockets[0].url, 'ws://127.0.0.1:6012/decision-lab/ws');
    assert.equal(h.elements.start.disabled, true);
    h.sockets[0].open();
    assert.equal(h.elements.start.disabled, false);
    assert.equal(h.elements.resume.disabled, true);
    assert.equal(h.elements.reconnect.disabled, true);
    assert.equal(h.sockets[0].sent.length, 0);
    assert.equal(h.requests.length, 1);
});

test('start sends only server-owned case ID; busy prevents duplicate sends', async () => {
    const { elements: e, sockets: [ws] } = await connected();
    for (const id of ['chat', 'properties', 'clarify', 'invalid']) {
        e.case.value = id; e.start.click(); e.start.click(); e.resume.click();
        assert.deepEqual(ws.sent.at(-1), { action: 'start', case_id: id });
        assert.equal(e.start.disabled, true);
        assert.equal(e.case.disabled, true);
        assert.match(e.status.textContent, /running/i);
        ws.message({ type: 'complete', status: 'completed', content: 'done', trace_id: 'trace-fixture' });
    }
    assert.equal(ws.sent.length, 4);
});

test('waiting result stores only in-memory continuation and resumes once with default query', async () => {
    const { elements: e, sockets: [ws] } = await connected();
    e.start.click(); waiting(ws);
    assert.match(e.status.textContent, /waiting_for_input/);
    assert.equal(e.resume.disabled, false);
    assert.equal(e.start.disabled, false);
    assert.match(e.continuation.textContent, /continuation-fixture/);
    e.resume.click(); e.resume.click();
    assert.deepEqual(ws.sent[1], { action: 'resume', continuation_id: 'continuation-fixture', query: 'SMILES: CCN' });
    assert.equal(ws.sent.length, 2);
    assert.equal(e.resume.disabled, true);
    ws.message({ type: 'complete', status: 'completed', content: 'done' });
    assert.equal(e.resume.disabled, true);
    assert.doesNotMatch(e.continuation.textContent, /continuation-fixture/);
});

test('agent_result renders while busy; only authoritative complete enables continuation', async () => {
    const { elements: e, sockets: [ws] } = await connected();
    e.start.click();
    ws.message({ type: 'agent_result', status: 'waiting_for_input', final_answer: 'Supply SMILES.',
        metadata: { continuation_id: 'only-result' }, warnings: ['fixture warning'],
        tool_result_sequence: [{ tool_name: 'property_calculator', data: [] }] });
    assert.equal(e.terminal.textContent, 'Supply SMILES.');
    assert.match(e.results.textContent, /property_calculator/);
    assert.match(e.warnings.textContent, /fixture warning/);
    assert.equal(e.start.disabled, true);
    assert.equal(e.resume.disabled, true);
    assert.equal(e.status.attributes['aria-busy'], 'true');
    assert.match(e.status.textContent, /awaiting complete/i);
    assert.doesNotMatch(e.continuation.textContent, /only-result/);
    e.start.click(); e.resume.click();
    assert.equal(ws.sent.length, 1);
    ws.message({ type: 'complete', status: 'waiting_for_input', continuation_id: 'authoritative-id' });
    assert.equal(e.resume.disabled, false);
    e.query.value = 'SMILES: CCO'; e.resume.click();
    assert.deepEqual(ws.sent[1], { action: 'resume', continuation_id: 'authoritative-id', query: 'SMILES: CCO' });
    ws.message({ type: 'agent_result', status: 'waiting_for_input', metadata: { continuation_id: 'not-registered' } });
    assert.equal(e.start.disabled, true);
    assert.equal(e.resume.disabled, true);
    assert.doesNotMatch(e.continuation.textContent, /not-registered/);
    ws.message({ type: 'complete', status: 'waiting_for_input' });
    assert.equal(e.resume.disabled, true);
    assert.doesNotMatch(e.continuation.textContent, /authoritative-id|not-registered/);
    ws.message({ type: 'complete', status: 'waiting_for_input', continuation_id: 'only-complete' });
    assert.equal(e.resume.disabled, false);
    e.start.click();
    assert.doesNotMatch(e.continuation.textContent, /only-complete/);
});

test('disconnect resets busy, says execution uncertain, reconnect keeps cookie without replay', async () => {
    const h = await connected(); const e = h.elements; const old = h.sockets[0];
    e.start.click(); e.disconnect.click();
    assert.equal(old.readyState, 3);
    assert.match(e.connection.textContent, /disconnected/i);
    assert.match(e.status.textContent, /execution uncertain/i);
    assert.doesNotMatch(e.status.textContent, /physically stopped|execution cancelled/i);
    assert.equal(e.start.disabled, true);
    assert.equal(e.reconnect.disabled, false);
    e.reconnect.click(); e.reconnect.click();
    assert.equal(h.sockets.length, 2);
    h.sockets[1].open();
    assert.equal(h.requests.length, 1);
    assert.deepEqual(h.sockets[1].sent, []);
    assert.equal(e.start.disabled, false);
    assert.match(e.status.textContent, /uncertain/i);
});

test('waiting continuation survives explicit reconnect but is never auto-resumed', async () => {
    const h = await connected(); waiting(h.sockets[0]);
    h.sockets[0].close(); h.elements.reconnect.click(); h.sockets[1].open();
    assert.equal(h.elements.resume.disabled, false);
    assert.deepEqual(h.sockets[1].sent, []);
    h.elements.resume.click();
    assert.equal(h.sockets[1].sent[0].continuation_id, 'continuation-fixture');
});

test('reset closes old socket before POST, clears continuation/results, and never replays', async () => {
    const h = await connected(); const e = h.elements; const old = h.sockets[0];
    waiting(old); e.reset.click(); e.reset.click();
    assert.deepEqual(h.operations, ['session', 'connect', 'close', 'session']);
    assert.doesNotMatch(e.continuation.textContent, /continuation-fixture/);
    assert.equal(e.resume.disabled, true);
    await settle();
    assert.equal(h.requests.length, 2);
    assert.equal(h.sockets.length, 2);
    h.sockets[1].open();
    assert.equal(e.resume.disabled, true);
    assert.equal(e.events.children.length, 0);
    assert.doesNotMatch(e.terminal.textContent, /Please supply/);
    assert.deepEqual(h.sockets[1].sent, []);
});

test('stale socket open/message/error/close cannot overwrite the current connection or result', async () => {
    const h = await connected(); const e = h.elements; const old = h.sockets[0];
    e.reset.click(); await settle(); const current = h.sockets[1]; current.open();
    e.start.click();
    const before = [e.status.textContent, e.connection.textContent, e.terminal.textContent];
    old.open(); waiting(old, 'stale-id');
    old.message({ type: 'agent_event', event: { message: 'stale event' } });
    old.message({ type: 'error', code: 'STALE', message: 'stale rejection' });
    old.emit('error'); old.close();
    assert.deepEqual([e.status.textContent, e.connection.textContent, e.terminal.textContent], before);
    assert.equal(e.start.disabled, true);
    assert.equal(e.events.children.length, 0);
    current.message({ type: 'complete', status: 'completed', content: 'current' });
    assert.equal(e.terminal.textContent, 'current');
    assert.equal(e.start.disabled, false);
});

test('structured results, events, terminal, warnings and trace render as inert text', async () => {
    const { elements: e, sockets: [ws] } = await connected();
    const hostile = '<img src=x onerror=alert(1)> **CCO** [x](javascript:alert(1))';
    e.start.click();
    ws.message({ type: 'agent_event', display_redacted_or_truncated: true,
        event: { event: 'tool_completed', tool: 'property_calculator', trace_id: hostile,
            message: hostile, payload: { warning: hostile } } });
    assert.ok(e.events.textContent.includes(hostile));
    const tool = { tool_name: 'property_calculator', step_id: 'property_calculator:1',
        status: 'succeeded', data: [{ smiles: 'CCO', properties: { fixture_value: 1 } }],
        warnings: [hostile], provenance: { source: 'test fixture, not scientific evidence' } };
    ws.message({ type: 'agent_result', status: 'partial', trace_id: hostile, final_answer: hostile,
        warnings: [hostile], tool_result_sequence: [tool], metadata: { task_acceptance: { satisfied: false } },
        error: { code: 'PARTIAL', message: hostile }, display_redacted_or_truncated: true });
    ws.message({ type: 'complete', status: 'partial', trace_id: hostile, content: hostile });
    assert.equal(e.terminal.textContent, hostile);
    assert.equal(e.trace.textContent, hostile);
    assert.match(e.results.textContent, /property_calculator/);
    assert.match(e.results.textContent, /"fixture_value": 1/);
    assert.match(e.results.textContent, /test fixture, not scientific evidence/);
    assert.match(e.results.textContent, /"satisfied": false/);
    assert.ok(e.warnings.textContent.includes(hostile));
    assert.match(e.warnings.textContent, /redacted|truncated/i);
    assert.match(e.error.textContent, /PARTIAL/);
    assert.match(e.status.textContent, /partial/);
    assert.equal(e.events.children[0].children.some((c) => c.tagName === 'PRE'), true);
    assert.equal(e.start.disabled, false);
});

test('event stream retains only newest 200 entries and reports the bounded count', async () => {
    const { elements: e, sockets: [ws] } = await connected();
    for (let i = 0; i < 205; i++) ws.message({ type: 'agent_event', event: { message: `event-${i}` } });
    assert.equal(e.events.children.length, 200);
    assert.match(e.events.children[0].textContent, /event-5/);
    assert.match(e.events.children.at(-1).textContent, /event-204/);
    assert.match(e['event-count'].textContent, /200/);
});

for (const status of ['completed', 'failed', 'partial', 'rejected', 'cancelled']) {
    test(`${status} result stays busy until complete clears busy/continuation`, async () => {
        const { elements: e, sockets: [ws] } = await connected();
        e.start.click();
        ws.message({ type: 'agent_result', status, final_answer: status, metadata: { continuation_id: 'not-waiting' } });
        assert.equal(e.terminal.textContent, status);
        assert.equal(e.start.disabled, true);
        assert.equal(e.resume.disabled, true);
        assert.equal(e.status.attributes['aria-busy'], 'true');
        e.start.click(); e.resume.click();
        assert.equal(ws.sent.length, 1);
        ws.message({ type: 'complete', status, content: status, continuation_id: 'not-waiting' });
        assert.equal(e.start.disabled, false);
        assert.equal(e.resume.disabled, true);
        assert.equal(e.status.attributes['aria-busy'], 'false');
        assert.doesNotMatch(e.continuation.textContent, /not-waiting/);
        assert.ok(e.status.textContent.includes(status));
    });
}

for (const code of ['invalid_request', 'invalid_clarification']) {
    test(`${code} preserves the waiting continuation so a rejected reply can be corrected`, async () => {
        const { elements: e, sockets: [ws] } = await connected();
        e.start.click(); waiting(ws);
        for (const query of ['', 'X'.repeat(4097), '[redacted]']) {
            e.query.value = query; e.resume.click();
            const sentCount = ws.sent.length;
            ws.message({ type: 'error', code, message: '<b>Correct the reply</b>' });
            assert.equal(e.resume.disabled, false);
            assert.equal(e.query.disabled, false);
            assert.equal(e.start.disabled, false);
            assert.equal(e.status.attributes['aria-busy'], 'false');
            assert.equal(e.continuation.textContent, 'continuation-fixture');
            assert.ok(e.error.textContent.includes('<b>Correct the reply</b>'));
            assert.equal(ws.sent.length, sentCount, 'no automatic retry');
            assert.equal(ws.sent.at(-1).continuation_id, 'continuation-fixture');
        }
        e.query.value = 'SMILES: CCN'; e.resume.click();
        assert.deepEqual(ws.sent.at(-1), { action: 'resume', continuation_id: 'continuation-fixture', query: 'SMILES: CCN' });
        ws.message({ type: 'complete', status: 'completed', content: 'done' });
        assert.equal(e.resume.disabled, true);
        assert.doesNotMatch(e.continuation.textContent, /continuation-fixture/);
    });
}

test('request_in_progress preserves active busy state and prevents another start or resume', async () => {
    for (const action of ['start', 'resume']) {
        const { elements: e, sockets: [ws] } = await connected();
        if (action === 'resume') waiting(ws);
        e[action].click();
        ws.message({ type: 'error', code: 'request_in_progress', message: 'A request is already running.' });
        assert.equal(e.status.attributes['aria-busy'], 'true');
        assert.equal(e.start.disabled, true);
        assert.equal(e.resume.disabled, true);
        assert.match(e.status.textContent, /running/i);
        assert.match(e.error.textContent, /request_in_progress/);
        if (action === 'resume') assert.equal(e.continuation.textContent, 'continuation-fixture');
        e.start.click(); e.resume.click();
        assert.equal(ws.sent.length, 1);
        ws.message({ type: 'complete', status: 'completed', content: 'done' });
        assert.equal(e.start.disabled, false);
        assert.equal(e.resume.disabled, true);
        assert.equal(e.status.attributes['aria-busy'], 'false');
    }
});

test('non-correctable errors and reset still clear a submitted continuation', async () => {
    const h = await connected(); const e = h.elements; const ws = h.sockets[0];
    waiting(ws); e.resume.click();
    ws.message({ type: 'error', code: 'resume_rejected', message: 'Continuation unavailable.' });
    assert.equal(e.resume.disabled, true);
    assert.doesNotMatch(e.continuation.textContent, /continuation-fixture/);
    waiting(ws); e.resume.click(); e.reset.click();
    await settle(); h.sockets[1].open();
    assert.equal(e.resume.disabled, true);
    assert.doesNotMatch(e.continuation.textContent, /continuation-fixture/);
});

for (const code of ['malformed', 'busy', 'resume_rejected']) {
    test(`server ${code} error is visible and clears busy without replay`, async () => {
        const { elements: e, sockets: [ws] } = await connected();
        e.start.click();
        ws.message({ type: 'error', code, message: '<b>Rejected</b>' });
        assert.equal(e.start.disabled, false);
        assert.equal(e.resume.disabled, true);
        assert.match(e.status.textContent, /error/i);
        assert.ok(e.error.textContent.includes(code));
        assert.ok(e.error.textContent.includes('<b>Rejected</b>'));
        assert.equal(ws.sent.length, 1);
    });
}

test('malformed inbound JSON and wrong envelopes fail visibly without throwing', async () => {
    const { elements: e, sockets: [ws] } = await connected();
    for (const data of ['{', 'null', '[]', '42', '{"type":"agent_event","event":null}']) {
        e.start.click();
        assert.doesNotThrow(() => ws.emit('message', { data }));
        assert.equal(e.start.disabled, false);
        assert.match(e.error.textContent, /invalid|malformed/i);
    }
});

test('socket error or send failure ends busy and requires explicit reconnect', async () => {
    for (const method of ['error', 'send']) {
        const h = await connected(); const e = h.elements; const ws = h.sockets[0];
        if (method === 'send') ws.sendError = true;
        e.start.click();
        if (method === 'error') ws.emit('error');
        assert.match(e.status.textContent, /uncertain/i);
        assert.equal(e.reconnect.disabled, false);
        assert.equal(e.start.disabled, true);
        e.reconnect.click(); h.sockets[1].open();
        assert.equal(e.start.disabled, false);
        assert.equal(h.requests.length, 1);
    }
});

test('session HTTP/JSON/network failures do not connect; reset retries explicitly', async () => {
    for (const response of [null, { ok: false }, { ok: true, json: async () => ({ success: false }) },
        { ok: true, json: async () => { throw new Error('bad JSON'); } }]) {
        let fail = true;
        const h = harness({ fetchImpl: async () => {
            if (!fail) return { ok: true, json: async () => ({ success: true }) };
            if (response === null) throw new Error('network unavailable');
            return response;
        } });
        await settle();
        assert.equal(h.sockets.length, 0);
        assert.equal(h.elements.start.disabled, true);
        assert.equal(h.elements.reconnect.disabled, true);
        assert.equal(h.elements.reset.disabled, false);
        assert.match(h.elements.error.textContent, /session/i);
        fail = false; h.elements.reset.click(); await settle();
        assert.equal(h.sockets.length, 1);
    }
});

test('reset failure invalidates old continuation even if old frames arrive during POST', async () => {
    let call = 0;
    const h = await connected({ fetchImpl: async () => ({ ok: ++call === 1,
        json: async () => ({ success: true }) }) });
    waiting(h.sockets[0]); h.elements.reset.click(); waiting(h.sockets[0], 'stale-reset');
    await settle();
    assert.equal(h.elements.resume.disabled, true);
    assert.equal(h.elements.reconnect.disabled, true);
    assert.doesNotMatch(h.elements.continuation.textContent, /stale-reset|continuation-fixture/);
    assert.equal(h.sockets.length, 1);
});
