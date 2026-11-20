/**
 * Small DOM and formatting helpers shared by every page.
 * Everything is built with createElement / textContent: no innerHTML anywhere, so text
 * coming from the API (names, reasons, error messages) can never become markup.
 */

/** Build an element. props: class, text, data {…}, on {event: fn}, plus any attribute or DOM property. */
export function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "data") Object.assign(node.dataset, value);
    else if (key === "on") for (const [type, fn] of Object.entries(value)) node.addEventListener(type, fn);
    else if (key.includes("-") || key === "for" || key === "role" || key === "list") node.setAttribute(key, value === true ? "" : value);
    else if (key in node) node[key] = value;
    else node.setAttribute(key, value === true ? "" : value);
  }
  node.append(...children.flat(Infinity).filter((c) => c !== null && c !== undefined && c !== false));
  return node;
}

/** replaceChildren that skips null / false, like el() does (the native one would print "null"). */
export function fill(node, ...children) {
  node.replaceChildren(...children.flat(Infinity).filter((c) => c !== null && c !== undefined && c !== false));
  return node;
}

export function shortAddress(addr) {
  return addr && addr.length > 12 ? `${addr.slice(0, 6)}…${addr.slice(-4)}` : addr || "";
}

export function shortHash(hash) {
  return hash && hash.length > 14 ? `${hash.slice(0, 8)}…${hash.slice(-4)}` : hash || "";
}

/** Only https links leave the site, and never with a referrer or an opener. */
export function extLink(url, text, props = {}) {
  if (typeof url !== "string" || !url.startsWith("https://")) return el("span", { text });
  return el("a", { href: url, target: "_blank", rel: "noopener noreferrer", ...props }, text,
    el("span", { class: "visually-hidden", text: " (opens in a new tab)" }));
}

export function copyButton(text, label = "Copy") {
  return el("button", { type: "button", class: "btn btn--ghost btn--sm", data: { copy: text }, text: label });
}

/* ---------- Toasts ---------- */
const MAX_TOASTS = 4;

/** @param {"info"|"success"|"warning"|"error"} level */
export function toast(text, level = "info", ms = level === "error" ? 9000 : 6000) {
  const box = document.querySelector("[data-toasts]");
  if (!box || !text) return;
  // The same message twice in a row (a double click, a poll and an action) is shown once.
  if (box.lastElementChild?.dataset.text === text) return;
  const close = () => {
    item.classList.add("is-leaving");
    setTimeout(() => item.remove(), 220);
  };
  const item = el("div", { class: `toast toast--${level}`, data: { text } },
    el("span", { text }),
    el("button", { type: "button", class: "toast__close", "aria-label": "Dismiss", text: "×", on: { click: close } }));
  box.append(item);
  while (box.children.length > MAX_TOASTS) box.firstElementChild.remove();
  setTimeout(close, ms);
}
export const notify = toast;

/* ---------- Formatting ---------- */
/** "1000.5", "EUR" -> "1,000.5 EUR". Amounts stay strings: no floats touch money. */
export function money(value, code = "") {
  const [whole, fraction] = String(value ?? "").split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${grouped}${fraction ? "." + fraction : ""}${code ? " " + code : ""}`;
}

export function fmtTime(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString(undefined, { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function fmtClock(iso) {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function fmtCountdown(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  const pad = (n) => String(n).padStart(2, "0");
  const days = Math.floor(s / 86400), hours = Math.floor((s % 86400) / 3600), minutes = Math.floor((s % 3600) / 60);
  if (days > 0) return `${days}d ${pad(hours)}:${pad(minutes)}:${pad(s % 60)}`;
  if (hours > 0) return `${pad(hours)}:${pad(minutes)}:${pad(s % 60)}`;
  return `${pad(minutes)}:${pad(s % 60)}`;
}

/* ---------- Status and roles ---------- */
const STATUS = {
  DRAFT: ["warning", "Being set up"],
  SETTLEMENT_READY: ["warning", "Ready to lock"],
  ACTIVE: ["info", "Locked"],
  IN_SETTLEMENT: ["warning", "In settlement"],
  RETURNED: ["success", "Returned to tenant"],
  SETTLED: ["success", "Settled"],
  FAILED: ["danger", "Failed"],
};

export function statusBadge(status) {
  const [tone, label] = STATUS[status] || ["", status];
  return el("span", { class: `badge${tone ? " badge--" + tone : ""}`, text: label });
}

export const ROLE_LABEL = { tenant: "Tenant", landlord: "Landlord", arbiter: "Arbiter" };

/* ---------- Busy buttons ---------- */
/**
 * Run fn while the button is disabled and shows a spinner with `label`
 * (e.g. "Submitting to the XRPL (about 4 s)"). A second click while busy does nothing.
 */
export async function busy(button, label, fn) {
  if (!button || button.disabled) return undefined;
  const original = [...button.childNodes];
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  button.replaceChildren(el("span", { class: "spinner", "aria-hidden": "true" }), label);
  try {
    return await fn();
  } finally {
    button.disabled = false;
    button.removeAttribute("aria-busy");
    button.replaceChildren(...original);
  }
}

export function statusLine(text, spinning = true) {
  return el("p", { class: "status-line" }, spinning && el("span", { class: "spinner", "aria-hidden": "true" }), text);
}

/** Re-render a section only when its data changed, so polling never resets focus, forms or animations. */
export function renderIfChanged(node, data, render) {
  const signature = JSON.stringify(data);
  if (node.dataset.sig === signature) return false;
  node.dataset.sig = signature;
  render(node);
  return true;
}
