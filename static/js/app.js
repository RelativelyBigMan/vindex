/**
 * Global frontend behaviour. Loaded as a module on every page by base.html.
 * No inline scripts or eval, so a strict Content-Security-Policy works.
 * Untrusted text is always written with textContent, never innerHTML.
 */
import { availableAdapters, connectWith, disconnect, currentSession } from "./wallet.js";

/* ---------- Config (from Django via json_script) ---------- */
function readConfig() {
  const el = document.getElementById("escrow-config");
  const defaults = {
    network: { id: "testnet", name: "Testnet", is_testnet: true, explorer_tx_url: "" },
    currency: { code: "USD", issuer: "", decimals: 2 },
  };
  if (!el) return defaults;
  try {
    const parsed = JSON.parse(el.textContent || "null");
    return parsed && typeof parsed === "object" ? { ...defaults, ...parsed } : defaults;
  } catch {
    return defaults;
  }
}
export const config = readConfig();

/* ---------- Notifications ---------- */
/**
 * @param {string} text
 * @param {"info"|"success"|"warning"|"error"} [level]
 */
export function notify(text, level = "info") {
  const box = document.querySelector("[data-js-messages]");
  if (!box) return;
  const div = document.createElement("div");
  div.className = `alert alert--${level}`;
  div.textContent = text;
  box.replaceChildren(div);
}

/* ---------- Helpers ---------- */
export function shortAddress(addr) {
  return addr && addr.length > 12 ? `${addr.slice(0, 6)}…${addr.slice(-4)}` : addr || "";
}

/** Address comparison. XRPL classic addresses are case-sensitive, so compare exactly. */
function sameAddress(a, b) {
  return Boolean(a) && Boolean(b) && a.trim() === b.trim();
}

/* ---------- Wallet UI ---------- */
function renderWallet() {
  const session = currentSession();
  const root = document.querySelector("[data-wallet]");
  if (root) {
    root.querySelector("[data-wallet-connect]").hidden = Boolean(session);
    const addrEl = root.querySelector("[data-wallet-address]");
    addrEl.hidden = !session;
    addrEl.textContent = session ? shortAddress(session.address) : "";
    addrEl.title = session ? session.address : "";
    root.querySelector("[data-wallet-disconnect]").hidden = !session;
  }

  // Anything that needs a wallet stays disabled until one is connected.
  document.querySelectorAll("[data-requires-wallet]").forEach((el) => {
    el.disabled = !session;
    el.setAttribute("aria-disabled", String(!session));
  });

  // Mark "You" on party cards, and only enable voting for actual parties.
  document.querySelectorAll("[data-party-address]").forEach((card) => {
    const isMe = session && sameAddress(card.dataset.partyAddress, session.address);
    const you = card.querySelector("[data-you]");
    if (you) you.hidden = !isMe;
  });
  const isParty = session && [...document.querySelectorAll("[data-party-address]")]
    .some((c) => sameAddress(c.dataset.partyAddress, session.address));
  document.querySelectorAll("[data-requires-party]").forEach((el) => {
    el.disabled = !isParty;
    el.setAttribute("aria-disabled", String(!isParty));
  });
  const hint = document.querySelector("[data-party-hint]");
  if (hint) hint.hidden = Boolean(isParty);
}

async function onConnectClick() {
  const adapters = await availableAdapters();
  if (adapters.length === 0) {
    notify("No supported wallet found. Wallet integration is not set up yet.", "warning");
    return;
  }
  try {
    // A wallet picker comes later; for now use the first available adapter.
    await connectWith(adapters[0], config.network.id);
    notify("Wallet connected.", "success");
  } catch (err) {
    notify(err instanceof Error ? err.message : "Could not connect wallet.", "error");
  }
}

/* ---------- Copy buttons: <button data-copy="text"> ---------- */
function initCopy() {
  document.addEventListener("click", async (e) => {
    const btn = e.target instanceof Element ? e.target.closest("[data-copy]") : null;
    if (!btn) return;
    try {
      await navigator.clipboard.writeText(btn.dataset.copy || "");
      notify("Copied to clipboard.", "success");
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
 * so the server (Django Decimal) must re-validate.
 */
function initAmountInputs() {
  const decimals = Number(config.currency.decimals) || 2;
  const pattern = new RegExp(`^\\d{1,12}(\\.\\d{1,${decimals}})?$`);
  document.querySelectorAll("input[data-amount]").forEach((input) => {
    input.addEventListener("input", () => {
      const ok = input.value === "" || pattern.test(input.value);
      input.setAttribute("aria-invalid", String(!ok));
      input.setCustomValidity(ok ? "" : `Enter a number with up to ${decimals} decimal places.`);
    });
  });
}

/* ---------- Boot ---------- */
function init() {
  document.querySelector("[data-wallet-connect]")?.addEventListener("click", onConnectClick);
  document.querySelector("[data-wallet-disconnect]")?.addEventListener("click", () => disconnect());
  document.addEventListener("wallet:connected", renderWallet);
  document.addEventListener("wallet:disconnected", renderWallet);
  initCopy();
  initConfirm();
  initAmountInputs();
  renderWallet();
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
else init();
