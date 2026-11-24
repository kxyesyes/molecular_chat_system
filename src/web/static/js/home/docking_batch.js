(function () {
  "use strict";

  const labels = {queued: "排队中", running: "运行中", completed: "已完成", partial: "部分完成",
    failed: "失败", cancel_requested: "正在取消", cancelled: "已取消", timed_out: "已超时", timeout: "已超时"};
  const storageKey = "medchat.docking.batch.task.v1";
  const endpoint = (task, action) => `/api/agent/docking/tasks/${task}/${action}`;
  const ligandValid = row => row && Number.isSafeInteger(row.ordinal) && row.ordinal > 0 &&
    typeof row.smiles === "string" && row.smiles.length > 0;
  function validBatch(batch, task) {
    return batch?.task_id === task && /^[0-9a-z]{4}$/i.test(batch.pdb_id) &&
      Array.isArray(batch.ligands) && batch.ligands.length >= 2 && batch.ligands.every(ligandValid) &&
      new Set(batch.ligands.map(row => row.ordinal)).size === batch.ligands.length;
  }
  function sameLigands(rows, expected) {
    return Array.isArray(rows) && rows.length === expected.length &&
      rows.every((row, i) => row?.ordinal === expected[i].ordinal && row.smiles === expected[i].smiles);
  }
  function create(options) {
    const doc = options.document, timer = options.setTimer || setTimeout, untimer = options.clearTimer || clearTimeout;
    const views = new Map();
    function add(parent, tag, text) {
      const el = doc.createElement(tag);
      if (text !== undefined) el.textContent = text;
      return parent.appendChild(el);
    }
    function button(parent, text, action) {
      const el = add(parent, "button", text);
      el.type = "button";
      el.addEventListener("click", action);
      return el;
    }
    function rowTable(parent, cols) {
      const scroll = add(parent, "div");
      scroll.className = "docking-batch-table";
      const table = add(scroll, "table"), header = add(add(table, "thead"), "tr");
      cols.forEach(label => add(header, "th", label).setAttribute("scope", "col"));
      return add(table, "tbody");
    }
    function ligandCells(parent, ligand) {
      const row = add(parent, "tr");
      add(row, "th", `候选 ${ligand.ordinal}`).setAttribute("scope", "row");
      add(row, "td", ligand.smiles).className = "docking-batch-smiles";
      return row;
    }
    function review(host, event, current, submit) {
      const batch = event.batch, prepared = event.preview.prepared;
      const params = prepared.confirmed_parameters, ref = prepared.reference_residue;
      const facts = add(host, "dl");
      facts.className = "docking-facts";
      for (const [label, value] of [["实验受体", batch.pdb_id],
        ["参考残基", `模型 ${ref.model} · 链 ${ref.chain} · ${ref.resname} ${ref.resseq}${ref.icode}`],
        ["盒中心 (Å)", params.center.join(", ")], ["盒尺寸 (Å)", params.size.join(", ")],
        ["搜索强度", params.exhaustiveness], ["每个配体的构象上限", params.num_modes]]) {
        add(facts, "dt", label); add(facts, "dd", value);
      }
      const rows = rowTable(host, ["编号", "配体 SMILES"]);
      batch.ligands.forEach(ligand => ligandCells(rows, ligand));
      const actions = add(host, "div");
      const preview = button(actions, "生成批量确认单", async () => {
        if (!current() || preview.disabled) return;
        preview.disabled = true;
        preview.textContent = "正在核对清单…";
        try {
          const proof = await options.request(endpoint(batch.task_id, "batch-preview"), {revision: event.revision});
          if (!current()) return;
          if (proof.task_id !== batch.task_id || proof.pdb_id !== batch.pdb_id ||
              !sameLigands(proof.ligands, batch.ligands) || proof.receptor_sha256 !== prepared.derived_sha256 ||
              !Object.keys(ref).every(key => proof.reference_residue?.[key] === ref[key]) ||
              !Object.keys(params).every(key => JSON.stringify(proof.parameters?.[key]) === JSON.stringify(params[key])) ||
              !/^[a-f0-9]{64}$/.test(proof.binding_digest) || !/^[a-f0-9]{64}$/.test(proof.approval_nonce))
            throw new Error("invalid preview");
          actions.replaceChildren();
          const approve = button(actions, "确认并运行批量对接", () => {
            if (!current() || approve.disabled) return;
            approve.disabled = true;
            return submit({approval_nonce: proof.approval_nonce, binding_digest: proof.binding_digest, confirm: true});
          });
        } catch (_) {
          if (current()) {
            actions.replaceChildren();
            add(actions, "p", "批量确认单无法核对，请取消后重新发起。").setAttribute("role", "alert");
          }
        }
      });
    }
    function display(view, result) {
      const rows = result.rows;
      if (result.task_id !== view.task || !Object.hasOwn(labels, result.status) ||
          !/^[a-z0-9]{4}$/i.test(result.pdb_id) || !Array.isArray(rows) || rows.length < 2 ||
          !rows.every(row => ligandValid(row) && Object.hasOwn(labels, row.status)) ||
          new Set(rows.map(row => row.ordinal)).size !== rows.length ||
          view.expected && (view.expected.pdb_id !== result.pdb_id || !sameLigands(rows, view.expected.ligands)))
        throw new Error("invalid batch status");
      view.expected ||= {pdb_id: result.pdb_id, ligands: rows};
      view.host ||= options.mountRestoredBatch();
      const host = view.host;
      host.replaceChildren(); host.className = "docking-consent-card";
      host.setAttribute("data-task-id", view.task);
      add(host, "h3", `批量对接 · ${labels[result.status]}`).setAttribute("role", "status");
      const ok = rows.filter(row => row.status === "completed").length;
      const failed = rows.filter(row => ["failed", "timed_out", "timeout"].includes(row.status)).length;
      add(host, "p", `受体 ${result.pdb_id} · 成功 ${ok} · 失败 ${failed} · 共 ${rows.length} 个配体`);
      const body = rowTable(host, ["编号", "配体 SMILES", "状态", "最佳 Vina 预测评分 (kcal/mol)", "构象数", "构象文件"]);
      rows.forEach(ligand => {
        const row = ligandCells(body, ligand), status = add(row, "td", labels[ligand.status]);
        const done = ligand.status === "completed";
        if (ligand.error) add(status, "div", ligand.error);
        add(row, "td", done && Number.isFinite(ligand.best_energy) ? ligand.best_energy : "—");
        add(row, "td", done && Number.isSafeInteger(ligand.total_poses) ? ligand.total_poses : "—");
        const download = add(row, "td"), path = endpoint(view.task, `batch-poses/${ligand.ordinal}`);
        if (done && ligand.download_path === path) {
          const link = add(download, "a", "下载");
          link.setAttribute("href", path); link.setAttribute("aria-label", `下载候选 ${ligand.ordinal} 的构象`);
        } else download.textContent = "—";
      });
      if (["running", "queued", "cancel_requested"].includes(result.status)) {
        if (view.authoring && !view.cancelling && result.status !== "cancel_requested") {
          const cancel = button(host, "取消批量对接", async () => {
            if (cancel.disabled || view.cancelButton !== cancel) return;
            cancel.disabled = true;
            view.cancelling = true;
            view.version += 1;
            untimer(view.pollTimer);
            try {await options.request(endpoint(view.task, "batch-cancel"), {});}
            catch (_) {view.cancelling = false;}
            return refresh(view);
          });
          view.cancelButton = cancel;
        } else view.cancelButton = null;
        view.pollTimer = timer(() => refresh(view), 2000);
      } else view.cancelButton = null;
    }
    async function refresh(view) {
      const version = view.version;
      try {
        const result = await options.request(endpoint(view.task, "batch-status"));
        if (view.version === version) display(view, result);
      } catch (_) {
        if (view.version !== version) return;
        view.host ||= options.mountRestoredBatch();
        view.host.className = "docking-consent-card";
        const message = add(view.host, "div");
        add(message, "p", "暂时无法读取批次状态。").setAttribute("role", "alert");
        const retry = button(message, "重新读取状态", () => {
          if (retry.disabled) return;
          retry.disabled = true;
          view.host.removeChild(message);
          return refresh(view);
        });
      }
    }
    async function submit(host, batch, proof) {
      const task = batch.task_id, view = {task, host, expected: batch, authoring: true, version: 0};
      views.set(task, view);
      try {options.storage?.setItem(storageKey, task);} catch (_) { /* Optional tab storage. */ }
      host.replaceChildren();
      add(host, "p", "正在提交批量对接…").setAttribute("role", "status");
      try {display(view, await options.request(endpoint(task, "batch-approve"), proof));}
      catch (_) {await refresh(view);}
    }
    function restore() {
      let task;
      try {task = options.storage?.getItem(storageKey);} catch (_) {return;}
      if (!task || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(task) || views.has(task)) return;
      const view = {task, host: null, version: 0};
      views.set(task, view);
      return refresh(view);
    }
    return {review, submit, restore};
  }
  window.HomeDockingBatch = Object.freeze({validBatch, create});
})();
