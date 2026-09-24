(function (root) {
  "use strict";
  const key = "medchat-scientific-reference-v1";
  const unavailable = "候选已显示，但科研引用不可用；请重新选择或重新生成。";
  const clone = value => JSON.parse(JSON.stringify(value));
  const pointer = ref => ({trace_id: ref.trace_id, presentation_id: ref.presentation_id, revision: ref.revision});
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

  function createController(options) {
    const {request, storage, onStatus = () => {}, onChange = () => {}} = options;
    let epoch = 0, selectionRevision = 0, collections = new Map(), active = null, selection = null;
    function save(ref) {
      try {
        if (ref) storage.setItem(key, JSON.stringify(pointer(ref)));
        else storage.removeItem(key);
      } catch (_) { onStatus("当前标签页存储不可用，刷新后无法自动恢复科研引用。"); }
    }
    function outgoing() {
      const current = active && collections.get(active);
      return current?.confirmed ? {reference: pointer(current.reference), ...(selection ? {selection: clone(selection)} : {})} : {};
    }
    function changed() {onChange(outgoing());}
    function clear() {
      epoch++; collections.clear(); active = selection = null; save(null); changed();
    }
    function startRequest() {
      epoch++; // keep confirmed context; invalidate pending ACKs/restores
    }
    function select(id, compoundKey) {
      const collection = collections.get(id);
      if (!collection?.confirmed || !collection.reference.ordered_keys.some(k => same(k, compoundKey))) {
        onStatus("请等待展示确认，或重新选择可用的候选。"); return false;
      }
      // Selection cancels stale restoration, not ACKs for other mounted views.
      selectionRevision++;
      active = id;
      selection = {observation_id: compoundKey[0], candidate_id: compoundKey[1]};
      save(collection.reference); changed(); return true;
    }
    function didMount(result, payload) {
      return result?.mounted === true && same(result.ordered_keys, payload.reference.ordered_keys);
    }
    async function present(payloads, render) {
      if (!payloads.length) return; // ordinary conversation retains the prior collection
      clear();
      const ticket = epoch;
      const pending = [];
      for (const payload of payloads) {
        try {
          const mounted = render(payload);
          if (!payload.reference || !didMount(mounted, payload)) {onStatus(unavailable); continue;}
          const ref = clone(payload.reference);
          const collection = {reference: ref, confirmed: false};
          collections.set(ref.presentation_id, collection);
          pending.push((async () => {
            try {
              const result = await request("confirm", ref);
              if (ticket !== epoch) return;
              if (result?.confirmed !== true) throw Error("unconfirmed");
              collection.confirmed = true;
            } catch (_) {if (ticket === epoch) onStatus(unavailable);}
          })());
        } catch (_) {onStatus(unavailable);}
      }
      await Promise.all(pending);
      if (ticket !== epoch) return;
      if (payloads.length === 1 && collections.size === 1) {
        const collection = collections.values().next().value;
        if (collection.confirmed) {active = collection.reference.presentation_id; save(collection.reference);}
      } else if (payloads.length > 1) onStatus("存在多个候选集合，请点击具体卡片选择要继续计算的分子。");
      changed();
    }
    async function restore(render) {
      const ticket = ++epoch;
      const selectedAtStart = selectionRevision;
      let p;
      try {
        const raw = storage.getItem(key);
        if (!raw) return;
        if (raw.length > 1024) throw Error("pointer too large");
        p = root.HomeMoleculeCandidates.normalizePointer(JSON.parse(raw));
        if (!p) throw Error("invalid pointer");
        const restored = await request("restore", p);
        if (ticket !== epoch || selectedAtStart !== selectionRevision) return;
        if (!restored || Object.keys(restored).sort().join() !== "events,expires_at,reference,source_status,warnings" ||
            !["succeeded", "partial"].includes(restored.source_status) || !Number.isFinite(restored.expires_at) ||
            restored.expires_at <= Date.now() / 1000 || !Array.isArray(restored.events) || restored.events.length !== 1 ||
            !Array.isArray(restored.warnings) || restored.warnings.some(w => typeof w !== "string" || w.length > 256)) throw Error("invalid restore");
        const normalized = root.HomeMoleculeCandidates.normalize(restored.events[0]);
        if (!normalized?.reference || !same(pointer(normalized.reference), p) || !same(normalized.reference, restored.reference)) throw Error("restore mismatch");
        const byId = new Map(normalized.candidates.map(c => [c.candidate_id, c]));
        const warnings = [...new Set([...normalized.warnings, ...restored.warnings])];
        if (restored.source_status === "partial") warnings.unshift("来源工作流仅部分完成；未完成分支的结果不可用。");
        const payload = {...normalized, warnings, candidates: normalized.reference.ordered_keys.map(k => byId.get(k[1]))};
        if (!didMount(render(payload), payload)) throw Error("restore not mounted");
        collections = new Map([[p.presentation_id, {reference: normalized.reference, confirmed: true}]]);
        active = p.presentation_id; selection = null; changed();
      } catch (_) {
        if (ticket === epoch && selectedAtStart === selectionRevision) {clear(); onStatus("科研引用无法恢复，请重新选择候选。");}
      }
    }
    return {clear, startRequest, select, present, restore, outgoing};
  }
  root.HomeScientificReferences = {createController};
})(window);
