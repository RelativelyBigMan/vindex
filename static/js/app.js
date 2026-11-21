/**
 * Global frontend behaviour. Loaded as a module on every page by base.html.
 * No inline scripts or eval, so a strict Content-Security-Policy works.
 * Untrusted text is always written with textContent, never innerHTML.
 */
import { initSound } from "./sound.js";
import { initFx } from "./fx.js";
import { post } from "./api.js";
import { config, loadMe, onPersona, signOut } from "./session.js";
import { ROLE_LABEL, busy, notify, shortAddress, toast } from "./ui.js";

export { config, notify, shortAddress };

/* ---------- Who is signed in: badge, sign in / sign out ---------- */
function renderIdentity(me) {
  const badge = document.querySelector("[data-persona]");
  if (badge) {
    badge.hidden = !me;
    if (me) {
      badge.className = `persona-badge role-${me.role}`;
      badge.querySelector("[data-persona-name]").textContent = me.name;
      badge.querySelector("[data-persona-role]").textContent = ROLE_LABEL[me.role] || me.role;
      badge.title = `Signed in as ${me.name} (${ROLE_LABEL[me.role]}) · ${me.address}`;
    }
  }
  document.querySelectorAll("[data-sign-out]").forEach((node) => { node.hidden = !me; });
  document.querySelectorAll("[data-signed-out]").forEach((node) => { node.hidden = Boolean(me); });
}

function initDemoBar() {
  document.querySelector("[data-run-bot]")?.addEventListener("click", (event) => runBot(event.currentTarget));
}

export async function runBot(button) {
  await busy(button, "Bot running…", async () => {
    try {
      const { processed } = await post("/api/demo/run-bot/");
      const returned = processed.filter((p) => p.action === "returned" || p.action === "reconciled_returned").length;
      const failed = processed.filter((p) => p.action === "error");
      if (failed.length) toast(`Bot: ${failed[0].error}`, "warning");
      else if (returned) toast(`Bot returned ${returned} deposit${returned === 1 ? "" : "s"} to the tenant.`, "success");
      else toast("Bot ran: no deposit is past its automatic return date yet (it waits 10 s past the deadline).", "info");
      document.dispatchEvent(new CustomEvent("bot:ran"));
    } catch { /* shown by api.js */ }
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
  document.querySelectorAll("[data-sign-out]").forEach((node) => node.addEventListener("click", () => signOut().catch(() => {})));
  onPersona(renderIdentity);
  initCopy();
  initConfirm();
  initAmountInputs();
  initSound();
  initFx();
  initDemoBar();
  // Uncaught errors must never leave a button spinning silently.
  window.addEventListener("unhandledrejection", (event) => {
    if (event.reason?.code) return; // API errors already produced a toast
    toast("Something went wrong in the page. Reload and try again.", "error");
  });
  loadMe().catch(() => renderIdentity(null));
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
else init();
