const TargetSearchApi = (() => {
  async function readJson(resp) {
    const data = await resp.json();
    if (!resp.ok) {
      throw new Error(data.detail || data.error || data.message || "请求失败");
    }
    return data;
  }

  async function search(query, filters = {}) {
    const params = new URLSearchParams({ query });
    Object.entries(filters).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== "") {
        params.set(key, value);
      }
    });
    return readJson(await fetch(`${TargetSearchConfig.API.SEARCH}?${params}`));
  }

  async function stats() {
    return readJson(await fetch(TargetSearchConfig.API.STATS));
  }

  async function health() {
    return readJson(await fetch(TargetSearchConfig.API.HEALTH));
  }

  async function validation() {
    return readJson(await fetch(TargetSearchConfig.API.VALIDATION));
  }

  async function pdeOverview(topStructuresPerTarget = 3) {
    const params = new URLSearchParams({ top_structures_per_target: String(topStructuresPerTarget) });
    return readJson(await fetch(`${TargetSearchConfig.API.PDE_OVERVIEW}?${params}`));
  }

  async function detail(targetId) {
    return readJson(await fetch(TargetSearchConfig.API.TARGET(targetId)));
  }

  async function sendToDocking(structureId) {
    return readJson(await fetch(TargetSearchConfig.API.SEND_TO_DOCKING(structureId), { method: "POST" }));
  }

  async function preflight(structureId, format = "cif") {
    return readJson(await fetch(TargetSearchConfig.API.PREFLIGHT(structureId, format)));
  }

  function downloadUrl(structureId, format) {
    return TargetSearchConfig.API.DOWNLOAD(structureId, format);
  }

  async function download(structureId, format) {
    const resp = await fetch(downloadUrl(structureId, format));
    const contentType = resp.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {
      const data = await resp.json();
      throw new Error(data.error || data.detail || data.message || "结构文件下载失败");
    }
    if (!resp.ok) {
      throw new Error(`结构文件下载失败：HTTP ${resp.status}`);
    }

    const blob = await resp.blob();
    let filename = `structure.${format}`;
    const disposition = resp.headers.get("content-disposition") || "";
    const match = disposition.match(/filename\*?=(?:UTF-8''|")?([^";]+)/i);
    if (match && match[1]) {
      filename = decodeURIComponent(match[1].replaceAll('"', ""));
    }
    return { blob, filename };
  }

  return { search, stats, health, validation, pdeOverview, detail, sendToDocking, preflight, downloadUrl, download };
})();
