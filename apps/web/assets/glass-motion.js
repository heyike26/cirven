/* Acknowledgement only: no routing, focus, content, permissions or API changes. */
(() => {
  "use strict";
  function attach() {
    const preference = window.matchMedia("(prefers-reduced-motion: reduce)");
    const active = new Set();
    const elementAnimations = new WeakMap();
    const reduced = () => preference.matches;
    const cancel = () => {
      for (const animation of active) animation.cancel();
      active.clear();
    };
    function play(element, frames, duration, name) {
      if (reduced() || document.visibilityState === "hidden" || typeof element.animate !== "function") return;
      elementAnimations.get(element)?.cancel();
      const animation = element.animate(frames, {duration, easing: "cubic-bezier(.16,1,.3,1)"});
      animation.id = name;
      active.add(animation);
      elementAnimations.set(element, animation);
      animation.finished.catch(() => {}).finally(() => {
        active.delete(animation);
        if (elementAnimations.get(element) === animation) elementAnimations.delete(element);
      });
    }
    const controls = "button:not(#sidebar-backdrop):not(#menu-toggle):not([data-go-view]), .reader-actions a, .run-heading a, #native-review-download";
    function press(target) {
      const control = target instanceof Element ? target.closest(controls) : null;
      if (!control || control.disabled || control.getAttribute("aria-disabled") === "true") return;
      if (control.getBoundingClientRect().width > 360) return;
      play(control, [{transform: "scale(1)"}, {transform: "scale(.985)", offset: .28}, {transform: "scale(1)"}], 130, "eeas-glass-control-press");
    }
    document.addEventListener("pointerdown", event => {
      if (event.button === 0 && event.isPrimary) press(event.target);
    }, {passive: true});
    document.addEventListener("keydown", event => {
      if (event.key !== "Enter" && event.key !== " ") return;
      const control = event.target instanceof Element ? event.target.closest(controls) : null;
      if (control) elementAnimations.get(control)?.cancel();
    });

    const toast = document.querySelector("#toast");
    if (toast) {
      let shown = !toast.hidden;
      let text = toast.textContent;
      const observeToast = () => {
        const visible = !toast.hidden;
        const next = toast.textContent;
        if (visible && next.trim() && (!shown || text !== next)) {
          // Capture the presented frame before cancelling. Repeated messages
          // retarget this ease animation; they do not replay the entrance.
          const presented = getComputedStyle(toast);
          const from = {opacity: presented.opacity,
            transform: new DOMMatrix(presented.transform === "none" ? undefined : presented.transform).toString()};
          elementAnimations.get(toast)?.cancel();
          const resting = getComputedStyle(toast);
          const to = {opacity: resting.opacity,
            transform: new DOMMatrix(resting.transform === "none" ? undefined : resting.transform).toString()};
          if (!shown) {
            const matrix = new DOMMatrix(to.transform === "none" ? undefined : to.transform);
            from.transform = matrix.translate(0, 5).toString();
          }
          if (from.opacity !== to.opacity || from.transform !== to.transform) {
            play(toast, [from, to], 240, "eeas-glass-toast-arrival");
          }
        } else if (!visible) elementAnimations.get(toast)?.cancel();
        shown = visible;
        text = next;
      };
      new MutationObserver(observeToast).observe(toast, {attributes: true, attributeFilter: ["hidden"], childList: true, subtree: true, characterData: true});
    }
    // These changes cancel immediately; they do not write the preference or wait.
    preference.addEventListener("change", () => { if (reduced()) cancel(); });
    document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") cancel(); });
    window.addEventListener("pagehide", cancel);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", attach, {once: true});
  else attach();
})();
