(function () {
  "use strict";
  // Live, socket-bound UI only. No storage, restored grants or scientific inference.
  const id = value => typeof value === "string" && /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(value);
  const hash = value => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
  const integer = value => Number.isSafeInteger(value) && value >= 0;
  const finite = value => typeof value === "number" && Number.isFinite(value);
  const vector = value => Array.isArray(value) && value.length === 3 && value.every(finite);
  const record = value => value !== null && typeof value === "object" && !Array.isArray(value);
  const fields = (value, allowed, required = allowed) => record(value) &&
    Object.keys(value).every(key => allowed.includes(key)) && required.every(key => Object.hasOwn(value, key));
  const scalar = value => typeof value === "string" && value.length <= 2048 ||
    typeof value === "boolean" || finite(value);
  const scalars = (value, allowed, required = []) => fields(value, allowed, required) && Object.values(value).every(scalar);
  const copy = value => JSON.parse(JSON.stringify(value));
  const sameData = (left, right) => left === right ||
    Array.isArray(left) && Array.isArray(right) && left.length === right.length && left.every((item, index) => sameData(item, right[index])) ||
    record(left) && record(right) && Object.keys(left).length === Object.keys(right).length &&
      Object.keys(left).every(key => Object.hasOwn(right, key) && sameData(left[key], right[key]));
  const parameterKeys = ["center", "size", "exhaustiveness", "num_modes"];
  const parameters = (value, ready = false) => fields(value, [...parameterKeys, ...(ready ? ["energy_range"] : [])]) &&
    vector(value.center) && vector(value.size) && value.size.every(n => n > 0) &&
    integer(value.exhaustiveness) && value.exhaustiveness > 0 && integer(value.num_modes) && value.num_modes > 0 &&
    (!ready || finite(value.energy_range));
  const sections = ["protein_selection", "reference_residue", "hetero_choices", "conformer_choices",
    "residues", "conformer_constraints", "connections", "limitations"];
  const sourceValid = value => fields(value, ["pdb_id", "source_sha256", "revision_version", "input_ready",
    "execution_authorized", "target", "choice_counts"]) && hash(value.source_sha256) &&
    typeof value.pdb_id === "string" && /^[0-9A-Za-z]{4}$/.test(value.pdb_id) &&
    value.input_ready === false && value.execution_authorized === false &&
    scalars(value.target, ["uniprot_id", "taxonomy_id"], ["uniprot_id", "taxonomy_id"]) &&
    fields(value.choice_counts, sections) && Object.values(value.choice_counts).every(integer);
  const preparedKeys = ["input_kind", "source_sha256", "derived_sha256", "ligand_sha256", "selection_sha256",
    "refinement_revision", "pocket_rule", "pocket_margin", "pocket_atom_count", "input_ready", "execution_authorized",
    "receptor_ref", "ligand_ref", "parameters_suggestion", "confirmed_parameters", "reference_residue", "ligand_provenance"];
  function preparedValid(value) {
    if (!fields(value, preparedKeys, preparedKeys.filter(key => key !== "confirmed_parameters"))) return false;
    if (!["source_sha256", "derived_sha256", "ligand_sha256", "selection_sha256"].every(key => hash(value[key])) ||
      !["receptor_ref", "ligand_ref"].every(key => typeof value[key] === "string" && /^sha256:[a-f0-9]{64}$/.test(value[key])) ||
      value.input_ready !== false || value.execution_authorized !== false || !integer(value.refinement_revision) ||
      !parameters(value.parameters_suggestion) || value.confirmed_parameters && !parameters(value.confirmed_parameters)) return false;
    return scalars(value.reference_residue, ["model", "chain", "resname", "resseq", "icode"], ["model", "chain", "resname", "resseq", "icode"]) &&
      scalars(value.ligand_provenance, ["input_kind", "rdkit_version", "method", "random_seed", "embedding_attempts",
        "explicit_hydrogens", "optimizer", "max_iterations", "optimizer_return_code"]) &&
      ["input_kind", "pocket_rule", "pocket_margin", "pocket_atom_count"].every(key => scalar(value[key]));
  }
  const consentKeys = ["schema", "preparation_id", "task_id", "trace_id", "binding_digest", "expires_at_ms",
    "receptor", "ligand", "parameters", "tool_policy"];
  function consentValid(value, event, nonce = false) {
    return fields(value, [...consentKeys, ...(nonce ? ["approval_nonce"] : [])]) &&
      value.schema === "DockingConsent@1" && value.preparation_id === event.preparation_id &&
      value.task_id === event.task_id && value.trace_id === event.request_id && hash(value.binding_digest) &&
      integer(value.expires_at_ms) && value.expires_at_ms <= 8640000000000000 &&
      (!nonce || hash(value.approval_nonce)) && parameters(value.parameters, true) &&
      ["receptor", "ligand"].every(key => fields(value[key], ["name", "size", "sha256"]) &&
        typeof value[key].name === "string" && value[key].name.length <= 255 && integer(value[key].size) && hash(value[key].sha256)) &&
      scalars(value.tool_policy, ["label", "digest", "execution_backend", "adapter_contract_version", "vina_limit_seconds", "operation_limit_seconds"],
        ["label", "digest", "execution_backend", "adapter_contract_version", "vina_limit_seconds", "operation_limit_seconds"]) &&
      hash(value.tool_policy.digest) && value.tool_policy.execution_backend === "local" &&
      integer(value.tool_policy.vina_limit_seconds) && value.tool_policy.vina_limit_seconds > 0 && value.tool_policy.vina_limit_seconds <= 300 &&
      integer(value.tool_policy.operation_limit_seconds) && value.tool_policy.operation_limit_seconds > 0 && value.tool_policy.operation_limit_seconds <= 420;
  }
  const scienceKeys = ["tool_name", "tool_version", "execution_backend", "input_hash", "config_hash", "tool_policy_digest",
    "source_digest", "original_receptor_digest", "ligand_identity_digest", "projection_digest", "score_kind", "pose_count",
    "best_energy_kcal_mol", "energy_unit", "pose"];
  function scienceValid(value, event) {
    return fields(value, scienceKeys) && value.tool_name === "molecular_docking" && value.execution_backend === "local" &&
      typeof value.tool_version === "string" && value.tool_version.length <= 128 && typeof value.score_kind === "string" && value.score_kind.length <= 128 &&
      ["input_hash", "config_hash", "tool_policy_digest", "source_digest", "original_receptor_digest", "ligand_identity_digest", "projection_digest"].every(key => hash(value[key])) &&
      finite(value.best_energy_kcal_mol) && integer(value.pose_count) && value.pose_count > 0 && value.energy_unit === "kcal/mol" &&
      fields(value.pose, ["artifact_id", "size_bytes", "sha256", "download_path"]) && value.pose.artifact_id === "pose" &&
      integer(value.pose.size_bytes) && value.pose.size_bytes > 0 && hash(value.pose.sha256) &&
      value.pose.download_path === `/api/agent/docking/tasks/${event.task_id}/artifacts/pose`;
  }
  const statuses = {ACTIVE: ["running", "执行中"], AWAITING_INPUT: ["awaiting_input", "等待补充输入"],
    READY: ["awaiting_consent", "等待人工确认 · 尚未运行 Vina"], SUCCEEDED: ["terminal", "对接计算完成"],
    FAILED: ["terminal", "对接失败"], CANCELLED: ["terminal", "已取消"], TIMED_OUT: ["terminal", "已超时"], UNKNOWN: ["terminal", "结果未确认"]};
  const eventKeys = ["type", "schema", "request_id", "root_trace_id", "task_id", "preparation_id", "origin_turn_id", "revision",
    "sequence", "send_generation", "phase", "segment_complete", "terminal", "view_status", "task_status", "cleanup_status",
    "reason_code", "persistence_pending", "missing_fields", "preview", "result"];
  function normalize(value) {
    try {
      if (!fields(value, eventKeys) || value.type !== "docking_event" || value.schema !== "DockingView@1" ||
        !["request_id", "root_trace_id", "task_id", "preparation_id", "origin_turn_id"].every(key => id(value[key])) ||
        value.request_id !== value.root_trace_id || !["revision", "sequence", "send_generation"].every(key => integer(value[key])) ||
        value.send_generation < 1 || !Object.hasOwn(statuses, value.view_status) || value.phase !== statuses[value.view_status][0] ||
        typeof value.terminal !== "boolean" || value.terminal !== (value.phase === "terminal") || typeof value.segment_complete !== "boolean" ||
        typeof value.persistence_pending !== "boolean" || value.task_status !== "unavailable" ||
        !["settled", "pending"].includes(value.cleanup_status) || typeof value.reason_code !== "string" || value.reason_code.length > 128 ||
        !Array.isArray(value.missing_fields) || value.missing_fields.length > 6 || new Set(value.missing_fields).size !== value.missing_fields.length ||
        !value.missing_fields.every(key => ["receptor_ref", "ligand_ref", ...parameterKeys].includes(key))) return null;
      if (value.preview !== null && (!fields(value.preview, ["source", "prepared", "consent"], []) ||
        value.terminal || !value.segment_complete ||
        value.preview.source && !sourceValid(value.preview.source) || value.preview.prepared && !preparedValid(value.preview.prepared) ||
        value.preview.consent && !consentValid(value.preview.consent, value))) return null;
      if (value.view_status === "SUCCEEDED" ? !scienceValid(value.result, value) : value.result !== null) return null;
      const raw = JSON.stringify(value);
      if (new TextEncoder().encode(raw).length > 65536) return null;
      return JSON.parse(raw);
    } catch (_) { return null; }
  }

  function create(options) {
    const doc = options.document || document, now = options.now || Date.now;
    const timer = options.setTimer || setTimeout, untimer = options.clearTimer || clearTimeout;
    let socket = null, enabled = false, pending = null;
    const cards = new Map();
    const node = (tag, text, parent) => {const el = doc.createElement(tag); if (text !== undefined) el.textContent = text; if (parent) parent.appendChild(el); return el;};
    const clear = (state, key) => {if (state[key] !== null) untimer(state[key]); state[key] = null;};
    function blocked() { return [...cards.values()].some(state => state.socket === socket && !state.readOnly && (state.blocked || state.pending)); }
    function notify() { options.onBusy?.(blocked()); }
    function live(state, revision = state.event.revision) {
      return enabled && state.socket === socket && !state.readOnly && !state.stopped && state.event.revision === revision && !state.event.terminal;
    }
    function forget(state) { state.proof = null; clear(state, "expiryTimer"); }
    function stop(state, message, heading = null) {
      state.stopped = true; state.notice = message; state.stopHeading = heading; forget(state); clear(state, "pollTimer"); clear(state, "workTimer");
      state.source?.dispose(); state.source = null; render(state); notify();
    }
    function watch(state, readOnly = false) {
      if (!enabled || !socket || !readOnly && state.socket !== socket) return false;
      if (readOnly) {state.socket = socket; state.resetSequence = true;}
      return options.send(socket, {type: "docking_watch", task_id: state.event.task_id});
    }
    function poll(state) {
      clear(state, "pollTimer");
      if (state.socket === socket && !state.readOnly && !state.stopped &&
        (state.event.phase === "running" || state.blocked)) {
        state.pollTimer = timer(() => {state.pollTimer = null; watch(state); poll(state);}, 2000);
      }
    }
    function currentKey(state) {return [state.event.request_id, state.event.task_id, state.event.preparation_id, state.event.revision].join(":");}
    async function request(state, path, body) {
      const key = currentKey(state), origin = state.socket;
      if (!live(state) || state.pending) return null;
      state.pending = true; state.blocked = true; render(state); notify();
      try {
        const result = await options.request(path, body);
        if (!live(state) || state.socket !== origin || currentKey(state) !== key) return null;
        return result;
      } catch (_) {
        if (state.socket === origin && currentKey(state) === key) stop(state, "请求未确认，不能重试授权。请取消本次请求后重新发起。");
        return null;
      } finally {state.pending = false; render(state); notify();}
    }
    const table = (parent, rows) => {
      const list = node("dl", undefined, parent); list.className = "docking-facts";
      rows.forEach(([label, value]) => {node("dt", label, list); node("dd", Array.isArray(value) ? value.join(", ") : String(value), list);});
    };
    function render(state) {
      const event = state.event;
      state.source?.dispose(); state.source = null;
      state.epoch += 1;
      const epoch = state.epoch;
      const validClick = () => state.epoch === epoch && live(state) && !state.pending;
      const box = state.box;
      box.replaceChildren(); box.className = "docking-consent-card"; box.setAttribute("data-task-id", event.task_id);
      const header = node("div", undefined, box); header.className = "docking-card-heading";
      node("span", "MOLECULAR DOCKING", header).className = "docking-eyebrow";
      const heading = node("h3", state.stopped ? (state.stopHeading || "结果未确认 / 本次确认已失效") : statuses[event.view_status][1], header);
      heading.setAttribute("role", "status");
      node("p", `任务 ${event.task_id} · 修订 ${event.revision}`, box).className = "docking-identity";
      if (state.notice) node("p", state.notice, box).className = "docking-notice";
      if (state.drainedFailure) return;
      if (event.cleanup_status === "pending" || event.persistence_pending || !event.segment_complete)
        node("p", "后台工作或清理尚未结束；这不是已完成的科学结果。", box);
      const action = (label, handler, allowed = true) => {
        const el = node("button", label, box); el.type = "button"; el.disabled = !allowed;
        el.addEventListener("click", handler); return el;
      };
      if (state.readOnly) {action("刷新状态（只读）", () => {if (state.epoch === epoch) watch(state, true);}); return;}
      if (event.view_status === "SUCCEEDED" && !state.stopped) {
        const result = event.result;
        node("p", "以下为 Vina 计算预测，不代表实验验证的结合活性。", box);
        table(box, [["最佳结合能 (kcal/mol)", result.best_energy_kcal_mol], ["构象数", result.pose_count],
          ["工具", result.tool_version], ["评分类型", result.score_kind], ["构象文件 SHA-256", result.pose.sha256],
          ["输入 SHA-256", result.input_hash], ["配置 SHA-256", result.config_hash], ["工具策略 SHA-256", result.tool_policy_digest]]);
        const link = node("a", "下载已验证构象", box); link.setAttribute("href", result.pose.download_path); return;
      }
      if (event.terminal) {
        // Logical terminal status can precede physical/durable cleanup. Keep
        // a status-only path without reviving grants or scientific rendering.
        if (state.blocked) action("刷新状态", () => {if (state.epoch === epoch) watch(state);}, state.socket === socket);
        return;
      }
      if (event.preview?.source && !state.stopped) {
        const source = event.preview.source;
        table(box, [["实验结构", source.pdb_id], ["靶点", source.target.uniprot_id], ["物种 Taxonomy", source.target.taxonomy_id], ["结构 SHA-256", source.source_sha256]]);
        if (event.reason_code === "scientific_selection_required" && !state.pending && window.HomeDockingSourceSelection) {
          const host = node("div", undefined, box), revision = event.revision;
          state.source = window.HomeDockingSourceSelection.mount(host, {event: copy(event),
            isCurrent: () => state.epoch === epoch && live(state, revision),
            request: async (path, body) => {
              if (state.epoch !== epoch || !live(state, revision)) throw new Error("stale selection");
              if (path.endsWith("/selection")) {state.blocked = true; notify();}
              return options.request(path, body);
            }, onSubmitted: () => {if (live(state, revision)) watch(state);},
            onError: () => {if (live(state, revision)) stop(state, "结构选择未确认，请取消后重新发起。");}});
        }
      }
      const prepared = event.preview?.prepared;
      if (prepared && !state.stopped) {
        table(box, [["口袋参考残基", Object.values(prepared.reference_residue).join(" / ")],
          ["口袋规则", prepared.pocket_rule], ["边距 (Å)", prepared.pocket_margin], ["口袋原子数", prepared.pocket_atom_count],
          ["原始结构 SHA-256", prepared.source_sha256],
          ["受体 SHA-256", prepared.derived_sha256], ["配体 SHA-256", prepared.ligand_sha256],
          ["结构选择 SHA-256", prepared.selection_sha256], ["3D 配体生成", JSON.stringify(prepared.ligand_provenance)]]);
        node("p", "配体三维坐标来自所示计算方法；口袋来自明确选择的共晶参考残基，尚未运行对接。", box);
        if (event.reason_code === "parameters_confirmation_required") {
          const inputs = {};
          parameterKeys.forEach(key => {
            const label = node("label", ({center: "中心 (Å，x,y,z)", size: "盒尺寸 (Å，x,y,z)", exhaustiveness: "搜索强度", num_modes: "构象上限"})[key], box);
            const input = node("input", undefined, label); input.type = "text";
            input.value = Array.isArray(prepared.parameters_suggestion[key]) ? prepared.parameters_suggestion[key].join(", ") : String(prepared.parameters_suggestion[key]);
            inputs[key] = input; input.disabled = state.pending;
          });
          action("确认参数（不运行对接）", async () => {
            if (!validClick()) return;
            const values = {};
            for (const key of parameterKeys) {
              const raw = inputs[key].value.trim();
              values[key] = ["center", "size"].includes(key) ? raw.split(",").map(item => item.trim() === "" ? NaN : Number(item)) : raw === "" ? NaN : Number(raw);
            }
            if (!parameters(values)) {state.notice = "请输入有效的三维坐标、正盒尺寸及正整数参数。"; render(state); return;}
            const result = await request(state, `/api/agent/docking/tasks/${event.task_id}/parameters`, {
              revision: event.revision, receptor_ref: prepared.receptor_ref, ligand_ref: prepared.ligand_ref, parameters: values});
            if (result) watch(state);
          }, validClick());
        }
        if (event.reason_code === "inputs_confirmed_staging_required") {
          table(box, parameterKeys.map(key => [key, prepared.confirmed_parameters?.[key] ?? prepared.parameters_suggestion[key]]));
          action("生成本次确认单", async () => {
            if (!validClick()) return;
            const revision = event.revision;
            const result = await request(state, "/api/agent/docking/preparations", {root: event.request_id,
              revision, receptor_ref: prepared.receptor_ref, ligand_ref: prepared.ligand_ref});
            if (!result || !live(state, revision)) return;
            if (result.root_id !== event.request_id || result.task_id !== event.task_id || result.preparation_id !== event.preparation_id ||
              result.phase !== "READY" || !consentValid(result.consent_preview, event, true) || result.consent_preview.expires_at_ms <= now()) {
              stop(state, "未收到有效的一次性确认单，请取消后重新发起。"); return;
            }
            state.proof = copy(result.consent_preview);
            clear(state, "expiryTimer");
            state.expiryTimer = timer(() => stop(state, "确认单已过期，请重新发起；不会自动运行。"), state.proof.expires_at_ms - now());
            render(state); watch(state);
          }, validClick() && !state.proof);
        }
      }
      const consent = event.preview?.consent;
      if (event.view_status === "READY" && consent && !state.stopped) {
        table(box, [["受体文件", consent.receptor.name], ["受体字节数", consent.receptor.size], ["受体 SHA-256", consent.receptor.sha256],
          ["配体文件", consent.ligand.name], ["配体字节数", consent.ligand.size], ["配体 SHA-256", consent.ligand.sha256],
          ["中心 (Å)", consent.parameters.center], ["盒尺寸 (Å)", consent.parameters.size], ["搜索强度", consent.parameters.exhaustiveness],
          ["构象上限", consent.parameters.num_modes], ["能量范围", consent.parameters.energy_range],
          ["工具策略", consent.tool_policy.label], ["适配器契约版本", consent.tool_policy.adapter_contract_version],
          ["策略 SHA-256", consent.tool_policy.digest], ["确认绑定 SHA-256", consent.binding_digest],
          ["Vina 限时 (秒)", consent.tool_policy.vina_limit_seconds], ["整段限时 (秒)", consent.tool_policy.operation_limit_seconds],
          ["确认单到期时间", new Date(consent.expires_at_ms).toISOString()]]);
        const hasProof = () => state.proof && consentKeys.every(key => sameData(state.proof[key], consent[key])) && state.proof.expires_at_ms > now();
        if (!hasProof()) node("p", state.approvalSent ? "本次确认已提交，正在等待执行状态；不会重复提交。" :
          "一次性确认凭据已丢失、过期或与预览不一致；请取消后重新发起，不能从刷新状态恢复授权。", box);
        action("确认并运行 Vina", async () => {
          if (!validClick() || !hasProof() || state.approvalSent) return;
          const proof = state.proof; state.approvalSent = true; forget(state);
          const body = {approval_nonce: proof.approval_nonce, binding_digest: proof.binding_digest, confirm: true};
          // This is the only approval dispatch, reachable only from this click handler.
          const result = await request(state, `/api/agent/docking/preparations/${event.preparation_id}/approve`, body);
          if (result && !state.stopped) {
            state.workTimer = timer(() => stop(state, "本地等待已超时，结果未确认；请取消并等待后台清理。"), consent.tool_policy.operation_limit_seconds * 1000);
            watch(state);
          }
        }, validClick() && Boolean(hasProof()) && !state.approvalSent);
      }
      if (event.view_status === "AWAITING_INPUT" && !prepared && !event.preview?.source && !state.stopped) {
        node("p", "缺少：" + event.missing_fields.map(key => ({receptor_ref: "受体结构", ligand_ref: "配体输入", center: "口袋中心", size: "盒尺寸", exhaustiveness: "搜索强度", num_modes: "构象上限"})[key]).join("、"), box);
        const label = node("label", "补充信息（已固定的输入不可更改）", box), input = node("textarea", undefined, label);
        input.maxLength = 8192; input.disabled = state.pending;
        action("补充本次请求", () => {
          if (!validClick() || !input.value.trim() || input.value.length > 8192) return;
          if (options.send(socket, {type: "docking_refine", request_id: event.request_id, revision: event.revision, message: input.value})) {
            state.blocked = true; state.notice = "正在核对补充信息…"; render(state); notify(); watch(state);
          }
        }, validClick());
      }
      const revoke = event.view_status === "READY" && !state.approvalSent;
      action(revoke ? "撤销本次确认" : "取消本次请求", async () => {
        if (state.epoch !== epoch || state.readOnly || state.cancelSent || state.socket !== socket) return;
        state.cancelSent = true; state.blocked = true; stop(state, "已请求取消；正在等待后台清理确认。不会展示迟到的成功结果。");
        try {
          await options.request(revoke ? `/api/agent/docking/preparations/${event.preparation_id}/revoke` : `/api/agent/docking/tasks/${event.task_id}/cancel`);
        } catch (_) {state.notice = "取消结果未确认，请手动刷新状态；不会重新提交。"; render(state);}
        watch(state);
      }, !state.cancelSent && state.socket === socket);
      action("刷新状态", () => {if (state.epoch === epoch) watch(state);}, state.socket === socket);
    }
    return {
      configure(currentSocket, available) {socket = currentSocket; enabled = available === true; pending = null; notify();},
      begin(currentSocket, host) {
        if (enabled && currentSocket === socket && host) {
          for (const state of cards.values()) state.ownsComposer = false;
          pending = {socket, host, accepted: null};
        }
      },
      accepted(message, currentSocket) {
        if (pending?.socket === currentSocket && currentSocket === socket && !pending.accepted && id(message.trace_id) && id(message.turn_id))
          pending.accepted = {root: message.trace_id, turn: message.turn_id};
      },
      blocked,
      handle(message, currentSocket) {
        if (message?.type === "error" && ["docking_unavailable", "docking_request_active", "docking_in_progress", "invalid_docking_control"].includes(message.code)) {
          if (!enabled || currentSocket !== socket) return false;
          const rejected = pending?.socket === socket && !pending.accepted && message.docking_request_rejected === true;
          const state = cards.get(message.trace_id);
          const attached = state?.socket === socket && !state.readOnly && state.event.origin_turn_id === message.turn_id;
          const awaiting = pending?.socket === socket && pending.accepted &&
            message.turn_id === pending.accepted.turn && message.trace_id === pending.accepted.root;
          const preflightUnavailable = message.docking_preflight_unavailable === true;
          const sourceUnavailable = message.docking_source_unavailable === true;
          if ((sourceUnavailable || preflightUnavailable) && (attached || awaiting)) {
            const notice = message.message || "结构来源或实验身份未通过对接认证；本次未启动对接。请重新选择并确认结构。";
            const heading = preflightUnavailable ? "结构预处理未完成 / 对接未启动" : "结构未认证 / 对接未启动";
            if (attached) {
              state.blocked = false; state.ownsComposer = false; state.drainedFailure = true;
              stop(state, notice, heading);
              options.onSegment?.({origin_turn_id: message.turn_id, request_id: message.trace_id});
            } else {
              pending.failureBox ||= node("p", undefined, pending.host);
              pending.failureBox.textContent = notice;
              pending = null;
            }
            notify();
            return true;
          }
          const failure = message.code === "docking_unavailable" && typeof message.docking_segment_complete === "boolean" && (attached || awaiting);
          if (rejected) {
            node("p", "对接请求未完成；未获得新的执行授权。请检查状态后重新发起。", pending.host);
            pending = null;
            options.onRejected?.();
            notify();
          } else if (failure) {
            const complete = message.docking_segment_complete;
            const notice = complete ? "对接请求未完成；后台已清理，未获得新的执行授权。" : "对接请求未完成；后台清理尚未确认，请等待或重新连接查看状态。";
            let release = complete;
            if (attached) {
              if (state.drainedFailure) return true;
              release = complete && state.ownsComposer;
              state.blocked = !complete;
              if (complete) {state.drainedFailure = true; state.ownsComposer = false;}
              stop(state, notice);
            } else {
              pending.failed = true;
              pending.failureBox ||= node("p", undefined, pending.host);
              pending.failureBox.textContent = notice;
              if (complete) pending = null;
            }
            if (release) options.onSegment?.({origin_turn_id: message.turn_id, request_id: message.trace_id});
            notify();
          } else if (message.docking_request_rejected === true) {
            return false; // A2 abandon can be pending without a chat DOM host.
          } else options.onError?.("对接控制未被接受；当前请求状态尚未确认。");
          return true;
        }
        if (message?.type !== "docking_event") return false;
        if (!enabled || currentSocket !== socket) return true;
        const event = normalize(message); if (!event) return true;
        let state = cards.get(event.request_id);
        if (!state) {
          if (!pending?.accepted || pending.socket !== socket || pending.accepted.root !== event.request_id || pending.accepted.turn !== event.origin_turn_id) return true;
          const notice = pending.failureBox?.textContent;
          pending.failureBox?.parentNode?.removeChild(pending.failureBox);
          state = {socket, event, box: node("section", undefined, pending.host), epoch: 0, source: null, pending: false, ownsComposer: true,
            proof: null, stopped: Boolean(pending.failed), notice, readOnly: false, blocked: false, pollTimer: null, expiryTimer: null, workTimer: null};
          cards.set(event.request_id, state); pending = null;
          // Old DOM may remain visible, but no more than 32 live controller records.
          if (cards.size > 32) {const old = cards.values().next().value; old.source?.dispose(); ["pollTimer", "expiryTimer", "workTimer"].forEach(key => clear(old, key)); cards.delete(old.event.request_id);}
        } else {
          if (state.drainedFailure) return true;
          if (state.socket !== socket || ["task_id", "preparation_id", "origin_turn_id"].some(key => state.event[key] !== event[key]) ||
            event.revision < state.event.revision || !state.resetSequence && (event.sequence <= state.event.sequence || event.send_generation <= state.event.send_generation)) return true;
          if (event.revision !== state.event.revision) forget(state);
          state.resetSequence = false;
        }
        const prior = state.event;
        state.event = event;
        options.onView?.(copy(event));
        state.blocked = !event.segment_complete || event.cleanup_status !== "settled" || event.persistence_pending;
        if (event.terminal) {forget(state); clear(state, "workTimer");}
        if (event.terminal && event.view_status !== "SUCCEEDED") state.stopped = true;
        if (event.preview?.consent && state.proof && !consentKeys.every(key => sameData(state.proof[key], event.preview.consent[key]))) forget(state);
        const presentation = value => JSON.stringify({...value, sequence: 0, send_generation: 0});
        if (state.epoch === 0 || presentation(prior) !== presentation(event)) render(state);
        if (!state.blocked && state.ownsComposer) {
          state.ownsComposer = false;
          options.onSegment?.(copy(event));
        }
        poll(state); notify(); return true;
      },
      disconnect(currentSocket) {
        if (socket !== currentSocket) return;
        enabled = false; pending = null;
        for (const state of cards.values()) if (state.socket === currentSocket) {
          state.readOnly = true; state.blocked = false; stop(state, "连接已断开，结果未确认。重连后仅可查看状态，不能恢复执行授权。");
        }
      },
      timeout(currentSocket) {
        if (currentSocket !== socket) return;
        if (pending?.socket === currentSocket && pending.accepted) {
          pending.failed = true;
          pending.failureBox ||= node("p", undefined, pending.host);
          pending.failureBox.textContent = "本地等待已超时，结果未确认；不会接受迟到的成功。";
        }
        for (const state of cards.values()) if (state.socket === currentSocket && !state.event.terminal)
          stop(state, "本地等待已超时，结果未确认；不会接受迟到的成功。");
      },
    };
  }
  window.HomeDockingConsent = Object.freeze({create, normalize});
})();
