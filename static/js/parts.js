/**
 * Pieces of the lease UI shared by the lease page, the create page, the arbiter desk and the stage.
 * Every function returns DOM nodes built from API data with textContent.
 */
import { fromMicro, percent, toMicro } from "./dec.js";
import { ROLE_LABEL, copyButton, el, extLink, fmtClock, fmtTime, money, shortAddress, shortHash } from "./ui.js";

const SETUP_STEPS = [
  ["funded", "Settlement account created"],
  ["trust_line", "Trust line to the deposit token"],
  ["signers", "Tenant, owner and arbiter set as signers (2 of 3)"],
  ["master_disabled", "Account's own key disabled"],
];

/** The four setup transactions, ticking as the ledger validates them, each with its explorer link. */
export function setupChecklist(state) {
  const reached = SETUP_STEPS.findIndex(([step]) => step === state.setup_step);
  const allDone = !["DRAFT", "FAILED"].includes(state.status) || state.setup_step === "master_disabled";
  const links = Object.fromEntries(state.events.filter((e) => e.kind.startsWith("setup_")).map((e) => [e.kind.slice(6), e.explorer_url]));
  return el("ol", { class: "checklist" }, SETUP_STEPS.map(([step, label], index) => {
    const done = allDone || index <= reached;
    const current = !done && index === reached + 1;
    const cls = done ? "is-done" : current ? (state.status === "FAILED" ? "is-failed" : "is-current") : "";
    return el("li", { class: cls }, el("span", { text: label }), done && links[step] ? extLink(links[step], "View transaction") : null);
  }));
}

export function verifiedBadge(party) {
  if (party.role !== "arbiter") return null;
  if (party.verified_arbiter === true) {
    return el("span", { class: "badge badge--success", title: "Credential issued by the safeDeposit Mediation Registry and accepted by this account. Read live from the ledger.", text: "Verified arbiter" });
  }
  if (party.verified_arbiter === false) return el("span", { class: "badge badge--warning", title: "This account holds no accepted arbiter credential on the ledger.", text: "Unverified" });
  return null;
}

export function partyCard(party, me) {
  const isMe = Boolean(me) && me.party_id === party.party_id;
  return el("article", { class: `card party party--${party.role}` },
    el("p", { class: "party__role" }, ROLE_LABEL[party.role] || party.role,
      isMe && el("span", { class: "badge badge--info party__you", text: "You" })),
    el("p", { class: "party__name", text: party.name }),
    el("p", { class: "mono small", title: party.address, text: shortAddress(party.address) }),
    el("div", { class: "row" }, verifiedBadge(party), copyButton(party.address, "Copy address"), extLink(party.explorer_url, "Explorer", { class: "small" })));
}

export function splitBar(tenantAmount, landlordAmount, code, total) {
  const sum = total ?? null;
  const tenantShare = sum ? percent(tenantAmount, sum) : 50;
  const bar = el("div", { class: "split", role: "img", "aria-label": `Tenant ${money(tenantAmount, code)}, owner ${money(landlordAmount, code)}` },
    el("span", { class: "split__tenant" }), el("span", { class: "split__landlord" }));
  // CSSOM, not an inline style attribute: allowed by the strict CSP.
  bar.children[0].style.flexGrow = String(tenantShare);
  bar.children[1].style.flexGrow = String(100 - tenantShare);
  return el("div", {}, bar, el("div", { class: "split-legend" },
    el("span", { class: "role-tenant" }, el("span", { class: "role-dot", "aria-hidden": "true" }), `Tenant ${money(tenantAmount, code)}`),
    el("span", { class: "role-landlord" }, el("span", { class: "role-dot", "aria-hidden": "true" }), `Owner ${money(landlordAmount, code)}`)));
}

const PROPOSAL_STATUS = { OPEN: ["warning", "Open"], EXECUTED: ["success", "Paid out"], SUPERSEDED: ["", "Replaced"], EXPIRED: ["danger", "Expired"] };

/**
 * @param {object} proposal  from the lease state
 * @param {object} state     the lease state it belongs to
 * @param {(button: HTMLButtonElement, proposal: object) => void} [onApprove]  shown only when the API allows it
 */
export function proposalCard(proposal, state, onApprove) {
  const [tone, label] = PROPOSAL_STATUS[proposal.status] || ["", proposal.status];
  const total = fromMicro((toMicro(proposal.tenant_amount) ?? 0n) + (toMicro(proposal.landlord_amount) ?? 0n));
  const canApprove = onApprove && state.actions.includes(`sign:${proposal.id}`);
  const retry = proposal.signatures_count >= proposal.signatures_required;
  const names = (list) => list.map((p) => `${p.name} (${ROLE_LABEL[p.role].toLowerCase()})`).join(", ");
  return el("article", { class: "card" },
    el("div", { class: "row row--between" },
      el("h3", { text: `${money(proposal.tenant_amount, state.currency)} to the tenant, ${money(proposal.landlord_amount, state.currency)} to the owner` }),
      el("span", { class: `badge${tone ? " badge--" + tone : ""}`, text: label })),
    el("div", { class: "mt-4" }, splitBar(proposal.tenant_amount, proposal.landlord_amount, state.currency, total)),
    proposal.reason && el("p", { class: "mt-4" }, el("span", { class: "sc muted", text: "Reason " }), proposal.reason),
    el("p", { class: "small muted mt-4", text: `Proposed by ${proposal.proposed_by.name} (${ROLE_LABEL[proposal.proposed_by.role].toLowerCase()}) at ${fmtClock(proposal.created_at)}` }),
    el("div", { class: "mt-4" },
      el("p", { class: "small", text: `Approvals: ${Math.min(proposal.signatures_count, proposal.signatures_required)} of ${proposal.signatures_required}${proposal.signed_by.length ? " · " + names(proposal.signed_by) : ""}` }),
      el("div", { class: "progress", "aria-hidden": "true" },
        el("span", { class: `progress__step${proposal.signatures_count >= 1 ? " is-filled" : ""}` }),
        el("span", { class: `progress__step${proposal.signatures_count >= 2 ? " is-filled" : ""}` })),
      proposal.status === "OPEN" && proposal.still_needed.length > 0 && proposal.signatures_count < proposal.signatures_required
        && el("p", { class: "small muted mt-4", text: `Still needed: one of ${names(proposal.still_needed)}` })),
    proposal.executed.length > 0 && el("p", { class: "small mt-4" }, "Payouts: ",
      proposal.executed.map((tx, i) => [i > 0 && ", ", extLink(tx.explorer_url, shortHash(tx.tx_hash), { class: "mono" })])),
    canApprove && el("div", { class: "actions mt-4" },
      el("button", {
        type: "button", class: "btn btn--primary", on: { click: (event) => onApprove(event.currentTarget, proposal) },
        title: "Signed by safeDeposit with your Testnet wallet's key",
      }, retry ? "Retry the payout" : "Approve this split", el("span", { class: "small", text: " (Testnet key)" }))));
}

export function eventItem(event, isNew = false) {
  return el("li", { class: isNew ? "is-new" : "", data: { eventId: String(event.id) } },
    el("time", { dateTime: event.created_at, text: fmtTime(event.created_at) }),
    el("span", { text: event.summary }),
    event.explorer_url ? extLink(event.explorer_url, "View on explorer") : el("span"));
}

/** Add events that aren't in the list yet (newest first); new ones animate in after the first render. */
export function syncEvents(list, events) {
  const first = !list.dataset.ready;
  list.dataset.ready = "1";
  const known = new Set([...list.children].map((li) => li.dataset.eventId));
  const fresh = events.filter((e) => !known.has(String(e.id)));
  // events are newest first; insert oldest-of-the-new first so the newest ends up on top
  fresh.reverse().forEach((event) => list.prepend(eventItem(event, !first)));
  return fresh.length;
}
