"use strict";
let csrf;
let activeProject;
let documentTarget;
let uploadQueue = [];
const uploadBatch = window.EEASUploadBatch;
let requirementTarget;
let taskRequirementRows = [];
let taskCount = null;
let selectedRefs = [];
let draftOrigin = "manual";
const documentNames = new Map();
const taskNames = new Map();
let activeRun;
let eventCursor = 0;
let displayedEvents = [];
let runTimer;
let repairSession;
let approvalDisplayKey;
let approvalRefreshAt = 0;
let extensionRows = [];
let extensionReview;
let extensionRefreshVersion = 0;
let nativeReview;
let nativeReviewTimer;
let nativeReviewEvents = [];
let nativeReviewCursor = 0;
let nativeContextDraft;
let runtimeCapabilities;
let extractionDraft;
const viewNames = {projects: "主页", library: "资料库", requirements: "需求与计划", tasks: "任务中心", approvals: "审批收件箱", capabilities: "扩展管理", results: "运行与结果", settings: "设置"};
let projectRows = [];
let documentRows = [];
let activeDocument;
let readerRequestVersion = 0;
let searchRequestVersion = 0;
let toastTimer;
let uploadSelectionVersion = 0;
let activeUploadRequest;
const initialRoute = new URLSearchParams(location.search);
// A software view starts at its own beginning. Browser restoration must not
// carry a tall, now-hidden view's offset into another route or a reload.
history.scrollRestoration = "manual";
function resetViewScroll() { window.scrollTo({left: 0, top: 0, behavior: "instant"}); }
window.addEventListener("pageshow", event => { if (!event.persisted) resetViewScroll(); });
const dedicatedRunWindow = initialRoute.get("window") === "run" && initialRoute.has("project") && initialRoute.has("run");
let activeView = "projects";
let navigationVersion = 0;
let projectSelectionVersion = 0;
let lastChosenView;
if (dedicatedRunWindow) document.body.classList.add("run-window");
function routeUrl(view = activeView, projectId = activeProject, runId = activeRun?.runId, runWindow = false) {
  const query = new URLSearchParams({view});
  if (projectId) query.set("project", projectId);
  if (runId) query.set("run", runId);
  if (nativeReview && nativeReview.projectId === projectId && nativeReview.runId === runId) query.set("review", nativeReview.id);
  if (runWindow) query.set("window", "run");
  return `/?${query}`;
}
function updateRoute() {
  history.replaceState({...history.state, eeasScrollPolicy: "view-start"}, "", routeUrl(activeView, activeProject, activeRun?.runId, dedicatedRunWindow));
  document.querySelector("#open-view-window").href = routeUrl(activeView, activeProject, activeRun?.runId, activeView === "results" && !!activeRun);
  document.querySelector("#open-workbench").href = routeUrl("tasks", activeProject, null);
  document.querySelector("#open-run-window").href = activeRun ? routeUrl("results", activeRun.projectId, activeRun.runId, true) : routeUrl("results", activeProject, null);
}
function selectView(view, update = true, explicit = false) {
  const previousView = activeView;
  if (!Object.hasOwn(viewNames, view)) view = "projects";
  if (dedicatedRunWindow && ["projects", "library", "requirements", "tasks"].includes(view)) view = "results";
  activeView = view;
  document.body.dataset.view = view;
  if (view === "tasks") renderTaskPreparation();
  closeSidebar();
  if (explicit) { navigationVersion += 1; lastChosenView = view; }
  if (previousView !== view) cancelViewMotion(document.querySelector(`[data-zone="${previousView}"]`));
  for (const panel of document.querySelectorAll("[data-zone]")) panel.hidden = panel.dataset.zone !== view;
  if (explicit && (previousView !== view || view === "projects")) resetViewScroll();
  for (const link of document.querySelectorAll("nav [data-view]")) {
    if (link.dataset.view === view) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  document.querySelector("#view-title").textContent = viewNames[view];
  document.querySelector("#section-breadcrumb").textContent = viewNames[view];
  moveNavigation(view, explicit);
  if (explicit && previousView !== view) animateView(document.querySelector(`[data-zone="${view}"]`));
  if (update) updateRoute();
}
for (const link of document.querySelectorAll("nav [data-view]")) link.addEventListener("click", event => {
  event.preventDefault();
  selectView(link.dataset.view, true, true);
  errorBox.textContent = "";
  if (link.dataset.view === "capabilities" && csrf) refreshCapabilities().catch(displayError);
});
selectView(initialRoute.get("view") || (initialRoute.get("run") ? "results" : "projects"), false);
const errorBox = document.querySelector("#error");
async function api(path, options = {}) {
  const headers = { ...options.headers };
  if (options.method && options.method !== "GET") {
    if (!(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
    headers["X-EEAS-CSRF"] = csrf;
  }
  if (options.body instanceof FormData && path.includes("/documents")) return uploadApi(path, {...options, headers});
  const response = await fetch(path, { ...options, headers });
  const data = await response.json();
  if (!response.ok) {
    const failure = new Error(data.error?.message || "请求失败");
    failure.reasons = data.error?.reasons;
    failure.code = data.error?.code;
    failure.status = response.status;
    throw failure;
  }
  document.querySelector("#connection-state").textContent = "已连接本地服务";
  document.querySelector("#connection-state").dataset.state = "ready";
  return data;
}
function displayError(error) {
  delete errorBox.dataset.settingsRefreshError;
  errorBox.textContent = error.message;
}
async function refreshCapabilities() {
  document.querySelector("#capability-summary").textContent = "正在读取实际版本";
  const [runtime, runner] = await Promise.all([api("/api/runtime"), api("/api/runner")]);
  document.querySelector("#settings-connection").textContent = `Python ${runner.python}，Claude Agent SDK ${runtime.version}。原生模型资格 ${runtime.live_verification}；OS 隔离 ${runner.sandbox}。`;
  runtimeCapabilities = runtime;
  updateNativeInputConsent();
  if (extractionDraft) renderExtraction();
  const configuration = runtime.route_configuration || {};
  const invalidRoute = configuration.status === "INVALID";
  const unknownValue = invalidRoute ? "无法读取" : "缺少";
  const warning = document.querySelector("#settings-route-warning");
  warning.hidden = !invalidRoute;
  warning.textContent = invalidRoute ? `公司配置读取失败，请检查选定文件。原因：${configuration.reason || "SETTINGS_ROUTE_INVALID"}` : "";
  const statuses = [["配置文件选择", invalidRoute ? "配置无效" : configuration.settings_selected ? "已明确选择" : "尚未选择"], ["产品凭据", configuration.credential_present ? "已提供；认证尚未验证" : unknownValue], ["模型标识", configuration.model_id_present ? "已提供" : unknownValue], ["调用端点", invalidRoute ? "无法读取" : configuration.endpoint_present ? "已提供" : "使用默认或尚未提供"], ["批准预算", configuration.budget_present ? "已明确提供" : unknownValue], ["认证验证", configuration.authentication_verified ? "已验证" : "尚未验证"], ["公司 API 兼容性", configuration.api_compatibility || runtime.intranet_target.api_compatibility]];
  const statusList = document.querySelector("#settings-route-status");
  statusList.replaceChildren();
  for (const [label, value] of statuses) { const term = document.createElement("dt"); const definition = document.createElement("dd"); term.textContent = label; definition.textContent = value; statusList.append(term, definition); }
  document.querySelector("#settings-route-sources").textContent = invalidRoute ? `配置状态：INVALID\n原因：${configuration.reason || "SETTINGS_ROUTE_INVALID"}\n无法读取当前配置来源与 SHA256。` : `路由来源：${JSON.stringify(configuration.sources || {})}\n模型来源：${JSON.stringify(configuration.model_sources || {})}\n配置 SHA256：${configuration.settings_sha256 || "尚无明确配置文件"}`;
  await refreshExtensions();
  document.querySelector("#capability-summary").textContent = `Claude Agent SDK ${runtime.version}\nLIVE：${runtime.live_verification} · ${runtime.code_execution_blockers.join(", ")}\n开发者登录用于产品：${runtime.developer_login_used ? "是" : "否"}\nRunner：${runner.profile} · Python ${runner.python} · ${runner.encoding}\n可执行范围：${runner.scope} · OS 隔离 ${runner.sandbox}\n本地扩展：登记 ${extensionRows.length} · 实测加载 ${extensionRows.filter(r => r.loaded).length} · 资格通过 ${extensionRows.filter(r => r.qualified).length}\nSDK LIVE 插件：未验证\n内网模型：${runtime.intranet_target.label} · 兼容性 ${runtime.intranet_target.api_compatibility}`;
}
document.querySelector("#refresh-capabilities").addEventListener("click", () => refreshCapabilities().catch(displayError));
document.querySelector("#retry-connection").addEventListener("click", async () => {
  errorBox.textContent = "";
  document.querySelector("#connection-state").textContent = "正在重试连接";
  const restoredView = activeView;
  const restoredNavigation = navigationVersion;
  try {
    if (!csrf) { await initialize(); return; }
    if (activeProject) await selectProject(activeProject, activeRun?.runId);
    else await refreshProjects();
    await refreshCapabilities();
    selectView(navigationVersion === restoredNavigation ? restoredView : lastChosenView);
  } catch (error) { displayError(error); }
});
async function refreshProjects() {
  const data = await api("/api/projects");
  projectRows = data.projects;
  document.querySelector("#home-project-count").textContent = data.projects.length;
  document.querySelector("#project-count-label").textContent = `${data.projects.length} 个工作空间`;
  document.querySelector("#projects-empty-help").hidden = data.projects.length > 0;
  const list = document.querySelector("#projects");
  list.replaceChildren();
  for (const project of data.projects) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.setAttribute("aria-label", project.name);
    const avatar = document.createElement("span");
    avatar.className = "project-avatar";
    avatar.textContent = project.name.slice(0, 1);
    const info = document.createElement("span");
    info.className = "project-info";
    const title = document.createElement("strong");
    title.textContent = project.name;
    const date = document.createElement("small");
    date.textContent = `创建于 ${new Date(project.created_at).toLocaleDateString("zh-CN")}`;
    info.append(title, date);
    button.append(avatar, info);
    button.dataset.projectId = project.id;
    if (project.id === activeProject) button.setAttribute("aria-current", "true");
    button.addEventListener("click", () => selectProject(project.id).catch(displayError));
    item.append(button);
    list.append(item);
  }
  if (!data.projects.length) list.textContent = "尚无项目";
}
document.querySelector("#project-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const submit = event.target.querySelector("button");
  submit.disabled = true;
  errorBox.textContent = "";
  try {
    const created = await api("/api/projects", { method: "POST", body: JSON.stringify({
      name: document.querySelector("#name").value,
      description: document.querySelector("#description").value
    }) });
    event.target.reset();
    await refreshProjects();
    await refreshCapabilities();
    await selectProject(created.id);
    await closeDialog(document.querySelector("#project-drawer"));
    dismissToast();
  } catch (error) { displayError(error); }
  finally { submit.disabled = false; }
});
async function initialize() {
  const restoredView = activeView;
  const restoredNavigation = navigationVersion;
  const restoredProjectSelection = projectSelectionVersion;
  try {
    const result = await api("/api/bootstrap", { headers: { "X-EEAS-Bootstrap": "1" } });
    csrf = result.csrf_token;
    await refreshProjects();
    await refreshCapabilities();
    document.querySelector("#project-form button").disabled = false;
    if (initialRoute.get("project") && projectSelectionVersion === restoredProjectSelection) {
      await selectProject(initialRoute.get("project"), initialRoute.get("run"), restoredNavigation);
    }
    selectView(navigationVersion === restoredNavigation ? restoredView : lastChosenView);
  } catch (error) { displayError(error); }
}
initialize();

async function selectProject(projectId, restoredRun = null, restoredNavigation = null) {
  projectSelectionVersion += 1;
  uploadSelectionVersion += 1;
  clearTimeout(runTimer);
  clearTimeout(nativeReviewTimer);
  nativeReview = null;
  activeRun = null;
  repairSession = null;
  document.querySelector("#run-panel").hidden = true;
  document.querySelector("#results-empty").hidden = false;
  approvalDisplayKey = null;
  document.querySelector("#execution-tasks").replaceChildren();
  document.querySelector("#task-dependencies").replaceChildren();
  activeProject = projectId;
  activeDocument = null;
  readerRequestVersion += 1;
  resetReader();
  documentRows = [];
  for (const id of ["home-document-count", "home-task-count", "home-run-count"]) document.querySelector("#" + id).textContent = "—";
  if (!restoredRun) updateRoute();
  document.querySelector("#extension-binding").textContent = "正在读取所选项目组合；打开能力/版本查看";
  document.querySelector("#extension-binding-details").textContent = "";
  document.body.classList.add("has-project");
  documentTarget = null;
  requirementTarget = null;
  taskRequirementRows = [];
  taskCount = null;
  renderTaskPreparation();
  selectedRefs = [];
  documentNames.clear();
  taskNames.clear();
  document.querySelector("#requirement-form").reset();
  document.querySelector("#requirements").replaceChildren();
  document.querySelector("#planning-tasks").replaceChildren();
  document.querySelector("#plan-requirements").disabled = true;
  document.querySelector("#requirement-state").textContent = "人工输入，等待确认";
  renderSourceRefs();
  document.querySelector("#document-target").textContent = "导入新资料";
  document.querySelector("#document-form").reset();
  document.querySelector("#document-content").textContent = "";
  document.querySelector("#search-results").replaceChildren();
  document.querySelector("#workspace").hidden = false;
  const detail = await api(`/api/projects/${encodeURIComponent(projectId)}`);
  if (activeProject !== projectId) return;
  dismissToast();
  const panel = document.querySelector("#detail");
  panel.textContent = `${detail.name}\n${detail.description}\n创建于 ${new Date(detail.created_at).toLocaleString("zh-CN")}`;
  panel.hidden = false;
  document.querySelector("#project-context").textContent = detail.name;
  document.querySelector("#sidebar-project-name").textContent = detail.name;
  document.querySelector("#home-scope").textContent = "所选资料、任务与运行均保留独立记录";
  for (const button of document.querySelectorAll("#projects [data-project-id]")) {
    if (button.dataset.projectId === projectId) button.setAttribute("aria-current", "true");
    else button.removeAttribute("aria-current");
  }
  await refreshDocuments(projectId);
  await refreshRequirements(projectId);
  await refreshTasks(projectId);
  await refreshApprovals(projectId);
  if (activeProject === projectId && restoredRun) {
    await viewRun(projectId, restoredRun, true, restoredNavigation === null || navigationVersion === restoredNavigation);
  }
  else if (activeProject === projectId) updateRoute();
}
async function refreshDocuments(projectId = activeProject) {
  const data = await api(`/api/projects/${projectId}/documents`);
  if (activeProject !== projectId) return;
  documentRows = data.documents;
  document.querySelector("#home-document-count").textContent = data.documents.length;
  document.querySelector("#document-count").textContent = `${data.documents.length} 份`;
  const list = document.querySelector("#documents");
  list.replaceChildren();
  for (const doc of data.documents) {
    documentNames.set(doc.id, doc.filename);
    const item = document.createElement("li");
    item.dataset.documentId = doc.id;
    if (activeDocument?.id === doc.id) item.setAttribute("aria-current", "true");
    const view = document.createElement("button");
    view.type = "button";
    view.textContent = `${doc.filename} · v${doc.revision}`;
    view.addEventListener("click", () => viewDocument(projectId, doc).catch(displayError));
    const meta = document.createElement("span");
    meta.className = "document-file-meta";
    meta.textContent = `${Number(doc.character_length || 0).toLocaleString()} 字符 · ${doc.line_count || 0} 行 · ${doc.chunk_count || 0} 段`;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "删除资料";
    remove.addEventListener("click", async () => {
      if (!window.confirm(`删除“${doc.filename}”？历史来源记录会保留，但该资料不再用于新任务。`)) return;
      try {
        await api(`/api/projects/${projectId}/documents/${doc.id}`, { method: "DELETE" });
        if (activeProject !== projectId) return;
        if (activeDocument?.id === doc.id) { activeDocument = null; readerRequestVersion += 1; resetReader(); }
        document.querySelector("#search-results").replaceChildren();
        if (documentTarget?.id === doc.id) documentTarget = null;
        await refreshDocuments(projectId);
        await refreshRequirements(projectId);
        showToast("资料已删除，历史来源记录保留");
      } catch (error) { displayError(error); }
    });
    const revise = document.createElement("button");
    revise.type = "button";
    revise.textContent = "上传新修订";
    revise.addEventListener("click", () => {
      if (activeProject !== projectId) return;
      prepareUpload(doc);
      openUpload();
    });
    item.append(view, meta, revise, remove);
    list.append(item);
  }
  if (!data.documents.length) list.textContent = "尚无资料。点击上传资料，导入第一份任务书。";
}
function resetReader() {
  document.querySelector("#document-structure").hidden = true;
  document.querySelector("#structure-list").replaceChildren();
  document.querySelector("#structure-links").replaceChildren();
  document.querySelector("#reader-title").textContent = "选择一份资料";
  document.querySelector("#reader-version").textContent = "";
  document.querySelector("#reader-meta").textContent = "完整原文按段读取，长任务书也能翻到尾页。";
  document.querySelector("#document-content").textContent = "资料原文将在这里显示";
  document.querySelector("#document-toc").replaceChildren();
  document.querySelector("#reader-identity").textContent = "";
  document.querySelector("#document-assets").replaceChildren();
  document.querySelector("#document-assets").hidden = true;
  document.querySelector("#document-warnings").hidden = true;
  document.querySelector("#document-download").hidden = true;
  document.querySelector("#reader-page").textContent = "尚未打开资料";
  for (const id of ["reader-first", "reader-prev", "reader-next", "reader-last"]) document.querySelector("#" + id).disabled = true;
}
async function viewDocument(projectId, doc) {
  if (activeProject !== projectId) return;
  document.querySelector("#document-assets").replaceChildren();
  document.querySelector("#document-assets").hidden = true;
  document.querySelector("#document-warnings").hidden = true;
  document.querySelector("#document-download").hidden = true;
  documentTarget = doc;
  document.querySelector("#document-structure").hidden = true;
  activeDocument = {projectId, id: doc.id, filename: doc.filename, revision: doc.revision, sha256: doc.sha256, format: doc.format, offset: 0, limit: 5, mode: "chunks"};
  const selected = activeDocument;
  document.querySelector("#document-target").textContent = `修订 ${doc.filename}（当前 v${doc.revision}）`;
  for (const item of document.querySelectorAll("#documents > li")) item.setAttribute("aria-current", String(item.dataset.documentId === doc.id));
  await readDocumentPage(selected, 0);
  if (activeDocument !== selected || activeProject !== projectId) return;
  await readDocumentStructure(selected);
  if (activeDocument !== selected || activeProject !== projectId) return;
  await readDocumentAssets(selected, doc);
}
async function readDocumentAssets(doc, metadata) {
  const data = await api(`/api/projects/${doc.projectId}/documents/${doc.id}/assets?revision=${doc.revision}`);
  if (activeProject !== doc.projectId || activeDocument !== doc) return;
  assertDocumentIdentity(data, doc);
  const assets = document.querySelector("#document-assets");
  assets.replaceChildren();
  assets.hidden = data.assets.length === 0;
  for (const asset of data.assets) {
    const figure = document.createElement("figure");
    const image = document.createElement("img");
    image.src = `/api/projects/${doc.projectId}/documents/${doc.id}/assets/${asset.asset_id}?revision=${doc.revision}`;
    image.alt = `${doc.filename} 原始图像，${asset.width} × ${asset.height} 像素`;
    image.width = asset.width;
    image.height = asset.height;
    image.loading = "lazy";
    const caption = document.createElement("figcaption");
    caption.textContent = `${asset.member_path} · ${asset.width} × ${asset.height} · ${doc.format === "pdf" ? "从 PDF 解码的图像预览" : "原始图像预览"}`;
    const bindings = document.createElement("div");
    bindings.className = "asset-source";
    for (const anchor of (asset.source_anchors || []).slice(0, 5)) {
      const source = (doc.structure?.graph?.nodes || []).find(node => node.source.part === anchor.part && node.source.node_id === anchor.node_id && node.source.scope === anchor.scope);
      if (!source) continue;
      const jump = document.createElement("button");
      jump.type = "button";
      jump.textContent = `来源 ${anchor.scope} · ${anchor.node_id}`;
      jump.addEventListener("click", () => selectStructureSource(doc, source).catch(displayError));
      bindings.append(jump);
    }
    if ((asset.source_anchors || []).length > 5) bindings.append(document.createTextNode(`另有 ${asset.source_anchors.length - 5} 个节点来源，可在结构预览中定位。`));
    caption.append(bindings);
    figure.append(image, caption);
    assets.append(figure);
  }
  const warnings = metadata.warnings || [];
  document.querySelector("#document-warnings").hidden = warnings.length === 0;
  document.querySelector("#document-warnings").textContent = warnings.map(documentWarning).join(" ");
  document.querySelector("#document-download").href = `/api/projects/${doc.projectId}/documents/${doc.id}/download?revision=${doc.revision}`;
  document.querySelector("#document-download").hidden = false;
}
function documentWarning(warning) {
  const exact = {IMAGE_TEXT_NOT_EXTRACTED: "图像已保存，未提取文字。", MODEL_VISION_NOT_VALIDATED: "图像理解尚未验证，请人工核对内容。",
    PDF_ACTIVE_ACTIONS_IGNORED: "PDF 中的自动动作未执行。", PDF_FILE_ATTACHMENTS_NOT_EXECUTED_OR_IMPORTED: "PDF 内嵌附件未执行或导入。",
    PDF_IMAGES_DECODED_TO_PNG_OR_JPEG_WITH_PAGE_PROVENANCE: "PDF 图像已解码为 PNG/JPEG，并保留页码来源。",
    "DOCX layout, numbering, formulas and embedded charts/objects are not rendered; no OCR; original package must be retained": "Word 显示已解析的正文、表格、页眉页脚和注释来源；原始排版、编号、公式及嵌入图表或对象尚未渲染，未进行 OCR。请保留并下载原始 DOCX 核对。",
    "XMind layout/style/markers are not rendered; images are preserved without OCR": "XMind 显示结构化预览，原始布局、样式和标记尚未渲染；图像保留，未进行 OCR。",
    "Visio geometry/formulas/styles/master inheritance are not rendered; connector endpoints are data, not inferred arrow direction; no OCR": "Visio 显示结构化预览，原始几何、公式、样式及母版继承尚未渲染；连接端点按原始数据展示，不推断箭头方向；未进行 OCR。"};
  if (exact[warning]) return exact[warning];
  const scan = /^PDF_PAGE_(\d+)_NO_TEXT_LAYER_OCR_OR_VISION_REQUIRED$/.exec(warning);
  if (scan) return `PDF 第 ${scan[1]} 页没有可提取的文字层，请人工核对原件或使用已验证的 OCR/视觉工具。`;
  const mapping = [["Topic hyperlink preserved as data, never followed: ", "节点外链仅保存为数据，未访问："], ["External image was not fetched: ", "未获取外部图像："],
    ["External OPC relationship was not followed: ", "未访问外部文件关系："], ["Unresolved connector endpoint: ", "连接端点未解析："],
    ["Unresolved XMind relationship: ", "XMind 关系端点未解析："], ["Missing/unsupported image reference: ", "图像引用缺失或不受支持："],
    ["Unresolved inherited master properties: ", "母版继承属性尚未解析："], ["Embedded image has no supported node binding: ", "嵌入图像没有可用的节点来源："]];
  for (const [prefix, label] of mapping) if (warning.startsWith(prefix)) return label + warning.slice(prefix.length);
  return `解析提示：${warning}`;
}
async function readDocumentStructure(doc, knownStructure = null) {
  if (!["docx", "pptx", "pdf", "visio-vsdx", "xmind-json", "xmind-xml"].includes(doc.format)) return;
  const data = knownStructure || await api(`/api/projects/${doc.projectId}/documents/${doc.id}/structure?revision=${doc.revision}`);
  if (activeDocument !== doc || activeProject !== doc.projectId) return;
  assertDocumentIdentity(data, doc);
  doc.structure = data;
  doc.structureOffset = 0;
  doc.structureScope = "";
  doc.structureSelected = null;
  doc.structureEntries = doc.format === "pdf" ? (data.outline?.pages || []).map(page => ({...page, id: `pdf:${page.page_number}`, text: `第 ${page.page_number} 页`, kind: "page", source: {scope: `page:${page.page_number}`, part: "PDF", node_id: String(page.page_number)}})) : data.graph?.nodes || [];
  const wordPartNames = {body: "正文", header: "页眉", footer: "页脚", footnotes: "脚注", endnotes: "尾注", comments: "批注"};
  doc.structureGroups = [...(data.outline?.pages || []), ...(data.outline?.sheets || []), ...(data.outline?.parts || []).map(part => ({scope: `docx:${part.part}`, name: `${wordPartNames[part.kind] || part.kind} · ${part.part}`}))];
  if (doc.format === "docx") doc.structureEntries = doc.structureEntries.map(node => node.kind === "document-part" ? {...node, name: doc.structureGroups.find(part => part.scope === node.source.scope)?.name || node.source.part} : node);
  document.querySelector("#document-structure").hidden = doc.structureEntries.length === 0;
  document.querySelector("#document-structure").open = true;
  document.querySelector("#structure-kind").textContent = doc.format === "pdf" ? "PDF 页目录与解析原文" : doc.format === "docx" ? "Word 结构化预览 · 正文与部件" : doc.format === "pptx" ? "PowerPoint 结构化预览 · 幻灯片与来源" : "结构化预览 · 节点与关系";
  document.querySelector("#structure-help").textContent = doc.format === "pdf" ? "按真实页码定位提取的文字，原始 PDF 排版请下载原件核对；扫描页不编造 OCR。" : doc.format === "docx" ? "按正文、表格、页眉页脚和注释部件定位完整解析原文；这是结构化内容预览，原始 Word 排版请下载 DOCX 核对。每组最多显示 50 个节点。" : doc.format === "pptx" ? "按真实幻灯片、文本、表格、备注和图像来源阅读；原 PowerPoint 排版、时序和图表渲染未执行，请下载 PPTX 核对。每组最多显示 50 个节点。" : "按解析出的节点、父子关系与连接端点阅读；原 Visio / XMind 排版尚未渲染。每组最多显示 50 个节点。";
  renderStructure(doc);
}
function shortNode(node) { const text = node?.text || node?.name || node?.id || "未解析端点"; return text.length > 160 ? text.slice(0, 160) + "…（原文定位可读完整内容）" : text; }
function renderStructure(doc) {
  if (activeDocument !== doc) return;
  const rows = doc.structureEntries.filter(node => !doc.structureScope || node.source.scope === doc.structureScope);
  const visible = rows.slice(doc.structureOffset, doc.structureOffset + 50);
  const select = document.querySelector("#structure-scope");
  select.closest("label").hidden = doc.format === "pdf";
  document.querySelector("#structure-scope-label").textContent = doc.format === "docx" ? "Word 部件" : "页面 / 工作表";
  select.setAttribute("aria-label", doc.format === "docx" ? "选择 Word 部件" : "选择页面或工作表");
  select.replaceChildren(new Option(doc.format === "docx" ? "全部 Word 部件" : "全部页面 / 工作表", ""));
  for (const scope of new Set([...doc.structureGroups.map(group => group.scope), ...visible.map(node => node.source.scope), ...(doc.structureScope ? [doc.structureScope] : [])].filter(Boolean))) {
    const group = doc.structureGroups.find(entry => entry.scope === scope);
    select.append(new Option(`${group?.name || scope} · ${scope}`, scope));
  }
  select.value = doc.structureScope;
  const list = document.querySelector("#structure-list");
  list.replaceChildren();
  for (const node of visible) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.nodeId = node.id;
    button.setAttribute("aria-current", String(doc.structureSelected?.id === node.id));
    button.textContent = shortNode(node);
    const source = document.createElement("small");
    source.textContent = `${node.source.scope} · 节点 ${node.source.node_id} · 行 ${node.line_start}–${node.line_end}`;
    button.append(source);
    button.addEventListener("click", () => selectStructureSource(doc, node).catch(displayError));
    item.append(button);
    list.append(item);
  }
  document.querySelector("#structure-page").textContent = `${rows.length ? doc.structureOffset + 1 : 0}–${Math.min(rows.length, doc.structureOffset + 50)} / ${rows.length} ${doc.format === "pdf" ? "页" : "节点"}`;
  document.querySelector("#structure-prev").disabled = doc.structureOffset === 0;
  document.querySelector("#structure-next").disabled = doc.structureOffset + 50 >= rows.length;
  const chosen = doc.structureSelected;
  const links = document.querySelector("#structure-links");
  links.replaceChildren();
  document.querySelector("#structure-active").textContent = chosen ? `来源 ${chosen.source.scope}\n${chosen.source.part} · ${chosen.source.node_id}\n行 ${chosen.line_start}–${chosen.line_end}` : "选择节点，查看父子与连接关系，并定位解析原文。";
  if (!chosen || doc.format === "pdf") return;
  const edges = (doc.structure.graph?.edges || []).filter(edge => edge.source === chosen.id || edge.target === chosen.id || edge.connector_id === chosen.id);
  for (const edge of edges.slice(0, 50)) {
    const item = document.createElement("li");
    const source = doc.structureEntries.find(node => node.id === edge.source);
    const target = doc.structureEntries.find(node => node.id === edge.target);
    item.textContent = `${({hierarchy: "父子", relationship: "关联", connector: "连接端点", attachment: "连接附着", "section-binding": "章节关联", "note-reference": "注释引用"})[edge.kind] || edge.kind} · ${shortNode(source)} 与 ${shortNode(target)}${edge.text ? " · " + edge.text : ""}`;
    const other = chosen.id === edge.source ? target : source;
    if (other) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = "定位关联节点原文";
      button.addEventListener("click", () => selectStructureSource(doc, other).catch(displayError));
      item.append(button);
    }
    links.append(item);
  }
  if (edges.length > 50) links.append(document.createTextNode(`已显示前 50 项，共 ${edges.length} 项关系。`));
  if (!edges.length) links.textContent = "解析结果中没有与该节点关联的关系。";
}
async function selectStructureSource(doc, node) {
  if (activeDocument !== doc || activeProject !== doc.projectId) return;
  doc.structureSelected = node;
  if (doc.structureScope && doc.structureScope !== node.source.scope) doc.structureScope = node.source.scope;
  const entries = doc.structureEntries.filter(entry => !doc.structureScope || entry.source.scope === doc.structureScope);
  doc.structureOffset = Math.max(0, Math.floor(entries.findIndex(entry => entry.id === node.id) / 50) * 50);
  renderStructure(doc);
  let start = node.char_start;
  if (!Number.isInteger(start)) {
    let left = 0, right = doc.totalChunks - 1;
    while (left < right) {
      const mid = Math.floor((left + right) / 2);
      const data = await api(`/api/projects/${doc.projectId}/documents/${doc.id}/chunks?revision=${doc.revision}&offset=${mid}&limit=1`);
      if (activeDocument !== doc || doc.structureSelected !== node) return;
      assertDocumentIdentity(data, doc);
      if (data.chunks[0].line_end >= node.line_start) right = mid;
      else left = mid + 1;
    }
    const data = await api(`/api/projects/${doc.projectId}/documents/${doc.id}/chunks?revision=${doc.revision}&offset=${left}&limit=1`);
    if (activeDocument !== doc || doc.structureSelected !== node) return;
    assertDocumentIdentity(data, doc);
    const chunk = data.chunks[0];
    let local = 0;
    for (let line = chunk.line_start; line < node.line_start; line++) local = chunk.content.indexOf("\n", local) + 1;
    start = chunk.char_start + local;
  }
  await readDocumentWindow(doc, start, node);
}
document.querySelector("#structure-scope").addEventListener("change", event => {
  const doc = activeDocument;
  if (!doc?.structure) return;
  doc.structureScope = event.target.value;
  doc.structureOffset = 0;
  doc.structureSelected = null;
  renderStructure(doc);
});
for (const direction of ["prev", "next"]) document.querySelector(`#structure-${direction}`).addEventListener("click", () => {
  const doc = activeDocument;
  if (!doc?.structure) return;
  doc.structureOffset = Math.max(0, doc.structureOffset + (direction === "next" ? 50 : -50));
  renderStructure(doc);
});
function assertDocumentIdentity(data, doc) {
  if (data.document_id !== doc.id || data.revision !== doc.revision || data.sha256 !== doc.sha256) throw new Error("资料版本或原文哈希不匹配，请重新选择资料");
}
function paintReaderIdentity(doc, data) {
  document.querySelector("#reader-title").textContent = doc.filename;
  document.querySelector("#reader-version").textContent = `修订 v${doc.revision}`;
  document.querySelector("#reader-identity").textContent = `资料 ${doc.id}\n修订 v${doc.revision}\n原文 SHA256 ${doc.sha256}`;
  document.querySelector("#reader-meta").textContent = `完整资料 ${data.total_characters.toLocaleString()} 字符 · 当前修订固定为 v${doc.revision}`;
}
async function readDocumentPage(doc, offset) {
  const requestVersion = ++readerRequestVersion;
  document.querySelector("#reader-page").textContent = "正在读取原文";
  const data = await api(`/api/projects/${doc.projectId}/documents/${doc.id}/chunks?revision=${doc.revision}&offset=${offset}&limit=${doc.limit}`);
  if (activeProject !== doc.projectId || activeDocument !== doc || requestVersion !== readerRequestVersion) return;
  assertDocumentIdentity(data, doc);
  Object.assign(doc, {mode: "chunks", offset, totalChunks: data.total_chunks, nextOffset: data.next_offset});
  paintReaderIdentity(doc, data);
  document.querySelector("#document-content").textContent = data.chunks.map(chunk => chunk.content).join("");
  const toc = document.querySelector("#document-toc");
  toc.replaceChildren();
  for (const chunk of data.chunks) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = `第 ${chunk.index + 1} 段 · ${chunk.line_start}–${chunk.line_end} 行`;
    button.addEventListener("click", () => {
      if (activeDocument !== doc) return;
      document.querySelector("#document-content").textContent = chunk.content;
      document.querySelector("#reader-meta").textContent = `第 ${chunk.index + 1} 段 · 行 ${chunk.line_start}–${chunk.line_end} · 字符 ${chunk.char_start.toLocaleString()}–${chunk.char_end.toLocaleString()}`;
      for (const row of toc.querySelectorAll("button")) row.removeAttribute("aria-current");
      button.setAttribute("aria-current", "true");
      document.querySelector(".reader-paper").scrollTop = 0;
    });
    toc.append(button);
  }
  document.querySelector(".reader-paper").scrollTop = 0;
  document.querySelector("#reader-page").textContent = `第 ${Math.floor(offset / doc.limit) + 1} / ${Math.max(1, Math.ceil(data.total_chunks / doc.limit))} 页 · 共 ${data.total_chunks} 段`;
  document.querySelector("#reader-first").disabled = offset === 0;
  document.querySelector("#reader-prev").disabled = offset === 0;
  document.querySelector("#reader-next").disabled = data.next_offset === null;
  document.querySelector("#reader-last").disabled = data.next_offset === null;
}
async function readDocumentWindow(doc, charStart, source = null, match = null) {
  const requestVersion = ++readerRequestVersion;
  const data = await api(`/api/projects/${doc.projectId}/documents/${doc.id}/content?revision=${doc.revision}&char_start=${charStart}&char_limit=8000`);
  if (activeProject !== doc.projectId || activeDocument !== doc || requestVersion !== readerRequestVersion || (source && doc.structureSelected !== source)) return;
  assertDocumentIdentity(data, doc);
  Object.assign(doc, {mode: "window", charStart: data.char_start, nextCharStart: data.next_char_start, totalCharacters: data.total_characters});
  paintReaderIdentity(doc, data);
  document.querySelector("#document-content").textContent = data.content;
  if (source) {
    const content = document.querySelector("#document-content");
    content.replaceChildren();
    let line = data.line_start;
    for (const fragment of data.content.match(/[^\n]*\n|[^\n]+$/g) || []) {
      if (line >= source.line_start && line <= source.line_end) {
        const mark = document.createElement("mark");
        mark.dataset.sourceLine = line;
        mark.textContent = fragment;
        content.append(mark);
      } else content.append(document.createTextNode(fragment));
      line += 1;
    }
  }
  if (match) {
    const letters = Array.from(data.content);
    const start = match.match_char_start - data.char_start;
    const end = match.match_char_end - data.char_start;
    if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end <= start || end > letters.length) throw new Error("命中字符范围与冻结原文窗口不匹配，请重新检索");
    const mark = document.createElement("mark");
    mark.dataset.matchCharStart = match.match_char_start;
    mark.dataset.matchCharEnd = match.match_char_end;
    mark.textContent = letters.slice(start, end).join("");
    document.querySelector("#document-content").replaceChildren(document.createTextNode(letters.slice(0, start).join("")), mark, document.createTextNode(letters.slice(end).join("")));
  }
  document.querySelector("#document-toc").replaceChildren();
  document.querySelector("#reader-page").textContent = `字符 ${data.char_start.toLocaleString()}–${data.char_end.toLocaleString()} / ${data.total_characters.toLocaleString()}`;
  document.querySelector("#reader-meta").textContent = `命中原文位置 · 第 ${data.line_start}–${data.line_end} 行 · 固定修订 v${doc.revision}`;
  if (source) document.querySelector("#reader-meta").textContent = `结构化来源 ${source.source.scope} · ${source.source.part} · ${source.source.node_id} · 行 ${source.line_start}–${source.line_end} · 固定修订 v${doc.revision}`;
  document.querySelector("#reader-first").disabled = data.char_start === 0;
  document.querySelector("#reader-prev").disabled = data.char_start === 0;
  document.querySelector("#reader-next").disabled = data.next_char_start === null;
  document.querySelector("#reader-last").disabled = data.next_char_start === null;
  document.querySelector(".reader-paper").scrollTop = 0;
  if (source || match) document.querySelector("#document-content mark:last-of-type")?.scrollIntoView({block: "center", inline: "nearest"});
}
for (const direction of ["first", "prev", "next", "last"]) document.querySelector(`#reader-${direction}`).addEventListener("click", async () => {
  const doc = activeDocument;
  if (!doc) return;
  try {
    if (doc.mode === "window" && ["prev", "next"].includes(direction)) await readDocumentWindow(doc, direction === "prev" ? Math.max(0, doc.charStart - 8000) : doc.nextCharStart);
    else {
      let offset = direction === "first" ? 0 : direction === "prev" ? Math.max(0, doc.offset - doc.limit) : direction === "next" ? doc.nextOffset : Math.floor((doc.totalChunks - 1) / doc.limit) * doc.limit;
      if (doc.mode === "window" && direction === "last") {
        const head = await api(`/api/projects/${doc.projectId}/documents/${doc.id}/chunks?revision=${doc.revision}&offset=0&limit=1`);
        assertDocumentIdentity(head, doc);
        offset = Math.floor((head.total_chunks - 1) / doc.limit) * doc.limit;
      }
      await readDocumentPage(doc, Math.max(0, offset));
    }
  } catch (error) { displayError(error); }
});
document.querySelector("#document-new").addEventListener("click", () => {
  prepareUpload();
});
function renderUploadQueue(items = uploadQueue) {
  const list = document.querySelector("#upload-file-list");
  list.replaceChildren();
  list.hidden = items.length === 0;
  const labels = {PENDING:"等待导入", UPLOADING:"正在导入", SAVED:"已保存", INVALID:"文件不支持", FAILED:"校验未通过", UNKNOWN:"保存结果待核对"};
  for (const item of items) {
    const row = document.createElement("li");
    row.dataset.state = item.state;
    const title = document.createElement("span"); title.className = "upload-file-title"; title.textContent = item.file.name;
    const state = document.createElement("span"); state.className = "upload-file-state"; state.textContent = labels[item.state];
    const size = document.createElement("span"); size.className = "upload-file-size"; size.textContent = `${(item.file.size / 1024).toFixed(1)} KiB`;
    row.append(title, state, size);
    if (item.error) { const error = document.createElement("span"); error.className = "upload-file-error"; error.textContent = item.error; row.append(error); }
    list.append(row);
  }
  const counts = uploadBatch.counts(items);
  const summary = document.querySelector("#upload-batch-summary");
  summary.hidden = !items.length;
  summary.textContent = `共 ${counts.total} 份 · ${counts.SAVED} 份已保存` + (counts.FAILED + counts.INVALID ? ` · ${counts.FAILED + counts.INVALID} 份未通过` : "") + (counts.UNKNOWN ? ` · ${counts.UNKNOWN} 份结果待核对` : "") + (counts.PENDING ? ` · ${counts.PENDING} 份等待导入` : "");
  document.querySelector("#upload-view-library").hidden = !counts.SAVED && !counts.UNKNOWN;
}
document.querySelector("#document-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const projectId = activeProject;
  const files = Array.from(document.querySelector("#document-file").files);
  if (!projectId) { uploadFailure("先在主页创建或选择一个项目，再上传资料。"); return; }
  if (!files.length) { uploadFailure("请选择资料文件。"); return; }
  if (documentTarget && files.length !== 1) { uploadFailure("修订只能选择一份资料；多份资料请改为新资料导入。"); return; }
  if (uploadQueue.length !== files.length || uploadQueue.some((item, index) => item.file !== files[index])) uploadQueue = uploadBatch.create(files);
  const items = uploadQueue;
  if (!items.some(item => item.state === "PENDING" || item.state === "FAILED")) {
    uploadFailure(items.find(item => item.error)?.error || "资料已经保存；请选择新的资料，或打开资料库核对。"); return;
  }
  const target = documentTarget ? {...documentTarget} : null;
  const source = document.querySelector("#document-source").value;
  const submit = event.target.querySelector('[type="submit"]');
  const drawer = document.querySelector("#upload-drawer");
  const token = {projectId, selection: uploadSelectionVersion, instance: drawer.__instanceVersion, navigation: navigationVersion, total: items.length, index: -1, stopRequested: false};
  activeUploadRequest = token;
  submit.disabled = true; submit.textContent = "正在导入原文…";
  document.querySelector("#upload-error").textContent = "";
  const stop = document.querySelector("#upload-stop-remaining"); stop.hidden = items.length < 2; stop.disabled = false;
  try {
    await uploadBatch.run(items, async item => {
      token.index = item.index;
      const data = new FormData(); data.append("file", item.file); data.append("source", source);
      let path = `/api/projects/${projectId}/documents`, method = "POST";
      if (target) { path += `/${target.id}`; method = "PUT"; data.append("expected_revision", target.revision); }
      return api(path, {method, body:data, uploadContext:token});
    }, () => { if (currentUpload(token)) renderUploadQueue(items); }, () => currentUpload(token) && drawer.open && !token.stopRequested);
    if (!currentUpload(token) || !drawer.open) return;
    renderUploadQueue(items);
    const counts = uploadBatch.counts(items);
    if (counts.SAVED) {
      await refreshDocuments(projectId);
      if (!currentUpload(token) || navigationVersion !== token.navigation || !drawer.open) return;
      await refreshRequirements(projectId);
      if (!currentUpload(token) || navigationVersion !== token.navigation || !drawer.open) return;
    }
    const problem = items.find(item => item.state === "FAILED" || item.state === "INVALID" || item.state === "UNKNOWN");
    if (problem) uploadFailure(problem.state === "UNKNOWN" ? `${problem.file.name}：保存结果待核对，请先打开资料库。不会自动重传这份文件。` : problem.error);
    if (items.length === 1 && counts.SAVED === 1) {
      const saved = items[0].saved;
      documentTarget = null; uploadQueue = []; event.target.reset();
      document.querySelector("#document-target").textContent = "导入新资料";
      document.querySelector("#upload-file-state").textContent = "尚未选择文件";
      await closeDialog(drawer);
      if (!currentUpload(token) || navigationVersion !== token.navigation) return;
      selectView("library", true, true);
      await viewDocument(projectId, {...saved, id:saved.document_id || saved.id});
      if (currentUpload(token)) showToast(`已导入 ${saved.filename}，原文与修订已保存`);
    }
  } catch (error) { if (currentUpload(token) && drawer.open) uploadFailure(error.message); }
  finally {
    if (currentUpload(token)) {
      const counts = uploadBatch.counts(items);
      submit.disabled = items.length > 1 && !counts.PENDING && !counts.FAILED;
      submit.textContent = counts.SAVED && items.length > 1 ? (submit.disabled ? "导入已结束" : "重试未导入项") : "导入资料";
      stop.hidden = true;
    }
  }
});
async function searchDocuments(projectId, keyword, offset = 0, append = false) {
  const version = ++searchRequestVersion;
  try {
    const data = await api(`/api/projects/${projectId}/search?keyword=${encodeURIComponent(keyword)}&offset=${offset}&limit=50`);
    if (activeProject !== projectId || version !== searchRequestVersion) return;
    const list = document.querySelector("#search-results");
    if (!append) list.replaceChildren();
    else list.querySelector(".search-more")?.remove();
    for (const hit of data.hits) {
      const item = document.createElement("li");
      item.textContent = `${hit.filename} · v${hit.revision} · 第 ${hit.line_start} 行：${hit.excerpt}`;
      const locate = document.createElement("button");
      locate.type = "button";
      locate.textContent = "查看命中原文";
      locate.addEventListener("click", async () => {
        if (activeProject !== projectId) return;
        try {
          const doc = {projectId, id: hit.document_id, filename: hit.filename, revision: hit.revision, sha256: hit.sha256, limit: 5, offset: 0};
          resetReader();
          activeDocument = doc;
          documentTarget = {id: doc.id, filename: doc.filename, revision: doc.revision, sha256: doc.sha256};
          const metadata = await api(`/api/projects/${projectId}/documents/${doc.id}/structure?revision=${doc.revision}`);
          if (activeProject !== projectId || activeDocument !== doc) return;
          assertDocumentIdentity(metadata, doc);
          doc.format = metadata.format;
          await readDocumentStructure(doc, metadata);
          if (activeProject !== projectId || activeDocument !== doc) return;
          await readDocumentAssets(doc, metadata);
          if (activeProject !== projectId || activeDocument !== doc) return;
          await readDocumentWindow(doc, Math.max(0, hit.match_char_start - 1500), null, hit);
        } catch (error) { displayError(error); }
      });
      const cite = document.createElement("button");
      cite.type = "button";
      cite.textContent = "引用该来源";
      cite.addEventListener("click", () => {
        if (activeProject !== projectId) return;
        const ref = {document_id: hit.document_id, revision: hit.revision, sha256: hit.sha256, line_start: hit.line_start, line_end: hit.line_end};
        if (!selectedRefs.some(r => JSON.stringify(r) === JSON.stringify(ref))) selectedRefs.push(ref);
        renderSourceRefs();
        document.querySelector("#plan-requirements").disabled = true;
        showToast(`已添加第 ${hit.line_start} 行来源，可在需求页核对`);
      });
      item.append(locate, cite);
      list.append(item);
    }
    if (!data.hits.length) list.textContent = "未找到匹配资料。请检查关键词或选择其它修订。";
    if (data.next_offset !== null) {
      const more = document.createElement("button");
      more.type = "button";
      more.className = "search-more";
      more.textContent = "加载后续命中";
      more.addEventListener("click", () => { more.disabled = true; searchDocuments(projectId, keyword, data.next_offset, true).catch(displayError); });
      list.append(more);
    }
  } catch (error) { displayError(error); }
}
document.querySelector("#search-form").addEventListener("submit", event => {
  event.preventDefault();
  searchDocuments(activeProject, document.querySelector("#keyword").value).catch(displayError);
});

function renderSourceRefs() {
  const list = document.querySelector("#source-refs");
  list.replaceChildren();
  for (const ref of selectedRefs) {
    const item = document.createElement("li");
    item.textContent = `${documentNames.get(ref.document_id) || "资料引用"} · v${ref.revision} · 行 ${ref.line_start}—${ref.line_end}`;
    list.append(item);
  }
  if (!selectedRefs.length) list.textContent = "当前草稿无来源引用；不会自动作为已验证事实";
}
function fillRequirement(payload) {
  document.querySelector("#requirement-title").value = payload.title;
  document.querySelector("#requirement-text").value = payload.requirements.map(r => r.text).join("\n");
  document.querySelector("#assumptions").value = payload.assumptions.join("\n");
  document.querySelector("#questions").value = payload.questions.join("\n");
  document.querySelector("#acceptance-text").value = payload.acceptance.map(a => a.text).join("\n");
  document.querySelector("#confirmed").checked = payload.confirmed;
  selectedRefs = [];
  for (const item of payload.requirements) for (const ref of item.source_refs) {
    if (!selectedRefs.some(r => JSON.stringify(r) === JSON.stringify(ref))) selectedRefs.push(ref);
  }
  draftOrigin = payload.origin;
  renderSourceRefs();
}
const sourceStatus = {CURRENT: "来源引用有效", STALE: "来源已变更，待复核", UNSOURCED: "含无来源需求"};
async function refreshRequirements(projectId = activeProject) {
  const data = await api(`/api/projects/${projectId}/requirements`);
  if (activeProject !== projectId) return;
  taskRequirementRows = data.requirements;
  const list = document.querySelector("#requirements");
  list.replaceChildren();
  for (const row of data.requirements) {
    const item = document.createElement("li");
    const view = document.createElement("button");
    view.textContent = `${row.payload.title} · v${row.revision} · ${sourceStatus[row.source_status]}`;
    view.addEventListener("click", () => {
      if (activeProject !== projectId) return;
      requirementTarget = row;
      fillRequirement(row.payload);
      document.querySelector("#requirement-state").textContent = `已保存 v${row.revision}；${sourceStatus[row.source_status]}`;
      document.querySelector("#plan-requirements").disabled = !row.payload.confirmed || row.source_status === "STALE";
    });
    item.append(view);
    list.append(item);
    if (requirementTarget?.id === row.id) {
      requirementTarget = row;
      document.querySelector("#requirement-state").textContent = `已保存 v${row.revision}；${sourceStatus[row.source_status]}`;
      if (row.source_status === "STALE") document.querySelector("#plan-requirements").disabled = true;
    }
  }
  renderTaskPreparation();
  const tasks = await api(`/api/projects/${projectId}/planning/tasks`);
  if (activeProject !== projectId) return;
  const taskList = document.querySelector("#planning-tasks");
  taskList.replaceChildren();
  for (const task of tasks.tasks) {
    const item = document.createElement("li");
    item.textContent = `${task.goal} · 需求 v${task.requirement_revision} · ${task.review_status === "NEEDS_REVIEW" ? "待复核" : "规划草稿"}`;
    taskList.append(item);
  }
}
function lines(id) { return document.querySelector(id).value.split("\n").map(s => s.trim()).filter(Boolean); }
document.querySelector("#requirement-form").addEventListener("input", () => {
  document.querySelector("#plan-requirements").disabled = true;
});
document.querySelector("#requirement-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const projectId = activeProject;
  const requirements = lines("#requirement-text").map((text, i) => ({id: `R${i+1}`, text, source_refs: selectedRefs}));
  const acceptance = lines("#acceptance-text").map((text, i) => ({id: `A${i+1}`, text,
    requirement_ids: requirements.map(r => r.id)}));
  const body = {schema_version: "eeas.requirements.v1", title: document.querySelector("#requirement-title").value,
    requirements, acceptance, assumptions: lines("#assumptions"), questions: lines("#questions"),
    confirmed: document.querySelector("#confirmed").checked, origin: draftOrigin};
  let path = `/api/projects/${projectId}/requirements`;
  const method = requirementTarget ? "PUT" : "POST";
  if (requirementTarget) path += `/${requirementTarget.id}?expected_revision=${requirementTarget.revision}`;
  try {
    const row = await api(path, {method, body: JSON.stringify(body)});
    if (activeProject !== projectId) return;
    requirementTarget = row;
    document.querySelector("#plan-requirements").disabled = !row.payload.confirmed || row.source_status === "STALE";
    await refreshRequirements(projectId);
  } catch (error) { displayError(error); }
});
document.querySelector("#requirement-new").addEventListener("click", () => {
  requirementTarget = null;
  draftOrigin = "manual";
  document.querySelector("#requirement-form").reset();
  document.querySelector("#requirement-state").textContent = "新建人工需求，等待确认";
  document.querySelector("#plan-requirements").disabled = true;
});
document.querySelector("#clear-sources").addEventListener("click", () => {
  selectedRefs = [];
  renderSourceRefs();
  document.querySelector("#plan-requirements").disabled = true;
});
document.querySelector("#extract-mock").addEventListener("click", async () => {
  const projectId = activeProject;
  if (!documentTarget) { displayError(new Error("先在资料列表选择一份资料")); return; }
  const source = {...documentTarget};
  try {
    const result = await api(`/api/projects/${projectId}/planning/extract`, {method: "POST", body: JSON.stringify({
      document_id: source.id, revision: source.revision, mode: "mock", include_images: false, search_enabled: false})});
    if (activeProject !== projectId || !documentTarget || documentTarget.id !== source.id || documentTarget.revision !== source.revision) return;
    applyExtractionProposal(result);
  } catch (error) { displayError(error); }
});
function extractionKey(projectId, doc) { return doc ? `${projectId}:${doc.id}:${doc.revision}:${doc.sha256}` : ""; }
function currentExtraction(draft) { return extractionDraft === draft && draft.key === extractionKey(activeProject, documentTarget); }
function extractionMode() { return document.querySelector("input[name=extraction-mode]:checked").value; }
function applyExtractionProposal(result) {
  requirementTarget = null;
  fillRequirement({...result.proposal, confirmed: false});
  document.querySelector("#requirement-state").textContent = result.mode === "mock" ? "Mock草案，模型未验证，等待人工编辑确认" : "原生建议，未独立验收，等待人工核对确认";
  document.querySelector("#plan-requirements").disabled = true;
}
function extractionSelectionChanged(draft) { draft.receipt = null; draft.result = null; draft.selectionVersion += 1; renderExtraction(); animateConsentBadge("#extract-context-badge"); }
async function readExtractionPage(draft, offset) {
  const version = ++draft.pageVersion;
  const data = await api(`/api/projects/${draft.projectId}/documents/${draft.doc.id}/chunks?revision=${draft.doc.revision}&offset=${offset}&limit=8`);
  if (!currentExtraction(draft) || version !== draft.pageVersion) return;
  assertDocumentIdentity(data, draft.doc);
  Object.assign(draft, {offset: data.offset, totalChunks: data.total_chunks, totalCharacters: data.total_characters, chunks: data.chunks, ready: true});
  renderExtraction();
}
async function openExtraction() {
  if (!activeProject || !documentTarget) { displayError(new Error("先在资料库选择需要提取的资料")); return; }
  const key = extractionKey(activeProject, documentTarget);
  const drawer = document.querySelector("#extraction-drawer");
  if (extractionDraft?.key === key && extractionDraft.ready) { renderExtraction(); openDialog(drawer); return; }
  const draft = {key, projectId: activeProject, doc: {...documentTarget}, offset: 0, imageOffset: 0, selected: null, assets: [], imageIds: new Set(), chunks: [], pageVersion: 0, selectionVersion: 0, ready: false};
  extractionDraft = draft;
  document.querySelector("input[name=extraction-mode][value=mock]").checked = true;
  document.querySelector("#extract-include-images").checked = false;
  document.querySelector("#extract-search-enabled").checked = false;
  document.querySelector("#extraction-error").textContent = "";
  document.querySelector("#extraction-state").textContent = "正在读取本次固定资料；超限不会静默截断。";
  renderExtraction(); openDialog(drawer);
  try {
    const assets = await api(`/api/projects/${draft.projectId}/documents/${draft.doc.id}/assets?revision=${draft.doc.revision}`);
    if (!currentExtraction(draft)) return;
    assertDocumentIdentity(assets, draft.doc); draft.assets = assets.assets;
    await readExtractionPage(draft, 0);
    if (currentExtraction(draft)) document.querySelector("#extraction-state").textContent = "核对资料与覆盖范围后提交；建议不会自动保存或验收。";
  } catch (error) { if (currentExtraction(draft)) document.querySelector("#extraction-error").textContent = error.message; }
}
function renderExtraction() {
  const draft = extractionDraft;
  if (!draft) return;
  const live = extractionMode() === "live";
  const images = document.querySelector("#extract-include-images"), search = document.querySelector("#extract-search-enabled");
  const imageReady = live && runtimeCapabilities?.image_input?.enabled_for_attempt;
  const searchReady = live && runtimeCapabilities?.search_mcp?.status === "CONFIGURED_FOR_ATTEMPT";
  if (!imageReady) { images.checked = false; draft.imageIds.clear(); }
  if (!searchReady) search.checked = false;
  images.disabled = !imageReady || !draft.assets.length || !!draft.busy;
  search.disabled = !searchReady || !!draft.busy;
  document.querySelector("#extraction-source").textContent = `${draft.doc.filename || documentNames.get(draft.doc.id)} · 固定 v${draft.doc.revision} · 原件 SHA256 ${draft.doc.sha256}`;
  const custom = document.querySelector("#extract-custom-chunks"); custom.checked = !!draft.selected; custom.disabled = !draft.ready || !!draft.busy;
  const list = document.querySelector("#extract-chunk-list"); list.replaceChildren();
  for (const chunk of draft.chunks) {
    const item = document.createElement("li"), label = document.createElement("label"), check = document.createElement("input");
    check.type = "checkbox"; check.dataset.chunkIndex = chunk.index; check.checked = draft.selected ? draft.selected.has(chunk.index) : true; check.disabled = !draft.selected || !!draft.busy;
    check.addEventListener("change", () => { if (!currentExtraction(draft) || draft.busy) return; if (check.checked) draft.selected.add(chunk.index); else draft.selected.delete(chunk.index); extractionSelectionChanged(draft); });
    label.append(check, document.createTextNode(`第 ${chunk.index + 1} 段 · 行 ${chunk.line_start}–${chunk.line_end} · ${chunk.char_end - chunk.char_start} 字符`));
    item.append(label); list.append(item);
  }
  document.querySelector("#extract-chunks-page").textContent = draft.ready ? `${draft.totalChunks ? draft.offset + 1 : 0}–${Math.min(draft.totalChunks, draft.offset + 8)} / ${draft.totalChunks} 段` : "正在读取";
  document.querySelector("#extract-chunks-prev").disabled = !draft.offset || !!draft.busy;
  document.querySelector("#extract-chunks-next").disabled = !draft.ready || draft.offset + 8 >= draft.totalChunks || !!draft.busy;
  document.querySelector("#extract-select-last").disabled = !draft.totalChunks || !!draft.busy;
  document.querySelector("#extract-preview-context").disabled = !draft.ready || !!draft.busy;
  document.querySelector("#extract-context-badge").textContent = draft.selected ? `明确选择 ${draft.selected.size} 段` : "完整资料";
  document.querySelector("#extract-context-summary").textContent = draft.ready ? `${draft.totalCharacters.toLocaleString()} 字符，${draft.totalChunks} 段。${draft.selected ? `省略 ${draft.totalChunks - draft.selected.size} 段，部分上下文不代表读过整份资料。` : "完整内容超限时将明确拒绝，不自动删减。"}` : "读取固定修订的段落目录";
  const receipt = draft.receipt;
  document.querySelector("#extract-context-receipt").textContent = receipt ? `${receipt.coverage === "COMPLETE" ? "完整覆盖" : "部分上下文 · 显式选择"}：${receipt.included_chunks.length} / ${receipt.total_chunks} 段，${receipt.included_characters.toLocaleString()} 字符 / ${receipt.included_utf8_bytes.toLocaleString()} UTF-8 字节。
上下文 SHA256 ${receipt.context_sha256}` : "尚未核对覆盖范围。改变所选段落后请重新核对。";
  document.querySelector("#extract-image-status").textContent = !live ? "Mock 不读取图像内容；切换到原生模型后仍需明确批准视觉输入。" : imageReady ? "本次允许尝试视觉输入，模型兼容性尚未验证。只发送明确选中的原图。" : `当前未批准视觉输入：${runtimeCapabilities?.image_input?.blockers?.join(", ") || "配置正在读取"}`;
  document.querySelector("#extract-search-status").textContent = !live ? "Mock 不执行联网检索。默认不发送检索许可。" : searchReady ? "已配置搜索专用 MCP；接口兼容性和服务费用尚未验证。" : `联网检索未配置或被拒绝：${runtimeCapabilities?.search_mcp?.reason || runtimeCapabilities?.search_mcp?.status || "配置正在读取"}`;
  const imageList = document.querySelector("#extract-image-list"); imageList.replaceChildren();
  for (const asset of draft.assets.slice(draft.imageOffset, draft.imageOffset + 4)) {
    const item = document.createElement("li"), label = document.createElement("label"), check = document.createElement("input"), image = document.createElement("img");
    check.type = "checkbox"; check.dataset.assetId = asset.asset_id; check.checked = draft.imageIds.has(asset.asset_id); check.disabled = !images.checked || !!draft.busy;
    check.addEventListener("change", () => { if (!currentExtraction(draft) || draft.busy) return; if (check.checked) draft.imageIds.add(asset.asset_id); else draft.imageIds.delete(asset.asset_id); draft.result = null; renderExtraction(); animateConsentBadge("#extract-image-badge"); });
    image.src = `/api/projects/${draft.projectId}/documents/${draft.doc.id}/assets/${asset.asset_id}?revision=${draft.doc.revision}`; image.alt = `原图 ${asset.member_path}`; image.loading = "lazy";
    label.append(check, image, document.createTextNode(`${asset.member_path} · ${asset.width} × ${asset.height}`)); item.append(label); imageList.append(item);
  }
  document.querySelector("#extract-images-page").textContent = `${draft.assets.length ? draft.imageOffset + 1 : 0}–${Math.min(draft.assets.length, draft.imageOffset + 4)} / ${draft.assets.length} 图`;
  document.querySelector("#extract-images-prev").disabled = !draft.imageOffset || !!draft.busy;
  document.querySelector("#extract-images-next").disabled = draft.imageOffset + 4 >= draft.assets.length || !!draft.busy;
  document.querySelector("#extract-image-badge").textContent = images.checked ? `明确选择 ${draft.imageIds.size} 图` : "默认关闭";
  document.querySelector("#extract-search-badge").textContent = search.checked ? "已明确勾选" : "默认关闭";
  const submit = document.querySelector("#submit-extraction"); submit.disabled = !draft.ready || !!draft.busy;
  submit.textContent = draft.busy ? "正在等待提取结果" : live ? "提交原生提取" : "生成 Mock 草案";
  for (const mode of document.querySelectorAll("input[name=extraction-mode]")) mode.disabled = !!draft.busy;
  document.querySelector("#use-extraction-result").hidden = !draft.result;
}
function extractionBody(draft) {
  if (!currentExtraction(draft)) throw new Error("资料或修订已经变化，请关闭后重新核对提取上下文");
  if (draft.selected && (!draft.selected.size || draft.selected.size > 50)) throw new Error("请明确选择 1 至 50 段，或取消选段使用完整资料");
  const images = document.querySelector("#extract-include-images").checked;
  const body = {document_id: draft.doc.id, revision: draft.doc.revision, mode: extractionMode(), include_images: images, search_enabled: document.querySelector("#extract-search-enabled").checked};
  if (draft.selected) body.chunk_indexes = [...draft.selected].sort((a,b) => a-b);
  if (images) {
    if (!draft.imageIds.size || draft.imageIds.size > (runtimeCapabilities?.image_input?.max_images || 0)) throw new Error("请选择至少一份原图，且不超过本次配置的图像数量上限");
    body.image_asset_ids = [...draft.imageIds];
  }
  return body;
}
for (const id of ["reader-extract", "configure-extraction"]) document.querySelector(`#${id}`).addEventListener("click", openExtraction);
document.querySelector("#close-extraction").addEventListener("click", () => closeDialog(document.querySelector("#extraction-drawer")));
document.querySelector("#extraction-drawer").addEventListener("cancel", event => { event.preventDefault(); closeDialog(event.currentTarget); });
for (const mode of document.querySelectorAll("input[name=extraction-mode]")) mode.addEventListener("change", () => { extractionDraft.result = null; renderExtraction(); animateConsentBadge("#extract-context-badge"); });
document.querySelector("#extract-custom-chunks").addEventListener("change", event => { if (!extractionDraft || extractionDraft.busy) return; extractionDraft.selected = event.target.checked ? new Set() : null; extractionSelectionChanged(extractionDraft); });
for (const direction of ["prev", "next"]) document.querySelector(`#extract-chunks-${direction}`).addEventListener("click", () => { const draft = extractionDraft; if (draft && !draft.busy) readExtractionPage(draft, Math.max(0, draft.offset + (direction === "next" ? 8 : -8))).catch(error => { if (currentExtraction(draft)) document.querySelector("#extraction-error").textContent = error.message; }); });
document.querySelector("#extract-select-last").addEventListener("click", async () => { const draft = extractionDraft; if (!draft?.totalChunks || draft.busy) return; draft.selected = new Set([draft.totalChunks - 1]); extractionSelectionChanged(draft); await readExtractionPage(draft, Math.floor((draft.totalChunks - 1) / 8) * 8).catch(error => { if (currentExtraction(draft)) document.querySelector("#extraction-error").textContent = error.message; }); });
for (const direction of ["prev", "next"]) document.querySelector(`#extract-images-${direction}`).addEventListener("click", () => { const draft = extractionDraft; if (!draft || draft.busy) return; draft.imageOffset = Math.max(0, draft.imageOffset + (direction === "next" ? 4 : -4)); renderExtraction(); });
document.querySelector("#extract-include-images").addEventListener("change", event => { if (!event.target.checked) extractionDraft.imageIds.clear(); extractionDraft.result = null; renderExtraction(); animateConsentBadge("#extract-image-badge"); });
document.querySelector("#extract-search-enabled").addEventListener("change", () => { extractionDraft.result = null; renderExtraction(); animateConsentBadge("#extract-search-badge"); });
document.querySelector("#extract-preview-context").addEventListener("click", async () => {
  const draft = extractionDraft, version = draft?.selectionVersion;
  try {
    const body = extractionBody(draft);
    const data = await api(`/api/projects/${draft.projectId}/documents/${draft.doc.id}/context`, {method: "POST", body: JSON.stringify({revision: draft.doc.revision, ...(body.chunk_indexes ? {chunk_indexes: body.chunk_indexes} : {})})});
    if (!currentExtraction(draft) || draft.selectionVersion !== version) return;
    assertDocumentIdentity(data, draft.doc); draft.receipt = data; renderExtraction();
  } catch (error) { if (currentExtraction(draft) && draft.selectionVersion === version) document.querySelector("#extraction-error").textContent = `上下文拒绝：${error.message}`; }
});
async function useExtractionResult(draft) {
  if (!currentExtraction(draft) || !draft.result) return;
  await closeDialog(document.querySelector("#extraction-drawer"));
  if (!currentExtraction(draft)) return;
  applyExtractionProposal(draft.result); selectView("requirements", true, true);
}
document.querySelector("#use-extraction-result").addEventListener("click", () => useExtractionResult(extractionDraft));
document.querySelector("#extraction-form").addEventListener("submit", async event => {
  event.preventDefault(); const draft = extractionDraft; if (!draft || draft.busy) return;
  const drawer = document.querySelector("#extraction-drawer"), instance = drawer.__instanceVersion;
  document.querySelector("#extraction-error").textContent = "";
  try {
    const body = extractionBody(draft); draft.busy = true; renderExtraction();
    document.querySelector("#extraction-state").textContent = body.mode === "live" ? "正在等待原生建议；返回不会自动保存、确认或验收。" : "正在解析明确选择的原始文本。";
    const result = await api(`/api/projects/${draft.projectId}/planning/extract`, {method: "POST", body: JSON.stringify(body)});
    if (!currentExtraction(draft)) return;
    draft.result = result;
    document.querySelector("#extraction-state").textContent = `已返回待人工核对的${result.mode === "live" ? "原生建议" : "Mock 草案"} · ${result.coverage === "COMPLETE" ? "完整文本覆盖" : "部分显式上下文"} · 响应上下文 SHA256 ${result.context_sha256 || "本次响应未提供"}${result.execution_id ? " · 原生记录 " + result.execution_id : ""}`;
    if (drawer.open && drawer.__instanceVersion === instance) await useExtractionResult(draft);
  } catch (error) { if (currentExtraction(draft)) { document.querySelector("#extraction-error").textContent = `提取被拒绝：${error.message}`; document.querySelector("#extraction-state").textContent = "本次未返回有效草案；超限不截断、未接通不编造结果。"; } }
  finally { draft.busy = false; if (currentExtraction(draft)) renderExtraction(); }
});

document.querySelector("#plan-requirements").addEventListener("click", async () => {
  const projectId = activeProject;
  if (!requirementTarget) return;
  try {
    await api(`/api/projects/${projectId}/requirements/${requirementTarget.id}/plan`, {
      method: "POST", body: JSON.stringify({revision: requirementTarget.revision})});
    await refreshRequirements(projectId);
  } catch (error) { displayError(error); }
});

function renderTaskPreparation() {
  const ready = row => !!row?.payload.confirmed && row.source_status !== "STALE";
  const available = taskRequirementRows.filter(ready);
  let status = "待选择需求", guidance = "先确认并保存一份需求，再创建执行任务。", phase = "confirm";
  if (requirementTarget?.source_status === "STALE") {
    status = "来源待复核";
    guidance = "所选需求的来源已变更；先复核并确认最新修订，再创建任务。";
  } else if (requirementTarget && !requirementTarget.payload.confirmed) {
    status = "需求待确认";
    guidance = "所选需求尚未确认；先确认并保存需求，再创建任务。";
  } else if (ready(requirementTarget)) {
    status = "已确认 · 可创建";
    guidance = "所选需求已确认；填写任务目标与执行边界，创建第一项任务。";
    phase = "create";
  } else if (available.length) {
    guidance = `已有 ${available.length} 份可用需求；先选择已确认需求，再创建任务。`;
    phase = "select";
  }
  const badge = document.querySelector("#task-source-status");
  badge.textContent = status;
  badge.dataset.phase = phase;
  document.querySelector("#task-source-title").textContent = requirementTarget?.payload.title || "尚未选择需求";
  document.querySelector("#task-select-requirement").textContent = phase === "create" ? "更换来源需求" : "选择已保存需求";
  document.querySelector("#task-source-note").textContent = requirementTarget
    ? `已保存 v${requirementTarget.revision} · ${sourceStatus[requirementTarget.source_status]} · 新任务固定该修订`
    : guidance;
  document.querySelector("#task-list-count").textContent = taskCount === null ? "正在读取任务" : `${taskCount} 项任务`;
  if (taskCount === 0) {
    const empty = document.createElement("li");
    empty.className = "task-empty";
    empty.dataset.phase = phase;
    const heading = document.createElement("h4");
    heading.textContent = "尚无执行任务";
    const note = document.createElement("p");
    note.textContent = guidance;
    empty.append(heading, note);
    document.querySelector("#execution-tasks").replaceChildren(empty);
  }
}
async function refreshTasks(projectId = activeProject) {
  const result = await api(`/api/projects/${projectId}/tasks`);
  const histories = await Promise.all(result.tasks.map(task => api(`/api/projects/${projectId}/tasks/${task.id}/runs`)));
  if (activeProject !== projectId) return;
  taskCount = result.tasks.length;
  document.querySelector("#home-task-count").textContent = result.tasks.length;
  document.querySelector("#home-run-count").textContent = histories.reduce((count, item) => count + item.runs.length, 0);
  const list = document.querySelector("#execution-tasks");
  const dependencies = document.querySelector("#task-dependencies");
  const selected = new Set(Array.from(dependencies.selectedOptions).map(o => o.value));
  list.replaceChildren();
  dependencies.replaceChildren();
  for (const [index, task] of result.tasks.entries()) {
    taskNames.set(`${task.id}:${task.revision}`, task.spec.goal);
    const item = document.createElement("li");
    item.dataset.taskId = task.id;
    const heading = document.createElement("h4");
    heading.className = "task-heading";
    heading.textContent = task.spec.goal;
    const meta = document.createElement("p");
    meta.textContent = `v${task.revision} · ${task.review_status === "CURRENT" ? "Mock任务" : "待复核"}`;
    const actions = document.createElement("div");
    actions.className = "task-actions";
    const start = document.createElement("button");
    start.textContent = "启动任务";
    start.disabled = task.review_status !== "CURRENT";
    start.addEventListener("click", async () => {
      start.disabled = true;
      try {
        const run = await api(`/api/projects/${projectId}/tasks/${task.id}/runs`, {method: "POST", body: JSON.stringify({
          revision: task.revision, operation_id: crypto.randomUUID()})});
        if (activeProject === projectId) await viewRun(projectId, run.id);
        if (activeProject === projectId) await refreshApprovals(projectId);
        if (activeProject === projectId) await refreshTasks(projectId);
      } catch (error) { displayError(error); }
      finally { if (task.review_status === "CURRENT") start.disabled = false; }
    });
    const history = document.createElement("button");
    history.textContent = "查看最近运行";
    history.addEventListener("click", async () => {
      try {
        const rows = await api(`/api/projects/${projectId}/tasks/${task.id}/runs`);
        if (rows.runs.length && activeProject === projectId) await viewRun(projectId, rows.runs.at(-1).id);
        else displayError(new Error("此任务尚无运行记录"));
      } catch (error) { displayError(error); }
    });
    actions.append(start, history);
    const latest = histories[index].runs.at(-1);
    if (latest) {
      const open = document.createElement("a");
      open.textContent = "独立窗口查看最近运行";
      open.href = routeUrl("results", projectId, latest.id, true);
      open.target = "_blank";
      open.rel = "noopener";
      actions.append(open);
    }
    item.append(heading, meta, actions);
    if (histories[index].runs.length) {
      const past = document.createElement("details");
      const label = document.createElement("summary");
      label.textContent = `运行记录（${histories[index].runs.length}）`;
      const runs = document.createElement("ul");
      runs.className = "past-runs";
      for (const run of histories[index].runs) {
        const row = document.createElement("li");
        row.dataset.runId = run.id;
        const current = document.createElement("button");
        current.type = "button";
        current.textContent = `查看第 ${run.attempt_id} 次运行`;
        current.addEventListener("click", () => viewRun(projectId, run.id).catch(displayError));
        const open = document.createElement("a");
        open.textContent = "独立窗口";
        open.href = routeUrl("results", projectId, run.id, true);
        open.target = "_blank";
        open.rel = "noopener";
        row.append(current, open, ` · ${executionText[run.execution_status]} · ${verificationText[run.verification_status]}`);
        runs.append(row);
      }
      past.append(label, runs);
      item.append(past);
    }
    list.append(item);
    const option = document.createElement("option");
    option.value = task.id;
    option.textContent = `${task.spec.goal} · v${task.revision}`;
    option.disabled = task.review_status !== "CURRENT";
    option.selected = selected.has(task.id);
    dependencies.append(option);
  }
  renderTaskPreparation();
}
document.querySelector("#refresh-tasks").addEventListener("click", () => refreshTasks().catch(displayError));
document.querySelector("#task-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (!requirementTarget?.payload.confirmed || requirementTarget.source_status === "STALE") {
    displayError(new Error("先选择一份已确认且来源未失效的已保存需求")); return;
  }
  const projectId = activeProject;
  try {
    await api(`/api/projects/${projectId}/tasks`, {method: "POST", body: JSON.stringify({
      goal: document.querySelector("#task-goal").value,
      requirement_refs: [{requirement_id: requirementTarget.id, revision: requirementTarget.revision, sha256: requirementTarget.sha256}],
      dependencies: Array.from(document.querySelector("#task-dependencies").selectedOptions).map(o => o.value),
      allowed_write_paths: lines("#task-paths"),
      mock_behavior: document.querySelector("#mock-behavior").value,
      timeout_seconds: Number(document.querySelector("#task-timeout").value)
      , max_steps: Number(document.querySelector("#task-steps").value), max_repairs: Number(document.querySelector("#task-repairs").value)
      , approval_required: document.querySelector("#approval-required").checked
    })});
    await refreshTasks(projectId);
  } catch (error) { displayError(error); }
});
const executionText = {QUEUED: "等待执行", AWAITING_APPROVAL: "等待审批", RUNNING: "执行中", RETURNED: "候选已返回", FAILED: "执行失败",
  CANCEL_REQUESTED: "正在取消", CANCELLED: "已取消", INTERRUPTED: "已中断", UNKNOWN_OUTCOME: "结果未知"};
const verificationText = {NOT_RUN: "未验收", RUNNING: "验收中", PASSED: "验收通过", FAILED: "验收失败", BLOCKED: "验收阻塞", NOT_APPLICABLE: "不适用"};
function eventLine(event) {
  const payload = event.payload;
  const descriptions = {queued: "任务已接纳", running: "执行开始", tool_request: "请求执行原创夹具", returned: "候选返回，仍需独立验收",
    failed: "执行失败", cancelled: "运行已取消", cancel_requested: "收到取消请求", waiting_dependency: "等待前置任务验收通过",
    dependency_failed: "前置任务失败", unknown_outcome: "结果未知，等待查证", verification: `独立验收：${payload.result || ""}`};
  const text = payload.type === "extension_result" ? `原创格式调用：${payload.snapshot_id.slice(0, 8)} · ${typeof payload.result === "string" ? payload.result : JSON.stringify(payload.result)}` : payload.type === "model_text" ? `Mock反馈：${payload.text}` : payload.type === "tool_result" ?
    `真实工具退出码：${payload.exit_code ?? "未启动"}` : descriptions[payload.type] || payload.type;
  return `${event.event_seq} · ${text}`;
}
async function viewRun(projectId, runId, restore = false, commitView = true) {
  document.querySelector("#runner-facts").textContent = "";
  clearTimeout(runTimer);
  activeRun = {projectId, runId};
  restoreNativeReview(projectId, runId);
  repairSession = null;
  document.querySelector("#verification-result").textContent = "";
  document.querySelector("#repair-status").textContent = "";
  document.querySelector("#run-artifacts").replaceChildren();
  document.querySelector("#results-empty").hidden = true;
  document.querySelector("#run-logs").textContent = "尚未读取日志";
  document.querySelector("#evidence-download").hidden = true;
  let cached;
  try { cached = JSON.parse(sessionStorage.getItem(`eeas-events:${projectId}:${runId}`) || "[]"); }
  catch { cached = []; }
  displayedEvents = Array.isArray(cached) ? cached.filter(e => Number.isInteger(e.event_seq)) : [];
  eventCursor = displayedEvents.at(-1)?.event_seq || 0;
  document.querySelector("#run-events").textContent = displayedEvents.map(eventLine).join("\n");
  document.querySelector("#run-panel").hidden = false;
  document.querySelector("#run-identity").textContent = `运行 ${runId} · 项目 ${projectId}`;
  document.querySelector("#open-run-window").href = routeUrl("results", projectId, runId, true);
  document.title = `${runId.slice(0, 8)} · EEAS 运行窗口`;
  if (commitView) selectView("results", true, !restore);
  else updateRoute();
  await pollRun(projectId, runId);
}
async function pollRun(projectId, runId) {
  if (activeProject !== projectId || activeRun?.runId !== runId) return;
  try {
    const [run, updates] = await Promise.all([api(`/api/projects/${projectId}/runs/${runId}?include_inputs=false`),
      api(`/api/projects/${projectId}/runs/${runId}/events?after=${eventCursor}`)]);
    if (activeProject !== projectId || activeRun?.runId !== runId) return;
    activeRun.taskId = run.task_id;
    document.querySelector("#start-native-review").disabled = !!nativeReview;
    for (const event of updates.events) {
      if (event.event_seq > eventCursor) { displayedEvents.push(event); eventCursor = event.event_seq; }
    }
    displayedEvents = displayedEvents.slice(-1000);
    sessionStorage.setItem(`eeas-events:${projectId}:${runId}`, JSON.stringify(displayedEvents));
    document.querySelector("#run-events").textContent = displayedEvents.map(eventLine).join("\n");
    const status = document.querySelector("#run-status");
    const state = `${run.execution_status}:${run.verification_status}`;
    if (status.dataset.state && status.dataset.state !== state) animateState(status);
    status.dataset.state = state;
    status.textContent = `Mock · ${executionText[run.execution_status]} · ${verificationText[run.verification_status]} · 第 ${run.attempt_id} 次运行`;
    const taskKey = `${run.task_id}:${run.task_revision}`;
    if (!taskNames.has(taskKey)) {
      const frozenTask = await api(`/api/projects/${projectId}/tasks/${run.task_id}?revision=${run.task_revision}`);
      if (activeProject !== projectId || activeRun?.runId !== runId) return;
      taskNames.set(taskKey, frozenTask.spec.goal);
    }
    const goal = taskNames.get(taskKey);
    document.querySelector("#run-title").textContent = `${goal} · 任务 v${run.task_revision}`;
    document.title = `${goal} · 第 ${run.attempt_id} 次 · EEAS`;
    document.querySelector("#cancel-run").disabled = !["QUEUED", "AWAITING_APPROVAL", "RUNNING", "CANCEL_REQUESTED"].includes(run.execution_status);
    document.querySelector("#verify-run").disabled = !["RETURNED", "FAILED"].includes(run.execution_status) || run.verification_status === "RUNNING";
    document.querySelector("#repair-run").disabled = run.verification_status !== "FAILED" || !!repairSession;
    const summaryKey = run.execution_status + ":" + run.verification_status;
    if (activeRun.summaryKey !== summaryKey || performance.now() - (activeRun.summaryAt || 0) > 2000) {
      const summary = await api(`/api/projects/${projectId}/runs/${runId}/results`);
      if (activeProject !== projectId || activeRun?.runId !== runId) return;
      renderRunResults(projectId, runId, summary);
      activeRun.summaryKey = summaryKey;
      activeRun.summaryAt = performance.now();
    }
    if (performance.now() - approvalRefreshAt > 2000) { await refreshApprovals(projectId); approvalRefreshAt = performance.now(); }
    if (repairSession) {
      const session = await api(`/api/projects/${projectId}/repair-sessions/${repairSession}`);
      if (activeProject !== projectId || activeRun?.runId !== runId) return;
      document.querySelector("#repair-status").textContent = `修复：${session.status} · 已启动 ${session.repairs_started}/${session.max_repairs} 次`;
      if (session.status !== "RUNNING") {
        const link = document.createElement("button");
        link.textContent = "查看最后修复运行";
        link.addEventListener("click", () => viewRun(projectId, session.current_run_id).catch(displayError));
        document.querySelector("#repair-status").append(" ", link);
      }
    }
  } catch (error) {
    if (activeProject === projectId && activeRun?.runId === runId) displayError(error);
  }
  if (activeProject === projectId && activeRun?.runId === runId) runTimer = setTimeout(() => pollRun(projectId, runId), 500);
}
document.querySelector("#cancel-run").addEventListener("click", async () => {
  if (!activeRun) return;
  const {projectId, runId} = activeRun;
  try {
    await api(`/api/projects/${projectId}/runs/${runId}/cancel`, {method: "POST"});
    clearTimeout(runTimer);
    await pollRun(projectId, runId);
  } catch (error) { displayError(error); }
});
document.querySelector("#inspect-runner").addEventListener("click", async () => {
  if (!activeRun) return;
  const {projectId, runId} = activeRun;
  try {
    const fact = await api(`/api/projects/${projectId}/runs/${runId}/runner`);
    if (activeProject === projectId && activeRun?.runId === runId) {
      document.querySelector("#runner-facts").textContent = `查证：${fact.state} · job ${fact.job_id || "无"} · 监督进程 ${fact.supervisor_alive ? "存活" : "未证实存活"} · 工具进程 ${fact.child_alive ? "存活" : "未证实存活"} · 退出码 ${fact.exit_code ?? "未知"}${fact.reason ? " · " + fact.reason : ""}`;
    }
  } catch (error) { displayError(error); }
});

async function refreshApprovals(projectId = activeProject) {
  const data = await api(`/api/projects/${projectId}/approvals`);
  if (activeProject !== projectId) return;
  const displayKey = projectId + JSON.stringify(data.approvals);
  if (approvalDisplayKey === displayKey) return;
  approvalDisplayKey = displayKey;
  const list = document.querySelector("#approvals");
  list.replaceChildren();
  for (const row of data.approvals.filter(a => a.status === "PENDING")) {
    const item = document.createElement("li");
    item.textContent = `${row.action.goal} · 任务 v${row.action.task_revision} · 写入 ${row.action.write_paths.join(", ")} · 有效至 ${row.expires_at} `;
    for (const [label, approve] of [["批准本次动作", true], ["拒绝本次动作", false]]) {
      const button = document.createElement("button");
      button.textContent = label;
      button.addEventListener("click", async () => {
        try {
          await api(`/api/projects/${projectId}/approvals/${row.id}/decision`, {method: "POST", body: JSON.stringify({
            action_hash: row.action_hash, approve})});
          await refreshApprovals(projectId);
        } catch (error) { displayError(error); }
      });
      item.append(button, " ");
    }
    list.append(item);
  }
  if (!list.children.length) list.textContent = "暂无待审批动作";
}

function renderRunResults(projectId, runId, summary) {
  const evidence = summary.evidence;
  activeRun.maxRepairs = summary.budget.max_repairs;
  document.querySelector("#repair-run").textContent = `请求修复（最多${summary.budget.max_repairs}次）`;
  if (!summary.budget.max_repairs) document.querySelector("#repair-run").disabled = true;
  document.querySelector("#run-budget").textContent = `预算：${summary.budget.timeout_seconds} 秒 · ${summary.budget.max_steps} 步 · 最多 ${summary.budget.max_repairs} 次修复 · 模型用量/费用：未知`;
  document.querySelector("#run-limitations").textContent = `来源 synthetic · 运行 mock · 仅原创固定夹具；Windows 原生、OS 隔离、LIVE 模型未验证`;
  document.querySelector("#verification-result").textContent = evidence ? `原创夹具验收：${evidence.payload.result} · 证据：${evidence.association_status}` : "尚无独立验收证据";
  const list = document.querySelector("#run-artifacts");
  list.replaceChildren();
  for (const [name, hash] of Object.entries(summary.artifact_hash)) {
    const item = document.createElement("li");
    if (hash) {
      const link = document.createElement("a");
      link.textContent = name;
      link.href = `/api/projects/${projectId}/runs/${runId}/artifacts/${name.split("/").map(encodeURIComponent).join("/")}`;
      item.append(link, ` · SHA256 ${hash}`);
    } else { item.textContent = `${name} · 文件尚未生成`; }
    list.append(item);
  }
  const download = document.querySelector("#evidence-download");
  download.hidden = !evidence;
  if (evidence) download.href = `/api/projects/${projectId}/evidence/${evidence.payload.evidence_id}/download`;
}
document.querySelector("#read-run-logs").addEventListener("click", async () => {
  if (!activeRun) return;
  const {projectId, runId} = activeRun;
  document.querySelector("#run-logs").textContent = "正在读取日志";
  try {
    const logs = await Promise.all(["stdout", "stderr"].map(stream => api(`/api/projects/${projectId}/runs/${runId}/logs/${stream}`)));
    if (activeProject !== projectId || activeRun?.runId !== runId) return;
    document.querySelector("#run-logs").textContent = logs.map(log => `${log.stream} · ${log.bytes} 字节 · SHA256 ${log.sha256}${log.output_limited ? " · 输出已按限额截断" : ""}\n${log.text || "（空）"}`).join("\n");
  } catch (error) { document.querySelector("#run-logs").textContent = "日志暂不可用，可重试"; displayError(error); }
});
document.querySelector("#verify-run").addEventListener("click", async () => {
  if (!activeRun) return;
  const {projectId, runId} = activeRun;
  try {
    const evidence = await api(`/api/projects/${projectId}/runs/${runId}/verify`, {method: "POST"});
    if (activeProject !== projectId || activeRun?.runId !== runId) return;
    const summary = await api(`/api/projects/${projectId}/runs/${runId}/results`);
    if (activeProject !== projectId || activeRun?.runId !== runId) return;
    renderRunResults(projectId, runId, summary);
    clearTimeout(runTimer);
    await pollRun(projectId, runId);
  } catch (error) { displayError(error); }
});
document.querySelector("#repair-run").addEventListener("click", async () => {
  if (!activeRun) return;
  const {projectId, runId} = activeRun;
  try {
    const session = await api(`/api/projects/${projectId}/runs/${runId}/repair`, {method: "POST", body: JSON.stringify({max_repairs: activeRun.maxRepairs ?? 3})});
    if (activeProject !== projectId || activeRun?.runId !== runId) return;
    repairSession = session.id;
    clearTimeout(runTimer);
    await pollRun(projectId, runId);
  } catch (error) { displayError(error); }
});

async function refreshExtensions() {
  const version = ++extensionRefreshVersion;
  const projectId = activeProject;
  document.querySelector("#extension-state").textContent = "正在读取实际扩展和项目组合";
  const [registered, pinned] = await Promise.all([api("/api/extensions"), projectId ? api(`/api/projects/${projectId}/extension-binding`) : Promise.resolve({binding: null})]);
  if (version !== extensionRefreshVersion || activeProject !== projectId) return;
  extensionRows = registered.extensions;
  renderExtensions();
  const binding = pinned.binding;
  document.querySelector("#extension-binding").textContent = !projectId ? "选择项目以查看固定组合" : binding ?
    `当前项目固定 ${binding.lock.provider} v${binding.lock.version}；稳定切换不会自动改动此组合。` : "项目尚未固定插件；新任务使用基础组合。";
  document.querySelector("#extension-binding-details").textContent = binding ? `快照 ${binding.lock.snapshot_id}\n组合锁 SHA256 ${binding.lock_hash}\n文件 SHA256\n${JSON.stringify(binding.lock.files_sha256, null, 2)}\nSDK LIVE 插件：${binding.lock.sdk_plugin_live}` : "";
  document.querySelector("#extension-state").textContent = extensionRows.length ? `已登记 ${extensionRows.length} 个不可变快照` : "尚无本地扩展，选择原创 ZIP 以登记候选。";
  syncExtensionFilePicker();
  updateExtensionApproval();
}
function renderExtensions() {
  const list = document.querySelector("#extensions");
  list.replaceChildren();
  for (const row of extensionRows) {
    const item = document.createElement("li");
    item.dataset.snapshotId = row.id;
    const title = document.createElement("h4");
    title.textContent = `${row.provider} v${row.version}`;
    const status = document.createElement("p");
    status.className = "extension-facts";
    status.textContent = `${row.channel === "STABLE" ? "稳定" : "候选"} · 支持：${row.supported ? "是" : "否"} · 已加载：${row.loaded ? "是" : "否"} · 资格通过：${row.qualified ? "是" : "否"} · 完整性：${row.integrity}`;
    const limitation = document.createElement("p");
    limitation.className = "extension-limit";
    limitation.textContent = !row.supported ? "原生参数已保留；此适配器未获执行资格，保持登记状态。" : `仅原创升级控制演练；SDK LIVE 插件未验证。${row.evidence_id ? " 实际资格证据 " + row.evidence_id : " 尚无实测加载证据。"}`;
    const detail = document.createElement("details");
    const label = document.createElement("summary");
    label.textContent = "清单、原生参数和文件哈希";
    const manifest = document.createElement("pre");
    manifest.textContent = `快照 ${row.id}\n${JSON.stringify({manifest: row.manifest, files_sha256: row.files_sha256}, null, 2)}`;
    detail.append(label, manifest);
    const qualify = document.createElement("button");
    qualify.type = "button";
    qualify.textContent = "执行原创资格检查";
    qualify.disabled = !row.supported || row.integrity !== "CURRENT" || row.qualified;
    qualify.addEventListener("click", async () => {
      qualify.disabled = true;
      document.querySelector("#extension-state").textContent = "正在执行实际模块初始化和格式调用";
      try { await api(`/api/extensions/${row.id}/qualify`, {method: "POST"}); await refreshCapabilities(); }
      catch (error) { displayError(error); document.querySelector("#extension-state").textContent = "资格检查未通过，可刷新查看实际状态"; qualify.disabled = false; }
    });
    const review = document.createElement("button");
    review.type = "button";
    review.textContent = "审阅切换或回滚";
    review.disabled = !row.qualified || row.channel === "STABLE";
    review.addEventListener("click", async () => {
      review.disabled = true;
      try {
        extensionReview = await api(`/api/extensions/${row.id}/review`, {method: "POST"});
        renderExtensionReview(row);
      } catch (error) { displayError(error); }
      finally { review.disabled = !row.qualified || row.channel === "STABLE"; }
    });
    const pin = document.createElement("button");
    pin.type = "button";
    pin.textContent = "为当前项目固定此组合";
    pin.disabled = !activeProject || !row.qualified || row.channel !== "STABLE";
    pin.addEventListener("click", async () => {
      const projectId = activeProject;
      pin.disabled = true;
      try {
        await api(`/api/projects/${projectId}/extension-binding`, {method: "POST", body: JSON.stringify({snapshot_id: row.id})});
        if (activeProject === projectId) await refreshExtensions();
      } catch (error) { displayError(error); pin.disabled = false; }
    });
    const actions = document.createElement("div");
    actions.className = "extension-actions";
    actions.append(qualify, review, pin);
    item.append(title, status, limitation, actions, detail);
    list.append(item);
  }
}
function renderExtensionReview(target) {
  const action = extensionReview.action;
  const old = extensionRows.find(r => r.id === action.expected_stable);
  document.querySelector("#extension-review").hidden = false;
  document.querySelector("#extension-review-title").textContent = "审阅稳定指针切换（含回滚）";
  document.querySelector("#extension-review-summary").textContent = `${action.provider}：${old ? "v" + old.version : "尚无稳定版本"} → v${target.version}。当前项目固定组合不随本次切换改变。`;
  const removed = (old?.manifest.permissions || []).filter(permission => !action.permissions.includes(permission));
  document.querySelector("#extension-review-diff").textContent = `新增权限：${action.new_permissions.join(", ") || "无"}\n移除权限：${removed.join(", ") || "无"}\n能力：${(old?.manifest.capabilities || []).join(", ") || "无"} → ${target.manifest.capabilities.join(", ")}\n原生参数（旧）：${JSON.stringify(old?.manifest.native_parameters || {})}\n原生参数（新）：${JSON.stringify(target.manifest.native_parameters)}`;
  document.querySelector("#extension-review-action").textContent = `审阅 ${extensionReview.id}\n动作 SHA256 ${extensionReview.action_hash}\n${JSON.stringify(action, null, 2)}`;
  document.querySelector("#extension-review-expiry").textContent = `本次动作有效至 ${extensionReview.expires_at}。关闭审阅保持当前稳定版本。`;
  const permissions = document.querySelector("#extension-review-permissions");
  permissions.replaceChildren();
  for (const permission of action.permissions) {
    const label = document.createElement("label");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = permission;
    checkbox.addEventListener("change", updateExtensionApproval);
    label.append(checkbox, `我批准 ${permission}`);
    permissions.append(label);
  }
  updateExtensionApproval();
}
function updateExtensionApproval() {
  const submit = document.querySelector("#approve-extension");
  if (!extensionReview) { submit.disabled = true; return; }
  const current = extensionRows.find(r => r.provider === extensionReview.action.provider && r.channel === "STABLE");
  const candidate = extensionRows.find(r => r.id === extensionReview.action.snapshot_id);
  const unchanged = (current?.id || null) === extensionReview.action.expected_stable;
  const permissions = Array.from(document.querySelectorAll("#extension-review-permissions input"));
  submit.disabled = !candidate?.qualified || !unchanged || Date.now() >= Date.parse(extensionReview.expires_at) || !permissions.every(input => input.checked);
}
function syncExtensionFilePicker() {
  const input = document.querySelector("#extension-file");
  const file = input.files[0];
  let problem = "";
  if (file && !/\.zip$/i.test(file.name)) problem = "请选择 ZIP 插件包；其他文件不会导入。";
  else if (file && file.size > 1048576) problem = `文件为 ${(file.size / 1024 / 1024).toFixed(2)} MiB，超过 1 MiB 上限。`;
  input.setCustomValidity(problem);
  if (problem) input.setAttribute("aria-invalid", "true");
  else input.removeAttribute("aria-invalid");
  document.querySelector("#extension-file-name").textContent = file ? file.name : "尚未选择文件";
  document.querySelector("#extension-file-error").textContent = problem;
  const form = document.querySelector("#extension-import-form");
  const busy = form.dataset.importing === "true";
  input.disabled = busy;
  document.querySelector("#choose-extension-file").disabled = busy;
  form.querySelector("[type=submit]").disabled = busy || !csrf || !file || !!problem;
}
document.querySelector("#choose-extension-file").addEventListener("click", () => document.querySelector("#extension-file").click());
document.querySelector("#extension-file").addEventListener("change", syncExtensionFilePicker);
document.querySelector("#extension-import-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (event.target.dataset.importing === "true") return;
  const file = document.querySelector("#extension-file").files[0];
  if (!file || !/\.zip$/i.test(file.name) || file.size > 1048576 || !csrf) { displayError(new Error("选择最大 1 MiB 的本地插件 ZIP")); return; }
  const submit = event.target.querySelector("[type=submit]");
  event.target.dataset.importing = "true";
  event.target.setAttribute("aria-busy", "true");
  submit.disabled = true;
  syncExtensionFilePicker();
  errorBox.textContent = "";
  const body = new FormData();
  body.append("file", file);
  try {
    await api("/api/extensions", {method: "POST", body});
    event.target.reset();
    syncExtensionFilePicker();
    document.querySelector("#extension-state").textContent = "候选已登记，正在刷新列表。";
    try { await refreshCapabilities(); }
    catch (error) {
      displayError(error);
      document.querySelector("#extension-state").textContent = "候选已登记；列表刷新失败，请点击刷新能力查看。";
    }
  } catch (error) {
    displayError(error);
    document.querySelector("#extension-state").textContent = error.status
      ? "服务未接受此文件；修正后可以重新导入。"
      : "未收到导入确认；请刷新列表核对候选，再决定是否重试。";
  } finally {
    delete event.target.dataset.importing;
    event.target.removeAttribute("aria-busy");
    syncExtensionFilePicker();
  }
});
document.querySelector("#close-extension-review").addEventListener("click", () => {
  extensionReview = null;
  document.querySelector("#extension-review").hidden = true;
});
document.querySelector("#extension-approval-form").addEventListener("submit", async event => {
  event.preventDefault();
  updateExtensionApproval();
  const submit = document.querySelector("#approve-extension");
  if (!extensionReview || submit.disabled) return;
  const review = extensionReview;
  submit.disabled = true;
  try {
    await api(`/api/extension-reviews/${review.id}/approve`, {method: "POST", body: JSON.stringify({action_hash: review.action_hash, permissions: review.action.permissions})});
    extensionReview = null;
    document.querySelector("#extension-review").hidden = true;
    await refreshCapabilities();
    document.querySelector("#extension-state").textContent = "审批已提交，已重新读取实际稳定版本；项目固定组合保持原值。";
  } catch (error) { displayError(error); updateExtensionApproval(); }
});

function nativeReviewBlocked(error) {
  const reasons = error.reasons?.join(", ") || error.message;
  if (nativeReview) nativeReview.operationError = reasons;
  document.querySelector("#native-review-status").textContent = `原生审阅 BLOCKED：${reasons}`;
}
function restoreNativeReview(projectId, runId) {
  nativeContextDraft = null;
  document.querySelector("#native-include-images").checked = false;
  document.querySelector("#native-search-enabled").checked = false;
  document.querySelector("#native-context-panel").hidden = true;
  document.querySelector("#load-native-context").disabled = false;
  clearTimeout(nativeReviewTimer);
  nativeReview = null;
  nativeReviewCursor = 0;
  nativeReviewEvents = [];
  document.querySelector("#native-review-events").textContent = "";
  document.querySelector("#native-review-identity").textContent = "";
  document.querySelector("#native-review-status").textContent = "尚无原生审阅";
  document.querySelector("#native-review-controls").hidden = true;
  document.querySelector("#start-native-review").disabled = true;
  let saved;
  try { saved = sessionStorage.getItem(`eeas-native-review:${projectId}:${runId}`); } catch { saved = null; }
  const restored = new URLSearchParams(location.search);
  if (restored.get("project") === projectId && restored.get("run") === runId && restored.get("review")) saved = restored.get("review");
  if (saved) {
    nativeReview = {projectId, runId, id: saved};
    pollNativeReview(projectId, runId, saved);
  }
  updateNativeInputConsent();
}
async function pollNativeReview(projectId, runId, reference) {
  clearTimeout(nativeReviewTimer);
  if (activeProject !== projectId || activeRun?.runId !== runId || nativeReview?.id !== reference) return;
  try {
    const [review, updates] = await Promise.all([api(`/api/projects/${projectId}/native-reviews/${reference}`),
      api(`/api/projects/${projectId}/native-reviews/${reference}/events?after=${nativeReviewCursor}`)]);
    if (activeProject !== projectId || activeRun?.runId !== runId || nativeReview?.id !== reference) return;
    if (review.task_binding.run_id !== runId) throw new Error("原生审阅引用不属于所选运行");
    nativeReview.record = review;
    updateNativeInputConsent();
    document.querySelector("#load-native-context").disabled = true;
    for (const control of document.querySelectorAll("#native-context-panel input,#native-context-panel button,#native-context-panel select")) control.disabled = true;
    const execution = review.native_execution;
    document.querySelector("#native-review-status").textContent = nativeReview.pendingControl ? "正在核对后续只读请求 · 等待服务器批准" : `原生只读审阅：${execution?.execution_status || review.admission_status} · 未独立验收${review.error_type ? " · " + review.error_type : ""}${nativeReview.operationError ? " · 操作 BLOCKED：" + nativeReview.operationError : ""}`;
    document.querySelector("#native-review-identity").textContent = `审阅引用 ${reference}${execution ? " · 原生执行 " + execution.id : " · 底座执行尚未记录"}${execution?.session_id ? " · 会话 " + execution.session_id : " · 尚无底座会话"}`;
    document.querySelector("#native-review-controls").hidden = false;
    document.querySelector("#native-review-download").href = `/api/projects/${projectId}/native-reviews/${reference}/result`;
    const continuable = !nativeReview.pendingControl && !!execution?.session_id && ["RETURNED", "FAILED"].includes(execution.execution_status);
    document.querySelector("#resume-native-review").disabled = !continuable;
    document.querySelector("#fork-native-review").disabled = !continuable;
    document.querySelector("#steer-native-review").disabled = !!nativeReview.pendingControl || !execution?.session_id || execution.execution_status !== "RUNNING";
    document.querySelector("#native-followup-prompt").disabled = !!nativeReview.pendingControl;
    document.querySelector("#cancel-native-review").disabled = !!nativeReview.pendingControl || (!["ADMITTED", "DISPATCHING"].includes(review.admission_status) && !["QUEUED", "RUNNING", "CANCEL_REQUESTED"].includes(execution?.execution_status));
    for (const event of updates.events) if (event.event_seq > nativeReviewCursor) { nativeReviewEvents.push(event); nativeReviewCursor = event.event_seq; }
    nativeReviewEvents = nativeReviewEvents.slice(-1000);
    document.querySelector("#native-review-events").textContent = nativeReviewEvents.map(event => `${event.event_seq} · ${event.payload.type}\n${JSON.stringify(event.payload.native || event.payload)}`).join("\n");
  } catch (error) { if (nativeReview?.id === reference) nativeReviewBlocked(error); }
  if (activeProject === projectId && activeRun?.runId === runId && nativeReview?.id === reference) nativeReviewTimer = setTimeout(() => pollNativeReview(projectId, runId, reference), 700);
}
function selectNativeReview(projectId, runId, review) {
  clearTimeout(nativeReviewTimer);
  nativeReview = {projectId, runId, id: review.admission_id};
  updateNativeInputConsent();
  nativeReviewCursor = 0;
  nativeReviewEvents = [];
  document.querySelector("#native-review-events").textContent = "";
  document.querySelector("#native-review-identity").textContent = `审阅引用 ${review.admission_id} · 等待读取底座执行`;
  document.querySelector("#native-review-status").textContent = `原生只读审阅：${review.admission_status} · 未独立验收`;
  document.querySelector("#native-review-download").href = `/api/projects/${projectId}/native-reviews/${review.admission_id}/result`;
  for (const button of document.querySelectorAll("#native-review-controls button")) button.disabled = true;
  document.querySelector("#native-followup-prompt").disabled = false;
  try { sessionStorage.setItem(`eeas-native-review:${projectId}:${runId}`, review.admission_id); } catch { /* URL remains the recoverable reference. */ }
  updateRoute();
  document.querySelector("#start-native-review").disabled = true;
  pollNativeReview(projectId, runId, review.admission_id);
}
function updateNativeInputConsent() {
  const images = document.querySelector("#native-include-images");
  const search = document.querySelector("#native-search-enabled");
  const imageCapability = runtimeCapabilities?.image_input;
  const searchCapability = runtimeCapabilities?.search_mcp;
  const frozen = nativeReview?.record?.native_execution;
  if (frozen) {
    const request = JSON.parse(frozen.request);
    images.checked = !!request.image_refs?.length;
    search.checked = request.search_enabled === true;
    document.querySelector("#native-input-consent-status").textContent = `已冻结：${request.image_refs?.length || 0} 份原始图像；联网检索${search.checked ? "已明确允许" : "未允许"}。原生返回不等于独立验收。`;
  } else {
    if (!nativeReview && !imageCapability?.enabled_for_attempt) { images.checked = false; nativeContextDraft?.imageSelections?.clear(); }
    if (!nativeReview && searchCapability?.status !== "CONFIGURED_FOR_ATTEMPT") search.checked = false;
    document.querySelector("#native-input-consent-status").textContent = `视觉输入：${imageCapability?.enabled_for_attempt ? "已允许本次尝试；模型兼容性尚未验证" : "当前未批准 · " + (imageCapability?.blockers?.join(", ") || "正在读取配置")}。联网检索：${searchCapability?.status === "CONFIGURED_FOR_ATTEMPT" ? "已配置搜索专用 MCP；网页原文未读取，接口兼容性尚未验证" : "未配置或被拒绝 · " + (searchCapability?.reason || searchCapability?.status || "正在读取配置") }。`;
  }
  const totalImages = nativeContextDraft?.scope.document_contexts.reduce((total, doc) => total + doc.assets.length, 0) || 0;
  images.disabled = !!nativeReview || !imageCapability?.enabled_for_attempt || !totalImages;
  search.disabled = !!nativeReview || searchCapability?.status !== "CONFIGURED_FOR_ATTEMPT";
  document.querySelector("#native-image-badge").textContent = images.checked ? (nativeReview ? "已冻结 · 包含图像" : `已选择 ${nativeContextDraft?.imageSelections.size || 0} 份`) : "默认关闭";
  document.querySelector("#native-search-badge").textContent = search.checked ? (nativeReview ? "已冻结 · 允许检索" : "已明确勾选") : "默认关闭";
}
function animateConsentBadge(id) {
  const badge = document.querySelector(id);
  badge.getAnimations().forEach(animation => animation.cancel());
  if (!reduceMotion()) badge.animate([{opacity: .35, transform: "translateY(5px)"}, {opacity: 1, transform: "translateY(0)"}], {duration: 420, easing: "cubic-bezier(.2,.75,.25,1)"});
}
document.querySelector("#native-search-enabled").addEventListener("change", () => { updateNativeInputConsent(); animateConsentBadge("#native-search-badge"); });
function imageSelectionKey(doc, asset) { return `${doc.document_id}:${doc.revision}:${asset.asset_id}`; }
function renderNativeImages() {
  const draft = nativeContextDraft;
  const doc = contextDocument();
  if (!draft || !doc) return;
  const list = document.querySelector("#native-image-list");
  list.replaceChildren();
  const allowed = document.querySelector("#native-include-images").checked;
  for (const asset of doc.assets.slice(draft.imageOffset, draft.imageOffset + 8)) {
    const item = document.createElement("li");
    const label = document.createElement("label");
    const check = document.createElement("input");
    check.type = "checkbox";
    check.dataset.assetId = asset.asset_id;
    check.checked = draft.imageSelections.has(imageSelectionKey(doc, asset));
    check.disabled = !allowed || !!nativeReview;
    check.addEventListener("change", () => {
      if (nativeContextDraft !== draft || nativeReview) return;
      const key = imageSelectionKey(doc, asset);
      if (check.checked) draft.imageSelections.set(key, {document_id: doc.document_id, revision: doc.revision, asset_id: asset.asset_id, sha256: asset.sha256});
      else draft.imageSelections.delete(key);
      renderNativeContext();
      animateConsentBadge("#native-image-badge");
    });
    const image = document.createElement("img");
    image.src = `/api/projects/${draft.projectId}/documents/${doc.document_id}/assets/${asset.asset_id}?revision=${doc.revision}`;
    image.alt = `原始图像 ${asset.member_path}`;
    image.loading = "lazy";
    label.append(check, image, document.createTextNode(`${asset.member_path} · ${asset.width} × ${asset.height} · v${doc.revision}`));
    item.append(label); list.append(item);
  }
  document.querySelector("#native-images-page").textContent = `${doc.assets.length ? draft.imageOffset + 1 : 0}–${Math.min(doc.assets.length, draft.imageOffset + 8)} / ${doc.assets.length} 份原始图像`;
  document.querySelector("#native-images-prev").disabled = draft.imageOffset === 0 || !!nativeReview;
  document.querySelector("#native-images-next").disabled = draft.imageOffset + 8 >= doc.assets.length || !!nativeReview;
  document.querySelector("#native-image-summary").textContent = `本任务共 ${draft.scope.document_contexts.reduce((total, entry) => total + entry.assets.length, 0)} 份图像。${allowed ? `明确选择 ${draft.imageSelections.size} 份；未选图像不会发送。` : "尚未允许图像输入，默认仅发送文本。"}`;
  updateNativeInputConsent();
}
function nativeInputConsent(scope) {
  const includeImages = document.querySelector("#native-include-images").checked;
  const searchEnabled = document.querySelector("#native-search-enabled").checked;
  const result = {include_images: includeImages, search_enabled: searchEnabled};
  if (includeImages) {
    const draft = nativeContextDraft;
    if (!draft || draft.projectId !== activeProject || draft.runId !== activeRun?.runId || scope.input_hash !== draft.scope.input_hash || scope.source_hash !== draft.scope.source_hash || JSON.stringify(scope.task_binding) !== JSON.stringify(draft.scope.task_binding)) throw new Error("任务或图像来源已变化，请重新核对冻结资料");
    if (!draft.imageSelections.size) throw new Error("允许图像输入后，请明确选择至少一份原始图像");
    if (draft.imageSelections.size > (runtimeCapabilities?.image_input?.max_images || 0)) throw new Error("所选图像超过本次明确配置的数量上限，请减少选择");
    result.image_selections = [...draft.imageSelections.values()];
  }
  return result;
}
document.querySelector("#native-include-images").addEventListener("change", event => {
  if (!event.target.checked) nativeContextDraft?.imageSelections.clear();
  renderNativeContext();
  animateConsentBadge("#native-image-badge");
});
for (const direction of ["prev", "next"]) document.querySelector(`#native-images-${direction}`).addEventListener("click", () => {
  if (!nativeContextDraft || nativeReview) return;
  nativeContextDraft.imageOffset = Math.max(0, nativeContextDraft.imageOffset + (direction === "next" ? 8 : -8));
  renderNativeImages();
});

function contextDocument() {
  return nativeContextDraft?.scope.document_contexts.find(doc => doc.document_id === document.querySelector("#native-context-document").value);
}
function renderNativeContext() {
  const draft = nativeContextDraft;
  const doc = contextDocument();
  if (!draft || !doc) return;
  draft.version = (draft.version || 0) + 1;
  const selected = draft.selections.get(doc.document_id);
  document.querySelector("#native-context-custom").checked = !!selected;
  const list = document.querySelector("#native-context-chunks");
  list.replaceChildren();
  for (const chunk of doc.chunks.slice(draft.offset, draft.offset + 20)) {
    const item = document.createElement("li");
    const label = document.createElement("label");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.dataset.chunkIndex = chunk.index;
    checkbox.checked = selected ? selected.has(chunk.index) : true;
    checkbox.disabled = !selected;
    checkbox.addEventListener("change", () => {
      if (!draft.selections.has(doc.document_id)) draft.selections.set(doc.document_id, new Set(doc.chunks.map(part => part.index)));
      const indexes = draft.selections.get(doc.document_id);
      if (checkbox.checked) indexes.add(chunk.index); else indexes.delete(chunk.index);
      renderNativeContext();
    });
    label.append(checkbox, document.createTextNode(`第 ${chunk.index + 1} 段 · 行 ${chunk.line_start}–${chunk.line_end} · ${chunk.char_end - chunk.char_start} 字符`));
    item.append(label);
    list.append(item);
  }
  document.querySelector("#native-context-page").textContent = `${doc.chunks.length ? draft.offset + 1 : 0}–${Math.min(doc.chunks.length, draft.offset + 20)} / ${doc.total_chunks} 段`;
  for (const direction of ["first", "prev"]) document.querySelector(`#native-context-${direction}`).disabled = draft.offset === 0;
  for (const direction of ["last", "next"]) document.querySelector(`#native-context-${direction}`).disabled = draft.offset + 20 >= doc.chunks.length;
  document.querySelector("#native-context-select-last").disabled = doc.chunks.length === 0;
  document.querySelector("#native-context-summary").textContent = `固定资料 v${doc.revision} · SHA256 ${doc.sha256}\n${selected ? `显式选择 ${selected.size} / ${doc.total_chunks} 段，省略 ${doc.total_chunks - selected.size} 段。` : `完整资料 ${doc.total_characters.toLocaleString()} 字符，${doc.total_chunks} 段。`}`;
  document.querySelector("#native-context-receipt").textContent = "尚未核对覆盖范围。选择变化后请重新核对。";
  delete document.querySelector("#native-context-receipt").dataset.error;
  renderNativeImages();
}
document.querySelector("#load-native-context").addEventListener("click", async () => {
  if (!activeRun?.taskId || nativeReview) return;
  const {projectId, runId, taskId} = activeRun;
  try {
    const scope = await api(`/api/projects/${projectId}/tasks/${taskId}/runs/${runId}/native-review-scope`);
    if (activeProject !== projectId || activeRun?.runId !== runId || nativeReview) return;
    nativeContextDraft = {projectId, runId, scope, selections: new Map(), imageSelections: new Map(), imageOffset: 0, offset: 0};
    document.querySelector("#native-include-images").checked = false;
    const select = document.querySelector("#native-context-document");
    select.replaceChildren();
    for (const doc of scope.document_contexts) select.append(new Option(`${documentNames.get(doc.document_id) || doc.document_id} · v${doc.revision}`, doc.document_id));
    document.querySelector("#native-context-panel").hidden = false;
    for (const control of document.querySelectorAll("#native-context-panel input,#native-context-panel button,#native-context-panel select")) control.disabled = false;
    if (!scope.document_contexts.length) {
      document.querySelector("#native-context-chunks").replaceChildren();
      document.querySelector("#native-context-summary").textContent = "本次冻结任务没有资料输入，将审阅任务与已确认需求。";
      for (const control of document.querySelectorAll("#native-context-panel input,#native-context-panel button,#native-context-panel select")) control.disabled = true;
      updateNativeInputConsent();
      return;
    }
    renderNativeContext();
  } catch (error) { if (activeProject === projectId && activeRun?.runId === runId) nativeReviewBlocked(error); }
});
document.querySelector("#native-context-document").addEventListener("change", () => { if (nativeContextDraft) { nativeContextDraft.offset = 0; nativeContextDraft.imageOffset = 0; renderNativeContext(); } });
document.querySelector("#native-context-custom").addEventListener("change", event => {
  const doc = contextDocument();
  if (!doc) return;
  if (event.target.checked) nativeContextDraft.selections.set(doc.document_id, new Set());
  else nativeContextDraft.selections.delete(doc.document_id);
  renderNativeContext();
});
for (const direction of ["first", "prev", "next", "last"]) document.querySelector(`#native-context-${direction}`).addEventListener("click", () => {
  const doc = contextDocument();
  if (!doc) return;
  const draft = nativeContextDraft;
  draft.offset = direction === "first" ? 0 : direction === "last" ? Math.max(0, Math.floor((doc.chunks.length - 1) / 20) * 20) : Math.max(0, draft.offset + (direction === "next" ? 20 : -20));
  renderNativeContext();
});
document.querySelector("#native-context-select-last").addEventListener("click", () => {
  const doc = contextDocument();
  if (!doc?.chunks.length) return;
  nativeContextDraft.selections.set(doc.document_id, new Set([doc.chunks.at(-1).index]));
  nativeContextDraft.offset = Math.floor((doc.chunks.length - 1) / 20) * 20;
  renderNativeContext();
});
function nativeChunkSelections(scope) {
  const draft = nativeContextDraft;
  if (!draft?.selections.size) return [];
  if (draft.projectId !== activeProject || draft.runId !== activeRun?.runId || scope.input_hash !== draft.scope.input_hash || scope.source_hash !== draft.scope.source_hash || JSON.stringify(scope.task_binding) !== JSON.stringify(draft.scope.task_binding)) throw new Error("冻结任务或来源已变化，请重新核对资料与选段");
  return [...draft.selections].map(([documentId, selected]) => {
    if (!selected.size) throw new Error("显式选段至少需要一段；也可以取消勾选，使用完整资料");
    if (selected.size > 50) throw new Error("每份资料最多显式选择 50 段，请减少所选段落");
    const doc = draft.scope.document_contexts.find(entry => entry.document_id === documentId);
    return {document_id: documentId, revision: doc.revision, chunk_indexes: [...selected].sort((a, b) => a - b)};
  });
}
document.querySelector("#preview-native-context").addEventListener("click", async () => {
  const draft = nativeContextDraft;
  const doc = contextDocument();
  if (!draft || !doc) return;
  const selected = draft.selections.get(doc.document_id);
  const version = draft.version;
  const receipt = document.querySelector("#native-context-receipt");
  try {
    if (selected && !selected.size) throw new Error("请至少选择一段，或使用完整资料");
    const data = await api(`/api/projects/${draft.projectId}/documents/${doc.document_id}/context`, {method: "POST", body: JSON.stringify({revision: doc.revision, ...(selected ? {chunk_indexes: [...selected].sort((a, b) => a - b)} : {})})});
    if (nativeContextDraft !== draft || contextDocument() !== doc || draft.version !== version) return;
    assertDocumentIdentity(data, {id: doc.document_id, revision: doc.revision, sha256: doc.sha256});
    receipt.textContent = `${data.coverage === "COMPLETE" ? "完整覆盖" : "部分上下文 · 显式选择"}：${data.included_chunks.length} / ${data.total_chunks} 段，省略 ${data.omitted_chunk_count} 段，${data.included_characters.toLocaleString()} 字符 / ${data.included_utf8_bytes.toLocaleString()} UTF-8 字节。\n上下文 SHA256 ${data.context_sha256}`;
    delete receipt.dataset.error;
  } catch (error) { if (nativeContextDraft === draft && contextDocument() === doc && draft.version === version) { receipt.textContent = `上下文拒绝：${error.message}`; receipt.dataset.error = "true"; } }
});
document.querySelector("#start-native-review").addEventListener("click", async () => {
  if (!activeRun?.taskId || nativeReview) return;
  const {projectId, runId, taskId} = activeRun;
  const reviewPrompt = document.querySelector("#native-review-prompt").value;
  document.querySelector("#start-native-review").disabled = true;
  document.querySelector("#native-review-status").textContent = "正在核对冻结输入与原生配置";
  try {
    const prefix = `/api/projects/${projectId}/tasks/${taskId}/runs/${runId}`;
    const scope = await api(prefix + "/native-review-scope");
    if (activeProject !== projectId || activeRun?.runId !== runId || nativeReview) return;
    const review = await api(prefix + "/native-reviews", {method: "POST", body: JSON.stringify({
      operation_id: crypto.randomUUID(), task_binding: scope.task_binding, input_hash: scope.input_hash,
      source_hash: scope.source_hash, needs_tools: false, native: {}, review_prompt: reviewPrompt, chunk_selections: nativeChunkSelections(scope), ...nativeInputConsent(scope)})});
    if (activeProject === projectId && activeRun?.runId === runId) selectNativeReview(projectId, runId, review);
  } catch (error) { if (activeProject === projectId && activeRun?.runId === runId) nativeReviewBlocked(error); }
  finally { if (activeProject === projectId && activeRun?.runId === runId && !nativeReview) document.querySelector("#start-native-review").disabled = false; }
});
for (const kind of ["resume", "fork", "steer"]) document.querySelector(`#${kind}-native-review`).addEventListener("click", async () => {
  const selected = nativeReview;
  const execution = selected?.record?.native_execution;
  const prompt = document.querySelector("#native-followup-prompt").value.trim();
  if (selected?.pendingControl) return;
  if (!selected || !execution?.session_id || !prompt) { nativeReviewBlocked(new Error("先填写后续只读指令并等待真实会话")); return; }
  selected.pendingControl = kind;
  delete selected.operationError;
  document.querySelector("#native-review-status").textContent = "正在核对后续只读请求 · 等待服务器批准";
  for (const button of document.querySelectorAll("#native-review-controls button")) button.disabled = true;
  document.querySelector("#native-followup-prompt").disabled = true;
  try {
    const review = await api(`/api/projects/${selected.projectId}/native-reviews/${selected.id}/${kind}`, {method: "POST", body: JSON.stringify({operation_id: crypto.randomUUID(), expected_session_id: execution.session_id, prompt})});
    if (nativeReview === selected) selectNativeReview(selected.projectId, selected.runId, review);
  } catch (error) { if (nativeReview === selected) nativeReviewBlocked(error); }
  finally { if (nativeReview === selected) { delete selected.pendingControl; pollNativeReview(selected.projectId, selected.runId, selected.id); } }
});
document.querySelector("#cancel-native-review").addEventListener("click", async () => {
  const selected = nativeReview;
  if (!selected) return;
  try { await api(`/api/projects/${selected.projectId}/native-reviews/${selected.id}/cancel`, {method: "POST"}); if (nativeReview?.id === selected.id) { delete nativeReview.operationError; pollNativeReview(selected.projectId, selected.runId, selected.id); } }
  catch (error) { if (nativeReview?.id === selected.id) nativeReviewBlocked(error); }
});

function closeSidebar() {
  const sidebar = document.querySelector("#desktop-sidebar");
  const returnFocus = document.body.classList.contains("sidebar-open") && sidebar.contains(document.activeElement);
  document.body.classList.remove("sidebar-open");
  sidebar.inert = window.matchMedia("(max-width:700px)").matches;
  document.querySelector("#menu-toggle").setAttribute("aria-expanded", "false");
  document.querySelector("#sidebar-backdrop").hidden = true;
  if (returnFocus) document.querySelector("#menu-toggle").focus();
}
function showToast(message) {
  clearTimeout(toastTimer);
  const toast = document.querySelector("#toast");
  toast.textContent = message;
  toast.hidden = false;
  toastTimer = setTimeout(() => { toast.hidden = true; }, 4000);
}
function dismissToast() {
  clearTimeout(toastTimer);
  document.querySelector("#toast").hidden = true;
}
function openUpload() {
  if (!activeProject) {
    selectView("projects", true, true);
    openProjectDrawer();
    showToast("先创建或选择一个项目，再导入资料");
    return;
  }
  document.querySelector("#upload-error").textContent = "";
  document.querySelector("#upload-project-label").textContent = `导入到 ${document.querySelector("#project-context").textContent}。原始文件与修订会一并保存。`;
  const drawer = document.querySelector("#upload-drawer");
  openDialog(drawer);
}
function uploadFailure(message) {
  const error = document.querySelector("#upload-error");
  error.textContent = message;
  document.querySelector("#document-file").setAttribute("aria-invalid", "true");
  error.focus();
}
function validateUpload(file) {
  const error = uploadBatch.errorFor(file);
  if (error) { uploadFailure(error); return false; }
  document.querySelector("#upload-error").textContent = "";
  document.querySelector("#document-file").removeAttribute("aria-invalid");
  return true;
}
document.querySelector("#document-file").setAttribute("aria-describedby", "upload-error upload-file-state");
for (const id of ["toolbar-upload", "home-upload", "library-upload"]) document.querySelector("#" + id).addEventListener("click", () => { prepareUpload(); openUpload(); });
document.querySelector("#close-upload").addEventListener("click", () => closeDialog(document.querySelector("#upload-drawer")));
document.querySelector("#upload-drawer").addEventListener("click", event => { if (event.target === event.currentTarget && event.clientX < event.currentTarget.getBoundingClientRect().left) closeDialog(event.currentTarget); });
document.querySelector("#document-file").addEventListener("change", () => {
  uploadSelectionVersion += 1;
  const files = Array.from(document.querySelector("#document-file").files);
  uploadQueue = uploadBatch.create(files);
  document.querySelector("#upload-file-state").textContent = files.length ? `${files.length} 份资料 · 合计 ${(files.reduce((total, file) => total + file.size, 0) / 1024).toFixed(1)} KiB` : "尚未选择文件";
  document.querySelector("#chosen-document-file-name").textContent = files.length > 1 ? `已选择 ${files.length} 份资料` : files[0]?.name || "尚未选择文件";
  renderUploadQueue();
  document.querySelector("#upload-error").textContent = "";
  document.querySelector("#document-file").removeAttribute("aria-invalid");
  if (documentTarget && files.length > 1) uploadFailure("修订只能选择一份资料；多份资料请改为新资料导入。");
  else if (files.length === 1) validateUpload(files[0]);
});
const dropzone = document.querySelector("#upload-dropzone");
for (const type of ["dragenter", "dragover"]) dropzone.addEventListener(type, event => { event.preventDefault(); dropzone.classList.add("drag-over"); });
for (const type of ["dragleave", "drop"]) dropzone.addEventListener(type, event => { event.preventDefault(); dropzone.classList.remove("drag-over"); });
dropzone.addEventListener("drop", event => {
  if (!event.dataTransfer.files.length) return;
  document.querySelector("#document-file").files = event.dataTransfer.files;
  document.querySelector("#document-file").dispatchEvent(new Event("change", {bubbles: true}));
});
document.querySelector("#menu-toggle").addEventListener("click", () => {
  const open = !document.body.classList.contains("sidebar-open");
  if (!open) { closeSidebar(); return; }
  document.querySelector("#desktop-sidebar").inert = false;
  document.body.classList.toggle("sidebar-open", open);
  document.querySelector("#menu-toggle").setAttribute("aria-expanded", String(open));
  document.querySelector("#sidebar-backdrop").hidden = !open;
  const sidebar = document.querySelector("#desktop-sidebar");
  (sidebar.querySelector("nav a[aria-current=page]") || sidebar.querySelector("nav a")).focus();
});
document.querySelector("#sidebar-backdrop").addEventListener("click", closeSidebar);
document.querySelector("#sidebar-projects").addEventListener("click", () => selectView("projects", true, true));
for (const button of document.querySelectorAll("[data-go-view]")) button.addEventListener("click", () => selectView(button.dataset.goView, true, true));
for (const link of document.querySelectorAll(".sidebar nav a")) link.title = link.textContent;
document.querySelector("#welcome-date").textContent = new Date().toLocaleDateString("zh-CN", {month: "long", day: "numeric", weekday: "long"});
const motionPreference = window.matchMedia("(prefers-reduced-motion: reduce)");
function syncMotionPreference() {
  document.querySelector("#motion-preference-note").textContent = motionPreference.matches
    ? "系统已开启减少动态效果，界面立即使用静态反馈。"
    : "完整动效已启用；操作系统的减少动态效果偏好会自动生效。";
}
syncMotionPreference();
motionPreference.addEventListener("change", () => { syncMotionPreference(); finishMotion(); });
document.addEventListener("keydown", event => {
  if (!window.matchMedia("(max-width:700px)").matches || !document.body.classList.contains("sidebar-open")) return;
  if (event.key === "Escape") { event.preventDefault(); closeSidebar(); return; }
  if (event.key !== "Tab") return;
  const sidebar = document.querySelector("#desktop-sidebar");
  const controls = [...sidebar.querySelectorAll("a[href],button:not([disabled])")].filter(control => control.getClientRects().length && getComputedStyle(control).visibility !== "hidden");
  if (!controls.length) return;
  const current = controls.indexOf(document.activeElement);
  const next = current < 0 ? (event.shiftKey ? controls.length - 1 : 0) : (current + (event.shiftKey ? -1 : 1) + controls.length) % controls.length;
  event.preventDefault();
  controls[next].focus();
});
const sidebarBreakpoint = window.matchMedia("(max-width:700px)");
sidebarBreakpoint.addEventListener("change", () => {
  // A native change can arrive after the user opened the now-mobile menu.
  // Preserve that current intent; desktop transitions still close the overlay.
  if (!sidebarBreakpoint.matches || !document.body.classList.contains("sidebar-open")) closeSidebar();
});
closeSidebar();

// Motion only responds to a real interaction or state; reduced motion finishes immediately.
function reduceMotion() { return window.matchMedia("(prefers-reduced-motion: reduce)").matches; }
function cancelViewMotion(panel) {
  if (!panel) return;
  for (const animation of panel.getAnimations({subtree: true})) {
    if (animation.id.startsWith("eeas-view-")) animation.cancel();
  }
}
function animateView(panel) {
  cancelViewMotion(panel);
  if (reduceMotion()) return;
  // Keep text fully painted throughout the entrance. Only spatial cues move.
  const entrance = panel.animate([{transform:"translateY(12px)"},{transform:"translateY(0)"}],{duration:280,easing:"cubic-bezier(.16,1,.3,1)"});
  entrance.id = "eeas-view-surface";
  const groups = [...panel.children].filter(element => !element.hidden && element.getBoundingClientRect().height > 0).slice(0, 5);
  groups.forEach((element, index) => {
    const animation = element.animate([{transform:"translateY(6px)"},{transform:"translateY(0)"}],{duration:260,delay:index*24,easing:"cubic-bezier(.16,1,.3,1)"});
    animation.id = "eeas-view-group";
  });
  panel.dataset.motionCount = Number(panel.dataset.motionCount || 0) + 1;
}
function moveNavigation(view, animate) {
  const indicator = document.querySelector("#nav-active-indicator");
  const target = document.querySelector(`.sidebar nav [data-view="${view}"]`);
  if (!target || !indicator) return;
  // Capture the painted position before cancellation removes the in-flight
  // effect. The stored target belongs to the last route, not the current frame.
  const visible = getComputedStyle(indicator).transform;
  const matrix = visible === "none" ? null : new DOMMatrixReadOnly(visible);
  const oldX = matrix ? matrix.m41 : target.offsetLeft;
  const old = matrix ? matrix.m42 : target.offsetTop;
  const next = target.offsetTop, nextX = target.offsetLeft;
  indicator.getAnimations().forEach(animation => animation.cancel());
  indicator.style.width = `${target.offsetWidth}px`;
  indicator.style.height = `${target.offsetHeight}px`;
  indicator.style.transform = `translate(${nextX}px,${next}px)`;
  indicator.dataset.position = next;
  if (animate && !reduceMotion() && (old !== next || oldX !== nextX)) {
    indicator.animate([{transform:`translate(${oldX}px,${old}px)`},{transform:`translate(${nextX}px,${next}px)`}],{duration:260,easing:"cubic-bezier(.22,1,.36,1)"});
    indicator.dataset.motionCount = Number(indicator.dataset.motionCount || 0) + 1;
  }
}
function animateState(element) {
  if (reduceMotion()) return;
  element.getAnimations().forEach(animation => animation.cancel());
  element.animate([{transform:"translateY(4px)"},{transform:"translateY(0)"}],{duration:220,easing:"cubic-bezier(.16,1,.3,1)"});
  element.dataset.motionCount = Number(element.dataset.motionCount || 0) + 1;
}
function openDialog(dialog) {
  dialog.__transitionVersion = (dialog.__transitionVersion || 0) + 1;
  dialog.__instanceVersion = (dialog.__instanceVersion || 0) + 1;
  dialog.getAnimations().forEach(animation => animation.cancel());
  delete dialog.dataset.closing;
  if (!dialog.open) dialog.showModal();
  if (!reduceMotion()) {
    const frames = dialog.id === "upload-drawer"
      ? [{transform:"translateX(44px)"},{transform:"translateX(-2px)",offset:.7},{transform:"translateX(0)"}]
      : [{transform:"translateY(18px) scale(.98)"},{transform:"translateY(-2px) scale(1.002)",offset:.7},{transform:"translateY(0) scale(1)"}];
    dialog.animate(frames,{duration:340,easing:"cubic-bezier(.16,1,.3,1)"});
    dialog.dataset.motionCount = Number(dialog.dataset.motionCount || 0) + 1;
  }
}
async function closeDialog(dialog) {
  if (!dialog.open || dialog.dataset.closing) return;
  const version = (dialog.__transitionVersion || 0) + 1;
  dialog.__transitionVersion = version;
  dialog.dataset.closing = "true";
  dialog.getAnimations().forEach(animation => animation.cancel());
  if (!reduceMotion()) await dialog.animate([{transform:"translate(0) scale(1)"},{transform:dialog.id === "upload-drawer" ? "translateX(30px)" : "translateY(10px) scale(.98)"}],{duration:180,easing:"cubic-bezier(.4,0,1,1)"}).finished.catch(() => {});
  if (dialog.__transitionVersion !== version) return;
  dialog.close();
  delete dialog.dataset.closing;
}
function openProjectDrawer() {
  document.querySelector("#project-form").reset();
  openDialog(document.querySelector("#project-drawer"));
  document.querySelector("#name").focus();
}
for (const id of ["new-project", "new-project-secondary"]) document.querySelector("#" + id).addEventListener("click", openProjectDrawer);
document.querySelector("#close-project-drawer").addEventListener("click", () => closeDialog(document.querySelector("#project-drawer")));
for (const dialog of document.querySelectorAll("dialog")) dialog.addEventListener("cancel", event => { event.preventDefault(); closeDialog(dialog); });
function prepareUpload(doc = null) {
  uploadSelectionVersion += 1;
  document.querySelector("#document-form").reset();
  documentTarget = doc;
  uploadQueue = [];
  document.querySelector("#document-file").multiple = !doc;
  renderUploadQueue();
  document.querySelector("#upload-stop-remaining").hidden = true;
  document.querySelector("#document-target").textContent = doc ? `上传 ${doc.filename} 的新修订（确认当前 v${doc.revision}）` : "导入新资料";
  document.querySelector("#upload-file-state").textContent = "尚未选择文件";
  document.querySelector("#chosen-document-file-name").textContent = "尚未选择文件";
  document.querySelector("#upload-error").textContent = "";
  document.querySelector("#document-file").removeAttribute("aria-invalid");
  document.querySelector("#upload-progress-panel").hidden = true;
  document.querySelector("#document-form [type=submit]").disabled = false;
  document.querySelector("#document-form [type=submit]").textContent = "导入资料";
}
function currentUpload(token) { return activeUploadRequest === token && activeProject === token.projectId && uploadSelectionVersion === token.selection && document.querySelector("#upload-drawer").__instanceVersion === token.instance; }
function uploadApi(path, options) {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open(options.method || "POST", path);
    for (const [name, value] of Object.entries(options.headers)) request.setRequestHeader(name, value);
    const panel = document.querySelector("#upload-progress-panel");
    const progress = document.querySelector("#upload-progress");
    const label = document.querySelector("#upload-progress-label");
    const percent = document.querySelector("#upload-progress-percent");
    const detail = document.querySelector("#upload-progress-detail");
    const token = options.uploadContext;
    const itemIndex = token.index;
    const currentItem = () => currentUpload(token) && token.index === itemIndex;
    const prefix = token.total > 1 ? `文件 ${itemIndex + 1} / ${token.total} · ` : "";
    panel.hidden = false;
    delete panel.dataset.loadedBytes;
    delete panel.dataset.totalBytes;
    progress.removeAttribute("value");
    label.textContent = prefix + "正在传输原始文件";
    percent.textContent = "";
    detail.textContent = "传输完成后仍需服务端解析、校验和保存。";
    request.upload.addEventListener("progress", event => {
      if (!currentItem() || !document.querySelector("#upload-drawer").open) return;
      panel.dataset.loadedBytes = event.loaded;
      panel.dataset.totalBytes = event.total;
      if (event.lengthComputable) {
        const value = Math.round(event.loaded / event.total * 100);
        progress.value = value;
        percent.textContent = value + "%";
        detail.textContent = `${(event.loaded / 1024).toFixed(1)} / ${(event.total / 1024).toFixed(1)} KiB 已传输`;
      }
    });
    request.upload.addEventListener("load", () => {
      if (!currentItem() || !document.querySelector("#upload-drawer").open) return;
      progress.value = 100;
      label.textContent = prefix + "文件已传输，正在解析与校验";
      detail.textContent = "等待服务端确认；此时尚未报告资料已保存。";
    });
    request.addEventListener("load", () => {
      let data;
      try { data = JSON.parse(request.responseText); }
      catch { reject(new Error("服务端未返回有效的资料记录，请检查本地服务")); return; }
      if (request.status < 200 || request.status >= 300) {
        const error = new Error(data.error?.message || "资料导入未通过");
        error.status = request.status;
        if (currentItem()) { label.textContent = "服务端校验未通过"; detail.textContent = error.message; }
        reject(error);
        return;
      }
      if (currentItem()) { label.textContent = "原文与修订已保存"; detail.textContent = `已收到真实资料记录 ${data.id || data.document_id}`; }
      document.querySelector("#connection-state").textContent = "已连接本地服务";
      document.querySelector("#connection-state").dataset.state = "ready";
      resolve(data);
    });
    request.addEventListener("error", () => { if (currentItem()) label.textContent = "连接中断"; reject(new Error("资料传输连接中断，请核对项目资料列表后再重试")); });
    request.send(options.body);
  });
}
window.addEventListener("resize", () => moveNavigation(activeView, false));
document.querySelector("#choose-document-file").addEventListener("click", event => { event.preventDefault(); document.querySelector("#document-file").click(); });
document.querySelector("#refresh-settings").addEventListener("click", async event => {
  const button = event.currentTarget;
  const feedback = document.querySelector("#settings-refresh-state");
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  feedback.textContent = "正在刷新";
  try {
    await refreshCapabilities();
    feedback.textContent = "运行状态已更新";
    if (errorBox.dataset.settingsRefreshError === "true") {
      errorBox.textContent = "";
      delete errorBox.dataset.settingsRefreshError;
    }
  } catch (error) {
    feedback.textContent = "刷新失败，请重试";
    displayError(error);
    errorBox.dataset.settingsRefreshError = "true";
  } finally {
    button.disabled = false;
    button.removeAttribute("aria-busy");
  }
});
function finishMotion() {
  if (reduceMotion()) {
    document.getAnimations().forEach(animation => animation.cancel());
    resetIllustrationLight();
  }
}

// The illustration's glass catches a local inspection light; work controls stay still.
const illustrationSurface = document.querySelector(".studio-hero");
const inspectionPointer = window.matchMedia("(hover: hover) and (pointer: fine)");
let inspectionFrame = 0;
let inspectionPoint = null;
function resetIllustrationLight() {
  if (inspectionFrame) cancelAnimationFrame(inspectionFrame);
  inspectionFrame = 0;
  inspectionPoint = null;
  illustrationSurface?.removeAttribute("data-inspecting");
}
illustrationSurface?.addEventListener("pointermove", event => {
  if (event.pointerType !== "mouse" || !inspectionPointer.matches || reduceMotion()) {
    resetIllustrationLight();
    return;
  }
  inspectionPoint = {x: event.clientX, y: event.clientY};
  if (inspectionFrame) return;
  inspectionFrame = requestAnimationFrame(() => {
    inspectionFrame = 0;
    if (!inspectionPoint || reduceMotion() || !inspectionPointer.matches) return resetIllustrationLight();
    const bounds = illustrationSurface.getBoundingClientRect();
    const x = inspectionPoint.x - bounds.left, y = inspectionPoint.y - bounds.top;
    if (!bounds.width || !bounds.height || x < bounds.width * .48 || x > bounds.width || y < 0 || y > bounds.height) return resetIllustrationLight();
    illustrationSurface.style.setProperty("--inspection-x", `${Math.min(x, bounds.width - 20)}px`);
    illustrationSurface.style.setProperty("--inspection-y", `${Math.max(20, Math.min(y, bounds.height - 20))}px`);
    illustrationSurface.dataset.inspecting = "true";
  });
});
illustrationSurface?.addEventListener("pointerleave", resetIllustrationLight);
inspectionPointer.addEventListener("change", resetIllustrationLight);
window.addEventListener("blur", resetIllustrationLight);
document.addEventListener("visibilitychange", () => { if (document.hidden) resetIllustrationLight(); });

document.querySelector("#upload-stop-remaining").addEventListener("click", event => {
  if (!activeUploadRequest || !currentUpload(activeUploadRequest)) return;
  activeUploadRequest.stopRequested = true; event.currentTarget.disabled = true;
  document.querySelector("#upload-progress-detail").textContent = "将停止后续文件；当前文件的保存结果仍等待服务端确认。";
});
document.querySelector("#upload-view-library").addEventListener("click", async () => {
  const projectId = activeProject;
  await closeDialog(document.querySelector("#upload-drawer"));
  if (activeProject !== projectId) return;
  await refreshDocuments(projectId);
  if (activeProject === projectId) selectView("library", true, true);
});
