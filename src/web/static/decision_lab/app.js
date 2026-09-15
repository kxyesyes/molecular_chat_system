(() => {
    'use strict';

    const BASE = '/decision-lab/';
    const EVENT_LIMIT = 200;
    const cases = new Set(['chat', 'properties', 'clarify', 'invalid']);
    const terminalStatuses = new Set(['waiting_for_input', 'completed', 'failed', 'partial', 'rejected', 'cancelled']);
    const ui = Object.fromEntries(['case', 'start', 'query', 'resume', 'disconnect',
        'reconnect', 'reset', 'connection', 'status', 'continuation', 'trace',
        'terminal', 'results', 'warnings', 'events', 'event-count', 'error']
        .map((name) => [name, document.getElementById(`lab-${name}`)]));

    let socket = null;
    let sessionReady = false;
    let sessionPending = false;
    let busy = false;
    let continuation = null;
    let warnings = [];
    let displayLimited = false;

    const isObject = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
    const text = (value) => typeof value === 'string' ? value : JSON.stringify(value, null, 2) ?? '';
    const isOpen = () => socket !== null && socket.readyState === WebSocket.OPEN;

    function controls() {
        const canSend = isOpen() && !busy && !sessionPending;
        ui.start.disabled = !canSend;
        ui.case.disabled = !canSend;
        ui.resume.disabled = !canSend || !continuation;
        ui.query.disabled = !canSend || !continuation;
        ui.disconnect.disabled = socket === null || sessionPending;
        ui.reconnect.disabled = !sessionReady || sessionPending || socket !== null;
        ui.reset.disabled = sessionPending;
        ui.continuation.textContent = continuation || 'None';
        ui.status.setAttribute('aria-busy', String(busy));
    }

    function status(value) {
        ui.status.textContent = value;
    }

    function showWarnings() {
        const lines = [...warnings];
        if (displayLimited) lines.push('Display redacted or truncated by the server; consult authorized evidence records.');
        ui.warnings.textContent = lines.length ? lines.join('\n') : 'None reported.';
    }

    function clearResults() {
        ui.trace.textContent = '—';
        ui.terminal.textContent = 'No result received.';
        ui.results.textContent = 'No structured tool results received.';
        ui.error.textContent = '';
        ui.events.textContent = '';
        ui['event-count'].textContent = `0 / ${EVENT_LIMIT} retained`;
        warnings = [];
        displayLimited = false;
        showWarnings();
    }

    function requestError(code, message) {
        if (code === 'request_in_progress') {
            // The rejected extra request does not end the server's active request.
            busy = true;
            status('running — awaiting active request completion');
        } else {
            busy = false;
            if (code !== 'invalid_request' && code !== 'invalid_clarification') continuation = null;
            status('error');
        }
        ui.error.textContent = `${text(code)}: ${text(message)}`;
        controls();
    }

    function receive(message) {
        if (!isObject(message)) {
            requestError('invalid_message', 'Malformed server message. No work was replayed.');
            return;
        }
        if (message.type === 'error') {
            requestError(message.code || 'request_rejected', message.message || 'Request rejected by server.');
            return;
        }
        if (message.type === 'agent_event') {
            if (!isObject(message.event)) {
                requestError('invalid_message', 'Malformed agent event.');
                return;
            }
            const event = message.event;
            const item = document.createElement('li');
            const heading = document.createElement('h3');
            heading.textContent = [event.event || 'agent_event', event.tool].filter(Boolean).map(text).join(' / ');
            const data = document.createElement('pre');
            data.textContent = JSON.stringify(event, null, 2);
            item.appendChild(heading);
            item.appendChild(data);
            ui.events.appendChild(item);
            while (ui.events.children.length > EVENT_LIMIT) ui.events.removeChild(ui.events.firstChild);
            ui['event-count'].textContent = `${ui.events.children.length} / ${EVENT_LIMIT} retained`;
            if (typeof event.trace_id === 'string') ui.trace.textContent = event.trace_id;
            displayLimited ||= message.display_redacted_or_truncated === true;
            showWarnings();
            return;
        }
        if (message.type !== 'agent_result' && message.type !== 'complete') {
            requestError('invalid_message', 'Unknown server message type.');
            return;
        }
        if (!terminalStatuses.has(message.status)) {
            requestError('invalid_message', 'Invalid terminal status; execution outcome is unknown.');
            return;
        }

        if (typeof message.trace_id === 'string') ui.trace.textContent = message.trace_id;
        const metadata = isObject(message.metadata) ? message.metadata : {};
        if (message.type === 'complete') {
            busy = false;
            status(message.status);
            const id = message.continuation_id;
            continuation = message.status === 'waiting_for_input' && typeof id === 'string' && id.trim() ? id : null;
        } else {
            // Result data precedes server continuation registration; only complete unlocks controls.
            busy = true;
            status('running — result received; awaiting complete');
        }
        const answer = message.type === 'complete' ? message.content : message.final_answer ?? message.message;
        if (answer !== undefined) ui.terminal.textContent = text(answer);
        if (message.type === 'agent_result') {
            // Keep ordered observations and their provenance intact; never derive scientific data from prose.
            const results = message.tool_result_sequence ?? message.tool_results ?? [];
            ui.results.textContent = JSON.stringify({ tool_results: results, metadata,
                evidence: message.evidence ?? [], artifacts: message.artifacts ?? [] }, null, 2);
            warnings = Array.isArray(message.warnings) ? message.warnings.map(text) : [];
            const observations = Array.isArray(results) ? results : isObject(results) ? Object.values(results) : [];
            for (const result of observations) {
                if (isObject(result) && Array.isArray(result.warnings)) {
                    warnings.push(...result.warnings.map(text));
                }
            }
            warnings = [...new Set(warnings)];
            ui.error.textContent = message.error ? text(message.error) : '';
        }
        displayLimited ||= message.display_redacted_or_truncated === true;
        showWarnings();
        controls();
    }

    function disconnect() {
        const previous = socket;
        // Detach before close: even queued callbacks must not touch the new session/socket.
        socket = null;
        busy = false;
        if (previous) previous.close();
        ui.connection.textContent = 'Disconnected';
        status('Disconnected — execution uncertain; physical tool cancellation is not confirmed.');
        controls();
    }

    function socketFailure() {
        disconnect();
        ui.error.textContent = 'Connection failed. Execution is uncertain. Reconnect explicitly; no work is replayed.';
    }

    function connect() {
        if (!sessionReady || sessionPending || socket !== null) return;
        ui.connection.textContent = 'Connecting';
        let current;
        try {
            const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:';
            current = new WebSocket(`${scheme}//${location.host}${BASE}ws`);
            socket = current;
        } catch (_) {
            socketFailure();
            return;
        }
        controls();
        current.addEventListener('open', () => {
            if (socket !== current) return;
            ui.connection.textContent = 'Connected · no automatic replay';
            controls();
        });
        current.addEventListener('message', (event) => {
            if (socket !== current) return;
            let message;
            try {
                message = JSON.parse(event.data);
            } catch (_) {
                requestError('invalid_message', 'Malformed server JSON. No work was replayed.');
                return;
            }
            receive(message);
        });
        current.addEventListener('error', () => {
            if (socket !== current) return;
            socketFailure();
        });
        current.addEventListener('close', () => {
            if (socket !== current) return;
            disconnect();
        });
    }

    async function resetSession() {
        if (sessionPending) return;
        const hadSession = sessionReady;
        sessionPending = true;
        sessionReady = false;
        disconnect();
        continuation = null;
        clearResults();
        ui.query.value = 'SMILES: CCN';
        ui.connection.textContent = 'Establishing session';
        status(hadSession ? 'Resetting session — previous execution uncertain.' : 'Initializing');
        controls();
        const controller = new AbortController();
        let timer;
        try {
            // Bound both headers and body; a timeout is not proof that server cleanup finished.
            await Promise.race([
                (async () => {
                    const response = await fetch(`${BASE}session`, { method: 'POST',
                        credentials: 'same-origin', signal: controller.signal });
                    if (!response.ok || (await response.json()).success !== true) {
                        throw new Error('session rejected');
                    }
                })(),
                new Promise((_, reject) => {
                    timer = setTimeout(() => {
                        controller.abort();
                        reject(new Error('session deadline exceeded'));
                    }, 30000);
                }),
            ]);
            sessionReady = true;
            status(hadSession ? 'Idle — new session; previous execution uncertain.' : 'Idle');
        } catch (_) {
            ui.connection.textContent = 'Session unavailable';
            requestError('session_failed', 'Session could not be established. Use Reset session to retry.');
        } finally {
            clearTimeout(timer);
            sessionPending = false;
            controls();
        }
        if (sessionReady) connect();
    }

    function send(payload) {
        if (!isOpen() || busy || sessionPending) return;
        clearResults();
        // Keep the last authoritative ID while submitting a reply: validation may reject
        // it without consuming the continuation. Busy still prevents duplicate submissions.
        if (payload.action === 'start') continuation = null;
        busy = true;
        status('running');
        controls();
        try {
            socket.send(JSON.stringify(payload));
        } catch (_) {
            socketFailure();
        }
    }

    ui.start.addEventListener('click', () => {
        if (cases.has(ui.case.value)) send({ action: 'start', case_id: ui.case.value });
    });
    ui.resume.addEventListener('click', () => {
        if (continuation) send({ action: 'resume', continuation_id: continuation, query: ui.query.value });
    });
    ui.disconnect.addEventListener('click', disconnect);
    ui.reconnect.addEventListener('click', connect);
    ui.reset.addEventListener('click', resetSession);
    // The self-hosted script is deferred: all controls exist before this single bootstrap.
    resetSession();
})();
