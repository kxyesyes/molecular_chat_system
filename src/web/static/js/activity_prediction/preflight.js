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

    document.getElementById("preflight-meta").innerHTML = `
      <div class="preflight-meta-item"><span>行数</span>${total}</div>
      <div class="preflight-meta-item"><span>列数</span>${headers.length}</div>
      <div class="preflight-meta-item"><span>分隔符</span>${
        preflightData.sep === "\t" ? "Tab(\\t)" : "Comma(,)"
      }</div>
    `;

    const colsHtml = headers
      .map(function (header) {
        const lower = header.toLowerCase();
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
        return `<div class="${cls}"><span class="col-badge ${badgeClass}">${badgeText}</span>${header}</div>`;
      })
      .join("");

    document.getElementById("preflight-cols").innerHTML = colsHtml;

    const thHtml = headers
      .map(function (header) {
        return `<th>${header}</th>`;
      })
      .join("");

    const tbodyHtml = rows
      .map(function (row) {
        return `<tr>${row
          .map(function (cell) {
            return `<td>${cell}</td>`;
          })
          .join("")}</tr>`;
      })
      .join("");

    document.getElementById(
      "preflight-preview",
    ).innerHTML = `<table class="preflight-table"><thead><tr>${thHtml}</tr></thead><tbody>${tbodyHtml}</tbody></table>`;

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

    document.getElementById("launch-summary").innerHTML = `
      <div class="summary-grid">
        <div class="summary-item"><div class="summary-label">数据文件</div><div class="summary-value">${file.name}</div></div>
        <div class="summary-item"><div class="summary-label">目标列 (Label)</div><div class="summary-value">${target}</div></div>
        <div class="summary-item"><div class="summary-label">任务类型</div><div class="summary-value">${task === "regression" ? "回归 Regression" : "分类 Classification"}</div></div>
        <div class="summary-item"><div class="summary-label">划分策略</div><div class="summary-value">${split}</div></div>
        <div class="summary-item"><div class="summary-label">Epochs</div><div class="summary-value">${epochs}</div></div>
        <div class="summary-item"><div class="summary-label">Learning Rate</div><div class="summary-value">${lr}</div></div>
        <div class="summary-item"><div class="summary-label">Batch Size</div><div class="summary-value">${bs}</div></div>
        <div class="summary-item"><div class="summary-label">Dropout</div><div class="summary-value">${dr}</div></div>
        <div class="summary-item"><div class="summary-label">Layers</div><div class="summary-value">${layers}</div></div>
        <div class="summary-item"><div class="summary-label">Hidden Size</div><div class="summary-value">${hidden}</div></div>
        <div class="summary-item"><div class="summary-label">Weight Decay</div><div class="summary-value">${decay}</div></div>
        <div class="summary-item"><div class="summary-label">Patience</div><div class="summary-value">${patience}</div></div>
        <div class="summary-item"><div class="summary-label">Loss Fn</div><div class="summary-value">${loss}</div></div>
        <div class="summary-item"><div class="summary-label">Scheduler</div><div class="summary-value">${scheduler}</div></div>
      </div>
    `;
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
