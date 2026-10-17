(function (global) {
  "use strict";

  // Independent limits: complete page JSON <= 2 MiB of compact UTF-8, <=150000
  // JSON values/containers (property names cost bytes, not extra value nodes),
  // and <=4096 inventory rows. Never enlarge budgets or truncate to make a source fit.
  const MAX_ROWS = 4096, MAX_BYTES = 2 * 1024 * 1024, MAX_NODES = 150000;
  const SECTIONS = "protein_selection reference_residue hetero_choices conformer_choices residues conformer_constraints connections limitations".split(" ");
  const TITLES = ["蛋白链与模型", "参考配体残基", "异源残基处理", "替代构象", "残基与原子事实", "构象约束", "连接事实", "结构限制"];
  const ROLES = "protein deposited_ligand glycan metal solvent buffer water cofactor other unproven".split(" ");
  const ID_FIELDS = "model chain resname resseq icode";
  const fail = () => { throw new Error("结构选择不可用：数据不完整、无效或超出界面上限。"); };
  function require(value) { if (!value) fail(); }
  const integer = (v, min, max) => Number.isSafeInteger(v) && v >= min && v <= max;
  const text = (v, pattern) => typeof v === "string" && pattern.test(v);
  const token = v => text(v, /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/);
  const label = v => text(v, /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/);
  const chainKey = r => JSON.stringify([r.model, r.chain]);
  const residueKey = r => JSON.stringify([r.model, r.chain, r.resname, r.resseq, r.icode]);
  function closed(value, fields) {
    require(value && typeof value === "object" && !Array.isArray(value));
    const keys = fields.split(" ");
    require(Object.keys(value).length === keys.length && keys.every(k => Object.prototype.hasOwnProperty.call(value, k)));
  }
  function identity(r) {
    closed(r, ID_FIELDS);
    require(integer(r.model, 1, 9999) && text(r.chain, /^[A-Za-z0-9]?$/) && text(r.resname, /^[A-Z0-9]{1,3}$/)
      && integer(r.resseq, -999, 9999) && text(r.icode, /^[A-Za-z0-9]?$/));
    return residueKey(r);
  }
  function unique(rows, key) {
    require(Array.isArray(rows));
    const index = new Map();
    rows.forEach(row => { const k = key(row); require(!index.has(k)); index.set(k, row); });
    return index;
  }

  function chargeBytes(budget, size) {
    require(budget.bytes + size <= MAX_BYTES);
    budget.bytes += size;
  }

  // Exact JSON.stringify string UTF-8 size, without creating escaped strings,
  // encoded buffers, or codepoint arrays. Lone surrogates are six-byte escapes;
  // valid surrogate pairs are a single four-byte Unicode code point.
  function chargeString(value, budget) {
    require(value.length <= 4096);
    chargeBytes(budget, 2);
    for (let i = 0; i < value.length; i++) {
      const code = value.codePointAt(i);
      if (code > 0xffff) { chargeBytes(budget, 4); i++; }
      else if (code === 34 || code === 92 || code === 8 || code === 9 || code === 10 || code === 12 || code === 13) chargeBytes(budget, 2);
      else if (code < 32 || code >= 0xd800 && code <= 0xdfff) chargeBytes(budget, 6);
      else chargeBytes(budget, code < 128 ? 1 : code < 2048 ? 2 : 3);
    }
  }

  // Capture before retaining/serializing. A shared ledger counts each complete
  // page envelope and all nested facts exactly once, including commas/colons.
  // Precheck array length BEFORE enumeration. For objects collect at most 32
  // keys, rejecting the 33rd without a whole-input Object.keys/entries array.
  // Data descriptors avoid getters; no caller-defined serialization hooks run.
  function detached(value, budget = {bytes: 0, nodes: 0}, depth = 0) {
    require(depth <= 12 && ++budget.nodes <= MAX_NODES);
    if (value === null) { chargeBytes(budget, 4); return value; }
    if (typeof value === "boolean") { chargeBytes(budget, value ? 4 : 5); return value; }
    if (typeof value === "number") { require(Number.isFinite(value)); chargeBytes(budget, JSON.stringify(value).length); return value; }
    if (typeof value === "string") { chargeString(value, budget); return value; }
    require(value && typeof value === "object");
    const array = Array.isArray(value), keys = [];
    let count = 0;
    if (array) {
      const length = Object.getOwnPropertyDescriptor(value, "length");
      require(length && integer(length.value, 0, MAX_ROWS));
      count = length.value;
      require(budget.nodes + count <= MAX_NODES);
      let seen = 0;
      for (const key in value) {
        require(seen < count && Object.prototype.hasOwnProperty.call(value, key) && key === String(seen));
        seen++;
      }
      require(seen === count); // Sparse arrays and enumerable expandos are not JSON input.
    } else {
      for (const key in value) {
        require(count < 32 && Object.prototype.hasOwnProperty.call(value, key));
        require(key !== "__proto__" && key !== "constructor" && key !== "prototype");
        keys.push(key); count++;
      }
      require(budget.nodes + count <= MAX_NODES);
    }
    chargeBytes(budget, 2 + Math.max(0, count - 1) + (array ? 0 : count));
    const copy = array ? [] : {};
    for (let i = 0; i < count; i++) {
      const key = array ? String(i) : keys[i];
      if (!array) chargeString(key, budget);
      const descriptor = Object.getOwnPropertyDescriptor(value, key);
      require(descriptor && descriptor.enumerable && Object.prototype.hasOwnProperty.call(descriptor, "value"));
      copy[key] = detached(descriptor.value, budget, depth + 1);
    }
    return copy;
  }

  function validateEvent(e) {
    require(e.type === "docking_event" && e.schema === "DockingView@1" && label(e.task_id)
      && label(e.preparation_id) && integer(e.revision, 0, Number.MAX_SAFE_INTEGER)
      && e.terminal === false && e.segment_complete === true && e.phase === "awaiting_input"
      && e.view_status === "AWAITING_INPUT" && e.reason_code === "scientific_selection_required");
    const s = e.preview && e.preview.source;
    require(s && text(s.source_sha256, /^[a-f0-9]{64}$/) && s.input_ready === false && s.execution_authorized === false);
    require(text(s.pdb_id, /^[0-9][A-Z0-9]{3}$/) && typeof s.revision_version === "string"
      && s.target && text(s.target.uniprot_id, /^[A-Z0-9]{6,10}$/) && integer(s.target.taxonomy_id, 1, 2147483647));
    closed(s.choice_counts, SECTIONS.join(" "));
    let total = 0;
    SECTIONS.forEach(k => { require(integer(s.choice_counts[k], 0, MAX_ROWS)); total += s.choice_counts[k]; });
    require(total <= MAX_ROWS && s.choice_counts.protein_selection > 0 && s.choice_counts.residues > 0);
    return s;
  }

  function validateInventory(data, source) {
    const proteins = unique(data.protein_selection, p => {
      closed(p, "model chain entity_id uniprot_id taxonomy_id eligible");
      require(integer(p.model, 1, 9999) && text(p.chain, /^[A-Za-z0-9]?$/) && token(p.entity_id)
        && text(p.uniprot_id, /^[A-Z0-9]{6,10}$/) && integer(p.taxonomy_id, 1, 2147483647)
        && typeof p.eligible === "boolean");
      require(p.eligible === (p.uniprot_id === source.target.uniprot_id && p.taxonomy_id === source.target.taxonomy_id));
      return chainKey(p);
    });
    require(proteins.size <= 63 && new Set(data.protein_selection.map(p => p.model)).size === 1);
    const serials = new Set();
    const residues = unique(data.residues, r => {
      closed(r, "residue role required_heavy_atoms atoms");
      const key = identity(r.residue);
      require(ROLES.includes(r.role));
      if (r.role === "protein") require(proteins.has(chainKey(r.residue)));
      unique(r.required_heavy_atoms, a => {
        closed(a, "name element"); require(text(a.name, /^[A-Za-z0-9'_*+-]{1,4}$/) && text(a.element, /^[A-Z][a-zA-Z]?$/) && !["H", "D"].includes(a.element)); return a.name;
      });
      require(Array.isArray(r.atoms) && r.atoms.length > 0);
      r.atoms.forEach(a => {
        closed(a, "serial name element altloc occupancy_hundredths");
        require(integer(a.serial, 1, 99999) && !serials.has(a.serial) && text(a.name, /^[A-Za-z0-9'_*+-]{1,4}$/)
          && text(a.element, /^[A-Z][a-zA-Z]?$/) && text(a.altloc, /^[A-Za-z0-9]?$/) && integer(a.occupancy_hundredths, 0, 100));
        serials.add(a.serial);
      });
      return key;
    });
    const references = unique(data.reference_residue, r => {
      const key = identity(r), fact = residues.get(key), p = proteins.get(chainKey(r));
      require(fact && fact.role === "deposited_ligand" && fact.required_heavy_atoms.length && p && p.eligible); return key;
    });
    require(references.size === data.residues.filter(r => {
      const p = proteins.get(chainKey(r.residue));
      return r.role === "deposited_ligand" && r.required_heavy_atoms.length > 0 && p && p.eligible;
    }).length);
    const heteros = unique(data.hetero_choices, h => {
      closed(h, "residue role actions"); const key = identity(h.residue), r = residues.get(key);
      require(r && r.role !== "protein" && h.role === r.role && Array.isArray(h.actions)
        && h.actions.length === 2 && new Set(h.actions).size === 2 && h.actions.includes("retain") && h.actions.includes("remove")); return key;
    });
    require(heteros.size === data.residues.filter(r => r.role !== "protein").length);
    const conformers = unique(data.conformer_choices, c => {
      closed(c, "residue altlocs"); const key = identity(c.residue), r = residues.get(key); require(r);
      const actual = new Set(r.atoms.map(a => a.altloc).filter(Boolean));
      unique(c.altlocs, a => { require(text(a, /^[A-Za-z0-9]$/) && actual.has(a)); return a; });
      require(c.altlocs.length > 0 && actual.size === c.altlocs.length); return key;
    });
    require(conformers.size === data.residues.filter(r => r.atoms.some(a => a.altloc)).length);
    unique(data.conformer_constraints, c => {
      closed(c, "id residues allowed_assignments"); require(token(c.id) && c.residues.length > 0 && c.residues.length <= 64);
      unique(c.residues, r => { const k = identity(r); require(residues.has(k)); return k; });
      require(Array.isArray(c.allowed_assignments) && c.allowed_assignments.length > 0 && c.allowed_assignments.length <= 256);
      unique(c.allowed_assignments, assignment => {
        require(Array.isArray(assignment) && assignment.length === c.residues.length);
        assignment.forEach((a, i) => { const choices = conformers.get(residueKey(c.residues[i])); require((choices ? choices.altlocs : [""]).includes(a)); });
        return JSON.stringify(assignment);
      }); return c.id;
    });
    unique(data.connections, c => {
      closed(c, "record_index record_type atom_serials kind symmetry");
      require(integer(c.record_index, 1, Number.MAX_SAFE_INTEGER) && ["CONECT", "LINK", "SSBOND"].includes(c.record_type)
        && ["covalent", "disulfide", "coordination", "unknown"].includes(c.kind) && Array.isArray(c.atom_serials)
        && c.atom_serials.length >= 2 && c.atom_serials.length <= 5 && c.atom_serials.every(s => serials.has(s))
        && Array.isArray(c.symmetry) && c.symmetry.length === (c.record_type === "CONECT" ? 0 : 2)
        && c.symmetry.every(s => ["identity", "nonidentity"].includes(s)));
      return c.record_index;
    });
    unique(data.limitations, l => {
      closed(l, "id kind residues source_record_id");
      require(token(l.id) && token(l.source_record_id) && ["missing_residues", "construct_truncation", "sequence_variant"].includes(l.kind)
        && Array.isArray(l.residues) && l.residues.length > 0);
      unique(l.residues, r => { const k = identity(r); require(proteins.has(chainKey(r)) && (!residues.has(k) || residues.get(k).role === "protein")); return k; });
      return l.id;
    });
    return residues;
  }

  // Mirror the producer's recorded-edge checks, without inferring a bond or
  // completing an absent conformer. The backend remains the science authority.
  function checkConnections(data, active, conformer, reference) {
    const atoms = new Map(), kept = new Set(), alternatives = new Set(), chosen = new Map();
    data.residues.forEach(r => {
      const key = residueKey(r.residue);
      r.atoms.forEach(a => {
        const atomKey = JSON.stringify([key, a.name, a.element]);
        atoms.set(a.serial, {atomKey, element: a.element});
        if (!active.has(key)) return;
        if (a.altloc && a.altloc !== conformer.get(key)) alternatives.add(a.serial);
        else if (key !== reference) { kept.add(a.serial); chosen.set(atomKey, a.serial); }
      });
    });
    const edges = [], verified = new Set(), kinds = new Map(), multiplicities = new Map();
    const edgeKey = (a, b, kind, multiplicity, symmetry = []) => JSON.stringify([Math.min(a, b), Math.max(a, b), kind, multiplicity, symmetry]);
    data.connections.forEach(c => {
      require(["covalent", "disulfide", "coordination"].includes(c.kind)
        && c.symmetry.every(s => s === "identity") && (c.record_type !== "SSBOND" || c.kind === "disulfide"));
      const left = c.atom_serials[0], counts = new Map();
      c.atom_serials.slice(1).forEach(right => counts.set(right, (counts.get(right) || 0) + 1));
      counts.forEach((multiplicity, right) => {
        const pair = edgeKey(left, right, "", 0);
        require(!kinds.has(pair) || kinds.get(pair) === c.kind); kinds.set(pair, c.kind);
        require(c.kind === "covalent" || multiplicity === 1);
        if (c.kind === "disulfide") require(atoms.get(left).element === "S" && atoms.get(right).element === "S");
        if (c.record_type === "CONECT") {
          require(!multiplicities.has(pair) || multiplicities.get(pair) === multiplicity); multiplicities.set(pair, multiplicity);
        }
        edges.push([left, right, c.kind, multiplicity, c.symmetry]); verified.add(edgeKey(left, right, c.kind, multiplicity, c.symmetry));
      });
    });
    edges.forEach(([left, right, kind, multiplicity, symmetry]) => {
      if (kept.has(left) === kept.has(right)) return;
      const removed = kept.has(left) ? right : left, retained = kept.has(left) ? left : right;
      require(alternatives.has(removed));
      const counterpart = chosen.get(atoms.get(removed).atomKey);
      require(counterpart !== undefined && verified.has(edgeKey(retained, counterpart, kind, multiplicity, symmetry)));
    });
  }

  function bodyFits(body) {
    let size = 0;
    for (const char of JSON.stringify(body)) {
      const code = char.codePointAt(0);
      size += code < 128 ? 1 : code < 2048 ? 2 : code < 65536 ? 3 : 4;
      if (size > 65536) return false;
    }
    return true;
  }

  function mount(host, options) {
    const doc = host.ownerDocument || global.document;
    const request = options.request, isCurrent = options.isCurrent;
    const onSubmitted = options.onSubmitted, onError = options.onError;
    let disposed = false, stale = false, busy = false, loaded = false, failed = false, sent = false;
    let event, source, data = {}, residues, reference = "";
    const selected = new Set(), hetero = new Map(), conformer = new Map(), reviewed = new Set();
    const listeners = [], controls = [];
    const root = doc.createElement("section"); host.appendChild(root);
    function element(tag, content, parent = root) {
      const node = doc.createElement(tag);
      if (content !== undefined) node.textContent = content;
      parent.appendChild(node); return node;
    }
    function listen(node, type, fn, dynamic = false) {
      node.addEventListener(type, fn); (dynamic ? controls : listeners).push([node, type, fn]);
    }
    function unlisten(list) { list.splice(0).forEach(([n, type, fn]) => n.removeEventListener(type, fn)); }
    function current() {
      if (disposed || stale) return false;
      try { if (typeof isCurrent === "function" && isCurrent() === true) return true; } catch (_) { /* Fail closed. */ }
      stale = true; return false;
    }
    const status = element("p", "仅准备对接输入，不授权或启动科学执行。"); status.setAttribute("role", "status");
    const open = element("button", "查看结构并选择"); open.type = "button";
    const facts = element("div"), form = element("div");
    const submit = element("button", "确认结构选择并生成输入"); submit.type = "button"; submit.disabled = true;
    function disable() {
      open.disabled = true; submit.disabled = true;
      controls.forEach(([n]) => { n.disabled = true; });
    }
    function unavailable() {
      if (!current()) return;
      failed = true; busy = false; disable();
      status.textContent = "结构选择不可用：数据不完整、无效、请求失败或超出界面上限（4096 条记录 / 2 MiB JSON / 150000 数据节点 / 64 KiB 提交）。请刷新当前任务。";
      if (typeof onError === "function") onError(new Error("结构选择不可用"));
    }
    function resetChoices() { reference = ""; hetero.clear(); conformer.clear(); reviewed.clear(); }
    function activeKeys() {
      const active = new Set();
      data.residues.forEach(r => {
        const k = residueKey(r.residue);
        if (r.role === "protein" && selected.has(chainKey(r.residue)) || k === reference || hetero.get(k) === "retain") active.add(k);
      });
      return active;
    }
    function relevantLimitations() { return data.limitations.filter(l => l.residues.some(r => selected.has(chainKey(r)))); }
    function selection() {
      require(loaded && !busy && !failed && !sent);
      const proteins = data.protein_selection.filter(p => selected.has(chainKey(p)));
      require(proteins.length > 0 && proteins.length <= 62 && proteins.every(p => p.eligible && p.model === proteins[0].model));
      const ref = data.reference_residue.find(r => residueKey(r) === reference);
      require(ref && selected.has(chainKey(ref)));
      const heteros = data.hetero_choices.filter(h => residueKey(h.residue) !== reference);
      require(heteros.every(h => ["retain", "remove"].includes(hetero.get(residueKey(h.residue)))));
      const active = activeKeys();
      const conformers = data.conformer_choices.filter(c => active.has(residueKey(c.residue)));
      require(conformers.every(c => c.altlocs.includes(conformer.get(residueKey(c.residue)))));
      data.conformer_constraints.forEach(c => {
        const keys = c.residues.map(residueKey);
        if (!keys.some(k => active.has(k))) return;
        require(keys.every(k => active.has(k)) && c.allowed_assignments.some(a => a.every((v, i) => v === (conformer.get(keys[i]) || ""))));
      });
      active.forEach(k => {
        const r = residues.get(k), chosen = r.atoms.filter(a => !a.altloc || a.altloc === conformer.get(k));
        require(new Set(chosen.map(a => a.name)).size === chosen.length && chosen.every(a => a.occupancy_hundredths > 0));
        const heavy = chosen.filter(a => !["H", "D"].includes(a.element));
        require(heavy.length === r.required_heavy_atoms.length && r.required_heavy_atoms.every(a => heavy.some(b => a.name === b.name && a.element === b.element)));
        if (k === reference) require(heavy.every(a => a.occupancy_hundredths === 100));
      });
      checkConnections(data, active, conformer, reference);
      const limits = relevantLimitations(); require(limits.every(l => reviewed.has(l.id)));
      return {
        protein_selection: {model: proteins[0].model, chains: proteins.map(p => ({chain: p.chain, entity_id: p.entity_id}))},
        reference_residue: ref,
        hetero_choices: heteros.map(h => ({residue: h.residue, action: hetero.get(residueKey(h.residue))})),
        conformer_choices: conformers.map(c => ({residue: c.residue, altloc: conformer.get(residueKey(c.residue))})),
        reviewed_limitations: limits.map(l => l.id).sort()
      };
    }
    function updateValidity() {
      submit.disabled = true;
      if (!current()) { disable(); return; }
      try {
        const body = {revision: event.revision, source_sha256: source.source_sha256, selection: selection()};
        if (!bodyFits(body)) { unavailable(); return; }
        submit.disabled = false;
      } catch (_) { /* Explicitly incomplete. */ }
    }
    function checkbox(role, caption, checked, enabled, change) {
      const wrap = element("label", undefined, form), input = element("input", undefined, wrap);
      input.type = "checkbox"; input.checked = checked; input.disabled = !enabled;
      input.setAttribute("data-role", role); input.setAttribute("aria-label", caption);
      element("span", caption, wrap);
      listen(input, "change", () => { if (!current() || busy || failed || sent || input.disabled) return; change(input.checked === true); }, true);
      return input;
    }
    function select(role, caption, choices, value, change) {
      const wrap = element("label", caption, form), input = element("select", undefined, wrap);
      input.setAttribute("data-role", role); input.setAttribute("aria-label", caption);
      element("option", "请选择（无默认选择）", input).value = "";
      choices.forEach(([key, title]) => { element("option", title, input).value = key; }); input.value = value;
      listen(input, "change", () => {
        if (!current() || busy || failed || sent) return;
        const v = choices.some(([key]) => key === input.value) ? input.value : "";
        change(v);
      }, true);
    }
    function renderChoices() {
      unlisten(controls); form.replaceChildren();
      data.protein_selection.forEach(p => {
        const key = chainKey(p);
        checkbox("protein", `模型 ${p.model} / 链 ${p.chain || "(空链)"} / 实体 ${p.entity_id} / ${p.uniprot_id} / 物种 ${p.taxonomy_id}${p.eligible ? "" : "（不符合目标）"}`,
          selected.has(key), p.eligible, value => { if (value) selected.add(key); else selected.delete(key); resetChoices(); renderChoices(); });
      });
      select("reference", "参考配体残基（用于生成输入，不保留在受体中）", data.reference_residue.flatMap((r, i) =>
        selected.has(chainKey(r)) ? [[String(i), JSON.stringify(r)]] : []),
      reference ? String(data.reference_residue.findIndex(r => residueKey(r) === reference)) : "", value => {
        reference = value === "" ? "" : residueKey(data.reference_residue[Number(value)]);
        hetero.clear(); conformer.clear(); reviewed.clear(); renderChoices();
      });
      if (reference) {
        data.hetero_choices.filter(h => residueKey(h.residue) !== reference).forEach(h => {
          const key = residueKey(h.residue);
          select("hetero", `明确处理 ${h.role} ${JSON.stringify(h.residue)}`, [["retain", "保留 retain"], ["remove", "移除 remove"]], hetero.get(key) || "", value => {
            hetero.set(key, value); conformer.delete(key); renderChoices();
          });
        });
        const active = activeKeys();
        data.conformer_choices.filter(c => active.has(residueKey(c.residue))).forEach(c => {
          const key = residueKey(c.residue);
          select("conformer", `替代构象 ${JSON.stringify(c.residue)}`, c.altlocs.map(a => [a, `altloc ${a}`]), conformer.get(key) || "", value => { conformer.set(key, value); updateValidity(); });
        });
        relevantLimitations().forEach(l => checkbox("limitation", `已审阅限制 ${l.id} / ${l.kind} / ${l.source_record_id} / ${JSON.stringify(l.residues)}`,
          reviewed.has(l.id), true, checked => { if (checked) reviewed.add(l.id); else reviewed.delete(l.id); updateValidity(); }));
      }
      updateValidity();
    }
    async function load() {
      if (!current() || busy || loaded || failed) return;
      busy = true; open.disabled = true; status.textContent = "正在完整加载结构事实；尚未选择或授权执行。";
      try {
        const budget = {nodes: 0, bytes: 0};
        for (const section of SECTIONS) {
          const count = source.choice_counts[section]; let offset = 0; data[section] = [];
          do {
            if (!current()) return;
            const response = await request(`/api/agent/docking/tasks/${encodeURIComponent(event.task_id)}/source-page`,
              {revision: event.revision, source_sha256: source.source_sha256, section, offset, limit: 32});
            if (!current()) return;
            const page = detached(response, budget);
            closed(page, "ok source_sha256 section offset next_offset items input_ready execution_authorized");
            require(page.ok === true && page.source_sha256 === source.source_sha256 && page.section === section && page.offset === offset
              && page.input_ready === false && page.execution_authorized === false && Array.isArray(page.items) && page.items.length <= 32);
            const end = offset + page.items.length;
            require(end <= count && (end === count ? page.next_offset === null : page.items.length > 0 && page.next_offset === end));
            data[section].push(...page.items); offset = end;
          } while (offset < count);
        }
        residues = validateInventory(data, source);
        if (!current()) return;
        SECTIONS.forEach((section, index) => {
          const group = element("details", undefined, facts);
          element("summary", `${TITLES[index]} (${data[section].length})`, group);
          data[section].forEach(row => element("pre", JSON.stringify(row, null, 2), group));
        });
        loaded = true; busy = false;
        status.textContent = "结构事实已完整加载。请明确选择；本步骤仅准备输入，不启动对接。选择仍需服务端科学验证。";
        renderChoices();
      } catch (_) { unavailable(); }
    }
    async function send() {
      if (!current() || busy || failed || sent || !loaded) return;
      let body;
      try {
        body = detached({revision: event.revision, source_sha256: source.source_sha256, selection: selection()});
        // A complete inventory must never become a silently truncated payload.
        if (!bodyFits(body)) { unavailable(); return; }
      } catch (_) { updateValidity(); return; }
      busy = true; disable();
      try {
        const result = await request(`/api/agent/docking/tasks/${encodeURIComponent(event.task_id)}/selection`, body);
        if (!current()) return;
        const receipt = detached(result);
        require(receipt && receipt.task_id === event.task_id && receipt.preparation_id === event.preparation_id
          && ["BUSY", "PREPARING", "AWAITING_INPUT"].includes(receipt.phase) && receipt.input_ready === false && receipt.execution_authorized === false);
        busy = false; sent = true; status.textContent = "结构选择已提交，正在准备输入；未授权执行。";
        if (typeof onSubmitted === "function" && current()) onSubmitted(receipt);
      } catch (_) { unavailable(); }
    }
    listen(open, "click", load); listen(submit, "click", send);
    try {
      require(typeof request === "function" && typeof isCurrent === "function");
      event = detached(options.event); source = validateEvent(event);
      if (current()) {
        element("pre", `来源（非执行授权）\n${JSON.stringify(source, null, 2)}`, facts);
        if (source.choice_counts.reference_residue === 0) {
          // A valid source can have no eligible pocket reference. This is a
          // scientific blocker, not an expired/failed confirmation. Keep all
          // preparation controls inert, including manually dispatched events.
          failed = true; disable();
          status.textContent = "当前结构没有经核验的合格参考配体，无法据此定义共晶口袋。请取消本次请求并选择其他结构；尚未运行 Vina。";
        }
      } else disable();
    } catch (_) { unavailable(); }
    return {dispose() {
      if (disposed) return;
      disposed = true; disable(); unlisten(listeners); unlisten(controls);
      if (root.parentNode === host) host.removeChild(root);
      data = {}; residues = null; selected.clear(); resetChoices();
    }};
  }
  global.HomeDockingSourceSelection = Object.freeze({mount});
})(window);
