"use strict";

window.ActivityPreflight = (function () {
  let preflightData = null;
  let pendingLaunch = null;
  let bound = false;
  const columnAliases = {
    smilesColumn: ["smiles", "canonical_smiles", "molecule"],
    targetColumn: ["pic50", "label", "activity", "target", "value"],
  };

  function previewColumn(id) {
    const input = document.getElementById(id);
    if (input.value && input.value !== input.dataset?.suggested) return input.value.trim();
    return preflightData.headers.find(header => columnAliases[id].includes(header.toLowerCase())) || input.defaultValue || "";
  }

  function setData(data) {
    preflightData = data;
  }

  function getData() {
    return preflightData;
  }

  function createTextElement(tagName, className, text) {
    const element = document.createElement(tagName);
    if (className) element.className = className;
    element.textContent = text;
    return element;
  }

  function createMetaItem(label, value) {
    const item = document.createElement("div");
    item.className = "preflight-meta-item";
    item.append(
      createTextElement("span", "", label),
      document.createTextNode(value),
    );
    return item;
  }

  function createSummaryItem(label, value) {
    const item = document.createElement("div");
    item.className = "summary-item";
    item.append(
      createTextElement("div", "summary-label", label),
      createTextElement("div", "summary-value", value),
    );
    return item;
  }

  function setTrainHint(msg, type) {
    const el = document.getElementById("trainHint");
    if (!el) return;
    const colors = {
      info: "var(--muted)",
      warn: "#f59e0b",
      ok: "var(--success)",
    };
    el.textContent = msg;
    el.style.color = colors[type || "info"] || "var(--muted)";
  }

  function open() {
    if (!preflightData) return;

    const headers = preflightData.headers;
    const rows = preflightData.rows;
    const total = preflightData.total;
    const smilesGuess = previewColumn("smilesColumn").toLowerCase();
    const labelGuess = previewColumn("targetColumn").toLowerCase();

    document.getElementById("preflight-meta").replaceChildren(
      createMetaItem("行数", total),
      createMetaItem("列数", headers.length),
      createMetaItem(
        "分隔符",
        preflightData.sep === "\t" ? "Tab(\\t)" : "Comma(,)",
      ),
    );

    const columnItems = headers.map(function (header) {
      const lower = String(header).toLowerCase();
      let badgeClass = "other";
      let badgeText = "COL";

      if (lower === smilesGuess) {
        badgeClass = "smiles";
        badgeText = "SMILES";
      } else if (lower === labelGuess) {
        badgeClass = "label";
        badgeText = "LABEL";
      }

      const cls =
        badgeClass !== "other"
          ? "preflight-col-item highlight"
          : "preflight-col-item";
      const item = document.createElement("div");
      item.className = cls;
      item.append(
        createTextElement("span", `col-badge ${badgeClass}`, badgeText),
        document.createTextNode(header),
      );
      return item;
    });

    document.getElementById("preflight-cols").replaceChildren(...columnItems);

    const table = document.createElement("table");
    table.className = "preflight-table";
    const thead = document.createElement("thead");
    const headerRow = document.createElement("tr");
    headers.forEach(function (header) {
      headerRow.appendChild(createTextElement("th", "", header));
    });
    thead.appendChild(headerRow);

    const tbody = document.createElement("tbody");
    rows.forEach(function (row) {
      const tableRow = document.createElement("tr");
      row.forEach(function (cell) {
        tableRow.appendChild(createTextElement("td", "", cell));
      });
      tbody.appendChild(tableRow);
    });
    table.append(thead, tbody);
    document.getElementById("preflight-preview").replaceChildren(table);

    document.getElementById("preflight-modal").style.display = "flex";
  }

  function close() {
    const el = document.getElementById("preflight-modal");
    if (el) el.style.display = "none";
  }

  function confirm() {
    if (preflightData) {
      for (const id of Object.keys(columnAliases)) {
        const input = document.getElementById(id);
        if (!input.value || input.value === input.dataset.suggested) {
          input.value = previewColumn(id);
          input.dataset.suggested = input.value;
        }
      }

      setTrainHint(
        `✅ 解析成功：${preflightData.total} 条分子, ${preflightData.headers.length} 列`,
        "ok",
      );
    }

    close();
  }

  function showLaunchModal() {
    const file = document.getElementById("trainFile").files[0];
    if (!preflightData || preflightData.file !== file || document.getElementById("trainBtn").disabled) return;
    const form = document.getElementById("trainForm");
    const data = new FormData(form);
    for (const key of ["target_column", "smiles_column", "classification_threshold"]) {
      data.set(key, data.get(key).trim());
    }
    if (!data.get("target_column")) {
      alert("请填写目标列名。");
      return;
    }
    data.set("epochs", Math.max(1, parseInt(data.get("epochs") || "50", 10)));
    for (const key of ["classification_threshold", "classification_direction"]) {
      if (data.get("task_type") !== "classification" || data.get(key) === "") data.delete(key);
    }
    const summaryGrid = document.createElement("div");
    summaryGrid.className = "summary-grid";
    [
      ["数据文件", "file"], ["SMILES 列名", "smiles_column"], ["目标列 (Label)", "target_column"],
      ["任务类型", "task_type"], ["分类阈值", "classification_threshold"], ["阈值方向", "classification_direction"],
      ["划分策略", "split_strategy"], ["Epochs", "epochs"], ["Learning Rate", "learning_rate"],
      ["Batch Size", "batch_size"], ["Dropout", "dropout"], ["Layers", "num_layers"], ["Hidden Size", "hidden_size"],
      ["Weight Decay", "weight_decay"], ["Patience", "patience"], ["Loss Fn", "loss_metric"], ["Scheduler", "lr_scheduler"],
    ].forEach(function ([label, key]) {
      if (!data.has(key)) return;
      const field = form.elements.namedItem(key);
      const value = key === "file" ? file.name : (field.selectedOptions?.[0]?.textContent ?? data.get(key));
      summaryGrid.appendChild(createSummaryItem(label, value));
    });
    document.getElementById("launch-summary").replaceChildren(summaryGrid);
    pendingLaunch = data;
    document.getElementById("launch-modal").style.display = "flex";
  }

  function closeLaunchModal() {
    pendingLaunch = null;
    document.getElementById("launch-modal").style.display = "none";
  }

  function takeLaunch() {
    const data = pendingLaunch;
    closeLaunchModal();
    return data && preflightData?.file === document.getElementById("trainFile").files[0] ? data : null;
  }

  function previewFile(file, text) {
    const lines = text.trim().split(/\r\n?|\n/);
    const sep = lines[0].replace(/"(?:[^"]|"")*"/g, "").includes("\t") ? "\t" : ",";
    let headers, total = 0;
    const rows = [];
    let row = [], field = "", quoted = false;
    for (const line of lines) {
      if (!quoted && !line.trim()) continue;
      for (let i = 0; i < line.length; i++) {
        const ch = line[i];
        if (ch === '"' && (quoted || !field)) {
          if (quoted && line[i + 1] === '"') { field += '"'; i++; }
          else quoted = !quoted;
        } else if (ch === sep && !quoted) {
          row.push(field.trim());
          field = "";
        } else field += ch;
      }
      if (quoted) { field += "\n"; continue; }
      row.push(field.trim());
      if (!headers) headers = row;
      else {
        total++;
        if (rows.length < 5) rows.push(row);
      }
      row = [];
      field = "";
    }
    if (quoted) throw new Error("文件的引号未闭合，请检查格式。");
    if (!total) throw new Error("⚠️ 文件行数不足，请检查格式。");
    return {file, headers, rows, sep, total};
  }

  function init() {
    if (bound) return;

    const trainFileInput = document.getElementById("trainFile");
    if (!trainFileInput) return;
    for (const id of Object.keys(columnAliases)) {
      const input = document.getElementById(id);
      input.dataset.suggested = input.defaultValue;
      input.addEventListener("input", () => { delete input.dataset.suggested; });
    }

    trainFileInput.addEventListener("change", function () {
      const file = this.files[0];
      setData(null);
      close();
      closeLaunchModal();
      for (const id of Object.keys(columnAliases)) {
        const input = document.getElementById(id);
        if (input.value === input.dataset.suggested) {
          input.value = input.defaultValue;
          input.dataset.suggested = input.value;
        }
      }
      setTrainHint(file ? "正在读取训练文件…" : "");
      if (!file) {
        document.getElementById("trainFileInfo").textContent = "未选择训练集";
        return;
      }

      document.getElementById("trainFileInfo").textContent = `${file.name}  (${(
        file.size / 1024
      ).toFixed(1)} KB)`;

      const reader = new FileReader();
      reader.onerror = () => {
        if (this.files[0] === file) setTrainHint("文件读取失败，请重新选择。", "warn");
      };
      reader.onload = function () {
        if (trainFileInput.files[0] !== file) return;
        try {
          setData(previewFile(file, reader.result));
        } catch (error) {
          setTrainHint(error.message, "warn");
          return;
        }
        setTrainHint("");
        open();
      };
      reader.readAsText(file);
    });

    bound = true;
  }

  return {
    setData: setData,
    getData: getData,
    setTrainHint: setTrainHint,
    open: open,
    close: close,
    confirm: confirm,
    showLaunchModal: showLaunchModal,
    closeLaunchModal: closeLaunchModal,
    confirmLaunch() { return window.ActivityMain.startTraining(); },
    takeLaunch: takeLaunch,
    init: init,
  };
})();
