"use strict";

window.ActivityPreflight = (function () {
  let preflightData = null;
  let bound = false;

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
    const smilesGuess = document
      .getElementById("smilesColumn")
      .value.trim()
      .toLowerCase();
    const labelGuess = document
      .getElementById("targetColumn")
      .value.trim()
      .toLowerCase();

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

      if (
        lower === smilesGuess ||
        lower === "smiles" ||
        lower === "canonical_smiles"
      ) {
        badgeClass = "smiles";
        badgeText = "SMILES";
      } else if (
        lower === labelGuess ||
        lower === "pic50" ||
        lower === "label" ||
        lower === "activity"
      ) {
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
      const headers = preflightData.headers;
      const smilesEl = document.getElementById("smilesColumn");
      const labelEl = document.getElementById("targetColumn");
      const smilesMatch = headers.find(function (header) {
        return ["smiles", "canonical_smiles", "molecule"].includes(
          header.toLowerCase(),
        );
      });
      const labelMatch = headers.find(function (header) {
        return ["pic50", "label", "activity", "target", "value"].includes(
          header.toLowerCase(),
        );
      });

      if (smilesMatch && !smilesEl.value) smilesEl.value = smilesMatch;
      if (labelMatch && !labelEl.value) labelEl.value = labelMatch;

      setTrainHint(
        `✅ 解析成功：${preflightData.total} 条分子, ${preflightData.headers.length} 列`,
        "ok",
      );
    }

    close();
  }

  function showLaunchModal() {
    const file = document.getElementById("trainFile").files &&
      document.getElementById("trainFile").files[0];
    if (!file) {
      alert("请先上传训练数据集。");
      return;
    }

    const target = document.getElementById("targetColumn").value.trim();
    if (!target) {
      alert("请填写目标列名。");
      return;
    }

    const task = document.getElementById("trainTaskType").value;
    const epochs = document.getElementById("epochs").value;
    const lr = document.getElementById("learningRate").value;
    const bs = document.getElementById("batchSize").value;
    const dr = document.getElementById("dropout").value;
    const split = document.getElementById("splitStrategy").value;
    const layers = document.getElementById("trainLayers").value;
    const hidden = document.getElementById("trainHidden").value;
    const decay = document.getElementById("trainWeightDecay").value;
    const patience = document.getElementById("trainPatience").value;
    const loss = document.getElementById("trainLoss").value;
    const scheduler = document.getElementById("trainScheduler").value;

    const summaryGrid = document.createElement("div");
    summaryGrid.className = "summary-grid";
    [
      ["数据文件", file.name],
      ["目标列 (Label)", target],
      [
        "任务类型",
        task === "regression" ? "回归 Regression" : "分类 Classification",
      ],
      ["划分策略", split],
      ["Epochs", epochs],
      ["Learning Rate", lr],
      ["Batch Size", bs],
      ["Dropout", dr],
      ["Layers", layers],
      ["Hidden Size", hidden],
      ["Weight Decay", decay],
      ["Patience", patience],
      ["Loss Fn", loss],
      ["Scheduler", scheduler],
    ].forEach(function (entry) {
      summaryGrid.appendChild(createSummaryItem(entry[0], entry[1]));
    });
    document.getElementById("launch-summary").replaceChildren(summaryGrid);
    document.getElementById("launch-modal").style.display = "flex";
  }

  function closeLaunchModal() {
    document.getElementById("launch-modal").style.display = "none";
  }

  function confirmLaunch() {
    closeLaunchModal();
    if (window.ActivityMain && typeof window.ActivityMain.startTraining === "function") {
      window.ActivityMain.startTraining();
    }
  }

  function init() {
    if (bound) return;

    const trainFileInput = document.getElementById("trainFile");
    if (!trainFileInput) return;

    trainFileInput.addEventListener("change", function () {
      const file = this.files && this.files[0];
      if (!file) return;

      document.getElementById("trainFileInfo").textContent = `${file.name}  (${(
        file.size / 1024
      ).toFixed(1)} KB)`;

      const reader = new FileReader();
      reader.onload = function (e) {
        const text = e.target.result;
        const lines = text.trim().split("\n").filter(Boolean);
        if (lines.length < 2) {
          setTrainHint("⚠️ 文件行数不足，请检查格式。", "warn");
          return;
        }

        const sep = lines[0].includes("\t") ? "\t" : ",";
        const headers = lines[0]
          .split(sep)
          .map(function (header) {
            return header.trim().replace(/"/g, "");
          });
        const rows = lines.slice(1, 6).map(function (line) {
          return line.split(sep).map(function (cell) {
            return cell.trim().replace(/"/g, "");
          });
        });

        setData({ headers: headers, rows: rows, sep: sep, total: lines.length - 1 });
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
    confirmLaunch: confirmLaunch,
    init: init,
  };
})();
