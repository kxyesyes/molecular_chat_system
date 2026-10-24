// Pi owns the loop and transcript. Python callbacks are single transactions.
import {
  createAgentSession, createExtensionRuntime, ModelRuntime,
  SessionManager, SettingsManager,
} from '@earendil-works/pi-coding-agent';
import {
  InMemoryCredentialStore, createAssistantMessageEventStream,
} from '@earendil-works/pi-ai';

const providerId = 'medchat-stdio';
const api = 'medchat-stdio';
const zeroCost = () => ({ input: 0, output: 0, cacheRead: 0, cacheWrite: 0 });

export async function runPiSession({
  cwd, model, systemPrompt, prompt, tools, requestModel, executeTool,
  onEvent = () => {}, signal, entries, initialMessages = [], maxModelCalls = 16,
}) {
  if (signal?.aborted) throw new Error('pi_cancelled');
  const names = tools.map(tool => tool.name);
  if (new Set(names).size !== names.length) throw new Error('pi_duplicate_tool');
  const runtime = await ModelRuntime.create({
    credentials: new InMemoryCredentialStore(), modelsPath: null,
    allowModelNetwork: false, refreshOnCreate: false,
  });
  let providerFailure;
  let modelCalls = 0;
  let terminated = false;
  runtime.registerProvider(providerId, {
    name: 'MedChat controlled provider', api,
    baseUrl: 'http://medchat.invalid', apiKey: 'internal-stdio-not-a-credential',
    models: [{
      id: model.id, name: model.id, api, reasoning: false, input: ['text'],
      contextWindow: model.contextWindow, maxTokens: model.maxTokens,
      cost: zeroCost(),
    }],
    streamSimple(piModel, context, options) {
      const stream = createAssistantMessageEventStream();
      const message = {
        role: 'assistant', content: [], api, provider: providerId, model: piModel.id,
        usage: { ...zeroCost(), totalTokens: 0, cost: { ...zeroCost(), total: 0 } },
        stopReason: 'stop', timestamp: Date.now(),
      };
      void (async () => {
        try {
          if (++modelCalls > maxModelCalls) throw new Error('pi_model_budget');
          if (options?.signal?.aborted) throw new Error('pi_cancelled');
          stream.push({ type: 'start', partial: message });
          const response = await requestModel({ messages: context.messages }, options?.signal);
          if (response.toolCall) {
            const call = { ...response.toolCall, type: 'toolCall' };
            message.content.push(call);
            message.stopReason = 'toolUse';
            stream.push({ type: 'toolcall_start', contentIndex: 0, partial: message });
            stream.push({ type: 'toolcall_end', contentIndex: 0, toolCall: call, partial: message });
          } else if (typeof response.text === 'string') {
            message.content.push({ type: 'text', text: response.text });
            stream.push({ type: 'text_start', contentIndex: 0, partial: message });
            stream.push({ type: 'text_end', contentIndex: 0, content: response.text, partial: message });
          } else {
            throw new Error('pi_provider_response_invalid');
          }
          stream.push({ type: 'done', reason: message.stopReason, message });
          stream.end(message);
        } catch (error) {
          providerFailure = error;
          message.stopReason = options?.signal?.aborted ? 'aborted' : 'error';
          // Provider exception text may contain sensitive upstream response data.
          message.errorMessage = 'pi_provider_failed';
          stream.push({ type: 'error', reason: message.stopReason, error: message });
          stream.end(message);
        }
      })();
      return stream;
    },
  });
  const manager = SessionManager.inMemory(cwd, undefined, entries);
  if (entries && initialMessages.length) throw new Error('pi_history_ambiguous');
  for (const previous of initialMessages) {
    if (!['user', 'assistant'].includes(previous.role) || typeof previous.content !== 'string') {
      throw new Error('pi_history_invalid');
    }
    manager.appendMessage(previous.role === 'user'
      ? { role: 'user', content: previous.content, timestamp: Date.now() }
      : { role: 'assistant', content: [{ type: 'text', text: previous.content }],
        api, provider: providerId, model: model.id, stopReason: 'stop', timestamp: Date.now(),
        usage: { ...zeroCost(), totalTokens: 0, cost: { ...zeroCost(), total: 0 } } });
  }
  const extensionRuntime = createExtensionRuntime();
  const resourceLoader = {
    getExtensions: () => ({ extensions: [], errors: [], runtime: extensionRuntime }),
    getSkills: () => ({ skills: [], diagnostics: [] }),
    getPrompts: () => ({ prompts: [], diagnostics: [] }),
    getThemes: () => ({ themes: [], diagnostics: [] }),
    getAgentsFiles: () => ({ agentsFiles: [] }),
    getSystemPrompt: () => systemPrompt,
    getSystemPromptSource: () => undefined,
    getAppendSystemPrompt: () => [], getAppendSystemPromptSources: () => [],
    extendResources: () => {}, reload: async () => {},
  };
  const customTools = tools.map(tool => ({
    ...tool, label: tool.name,
    async execute(id, args, toolSignal) {
      const result = await executeTool({ id, name: tool.name, arguments: args }, toolSignal);
      if (result.terminate === true) terminated = true;
      return {
        content: [{ type: 'text', text: typeof result.content === 'string'
          ? result.content : JSON.stringify(result.content) }],
        details: result.details ?? null, terminate: result.terminate === true,
      };
    },
  }));
  const { session } = await createAgentSession({
    cwd, agentDir: cwd, modelRuntime: runtime,
    model: runtime.getModel(providerId, model.id), thinkingLevel: 'off',
    tools: names, customTools, resourceLoader, sessionManager: manager,
    settingsManager: SettingsManager.inMemory({
      compaction: { enabled: false }, retry: { enabled: false },
    }),
  });
  const unsubscribe = session.subscribe(onEvent);
  const abort = () => session.agent.abort();
  signal?.addEventListener('abort', abort, { once: true });
  try {
    if (signal?.aborted) throw new Error('pi_cancelled');
    await session.prompt(prompt);
    if (signal?.aborted) throw new Error('pi_cancelled');
    if (providerFailure) throw providerFailure;
    if (!terminated) throw new Error('pi_terminal_missing');
    return {
      runtime: 'pi', terminated, modelCalls,
      activeTools: session.getActiveToolNames(),
      entries: [manager.getHeader(), ...manager.getEntries()],
    };
  } finally {
    signal?.removeEventListener('abort', abort);
    unsubscribe();
    session.dispose();
  }
}
