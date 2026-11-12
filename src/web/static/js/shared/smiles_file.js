"use strict";

window.MedChatSmilesFile = (() => {
  const header = /^(?:canonical_)?smiles$/i;
  const comment = /^(?:#|;|\/\/)/;

  function firstCsvSmiles(text) {
    let row = [], field = "", quoted = false;
    let column;
    for (const line of text.split(/\r\n?|\n/)) {
      if (!quoted) {
        const start = line.trimStart();
        // Numeric specimen IDs (e.g. #1,CCO) may precede the SMILES column.
        const specimen = column > 0 && /^#\d+\s*,/.test(start);
        if (!start || (comment.test(start) && !specimen)) continue;
      }
      for (let i = 0; i < line.length; i++) {
        const char = line[i];
        if (char === '"') {
          if (quoted && line[i + 1] === '"') {
            field += '"';
            i++;
          } else {
            quoted = !quoted;
          }
        } else if (!quoted && char === ",") {
          row.push(field.trim());
          field = "";
        } else {
          field += char;
        }
      }
      if (quoted) {
        field += "\n";
        continue;
      }
      row.push(field.trim());
      if (column === undefined && row.some(Boolean)) {
        column = row.findIndex(value => header.test(value));
        if (column < 0) {
          column = 0;
          if (row[0]) return row[0];
        }
      } else if (row[column]) return row[column];
      row = [];
      field = "";
    }
    return "";
  }

  function extractUploadedSmiles(filename, content) {
    const text = String(content || "").trim();
    // Older design imports also accepted a name,smiles header in text files.
    if (/\.csv$/i.test(filename) || /^name\s*,\s*smiles\s*[\r\n]/i.test(text)) {
      return firstCsvSmiles(text);
    } else {
      for (const raw of text.split(/\r\n?|\n/)) {
        const line = raw.trim();
        if (!line || comment.test(line)) continue;
        // A CXSMILES extension is part of the structure, before any record title.
        const match = line.match(/^([^\s,;]+(?:[ \t]+\|[^|]*\|)?)/);
        if (match && !header.test(match[1])) return match[1];
      }
    }
    return "";
  }
  return { extractUploadedSmiles };
})();
