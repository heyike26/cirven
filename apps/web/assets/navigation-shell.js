/* Discrete navigation visibility: no API, permissions or project changes. */
(() => {
  "use strict";
  const navigation = document.querySelector("#desktop-sidebar");
  const reveal = document.querySelector("#navigation-reveal");
  if (!navigation || !reveal) return;
  const desktop = matchMedia("(min-width: 701px)");
  const positions = new WeakMap();
  let windowPosition = window.scrollY;
  let pending = null;
  let frame = 0;
  let hovered = false;
  let readingInput = false;
  function show() {
    if (frame) cancelAnimationFrame(frame);
    frame = 0;
    pending = null;
    readingInput = false;
    document.body.classList.remove("navigation-hidden");
    reveal.setAttribute("aria-expanded", "true");
  }
  function hide() {
    if (!desktop.matches || hovered || navigation.contains(document.activeElement)
        || document.querySelector("dialog[open]")) return;
    document.body.classList.add("navigation-hidden");
    reveal.setAttribute("aria-expanded", "false");
  }
  function scroll(event) {
    const target = event.target;
    if (target instanceof Element && (navigation.contains(target)
        || !target.closest("#main-content"))) return;
    const value = target instanceof Element ? target.scrollTop : window.scrollY;
    const previous = target instanceof Element ? (positions.get(target) || 0) : windowPosition;
    if (target instanceof Element) positions.set(target, value);
    else windowPosition = value;
    if (Math.abs(value - previous) < 3) return;
    // Focus, document selection and route changes also scroll the page. Keep
    // navigation available for those operations; hide only while reading.
    pending = {value, down: value > previous, readingInput};
    readingInput = false;
    if (frame) return;
    frame = requestAnimationFrame(() => {
      frame = 0;
      if (!pending) return;
      if (!pending.down || pending.value < 24) show();
      else if (pending.readingInput) hide();
      pending = null;
    });
  }
  navigation.addEventListener("pointerenter", () => { hovered = true; show(); });
  navigation.addEventListener("pointerleave", () => { hovered = false; });
  navigation.addEventListener("focusin", show);
  reveal.addEventListener("pointerenter", show);
  reveal.addEventListener("focus", show);
  reveal.addEventListener("click", () => {
    show();
    navigation.querySelector('[aria-current="page"]')?.focus();
  });
  document.addEventListener("keydown", event => {
    if (["Tab", "Escape"].includes(event.key)) show();
    if (event.isTrusted && ["PageDown", "PageUp", "ArrowDown", "ArrowUp", "Home", "End", " "].includes(event.key)
        && !event.target.closest("input,textarea,select,button,[contenteditable]")) readingInput = true;
  });
  document.addEventListener("wheel", event => { if (event.isTrusted) readingInput = true; }, {capture: true, passive: true});
  document.addEventListener("touchmove", event => { if (event.isTrusted) readingInput = true; }, {capture: true, passive: true});
  document.addEventListener("pointerdown", event => {
    show();
    readingInput = event.isTrusted && event.target === document.documentElement;
  }, {capture: true, passive: true});
  document.addEventListener("focusin", () => { readingInput = false; show(); });
  document.addEventListener("scroll", scroll, {capture: true, passive: true});
  desktop.addEventListener("change", show);
  window.addEventListener("pageshow", show);
  window.addEventListener("pagehide", () => { if (frame) cancelAnimationFrame(frame); frame = 0; });
  show();
})();
