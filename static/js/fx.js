/**
 * Decorative motion. Everything here is progressive enhancement:
 *  - pages are complete and readable without it (no content starts hidden in HTML/CSS alone)
 *  - nothing runs when the user prefers reduced motion
 *  - only transform/opacity/clip-path are animated (cheap for the GPU)
 *  - all DOM writes use classList/textContent, never innerHTML
 */

const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

/* Elements that fade up as they scroll into view. Children of a grid are staggered. */
const REVEAL_SELECTOR = [
  ".sec", ".abstract", ".stats", ".card", ".defn", ".table-wrap",
  ".equation", ".empty", "fieldset",
].join(",");

function initReveal(observe) {
  const fold = window.innerHeight;
  document.querySelectorAll(REVEAL_SELECTOR).forEach((el) => {
    // Already on screen at load: the page-enter animation covers it, so don't hide it (avoids a flash).
    if (el.getBoundingClientRect().top < fold * 0.9) {
      el.classList.add("is-visible");
      return;
    }
    const parent = el.parentElement;
    if (parent && parent.classList.contains("grid")) {
      const index = [...parent.children].indexOf(el);
      el.style.setProperty("--reveal-delay", `${index * 90}ms`);   // CSSOM, not an inline style attribute: CSP-safe
    }
    el.classList.add("reveal");
    observe(el);
  });
}

/* Count-up for numbers. The final text is always the original string (no float rounding). */
function countUp(el) {
  const node = [...el.childNodes].find((n) => n.nodeType === Node.TEXT_NODE && n.textContent.trim());
  if (!node) return;
  const original = node.textContent;
  const match = original.trim().match(/^(\d+)(?:\.(\d+))?$/);
  if (!match) return;
  const target = Number(original.trim());
  const decimals = match[2] ? match[2].length : 0;
  const duration = 900;
  const start = performance.now();
  const step = (now) => {
    const t = Math.min(1, (now - start) / duration);
    const eased = 1 - Math.pow(1 - t, 3);
    node.textContent = t < 1 ? (target * eased).toFixed(decimals) : original;
    if (t < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

function initCounters(observe) {
  // The count-up lasts under a second and always ends on the exact original text.
  document.querySelectorAll("[data-count], .amount").forEach((el) => observe(el, () => countUp(el)));
}

/* Typeset equations when they scroll into view (CSS keeps them visible when JS is off). */
function initEquations(observe) {
  document.querySelectorAll(".equation--type").forEach((el) => observe(el));
}

/* Ripple from the click point on every button. */
function initRipple() {
  document.addEventListener("pointerdown", (e) => {
    const btn = e.target instanceof Element ? e.target.closest(".btn") : null;
    if (!btn || btn.disabled || btn.getAttribute("aria-disabled") === "true") return;
    const rect = btn.getBoundingClientRect();
    const dot = document.createElement("span");
    dot.className = "ripple";
    dot.setAttribute("aria-hidden", "true");
    dot.style.left = `${e.clientX - rect.left}px`;
    dot.style.top = `${e.clientY - rect.top}px`;
    btn.appendChild(dot);
    dot.addEventListener("animationend", () => dot.remove(), { once: true });
  });
}

export function initFx() {
  if (reduceMotion.matches) return;
  document.documentElement.classList.add("fx");

  const callbacks = new WeakMap();
  const io = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      entry.target.classList.add("is-visible");
      callbacks.get(entry.target)?.();
      io.unobserve(entry.target);
    });
  }, { rootMargin: "0px 0px -8% 0px", threshold: 0.12 });

  const observe = (el, cb) => {
    if (cb) callbacks.set(el, cb);
    io.observe(el);
  };

  initReveal(observe);
  initCounters(observe);
  initEquations(observe);
  initRipple();
}
