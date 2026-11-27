/* UI queue over the existing document API. No automatic retry of uncertain writes. */
(function (scope) {
  "use strict";
  function errorFor(file) {
    if (!file) return "请选择资料文件。";
    if (!/\.(md|txt|docx|pptx|pdf|png|jpe?g|vsdx|xmind)$/i.test(file.name))
      return `暂不支持 ${file.name.split(".").pop().toUpperCase()} 格式。请选择 Markdown、TXT、Word（DOCX）、PowerPoint（PPTX）、PDF、PNG、JPEG、VSDX 或 XMind 文件；旧版 DOC / PPT、含宏 DOCM / PPTM 和 VSD 暂不支持，请导出为受支持格式。`;
    const maximum = /\.(md|txt)$/i.test(file.name) ? 1 : 16;
    if (file.size > maximum * 1024 * 1024)
      return `文件 ${file.name} 为 ${(file.size / 1024 / 1024).toFixed(2)} MiB，超过 ${maximum} MiB 上限。请拆分或压缩后上传。`;
    return null;
  }
  function create(files) {
    return Array.from(files, (file, index) => {
      const error = errorFor(file);
      return {index, file, state: error ? "INVALID" : "PENDING", error, saved: null};
    });
  }
  function counts(items) {
    const result = {total: items.length, SAVED: 0, FAILED: 0, INVALID: 0, UNKNOWN: 0, PENDING: 0, UPLOADING: 0};
    for (const item of items) result[item.state] += 1;
    return result;
  }
  async function run(items, send, notify = () => {}, alive = () => true) {
    for (const item of items) {
      if (!alive()) break;
      if (!new Set(["PENDING", "FAILED"]).has(item.state)) continue;
      item.state = "UPLOADING";
      item.error = null;
      notify(item, items);
      try {
        const saved = await send(item);
        if (!saved || !(saved.id || saved.document_id)) throw new Error("未收到实际资料记录，请先核对资料库。");
        item.saved = saved;
        item.state = "SAVED";
      } catch (error) {
        item.error = error.message || "上传结果需要核对。";
        item.state = Number.isInteger(error.status) && error.status >= 400 && error.status < 500 ? "FAILED" : "UNKNOWN";
      }
      if (alive()) notify(item, items);
      if (item.state === "UNKNOWN") break;
    }
    return counts(items);
  }
  const api = {create, errorFor, counts, run};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else scope.EEASUploadBatch = api;
})(typeof window === "undefined" ? globalThis : window);
