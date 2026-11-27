"use strict";

// One original demonstration dataset. This page has no product/API transport.
const labCanvas = document.getElementById("lab-canvas");
const navigation = document.getElementById("lab-navigation");
const indicator = document.getElementById("lab-nav-indicator");
const article = document.getElementById("lab-article");
const contextPanel = document.getElementById("lab-context-panel");
const drawer = document.getElementById("lab-object-drawer");
const contextTrigger = document.getElementById("lab-open-context");
const reduceInput = document.getElementById("lab-reduce-motion");
const motionStatus = document.getElementById("lab-motion-status");
const currentView = document.getElementById("lab-current-view");
const drawerPage = document.getElementById("lab-drawer-page");
const sourceContent = Array.from(article.children, child => child.cloneNode(true));
const reduceQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
const wideQuery = window.matchMedia("(min-width: 1101px)");
const activeMotion = new Map();
const navButtons = Array.from(navigation.querySelectorAll("button[data-page]"));
const modeButtons = Array.from(document.querySelectorAll("button[data-mode]"));
const pages = Object.freeze({
  source: { label: "原文", title: "通信链路与数据交接" },
  notes: {
    label: "标注", title: "读到这里，保留一条疑问",
    intro: "标注与原文并排思考，而不提前替原文作出结论。",
    sections: [
      { heading: "这条疑问来自哪里", paragraphs: ["原创示意原文提到，通信中断后记录保留在本地，恢复连接后再区分新数据与尚未传送的记录。", "这里保留一条示意标注：‘恢复连接’是否需要附带明确的等待时间？原文没有给出这个数字。"] },
      { heading: "保留来源，避免猜测", paragraphs: ["当前对象仍是同一份‘通信链路说明 / 原创示意 v1’。切换导航布局不会更换材料，也不会将这条疑问转换成已经确认的需求。", "查看依据会展开当前对象的信息。你可以用键盘打开、阅读和返回；窗口变窄时，也不需要依靠消失的菜单文字辨认当前位置。"] }
    ]
  },
  task: {
    label: "任务", title: "先读清楚，再写下一步",
    intro: "任务说明借用同一份原创材料，观察操作区如何与正文保持联系。",
    sections: [
      { heading: "示意目标", paragraphs: ["核对通信恢复前后的记录分类规则，整理原文已经说明的条件与仍需人工澄清的问题。", "这只是布局试验中的目标文字。它没有创建 Formal Task，没有选择运行环境，也没有触发 Runner 或模型。"] },
      { heading: "正文与对象要一起留在视线内", paragraphs: ["任务页面仍显示相同的材料名称和示意版本。打开依据时，抽屉从触发位置展开；关闭后，焦点回到‘查看依据’按钮。", "比较三种布局时，可以先阅读一段内容，再切换导航结构。页面选择保持不变，只有导航位置和辅助信息的组织方式改变。"] }
    ]
  },
  review: {
    label: "审阅", title: "把人工判断留给人工",
    intro: "审阅说明展示上下文，不用状态颜色代替证据。",
    sections: [
      { heading: "先确认读的是哪份材料", paragraphs: ["本页的来源是原创示意，版本是示意 v1。它没有真实项目 ID、原件哈希、审批记录或测试回执。", "这份说明没有‘已通过’结论。原文里的条件、提出的问题和人工确认应该保持清晰区分。"] },
      { heading: "操作反馈保持克制", paragraphs: ["选中指示只用一个短暂的移动告诉你当前位置发生了变化。正文不逐段滑入，数据不反复跳动，玻璃表面也不持续漂浮。", "勾选减少动态，或在系统中开启同一设置，正在运行的过渡会立即取消。最终位置与完整内容保留，不需要等待动画才能继续阅读。"] }
    ]
  }
});
let pageKey = "source";
let modeKey = "floating";
let motionReduced = false;
let drawerReturnTarget = null;
let resizeFrame = 0;

function cancelMotion(element) {
  const animation = activeMotion.get(element);
  if (animation) {
    activeMotion.delete(element);
    animation.cancel();
  }
}

function cancelAllMotion() {
  for (const animation of activeMotion.values()) animation.cancel();
  activeMotion.clear();
}

function animateOnce(element, frames) {
  cancelMotion(element);
  if (motionReduced || document.hidden || typeof element.animate !== "function") return;
  const animation = element.animate(frames, { duration: 240, easing: "cubic-bezier(.22, 1, .36, 1)" });
  activeMotion.set(element, animation);
  const release = () => {
    if (activeMotion.get(element) === animation) activeMotion.delete(element);
  };
  animation.finished.then(release, release);
}

function placeIndicator(previous = null) {
  cancelMotion(indicator);
  const button = navButtons.find(item => item.dataset.page === pageKey);
  const navRect = navigation.getBoundingClientRect();
  const buttonRect = button.getBoundingClientRect();
  indicator.style.left = `${buttonRect.left - navRect.left - navigation.clientLeft}px`;
  indicator.style.top = `${buttonRect.top - navRect.top - navigation.clientTop}px`;
  indicator.style.width = `${buttonRect.width}px`;
  indicator.style.height = `${buttonRect.height}px`;
  if (!previous || !previous.width || !previous.height) return;
  const finalRect = indicator.getBoundingClientRect();
  if (!finalRect.width || !finalRect.height) return;
  animateOnce(indicator, [
    { transform: `translate(${previous.left - finalRect.left}px, ${previous.top - finalRect.top}px) scale(${previous.width / finalRect.width}, ${previous.height / finalRect.height})` },
    { transform: "none" }
  ]);
}

function syncContextPanel() {
  contextPanel.hidden = modeKey !== "top" || !wideQuery.matches;
}

function setMode(nextMode) {
  if (nextMode === modeKey) return;
  const previous = indicator.getBoundingClientRect();
  modeKey = nextMode;
  labCanvas.dataset.mode = nextMode;
  for (const button of modeButtons) button.setAttribute("aria-pressed", String(button.dataset.mode === nextMode));
  syncContextPanel();
  placeIndicator(previous);
}

function appendText(tag, text, className = "") {
  const element = document.createElement(tag);
  element.textContent = text;
  if (className) element.className = className;
  article.append(element);
  return element;
}

function renderPage(nextPage) {
  if (nextPage === pageKey) return;
  const previous = indicator.getBoundingClientRect();
  pageKey = nextPage;
  for (const button of navButtons) {
    if (button.dataset.page === nextPage) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  }
  currentView.textContent = `正在阅读${pages[nextPage].label}`;
  drawerPage.textContent = pages[nextPage].label;
  article.replaceChildren();
  if (nextPage === "source") {
    article.append(...sourceContent.map(child => child.cloneNode(true)));
  } else {
    const page = pages[nextPage];
    appendText("h1", page.title).id = "lab-page-title";
    appendText("p", page.intro, "article-intro");
    for (const section of page.sections) {
      appendText("h2", section.heading);
      for (const paragraph of section.paragraphs) appendText("p", paragraph);
    }
    appendText("p", "原创示意内容结束。此页不会创建任务、提交审批、调用模型或写入 EEAS 数据库。", "source-end");
  }
  article.scrollIntoView({ block: "start", behavior: "auto" });
  placeIndicator(previous);
}

function setMotionPolicy() {
  motionReduced = reduceInput.checked || reduceQuery.matches;
  document.documentElement.dataset.motion = motionReduced ? "reduced" : "enabled";
  motionStatus.textContent = reduceQuery.matches ? "系统已要求减少动态" : reduceInput.checked ? "动态已关闭，内容保留" : "遵循系统动态设置";
  if (motionReduced) {
    cancelAllMotion();
    placeIndicator();
  }
}

function openDrawer() {
  if (drawer.open) return;
  drawerReturnTarget = contextTrigger;
  const triggerRect = contextTrigger.getBoundingClientRect();
  drawerPage.textContent = pages[pageKey].label;
  drawer.showModal();
  const finalRect = drawer.getBoundingClientRect();
  if (!finalRect.width || !finalRect.height) return;
  animateOnce(drawer, [
    { transform: `translate(${triggerRect.left - finalRect.left}px, ${triggerRect.top - finalRect.top}px) scale(${triggerRect.width / finalRect.width}, ${triggerRect.height / finalRect.height})`, opacity: .25 },
    { transform: "none", opacity: 1 }
  ]);
}

function closeDrawer() {
  cancelMotion(drawer);
  if (!drawer.open) return;
  const target = drawerReturnTarget;
  drawerReturnTarget = null;
  drawer.close();
  if (target?.isConnected) target.focus({ preventScroll: true });
}

function settleGeometry() {
  cancelAllMotion();
  if (resizeFrame) cancelAnimationFrame(resizeFrame);
  resizeFrame = requestAnimationFrame(() => {
    resizeFrame = 0;
    syncContextPanel();
    placeIndicator();
  });
}

for (const button of modeButtons) button.addEventListener("click", () => setMode(button.dataset.mode));
for (const button of navButtons) button.addEventListener("click", () => renderPage(button.dataset.page));
contextTrigger.addEventListener("click", openDrawer);
document.getElementById("lab-close-context").addEventListener("click", closeDrawer);
document.getElementById("lab-return-reading").addEventListener("click", closeDrawer);
drawer.addEventListener("cancel", event => { event.preventDefault(); closeDrawer(); });
drawer.addEventListener("close", () => {
  if (drawer.open) return;
  cancelMotion(drawer);
  // Native close is queued; it must not steal a newer keyboard intent.
  drawerReturnTarget = null;
});
drawer.addEventListener("click", event => {
  if (event.target !== drawer) return;
  const rect = drawer.getBoundingClientRect();
  if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) closeDrawer();
});
reduceInput.addEventListener("change", setMotionPolicy);
reduceQuery.addEventListener("change", setMotionPolicy);
wideQuery.addEventListener("change", settleGeometry);
window.addEventListener("resize", settleGeometry);
document.addEventListener("visibilitychange", () => { if (document.hidden) cancelAllMotion(); });
window.addEventListener("pagehide", () => { cancelAllMotion(); if (resizeFrame) cancelAnimationFrame(resizeFrame); });
if (document.fonts) document.fonts.ready.then(settleGeometry, settleGeometry);
setMotionPolicy();
syncContextPanel();
placeIndicator();
