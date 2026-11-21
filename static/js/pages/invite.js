/** Invite link: show the terms, ask for sign-in, then accept or decline. */
import { get, post } from "../api.js";
import { startSignIn } from "../app.js";
import { config } from "../session.js";
import { ROLE_LABEL, busy, el, fill, fmtTime, money } from "../ui.js";

const root = document.querySelector("[data-invite-page]");
const token = root.dataset.token;
const $ = (selector) => root.querySelector(selector);
const RULES = {
  landlord: "As landlord you receive any agreed damage payout. You can claim damage until the grace period ends; after that the deposit returns to the tenant automatically.",
  tenant: "As tenant you lock the deposit from your own Xaman account. If nothing goes wrong it comes back to you automatically after the lease end plus the grace period.",
  arbiter: "As arbiter you only act in a dispute: any two of tenant, landlord and you must approve how the deposit is split. You never hold the money.",
};

async function render() {
  const data = await get(`/api/invites/${token}/`);
  const lease = data.lease;
  $("[data-title]").textContent = `You're invited as ${ROLE_LABEL[data.role].toLowerCase()}`;
  if (lease.amount === undefined) { // a used invite seen by someone else: the API sends no terms
    $("[data-subtitle]").textContent = "";
    $("[data-terms]").hidden = true;
    const used = $("[data-decision]");
    used.hidden = false;
    return fill(used, el("p", { class: "alert alert--warning", text: "This invite has already been used." }));
  }
  $("[data-terms]").hidden = false;
  $("[data-subtitle]").textContent = `${lease.created_by} started this agreement${lease.title ? ` for ${lease.title}` : ""}.`;
  fill($("[data-terms]"),
    el("h2", { class: "card__title", text: "Terms" }),
    el("dl", { class: "details" },
      el("dt", { text: "Deposit" }), el("dd", { text: money(lease.amount, lease.currency_label) }),
      el("dt", { text: "Lease ends" }), el("dd", { text: fmtTime(lease.end_at) }),
      el("dt", { text: "Automatic return" }), el("dd", { text: `${fmtTime(lease.cancel_after)} (after a grace period of ${lease.grace_minutes} minutes)` }),
      el("dt", { text: "Parties so far" }), el("dd", { text: lease.parties.map((p) => `${p.name} (${p.role}, ${p.address})`).join("; ") || "none" }),
      el("dt", { text: "Network" }), el("dd", { text: `XRP Ledger ${config.network.name}: test funds only` })),
    el("p", { class: "mt-4", text: RULES[data.role] }));

  const decision = $("[data-decision]");
  decision.hidden = false;
  if (lease.status !== "PENDING_ACCEPTANCE") {
    return fill(decision, el("p", { text: `This agreement is ${lease.status_label.toLowerCase()}.` }),
      data.accepted_by_you && el("a", { class: "btn btn--primary", href: `/leases/${lease.id}/`, text: "Open the agreement" }));
  }
  if (data.accepted_by_you) return fill(decision, el("p", { text: "You've accepted." }), el("a", { class: "btn btn--primary", href: `/leases/${lease.id}/`, text: "Open the agreement" }));
  if (data.used) return fill(decision, el("p", { class: "alert alert--warning", text: "This invite has already been used." }));
  if (!data.signed_in) {
    return fill(decision, el("p", { text: "Sign in with Xaman to accept. Signing in for the first time creates your account." }),
      el("button", { type: "button", class: "btn btn--primary", text: "Sign in with Xaman", on: { click: async (e) => { await startSignIn(e.currentTarget); await render(); } } }));
  }
  if (data.already_party) return fill(decision, el("p", { class: "alert alert--warning", text: "You already have another role in this agreement." }));
  return fill(decision,
    el("div", { class: "actions" },
      el("button", { type: "button", class: "btn btn--primary", text: data.role === "arbiter" ? "Accept the arbiter role" : "Accept", on: { click: (e) => decide(e.currentTarget, "accept") } }),
      el("button", { type: "button", class: "btn btn--ghost", text: "Decline", on: { click: (e) => decide(e.currentTarget, "decline") } })));
}

async function decide(button, what) {
  if (what === "decline" && !window.confirm("Decline? This closes the agreement for everyone.")) return;
  await busy(button, what === "accept" ? "Accepting…" : "Declining…", async () => {
    try {
      const result = await post(`/api/invites/${token}/${what}/`);
      if (result.lease_id) window.location.assign(`/leases/${result.lease_id}/`);
      else await render();
    } catch { /* shown by api.js */ }
  });
}

render().catch((error) => fill($("[data-terms]"), el("p", { class: "alert alert--warning", text: error.message })));
