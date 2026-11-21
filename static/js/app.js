/**
 * Global frontend behaviour. Loaded as a module on every page by base.html.
 * No inline scripts or eval, so a strict Content-Security-Policy works.
 * Untrusted text is always written with textContent, never innerHTML.
 */
import { signIn } from "./actions.js";
import { initSound } from "./sound.js";
import { initFx } from "./fx.js";
import { post } from "./api.js";
import { config, loadMe, onMe, signOut } from "./session.js";
import { busy, notify, shortAddress, toast } from "./ui.js";

export { config, notify, shortAddress };

/* ---------- Who am I: header identity, sign-in and the first-name prompt ---------- */
function renderIdentity(me) {
  const badge = document.querySelector("[data-me]");
  if (badge) {
    badge.hidden = !me;
    if (me) {
      badge.querySelector("[data-me-name]").textContent = me.name;
      badge.querySelector("[data-me-address]").textContent = shortAddress(me.address);
      badge.title = me.address;
    }
  }
  document.querySelectorAll("[data-signed-in-only]").forEach((node) => { node.hidden = !me; });
  const signInButton = document.querySelector("[data-signin]");
  if (signInButton) signInButton.hidden = Boolean(me) || !config.xaman_configured;
  const signOutButton = document.querySelector("[data-signout]");
  if (signOutButton) signOutButton.hidden = !me;
  if (me?.needs_name) askName();
}

function askName() {
  const dialog = document.querySelector("[data-name-dialog]");
  if (!dialog?.showModal || dialog.open) return;
  dialog.showModal();
}

function initNameDialog() {
  const dialog = document.querySelector("[data-name-dialog]");
  const form = dialog?.querySelector("[data-name-form]");
  if (!form) return;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const name = form.elements.display_name.value.trim();
    if (!name) return;
    try {
      await post("/api/me/", { display_name: name });
    } catch { return; /* shown by api.js; keep the dialog open */ }
    dialog.close();
    await loadMe(true).catch(() => {});
  });
}

/** Same-site paths only: one leading "/", never "//host" or "/\host". */
const safeNext = (next) => {
  if (typeof next !== "string" || !next.startsWith("/") || next[1] === "/" || next[1] === "\\") return false;
  // eslint-disable-next-line no-control-regex
  if (/[\x00-\x1f\x7f]/.test(next)) return false;
  try { return new URL(next, location.origin).origin === location.origin; } catch { return false; }
};

export async function startSignIn(button) {
  await busy(button, "Waiting for Xaman…", async () => {
    try {
      await signIn();
      const next = new URLSearchParams(location.search).get("next");
      if (safeNext(next)) location.assign(next);
      else if (location.pathname === "/") location.assign("/leases/");
    } catch (error) {
      if (!error.code) toast(error.message, "warning");
    }
  });
}

/* ---------- Copy buttons: <button data-copy="text"> ---------- */
function initCopy() {
  document.addEventListener("click", async (e) => {
    const btn = e.target instanceof Element ? e.target.closest("[data-copy]") : null;
    if (!btn) return;
    try {
      await navigator.clipboard.writeText(btn.dataset.copy || "");
      // Brief "Copied" label on the button itself, then restore.
      btn.dataset.label ||= btn.textContent;
      btn.textContent = "Copied";
      clearTimeout(Number(btn.dataset.timer));
      btn.dataset.timer = String(setTimeout(() => { btn.textContent = btn.dataset.label; }, 1400));
    } catch {
      notify("Copy failed. Select the text and copy it manually.", "warning");
    }
  });
}

/* ---------- Confirmations: <form data-confirm="Are you sure?"> ---------- */
function initConfirm() {
  document.addEventListener("submit", (e) => {
    const form = e.target;
    if (!(form instanceof HTMLFormElement)) return;
    const submitter = e.submitter;
    const message = submitter?.dataset.confirm || form.dataset.confirm;
    if (message && !window.confirm(message)) e.preventDefault();
  });
}

/* ---------- Decimal amount inputs: <input data-amount> ----------
 * Amounts are kept as strings end to end. JS floats can't represent money exactly,
 * so the server (Django Decimal) re-validates.
 */
const AMOUNT = /^\d{1,12}(\.\d{1,6})?$/;
function initAmountInputs() {
  document.addEventListener("input", (e) => {
    const input = e.target;
    if (!(input instanceof HTMLInputElement) || !("amount" in input.dataset)) return;
    const ok = input.value === "" || AMOUNT.test(input.value);
    input.setAttribute("aria-invalid", String(!ok));
    input.setCustomValidity(ok ? "" : "Enter a number with up to 6 decimal places.");
  });
}

/* ---------- Boot ---------- */
function init() {
  document.querySelector("[data-signin]")?.addEventListener("click", (event) => startSignIn(event.currentTarget));
  document.querySelector("[data-signout]")?.addEventListener("click", () => signOut().catch(() => {}));
  initNameDialog();
  onMe(renderIdentity);
  initCopy();
  initConfirm();
  initAmountInputs();
  initSound();
  initFx();
  // Uncaught errors must never leave a button spinning silently.
  window.addEventListener("unhandledrejection", (event) => {
    if (event.reason?.code) return; // API errors already produced a toast
    toast("Something went wrong in the page. Reload and try again.", "error");
  });
  loadMe().catch(() => renderIdentity(null));
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
else init();
