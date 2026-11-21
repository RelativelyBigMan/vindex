/**
 * Lease detail. Polls GET /api/leases/<id>/ every 3 s and renders it.
 * What the account may do comes ONLY from the API's actions list.
 */
import { anchorEvidence, approveSplit, addTrustLine, lockDeposit, proposeSplit, releaseDeposit, retryPayout, uploadEvidence, verifyEvidence } from "../actions.js";
import { get, post } from "../api.js";
import { partyCard, proposalCard, setupChecklist, splitBar, syncEvents } from "../parts.js";
import { poll } from "../poll.js";
import { splitForm } from "../splitform.js";
import { ROLE_LABEL, busy, copyButton, el, extLink, fill, fmtCountdown, fmtTime, money, renderIfChanged, shortAddress, shortHash, statusBadge, statusLine, toast } from "../ui.js";

const root = document.querySelector("[data-lease-page]");
const leaseId = root.dataset.leaseId;
const $ = (selector) => root.querySelector(selector);
const IN_XAMAN = "Confirm in Xaman…";
const ROLE_ORDER = ["tenant", "landlord", "arbiter"];

let state = null;
let previousStatus = null;
let deadline = null;        // local time at which the automatic return date is reached
let openForm = null;        // "claim" | "propose" | null
let poller = null;
let acting = false;
const verdicts = new Map(); // evidence id -> {text, match}

/* ---------- loading ---------- */
async function refresh() {
  try {
    show(await get(`/api/leases/${leaseId}/`, { quiet: true }));
    $("[data-offline]").hidden = true;
  } catch (error) {
    if (error.status === 404 || error.status === 409) {
      $("[data-content]").hidden = true;
      $("[data-forbidden]").hidden = false;
      $("[data-forbidden-text]").textContent = error.status === 404
        ? "This deposit isn't visible to your account."
        : error.message;
      $("[data-title]").textContent = "Deposit";
    } else {
      $("[data-offline]").hidden = false;
    }
  }
}

/** Apply a state the server just returned from an action, without waiting for the next poll. */
function show(next) {
  state = next;
  $("[data-forbidden]").hidden = true;
  $("[data-content]").hidden = false;
  render();
}

async function act(button, label, fn) {
  await busy(button, label, async () => {
    acting = true; // polls keep the rest fresh, but must not rebuild the panel under a running action
    try {
      const result = await fn();
      acting = false;
      if (result && result.status) show(result);
      else await refresh();
    } catch (error) {
      acting = false;
      if (!error.code) toast(error instanceof Error ? error.message : "That didn't work.", "error"); // wallet errors; API errors toast themselves
      await refresh();
    } finally {
      acting = false;
    }
  });
}

/* ---------- render ---------- */
function render() {
  const s = state;
  const me = s.me;
  const tenant = s.parties.find((p) => p.role === "tenant") || { name: "the tenant" };
  document.title = `${s.title || "Deposit"} · safeDeposit`;
  $("[data-title]").textContent = s.title || `Deposit of ${money(s.amount, s.currency)}`;
  $("[data-subtitle]").textContent = `${money(s.amount, s.currency_label)} · lease ends ${fmtTime(s.end_at)} · you are the ${s.role}`;
  $("[data-status]").replaceChildren(statusBadge(s.status));

  if (previousStatus && previousStatus !== s.status) {
    const tone = s.status === "FAILED" ? "error" : ["RETURNED", "SETTLED"].includes(s.status) ? "success" : "info";
    toast(s.events[0]?.summary || `Status: ${s.status_label}`, tone);
  }
  previousStatus = s.status;
  // The return date never moves, so a slow (stale) answer can only make it look later: keep the earliest.
  if (typeof s.seconds_to_cancel_after === "number") deadline = Math.min(deadline ?? Infinity, Date.now() + s.seconds_to_cancel_after * 1000);
  else deadline = null;

  renderIfChanged($("[data-outcome]"), [s.status, s.error, s.proposals.find((p) => p.status === "EXECUTED")], (node) => renderOutcome(node, s, tenant));
  renderIfChanged($("[data-parties]"), [s.parties, s.open_roles, me?.party_id], (node) => node.replaceChildren(
    ...s.parties.map((p) => partyCard(p, me)),
    ...s.open_roles.map((role) => el("article", { class: `card party party--${role}` }, el("p", { class: "sc muted", text: ROLE_LABEL[role] }), el("p", { class: "muted", text: "Not accepted yet" })))));
  renderAgreement(s);
  const onLedger = !["PENDING_ACCEPTANCE", "CANCELLED"].includes(s.status) || Boolean(s.settlement);
  $("[data-ledger-cards]").hidden = !onLedger;
  if (onLedger) {
    renderSetup(s, me);
    renderEscrow(s);
    renderIfChanged($("[data-settlement]"), s.settlement, (node) => renderSettlement(node, s));
    $("[data-live-tag]").hidden = !(s.settlement && s.settlement.source === "ledger");
  } else {
    $("[data-setup]").hidden = true;
  }
  renderActions(s, me);
  renderProposals(s);
  renderEvidence(s, me);
  syncEvents($("[data-events]"), s.events);
  $("[data-no-events]").hidden = s.events.length > 0;
  tick();
}

function renderOutcome(node, s, tenant) {
  if (s.status === "RETURNED") {
    node.replaceChildren(el("div", { class: "outcome" },
      el("p", { class: "outcome__mark", "aria-hidden": "true", text: "✓" }),
      el("h2", { text: `Deposit returned to ${tenant.name}` }),
      el("p", { text: `${money(s.amount, s.currency)} went back to the tenant when the lease ended. Nobody had to approve it: the ledger did it.` })));
  } else if (s.status === "SETTLED") {
    const paid = s.proposals.find((p) => p.status === "EXECUTED");
    node.replaceChildren(el("div", { class: "outcome" },
      el("p", { class: "outcome__mark", "aria-hidden": "true", text: "✓" }),
      el("h2", { text: "Deposit settled" }),
      paid && el("p", { text: `Approved by ${paid.signed_by.map((p) => p.name).join(" and ")}.` }),
      paid && splitBar(paid.tenant_amount, paid.landlord_amount, s.currency, s.amount),
      paid && el("p", { class: "small" }, "Payouts: ", paid.executed.map((tx, i) => [i > 0 && ", ", extLink(tx.explorer_url, shortHash(tx.tx_hash), { class: "mono" })]))));
  } else if (s.status === "FAILED") {
    node.replaceChildren(el("div", { class: "outcome outcome--failed" },
      el("p", { class: "outcome__mark", "aria-hidden": "true", text: "!" }),
      el("h2", { text: "This deposit didn't start" }),
      el("p", { text: s.error || "Setup failed." }),
      el("p", { text: "No deposit was locked. You can start again with the same details." }),
      el("p", {}, el("a", { class: "btn btn--primary", href: "/leases/new/", text: "Start again" }))));
  } else if (s.status === "CANCELLED") {
    node.replaceChildren(el("div", { class: "outcome outcome--failed" },
      el("p", { class: "outcome__mark", "aria-hidden": "true", text: "×" }),
      el("h2", { text: "This agreement was cancelled" }),
      el("p", { text: s.error || "It was closed before any deposit was locked." })));
  } else {
    node.replaceChildren();
  }
}

function renderAgreement(s) {
  const card = $("[data-agreement]");
  card.hidden = s.status !== "PENDING_ACCEPTANCE";
  if (card.hidden || acting) return;
  renderIfChanged($("[data-agreement-body]"), [s.agreement, s.invites, s.actions, s.parties, openForm], (node) => {
    const a = s.agreement;
    const arbiter = s.parties.find((p) => p.role === "arbiter");
    const check = (done, text) => el("li", { class: done ? "is-done" : "" }, el("span", { text }));
    const items = el("ul", { class: "checklist" },
      check(s.parties.some((p) => p.role === "tenant"), "Tenant accepted"),
      check(s.parties.some((p) => p.role === "landlord"), "Landlord accepted"),
      check(a.arbiter_accepted, arbiter ? `Arbiter ${arbiter.name} accepted the role` : "Arbiter accepted the role"),
      check(a.tenant_approved_arbiter, "Tenant approved the arbiter"),
      check(a.landlord_approved_arbiter, "Landlord approved the arbiter"));
    const invites = s.invites.map((invite) => el("div", { class: "field" },
      el("span", { class: "label", text: `Invite link for the ${invite.role}` }),
      el("div", { class: "row" }, el("code", { class: "mono small", text: invite.url }), copyButton(invite.url, "Copy link"))));
    const buttons = [];
    if (s.actions.includes("approve_arbiter")) {
      buttons.push(el("button", { type: "button", class: "btn btn--primary", text: `Approve ${arbiter ? arbiter.name : "the arbiter"}`,
        on: { click: (e) => act(e.currentTarget, "Saving…", () => post(`/api/leases/${leaseId}/approve-arbiter/`)) } }));
    }
    if (s.actions.includes("change_arbiter")) {
      buttons.push(el("button", { type: "button", class: "btn", "aria-expanded": String(openForm === "arbiter"), on: { click: () => toggleForm("arbiter") } }, arbiter ? "Propose a different arbiter" : "Name the arbiter by address"));
    }
    if (s.actions.includes("cancel")) {
      buttons.push(el("button", { type: "button", class: "btn btn--ghost", text: "Cancel agreement",
        on: { click: (e) => { if (window.confirm("Cancel this agreement for everyone?")) act(e.currentTarget, "Cancelling…", () => post(`/api/leases/${leaseId}/cancel/`)); } } }));
    }
    let form = null;
    if (openForm === "arbiter" && s.actions.includes("change_arbiter")) {
      const input = el("input", { class: "mono", name: "address", maxlength: "64", placeholder: "r…", spellcheck: "false", autocomplete: "off", "aria-label": "Arbiter's XRP Ledger address" });
      form = el("form", { class: "form mt-4", on: { submit: (e) => {
        e.preventDefault();
        act(e.submitter || e.currentTarget.querySelector("button"), "Saving…", async () => {
          const next = await post(`/api/leases/${leaseId}/arbiter/`, { address: input.value.trim() });
          openForm = null;
          return next;
        });
      } } }, el("p", { class: "small muted", text: "Changing the arbiter resets both approvals and the arbiter's acceptance, and creates a new arbiter invite link." }), input,
        el("div", { class: "actions" }, el("button", { type: "submit", class: "btn btn--primary", text: "Propose this arbiter" })));
    }
    fill(node, items, invites.length > 0 && el("div", { class: "mt-4 stack" }, el("p", { class: "small muted", text: "Send each link to the right person. Each works once." }), ...invites),
      buttons.length > 0 && el("div", { class: "actions mt-4" }, buttons), form);
  });
}

function renderSetup(s, me) {
  if (acting) return;
  const card = $("[data-setup]");
  const visible = ["DRAFT", "SETTLEMENT_READY"].includes(s.status);
  card.hidden = !visible;
  if (!visible) return;
  renderIfChanged($("[data-setup-body]"), [s.status, s.setup_step, s.actions, faucetState, s.events.length], (node) => {
    node.replaceChildren(setupChecklist(s), lockStep(s));
  });
}

/** After setup: the tenant locks the deposit; the faucet state says whether the account can hold and pay it. */
function lockStep(s) {
  if (s.status === "DRAFT") return statusLine("Waiting for the ledger to validate each step (about 20 s in total)…");
  if (!s.actions.includes("lock_deposit")) {
    const tenant = s.parties.find((p) => p.role === "tenant");
    return el("p", { class: "note mt-4", text: `The settlement account is ready. Waiting for ${tenant ? tenant.name : "the tenant"} to lock the deposit.` });
  }
  const box = el("div", { class: "mt-8 stack" },
    el("p", { text: `Lock ${money(s.amount, s.currency)} in an escrow that pays into the settlement account, or back to you after ${fmtTime(s.cancel_after)}.` }));
  const funds = faucetState;
  if (funds && !funds.trust_lines[s.currency]) {
    box.append(el("p", { class: "alert alert--warning", text: `Your account can't hold ${s.currency_label} yet.` }),
      el("button", { type: "button", class: "btn", text: `Add ${s.currency} trust line in Xaman`, on: { click: (event) => walletStep(event.currentTarget, () => addTrustLine(s.currency)) } }));
    return box;
  }
  if (funds && Number(funds.balances[s.currency] || 0) < Number(s.amount)) {
    box.append(el("p", { class: "alert alert--warning" }, `You hold ${money(funds.balances[s.currency] || 0, s.currency)}; the deposit is ${money(s.amount, s.currency)}. Get test tokens on `, el("a", { href: "/leases/", text: "your dashboard" }), "."));
    return box;
  }
  box.append(el("div", { class: "actions" }, el("button", {
    type: "button", class: "btn btn--primary btn--lg",
    on: { click: (event) => act(event.currentTarget, IN_XAMAN, () => lockDeposit(leaseId)) },
  }, "Lock deposit in Xaman")), el("p", { class: "small muted", text: "Signed in your own Xaman app. safeDeposit never sees your key." }));
  return box;
}

let faucetState = null;
async function loadFaucet() {
  try { faucetState = await get("/api/faucet/", { quiet: true }); } catch { faucetState = null; }
}

async function walletStep(button, fn) {
  await busy(button, IN_XAMAN, async () => {
    try {
      await fn();
      await loadFaucet();
      render();
    } catch (error) {
      if (!error.code) toast(error.message, "warning");
    }
  });
}

function renderEscrow(s) {
  $("[data-amount]").replaceChildren(money(s.amount), el("span", { class: "amount__unit", text: s.currency_label }));
  const locked = s.status === "ACTIVE";
  const label = { DRAFT: "Not locked yet", SETTLEMENT_READY: "Not locked yet", ACTIVE: "Locked on the ledger", IN_SETTLEMENT: "Released to settlement", SETTLED: "Released to settlement", RETURNED: "Returned to tenant", FAILED: "Never locked" }[s.status];
  renderIfChanged($("[data-escrow-state]"), [s.status], (node) => node.replaceChildren(el("span", { class: `badge${locked ? " badge--info" : ""}`, text: label })));
  $("[data-countdown-box]").hidden = !locked;
  renderIfChanged($("[data-escrow-details]"), [s.escrow, s.settlement?.address, s.cancel_after], (node) => {
    const rows = [];
    const row = (term, ...value) => rows.push(el("dt", { text: term }), el("dd", {}, ...value));
    if (s.settlement) row("Destination", el("span", { class: "mono small", title: s.settlement.address, text: shortAddress(s.settlement.address) }), " (settlement account)");
    row(locked || !s.escrow ? "Automatic return" : "Return date", fmtTime(s.cancel_after));
    if (s.escrow) {
      row("Escrow", el("span", { class: "mono small", text: `${shortAddress(s.escrow.owner)} #${s.escrow.sequence}` }), " ", extLink(s.escrow.explorer_url, "Explorer", { class: "small" }));
    }
    row("Token", `${s.currency_label}, issued by `, el("span", { class: "mono small", title: s.issuer_address, text: shortAddress(s.issuer_address) }));
    node.replaceChildren(...rows);
  });
}

function renderSettlement(node, s) {
  const st = s.settlement;
  if (!st) {
    node.replaceChildren(el("p", { class: "muted", text: s.status === "FAILED" ? "No settlement account was created." : "Being created on the ledger…" }));
    return;
  }
  if (st.error) {
    node.replaceChildren(el("p", { class: "alert alert--warning", text: st.error }));
    return;
  }
  node.replaceChildren(
    el("ul", { class: "signers" }, [...st.signers].sort((a, b) => ROLE_ORDER.indexOf(a.role) - ROLE_ORDER.indexOf(b.role)).map((signer) => el("li", { class: `role-${signer.role}` },
      el("span", { class: "role-dot", "aria-hidden": "true" }),
      el("strong", { text: signer.name || "Unknown signer" }),
      el("span", { class: "sc muted", text: ROLE_LABEL[signer.role] || "not a party" }),
      el("span", { class: "mono small", title: signer.address, text: shortAddress(signer.address) })))),
    el("dl", { class: "details mt-4" },
      el("dt", { text: "Required" }), el("dd", { text: `${st.quorum} of ${st.signers.length} signatures` }),
      el("dt", { text: "Own key" }), el("dd", {}, st.master_disabled
        ? el("span", { class: "badge badge--success", text: "Disabled" })
        : el("span", { class: "badge badge--danger", text: "Still active" })),
      el("dt", { text: "Holds" }), el("dd", { text: money(st.balance, s.currency) })),
    el("div", { class: "actions mt-4" },
      el("button", { type: "button", class: "btn btn--ghost btn--sm", text: "Inspect ledger data", on: { click: (event) => openInspector(event.currentTarget) } }),
      extLink(st.explorer_url, "Explorer", { class: "small" })));
}

/* ---------- actions ---------- */
function renderActions(s, me) {
  const card = $("[data-actions-card]");
  const relevant = s.actions.filter((a) => !a.startsWith("sign:") && !a.startsWith("retry:") && !["upload_evidence", "lock_deposit", "approve_arbiter", "change_arbiter", "cancel"].includes(a));
  if (acting) return;
  card.hidden = relevant.length === 0;
  if (card.hidden) return;
  renderIfChanged($("[data-actions]"), [relevant, s.status, s.settlement?.balance, me?.party_id, openForm], (node) => {
    const buttons = [];
    if (relevant.includes("release_early")) {
      buttons.push(el("button", {
        type: "button", class: "btn btn--tint-tenant",
        on: { click: (event) => {
          if (!window.confirm("Move out early? The deposit leaves the escrow and goes to the settlement account, where the landlord (or the arbiter) must approve your refund.")) return;
          act(event.currentTarget, "Submitting to the XRPL (about 4 s)", () => releaseDeposit(leaseId, "early_move_out"));
        } },
      }, "I'm moving out early"));
    }
    if (relevant.includes("claim_damage")) {
      buttons.push(el("button", { type: "button", class: "btn btn--danger", "aria-expanded": String(openForm === "claim"), on: { click: () => toggleForm("claim") } }, "Claim damage"));
    }
    if (relevant.includes("propose")) {
      const hasOpen = s.proposals.some((p) => p.status === "OPEN");
      buttons.push(el("button", { type: "button", class: "btn", "aria-expanded": String(openForm === "propose"), on: { click: () => toggleForm("propose") } }, hasOpen ? "Propose a different split" : "Propose a split"));
    }
    if (relevant.includes("share_verify")) {
      const url = `${location.origin}/verify/${leaseId}/`;
      buttons.push(copyButton(url, "Share verification link"), el("a", { class: "small", href: url, target: "_blank", rel: "noopener", text: "Open the public check" }));
    }
    let form = null;
    if (openForm === "claim" && relevant.includes("claim_damage")) {
      form = splitForm({
        total: s.amount, code: s.currency, submitLabel: "Claim damage and propose this split", tenant: s.amount,
        intro: "Claiming moves the deposit out of the escrow into the 2-of-3 settlement account, then proposes your split. You can't take anything alone: the tenant or the arbiter must approve.",
        onCancel: () => toggleForm(null),
        onSubmit: (values, button) => act(button, "Releasing and preparing the split…", async () => {
          await releaseDeposit(leaseId, "damage_claim");
          openForm = null;
          const { proposal_id: proposalId } = await proposeSplit(leaseId, values.tenant, values.landlord, values.reason);
          return approveSplit(proposalId);
        }),
      });
    } else if (openForm === "propose" && relevant.includes("propose") && s.settlement?.balance) {
      form = splitForm({
        total: s.settlement.balance, code: s.currency, submitLabel: "Propose this split",
        intro: `The settlement account holds ${money(s.settlement.balance, s.currency)}. A new proposal replaces the open one; you then approve it in Xaman.`,
        onCancel: () => toggleForm(null),
        onSubmit: (values, button) => act(button, "Preparing the payout…", async () => {
          const { proposal_id: proposalId } = await proposeSplit(leaseId, values.tenant, values.landlord, values.reason);
          openForm = null;
          return approveSplit(proposalId);
        }),
      });
    }
    fill(node, el("div", { class: "actions" }, buttons), form && el("div", { class: "mt-8" }, form));
  });
}

function toggleForm(name) {
  openForm = openForm === name ? null : name;
  render();
}

function renderProposals(s) {
  const section = $("[data-proposals-section]");
  section.hidden = s.proposals.length === 0;
  renderIfChanged($("[data-proposals]"), [s.proposals, s.actions], (node) => {
    node.replaceChildren(...s.proposals.filter((p) => p.status !== "SUPERSEDED" || s.proposals.length === 1).map((proposal) =>
      proposalCard(proposal, s, (button) => act(button, IN_XAMAN, () => approveSplit(proposal.id)), (button) => act(button, "Submitting…", () => retryPayout(proposal.id)))));
  });
}

/* ---------- evidence ---------- */
function renderEvidence(s, me) {
  const canUpload = s.actions.includes("upload_evidence");
  $("[data-evidence-section]").hidden = !canUpload && s.evidence.length === 0;
  $("[data-evidence-form]").hidden = !canUpload;
  renderIfChanged($("[data-gallery]"), [s.evidence, me?.party_id, [...verdicts]], (node) => {
    node.replaceChildren(...s.evidence.map((item) => evidenceCard(item, me)));
  });
}

function evidenceCard(item, me) {
  const verdict = verdicts.get(item.id);
  const check = (tamper) => async (event) => {
    await busy(event.currentTarget, "Reading the ledger…", async () => {
      try {
        const result = await verifyEvidence(item, leaseId, { tamper });
        verdicts.set(item.id, result.match
          ? { match: true, text: `Match. This photo's fingerprint equals the one anchored on the ledger${result.date ? " on " + fmtTime(result.date.toISOString()) : ""}.` }
          : { match: false, text: tamper
            ? `Mismatch, as expected: one byte was changed, so the fingerprint became ${shortHash(result.computed)} instead of ${shortHash(result.anchored || "")}.`
            : "Mismatch. This file is not the one that was anchored on the ledger." });
      } catch (error) {
        verdicts.set(item.id, { match: false, text: `Couldn't check: ${error.message || "the ledger didn't answer"}.` });
      }
      render();
    });
  };
  return el("article", { class: "shot" },
    el("img", { src: item.url, alt: `${item.kind_label} photo uploaded by ${item.uploaded_by}`, loading: "lazy", decoding: "async" }),
    el("div", { class: "shot__body" },
      el("p", {}, el("span", { class: "badge", text: item.kind_label }), ` ${item.uploaded_by}`),
      el("p", { class: "muted", text: fmtTime(item.created_at) }),
      el("p", { class: "mono", title: item.sha256, text: `sha256 ${shortHash(item.sha256)}` }),
      item.anchor_tx_hash
        ? el("p", {}, extLink(item.explorer_url, "Anchored on XRPL"))
        : el("p", { class: "verdict--mismatch", text: "Not anchored yet" }),
      item.anchor_tx_hash && el("div", { class: "actions" },
        el("button", { type: "button", class: "btn btn--sm", text: "Verify", on: { click: check(false) } }),
        el("button", { type: "button", class: "btn btn--ghost btn--sm", text: "Verify an altered copy", title: "Changes one byte of the photo in memory, then checks it", on: { click: check(true) } })),
      !item.anchor_tx_hash && me && item.uploaded_by_id === me.party_id && el("button", {
        type: "button", class: "btn btn--sm", text: "Anchor on the ledger",
        on: { click: (event) => act(event.currentTarget, IN_XAMAN, async () => { await anchorEvidence(item); }) },
      }),
      verdict && el("p", { class: `verdict ${verdict.match ? "verdict--match" : "verdict--mismatch"}`, role: "status", text: verdict.text })));
}

$("[data-evidence-form]").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const file = form.elements.file.files[0];
  if (!file) return toast("Choose a photo first.", "warning");
  if (file.size > 5 * 1024 * 1024) return toast("Photos can be at most 5 MB.", "warning");
  const data = new FormData(form);
  return act(form.querySelector("button[type=submit]"), "Uploading, then confirm in Xaman…", async () => {
    const item = await uploadEvidence(leaseId, data);
    form.reset();
    if (item.anchor_error) toast(`Photo stored, but not anchored yet: ${item.anchor_error}`, "warning");
    else toast("Photo stored and its fingerprint anchored on the ledger.", "success");
  });
});

/* ---------- settlement inspector ---------- */
async function openInspector(button) {
  const dialog = document.querySelector("[data-inspector]");
  const body = dialog.querySelector("[data-inspector-body]");
  await busy(button, "Reading the ledger…", async () => {
    try {
      const data = await get(`/api/leases/${leaseId}/settlement/`);
      const account = data.raw.account_data || {};
      const list = data.raw.signer_lists[0] || {};
      const line = data.raw.lines.find((l) => l.currency === data.currency && l.account === data.issuer_address);
      const fact = (ok, text) => el("li", { class: ok ? "is-done" : "is-failed" }, el("span", { text }));
      body.replaceChildren(
        el("p", { class: "mono small", text: data.address }),
        el("ul", { class: "checklist mt-4" },
          fact(Boolean(account.Flags & 0x00100000), `Own key disabled (lsfDisableMaster ${account.Flags & 0x00100000 ? "set" : "NOT set"})`),
          fact(list.SignerQuorum === 2, `Signer quorum: ${list.SignerQuorum ?? "none"}`),
          fact((list.SignerEntries || []).length === 3, `Signers: ${(list.SignerEntries || []).map((e) => shortAddress(e.SignerEntry.Account)).join(", ") || "none"}`),
          fact(Boolean(line), line ? `Trust line to ${data.currency}: balance ${line.balance}` : `No ${data.currency} trust line`),
          fact(true, `XRP for reserves: ${(Number(account.Balance || 0) / 1e6).toFixed(2)} (ledger ${data.raw.ledger_index})`)),
        el("details", { class: "mt-4" }, el("summary", { text: "Raw JSON" }), el("pre", { class: "raw", tabIndex: 0, text: JSON.stringify(data.raw, null, 2) })),
        el("p", { class: "small mt-4" }, extLink(data.explorer_url, "Open this account in the explorer")));
      dialog.showModal();
    } catch { /* shown by api.js */ }
  });
}

/* ---------- countdown ---------- */
let dueShown = false;

function tick() {
  if (!state || state.status !== "ACTIVE" || deadline === null || !Number.isFinite(deadline)) return;
  const left = Math.max(0, Math.round((deadline - Date.now()) / 1000));
  const node = $("[data-countdown]");
  const label = $("[data-countdown-label]");
  if (left > 0) {
    node.textContent = fmtCountdown(left);
    node.classList.remove("countdown--due");
    label.textContent = "Automatic return in";
    dueShown = false;
  } else if (!dueShown) {
    dueShown = true; // built once, so the button keeps its state between polls
    node.textContent = "due";
    node.classList.add("countdown--due");
    label.textContent = "Return date passed. The bot returns it about 10 s later.";
  }
}

setInterval(tick, 1000);
loadFaucet().then(() => state && render());
poller = poll(refresh, 3000);
