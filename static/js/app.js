/**
 * Global frontend behaviour. Loaded as a module on every page by base.html.
 * No inline scripts or eval, so a strict Content-Security-Policy works.
 * Untrusted text is always written with textContent, never innerHTML.
 */
import { connectBrowserWallet, disconnectBrowserWallet } from "./wallet.js";
import { initSound } from "./sound.js";
import { initFx } from "./fx.js";
import { get, post } from "./api.js";
import { actAs, config, loadMe, onPersona } from "./session.js";
import { ROLE_LABEL, busy, el, notify, shortAddress, toast } from "./ui.js";

export { config, notify, shortAddress };

/* ---------- Who am I: persona badge and wallet buttons ---------- */
function renderIdentity(me) {
  const badge = document.querySelector("[data-persona]");
  if (badge) {
    badge.hidden = !me;
    if (me) {
      badge.className = `persona-badge role-${me.role}`;
      badge.querySelector("[data-persona-name]").textContent = me.name;
      badge.querySelector("[data-persona-role]").textContent = ROLE_LABEL[me.role] || me.role;
      badge.title = `Acting as ${me.name} (${me.role}) · ${me.address}`;
    }
  }
  const linked = Boolean(me) && me.wallet_type !== "demo";
  const root = document.querySelector("[data-wallet]");
  if (root) {
    root.querySelector("[data-wallet-connect]").hidden = linked;
    const address = root.querySelector("[data-wallet-address]");
    address.hidden = !linked;
    address.textContent = linked ? shortAddress(me.address) : "";
    address.title = linked ? me.address : "";
    root.querySelector("[data-wallet-disconnect]").hidden = !linked;
  }
  document.querySelectorAll("[data-demo-personas] .chip").forEach((chip) => {
    chip.setAttribute("aria-pressed", String(Boolean(me) && Number(chip.dataset.partyId) === me.party_id));
  });
}

/** Ask a first-time wallet which party it is. Resolves to a role, or null when cancelled. */
function askRole() {
  const dialog = document.querySelector("[data-role-dialog]");
  if (!dialog?.showModal) return Promise.resolve(null);
  return new Promise((resolve) => {
    dialog.returnValue = "";
    dialog.addEventListener("close", () => resolve(dialog.returnValue || null), { once: true });
    dialog.showModal();
  });
}

async function onConnectClick(event) {
  await busy(event.currentTarget, "Waiting for the wallet…", async () => {
    try {
      const me = await connectBrowserWallet(askRole);
      toast(`Wallet connected as ${ROLE_LABEL[me.role].toLowerCase()} ${shortAddress(me.address)}.`, "success");
    } catch (error) {
      // API errors were already shown by api.js; wallet errors are shown here.
      if (!error.code) toast(error instanceof Error ? error.message : "Could not connect the wallet.", "warning");
    }
  });
}

async function onDisconnectClick() {
  try {
    await disconnectBrowserWallet();
    toast("Wallet disconnected.", "info");
  } catch { /* shown by api.js */ }
}

/* ---------- Demo bar: persona chips and the bot button ---------- */
async function initDemoBar() {
  const bar = document.querySelector("[data-demo-bar]");
  if (!bar) return;
  const group = bar.querySelector("[data-demo-personas]");
  try {
    const { personas } = await get("/api/demo/personas/", { quiet: true });
    group.replaceChildren(...personas.map((persona) => el("button", {
      type: "button", class: `chip role-${persona.role}`, "aria-pressed": "false",
      title: `Act as ${persona.name} (${persona.role})`,
      data: { partyId: String(persona.party_id) },
      on: {
        click: async (event) => {
          const chip = event.currentTarget;
          if (chip.getAttribute("aria-pressed") === "true") return;
          group.querySelectorAll(".chip").forEach((c) => { c.disabled = true; });
          try {
            await actAs(persona.party_id);
            toast(`Now acting as ${persona.name} (${persona.role}).`, "info", 2500);
          } catch { /* shown by api.js */ }
          group.querySelectorAll(".chip").forEach((c) => { c.disabled = false; });
        },
      },
    }, el("span", { class: "role-dot", "aria-hidden": "true" }), persona.name.split(" ")[0])));
    loadMe().then(renderIdentity).catch(() => {});
  } catch {
    group.replaceChildren(el("span", { text: "no personas yet: run bootstrap_demo" }));
  }

  bar.querySelector("[data-run-bot]")?.addEventListener("click", (event) => runBot(event.currentTarget));
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
  document.querySelector("[data-wallet-connect]")?.addEventListener("click", onConnectClick);
  document.querySelector("[data-wallet-disconnect]")?.addEventListener("click", onDisconnectClick);
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
