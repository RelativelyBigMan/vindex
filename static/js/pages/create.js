/**
 * New deposit: the form, then the live setup checklist (polls every 2 s), then the lock step.
 */
import { lockDeposit } from "../actions.js";
import { get, post } from "../api.js";
import { setupChecklist } from "../parts.js";
import { poll } from "../poll.js";
import { onPersona } from "../session.js";
import { busy, el, fmtTime, money, statusLine, toast } from "../ui.js";

const $ = (selector) => document.querySelector(selector);
const form = $("[data-lease-form]");
const PASTE = "__paste__";
let me = null;
let arbiters = [];

/* ---------- the form ---------- */
function fillArbiters() {
  const select = form.querySelector('[data-party-select="arbiter"]');
  // Verified arbiters first; an unverified one is still selectable, and says so.
  const sorted = [...arbiters].sort((a, b) => Number(b.verified_arbiter === true) - Number(a.verified_arbiter === true));
  select.replaceChildren(
    ...sorted.map((p) => el("option", { value: String(p.party_id), text: `${p.name} (${p.verified_arbiter ? "verified" : "unverified"})` })),
    el("option", { value: PASTE, text: "Enter a username or address…" }));
  onArbiterChoice();
}

function onArbiterChoice() {
  const pasting = form.elements.arbiter.value === PASTE;
  const input = form.elements.arbiter_address;
  input.hidden = !pasting;
  input.required = pasting;
  $("[data-arbiter-warning]").hidden = true;
}

let credentialCheck = 0;
async function checkArbiterAddress() {
  const warning = $("[data-arbiter-warning]");
  const address = form.elements.arbiter_address.value.trim();
  warning.hidden = true;
  if (form.elements.arbiter.value !== PASTE || !/^r[1-9A-HJ-NP-Za-km-z]{24,34}$/.test(address)) return;
  const ticket = ++credentialCheck;
  try {
    const result = await get(`/api/credentials/${encodeURIComponent(address)}/`, { quiet: true });
    if (ticket !== credentialCheck) return;
    warning.hidden = result.verified_arbiter;
    warning.textContent = "This address holds no verified-arbiter credential on the ledger. You can still use it, but nobody vouches for its independence.";
  } catch { /* the server validates the address on submit */ }
}

function onEndMode() {
  const minutes = form.elements.end_mode.value === "minutes";
  $("[data-end-minutes]").hidden = !minutes;
  $("[data-end-date]").hidden = minutes;
  form.elements.end_in_minutes.required = minutes;
  form.elements.end_date.required = !minutes;
}

function payload() {
  const data = {
    title: form.elements.title.value.trim(),
    currency: form.elements.currency.value,
    amount: form.elements.amount.value.trim(),
    grace_minutes: form.elements.grace_minutes.value,
  };
  if (form.elements.end_mode.value === "minutes") data.end_in_minutes = form.elements.end_in_minutes.value;
  else data.end_date = form.elements.end_date.value;
  data.landlord_address = form.elements.landlord_address.value.trim();
  if (form.elements.arbiter.value === PASTE) data.arbiter_address = form.elements.arbiter_address.value.trim();
  else data.arbiter_id = Number(form.elements.arbiter.value);
  return data;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!form.reportValidity()) return;
  await busy($("[data-submit]"), "Creating the lease…", async () => {
    try {
      const { lease_id: leaseId } = await post("/api/leases/", payload());
      watchSetup(leaseId);
    } catch { /* shown by api.js; the form keeps its values for another try */ }
  });
});
form.elements.end_mode.forEach((radio) => radio.addEventListener("change", onEndMode));
form.elements.arbiter.addEventListener("change", onArbiterChoice);
form.elements.arbiter_address.addEventListener("change", checkArbiterAddress);

/* ---------- after submit: live setup checklist, then the lock step ---------- */
function watchSetup(leaseId) {
  const section = $("[data-setup]");
  const body = $("[data-setup-body]");
  form.hidden = true;
  section.hidden = false;
  section.scrollIntoView({ block: "start" });
  history.replaceState(null, "", `?lease=${leaseId}`); // a reload resumes here instead of showing an empty form
  let locking = false;

  const poller = poll(async () => {
    if (locking) return;
    let state;
    try {
      state = await get(`/api/leases/${leaseId}/`, { quiet: true });
    } catch (error) {
      if (error.status === 404) { location.href = "/leases/"; }
      return;
    }
    const parts = [setupChecklist(state)];
    if (state.status === "DRAFT") {
      parts.push(statusLine("Each step is validated by the ledger (about 20 s in total)…"));
    } else if (state.status === "SETTLEMENT_READY" && state.actions.includes("lock_deposit")) {
      if (state.me.can_sign) {
        // Demo mode: the server signs with the tenant persona's Testnet key.
        parts.push(el("div", { class: "mt-8 stack" },
          el("p", { text: `The settlement account is ready. Lock ${money(state.amount, state.currency)}: it pays into that account, or back to you after ${fmtTime(state.cancel_after)}.` }),
          el("div", { class: "actions" }, el("button", {
            type: "button", class: "btn btn--primary btn--lg", text: "Sign & lock deposit",
            on: { click: async (event) => {
              locking = true;
              await busy(event.currentTarget, "Submitting to the XRPL (about 4 s)", async () => {
                try {
                  await lockDeposit(leaseId, state.me);
                  toast("Deposit locked on the ledger.", "success");
                  location.href = `/leases/${leaseId}/`;
                } catch { locking = false; }
              });
            } },
          })),
          el("p", { class: "small muted", text: "Signed by safeDeposit with your Testnet wallet's key." })));
      } else {
        // WALLET SIGNING HOOK: a connected browser wallet signs on the lease page, which first
        // checks the wallet's trust line and balance (lease.js lockStep).
        parts.push(el("div", { class: "mt-8 stack" },
          el("p", { text: "The settlement account is ready. Continue to sign the deposit in your wallet." }),
          el("div", { class: "actions" }, el("a", { class: "btn btn--primary btn--lg", href: `/leases/${leaseId}/`, text: "Continue to lock the deposit" }))));
      }
    } else if (state.status === "FAILED") {
      poller.stop();
      parts.push(el("div", { class: "mt-8 stack" },
        el("p", { class: "alert alert--error", text: state.error || "Setup failed." }),
        el("p", { text: "Nothing was locked. You can try again with the same details." }),
        el("div", { class: "actions" }, el("button", {
          type: "button", class: "btn btn--primary", text: "Try again",
          on: { click: () => { section.hidden = true; form.hidden = false; history.replaceState(null, "", location.pathname); } },
        }))));
    } else {
      poller.stop();
      location.href = `/leases/${leaseId}/`;
      return;
    }
    const signature = JSON.stringify([state.status, state.setup_step, state.events.length]);
    if (body.dataset.sig !== signature) {
      body.dataset.sig = signature;
      body.replaceChildren(...parts);
    }
  }, 2000);
}

/* ---------- boot ---------- */
onPersona((next) => {
  me = next;
  const isTenant = Boolean(me) && me.role === "tenant";
  $("[data-not-tenant]").hidden = isTenant || !me;
  $("[data-tenant-name]").textContent = me ? me.name : "…";
  $("[data-submit]").disabled = !isTenant;
});

async function init() {
  onEndMode();
  try { arbiters = (await get("/api/arbiters/", { quiet: true })).arbiters; } catch { arbiters = []; }
  fillArbiters();
  const resume = new URLSearchParams(location.search).get("lease");
  if (resume && /^[0-9a-f-]{36}$/.test(resume)) watchSetup(resume);
}
init();
