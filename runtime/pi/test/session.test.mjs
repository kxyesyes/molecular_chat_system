// Scripted provider and tools prove orchestration contracts, not live science.
import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

const sessionModule = new URL('../session.mjs', import.meta.url);

async function loadHost() {
  assert.equal(existsSync(sessionModule), true,
    'the official Pi session host must exist');
  return import(sessionModule.href);
}

const parameters = {
  type: 'object', properties: { input_ref: { type: 'string' } },
  required: ['input_ref'], additionalProperties: false,
};

test('official Pi consumes a tool observation and terminates without another model call', async () => {
  const { runPiSession } = await loadHost();
  const cwd = mkdtempSync(join(tmpdir(), 'medchat-pi-session-'));
  const calls = [];
  const events = [];
  const dispatched = [];
  try {
    const result = await runPiSession({
      cwd,
      model: { id: 'scripted-contract', contextWindow: 16000, maxTokens: 1000 },
      systemPrompt: 'Contract-only test. Use only supplied tools and observations.',
      prompt: 'Inspect the supplied molecule, then finish.',
      tools: [
        { name: 'property_calculator', description: 'Contract fixture, not real science.', parameters },
        { name: 'finish_task', description: 'Finish only after Python validation.', parameters },
      ],
      requestModel: async ({ messages }) => {
        calls.push(structuredClone(messages));
        if (calls.length === 1) {
          return { toolCall: { id: 'call-1', name: 'property_calculator', arguments: { input_ref: 'user' } } };
        }
        assert.equal(calls.length, 2, 'Pi must stop on the terminal tool result');
        const observation = messages.findLast(message => message.role === 'toolResult');
        assert.ok(observation, 'Pi must own and forward the completed observation');
        assert.match(JSON.stringify(observation.content), /contract-observation-1/);
        return { toolCall: { id: 'call-2', name: 'finish_task', arguments: { input_ref: 'contract-observation-1' } } };
      },
      executeTool: async (call) => {
        dispatched.push(call);
        return call.name === 'finish_task'
          ? { content: 'Validated contract finish', terminate: true }
          : { content: 'contract-observation-1' };
      },
      onEvent: event => events.push(event),
    });
    assert.deepEqual(dispatched.map(call => call.name), ['property_calculator', 'finish_task']);
    assert.equal(result.runtime, 'pi');
    assert.equal(result.terminated, true);
    assert.deepEqual(result.activeTools.sort(), ['finish_task', 'property_calculator']);
    assert.ok(events.some(event => event.type === 'tool_execution_start'));
    assert.ok(events.some(event => event.type === 'agent_settled'));
    assert.ok(result.entries.some(entry => entry.type === 'message'));
    assert.equal(existsSync(join(cwd, '.pi')), false, 'personal/project discovery must not create config');
  } finally {
    rmSync(cwd, { recursive: true, force: true });
  }
});

test('ordinary provider text is not accepted as a validated scientific terminal', async () => {
  const { runPiSession } = await loadHost();
  const cwd = mkdtempSync(join(tmpdir(), 'medchat-pi-no-terminal-'));
  try {
    await assert.rejects(runPiSession({
      cwd, model: { id: 'scripted-contract', contextWindow: 16000, maxTokens: 1000 },
      systemPrompt: 'Contract fixture.', prompt: 'Complete the request.', tools: [],
      requestModel: async () => ({ text: 'Unvalidated success claim' }),
      executeTool: async () => assert.fail('no domain tools were authorized'),
      onEvent: () => {},
    }), /pi_terminal_missing/);
  } finally {
    rmSync(cwd, { recursive: true, force: true });
  }
});

test('server-owned prior text is installed through the Pi session manager', async () => {
  const { runPiSession } = await loadHost();
  const result = await runPiSession({
    cwd: process.cwd(), model: { id: 'contract', contextWindow: 16000, maxTokens: 1000 },
    systemPrompt: 'Contract fixture.', prompt: 'new question',
    initialMessages: [{ role: 'user', content: 'previous question' },
      { role: 'assistant', content: 'previous response' }],
    tools: [{ name: 'finish_task', description: 'finish', parameters: { type: 'object', properties: {} } }],
    requestModel: async ({ messages }) => {
      assert.ok(messages.some(message => JSON.stringify(message.content).includes('previous response')));
      return { toolCall: { id: 'finish-1', name: 'finish_task', arguments: {} } };
    },
    executeTool: async () => ({ content: 'validated', terminate: true }),
  });
  assert.equal(result.terminated, true);
});

test('Pi sessions cannot share provider callbacks or histories across concurrent owners', async () => {
  const { runPiSession } = await loadHost();
  const owners = ['owner-a', 'owner-b'];
  const results = await Promise.all(owners.map(owner => runPiSession({
    cwd: process.cwd(), model: { id: owner, contextWindow: 16000, maxTokens: 1000 },
    systemPrompt: 'Contract fixture.', prompt: owner,
    tools: [{ name: 'finish_task', description: 'finish', parameters: { type: 'object', properties: {} } }],
    requestModel: async ({ messages }) => {
      await new Promise(resolve => setTimeout(resolve, 10));
      const content = messages.findLast(message => message.role === 'user').content;
      assert.equal(typeof content === 'string' ? content : content.map(block => block.text).join(''), owner);
      return { toolCall: { id: owner, name: 'finish_task', arguments: {} } };
    },
    executeTool: async call => {
      assert.equal(call.id, owner);
      return { content: owner, terminate: true };
    },
  })));
  assert.equal(results.length, 2);
  assert.ok(results.every(result => result.terminated));
});

test('an unavailable shell tool never reaches the MedChat callback', async () => {
  const { runPiSession } = await loadHost();
  let requests = 0;
  const result = await runPiSession({
    cwd: process.cwd(), model: { id: 'contract', contextWindow: 16000, maxTokens: 1000 },
    systemPrompt: 'Contract fixture.', prompt: 'test',
    tools: [{ name: 'finish_task', description: 'finish', parameters: { type: 'object', properties: {} } }],
    requestModel: async ({ messages }) => {
      requests += 1;
      if (requests === 1) return { toolCall: { id: 'shell', name: 'bash', arguments: { command: 'whoami' } } };
      assert.equal(messages.findLast(message => message.role === 'toolResult').isError, true);
      return { toolCall: { id: 'finish', name: 'finish_task', arguments: {} } };
    },
    executeTool: async call => {
      assert.equal(call.name, 'finish_task');
      return { content: 'rejected shell attempt', terminate: true };
    },
  });
  assert.deepEqual(result.activeTools, ['finish_task']);
});

test('model budget stops Pi before dispatching another provider transaction', async () => {
  const { runPiSession } = await loadHost();
  let requests = 0;
  await assert.rejects(runPiSession({
    cwd: process.cwd(), model: { id: 'contract', contextWindow: 16000, maxTokens: 1000 },
    systemPrompt: 'Contract fixture.', prompt: 'test', maxModelCalls: 1,
    tools: [{ name: 'inspect', description: 'fixture', parameters: { type: 'object', properties: {} } }],
    requestModel: async () => {
      requests += 1;
      return { toolCall: { id: 'inspect', name: 'inspect', arguments: {} } };
    },
    executeTool: async () => ({ content: 'observed' }),
  }), /pi_model_budget/);
  assert.equal(requests, 1);
});
