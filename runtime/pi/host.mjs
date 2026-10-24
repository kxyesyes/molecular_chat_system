// One admitted run per process. Stdout is bounded protocol, never diagnostic logs.
import { runPiSession } from './session.mjs';

const MAX_FRAME_BYTES = 1024 * 1024;
let buffer = Buffer.alloc(0);
let runId;
let sequence = 0;
let started = false;
let completed = false;
const pending = new Map();
const controller = new AbortController();

function send(value) {
  const encoded = Buffer.from(JSON.stringify({ v: 1, ...value }) + '\n', 'utf8');
  if (encoded.length > MAX_FRAME_BYTES) throw new Error('pi_frame_too_large');
  process.stdout.write(encoded);
}

function fail(error) {
  if (completed) return;
  completed = true;
  controller.abort();
  for (const request of pending.values()) request.reject(new Error('pi_protocol_failed'));
  pending.clear();
  const code = ['pi_model_budget', 'pi_terminal_missing', 'pi_cancelled'].includes(error?.message)
    ? error.message : 'pi_runtime_failed';
  send({ type: 'failed', runId, code });
  process.stdin.destroy();
  process.exitCode = 1;
}

function request(operation, payload) {
  if (completed || pending.size) return Promise.reject(new Error('pi_request_overlap'));
  const id = String(++sequence);
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    send({ type: 'request', runId, id, operation, payload });
  });
}

function receive(frame) {
  if (!frame || frame.v !== 1 || typeof frame !== 'object') throw new Error('pi_protocol_invalid');
  if (frame.type === 'start' && !started) {
    if (typeof frame.runId !== 'string' || !/^[a-f0-9]{32}$/.test(frame.runId)) throw new Error('pi_identity_invalid');
    started = true;
    runId = frame.runId;
    void runPiSession({
      ...frame.options, cwd: process.cwd(), signal: controller.signal,
      requestModel: payload => request('model', payload),
      executeTool: payload => request('tool', payload),
      // Only lifecycle names cross this channel; no raw provider errors or prose.
      onEvent: event => {
        if (['agent_start', 'agent_settled', 'tool_execution_start', 'tool_execution_end'].includes(event.type)) {
          send({ type: 'event', runId, event: { type: event.type } });
        }
      },
    }).then(result => {
      send({ type: 'result', runId, result });
      completed = true;
      process.stdin.destroy();
    }).catch(fail);
  } else if (frame.type === 'response' && frame.runId === runId && pending.has(frame.id)) {
    const callback = pending.get(frame.id);
    pending.delete(frame.id);
    callback.resolve(frame.payload);
  } else {
    throw new Error('pi_protocol_invalid');
  }
}

process.stdin.on('data', chunk => {
  try {
    buffer = Buffer.concat([buffer, chunk]);
    let newline;
    while ((newline = buffer.indexOf(10)) >= 0) {
      if (newline + 1 > MAX_FRAME_BYTES) throw new Error('pi_frame_too_large');
      const line = buffer.subarray(0, newline);
      buffer = buffer.subarray(newline + 1);
      receive(JSON.parse(line.toString('utf8')));
    }
    if (buffer.length >= MAX_FRAME_BYTES) throw new Error('pi_frame_too_large');
  } catch { fail(); }
});
process.stdin.on('end', () => { if (!completed) fail(); });
process.stdin.on('error', fail);
send({ type: 'ready' });
