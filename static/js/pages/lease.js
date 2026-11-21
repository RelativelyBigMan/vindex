/**
 * Lease detail: the main demo screen. Polls GET /api/leases/<id>/ every 3 s and renders it.
 * What the current persona may do comes ONLY from the API's `actions` list.
 */
import { anchorEvidence, approveSplit, addTrustLine, lockDeposit, proposeSplit, releaseDeposit, uploadEvidence, verifyEvidence } from "../actions.js";
import { get, post } from "../api.js";
import { runBot } from "../app.js";
import { partyCard, proposalCard, setupChecklist, splitBar, syncEvents } from "../parts.js";
import { poll } from "../poll.js";
import { config, currentMe, loadMe, onPersona } from "../session.js";
import { splitForm } from "../splitform.js";
import { ROLE_LABEL, busy, copyButton, el, extLink, fill, fmtCountdown, fmtTime, money, renderIfChanged, shortAddress, shortHash, statusBadge, statusLine, toast } from "../ui.js";

const root = document.querySelector("[data-lease-page]");
const leaseId = root.dataset.leaseId;
const $ = (selector) => root.querySelector(selector);
const SUBMITTING = "Submitting to the XRPL (about 4 s)";
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
        ? "This deposit isn't yours to see: your account is not its tenant, owner or arbiter."
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
  const tenant = s.parties[0];
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
  deadline = Math.min(deadline ?? Infinity, Date.now() + s.seconds_to_cancel_after * 1000);

  renderIfChanged($("[data-outcome]"), [s.status, s.error, s.proposals.find((p) => p.status === "EXECUTED")], (node) => renderOutcome(node, s, tenant));
  renderIfChanged($("[data-parties]"), [s.parties, me?.party_id], (node) => node.replaceChildren(...s.parties.map((p) => partyCard(p, me))));
  renderSetup(s, me);
  renderEscrow(s);
  renderIfChanged($("[data-settlement]"), s.settlement, (node) => renderSettlement(node, s));
  $("[data-live-tag]").hidden = !(s.settlement && s.settlement.source === "ledger");
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
  } else {
    node.replaceChildren();
  }
}

function renderSetup(s, me) {
  if (acting) return;
  const card = $("[data-setup]");
  const visible = ["DRAFT", "SETTLEMENT_READY"].includes(s.status);
  card.hidden = !visible;
  if (!visible) return;
  renderIfChanged($("[data-setup-body]"), [s.status, s.setup_step, s.actions, currentMe()?.balances, s.events.length], (node) => {
    node.replaceChildren(setupChecklist(s), lockStep(s, me));
  });
}

/** After setup: the tenant locks the deposit. A browser wallet first needs the trust line and the tokens. */
function lockStep(s, me) {
  if (s.status === "DRAFT") return statusLine("Waiting for the ledger to validate each step (about 20 s in total)…");
  if (!s.actions.includes("lock_deposit")) return el("p", { class: "note mt-4", text: `The settlement account is ready. Waiting for ${s.parties[0].name} (tenant) to lock the deposit.` });
  const full = currentMe();
  const lock = el("button", {
    type: "button", class: "btn btn--primary btn--lg",
    on: { click: (event) => act(event.currentTarget, me.can_sign ? SUBMITTING : "Confirm in your wallet…", () => lockDeposit(leaseId, me)) },
  }, "Sign & lock deposit");
  const box = el("div", { class: "mt-8 stack" },
    el("p", { text: `Lock ${money(s.amount, s.currency)} in an escrow that pays into the settlement account, or back to you after ${fmtTime(s.cancel_after)}.` }));
  if (!me.can_sign && full && full.party_id === me.party_id && full.balances) {
    // Wallet signing (Patch 5): check the wallet can hold and pay the deposit token before asking it to sign.
    const held = full.balances[s.currency];
    if (held === undefined) {
      box.append(el("p", { class: "alert alert--warning", text: `Your wallet has no trust line for ${s.currency_label} yet, so it can't hold the deposit token.` }),
        el("button", { type: "button", class: "btn", text: `Add ${s.currency} trust line in wallet`, on: { click: (event) => walletStep(event.currentTarget, () => addTrustLine(s.currency, me)) } }));
      return box;
    }
    if (Number(held) < Number(s.amount)) {
      box.append(el("p", { class: "alert alert--warning", text: `Your wallet holds ${money(held, s.currency)}; the deposit is ${money(s.amount, s.currency)}.` }),
        config.demo_mode && el("button", { type: "button", class: "btn", text: "Top up test tokens", on: { click: (event) => walletStep(event.currentTarget, () => post("/api/demo/fund/")) } }));
      return box;
    }
  }
  box.append(el("div", { class: "actions" }, lock),
    el("p", { class: "small muted", text: me.can_sign ? "Signed by safeDeposit with your Testnet wallet's key." : "Signed in your own wallet. safeDeposit never sees your key." }));
  return box;
}

async function walletStep(button, fn) {
  await busy(button, "Waiting for the ledger…", async () => {
    try {
      await fn();
      await new Promise((resolve) => setTimeout(resolve, 4000)); // let the ledger validate it
      await loadMe(true);
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
  const relevant = s.actions.filter((a) => !a.startsWith("sign:") && a !== "upload_evidence" && a !== "lock_deposit");
  if (acting) return;
  card.hidden = relevant.length === 0;
  if (card.hidden) return;
  renderIfChanged($("[data-actions]"), [relevant, s.status, s.settlement?.balance, me?.party_id, openForm], (node) => {
    const confirmLabel = me.can_sign ? SUBMITTING : "Confirm in your wallet…";
    const buttons = [];
    if (relevant.includes("release_early")) {
      buttons.push(el("button", {
        type: "button", class: "btn btn--tint-tenant",
        on: { click: (event) => {
          if (!window.confirm("Move out early? The deposit leaves the escrow and goes to the settlement account, where the owner (or the arbiter) must approve your refund.")) return;
          act(event.currentTarget, confirmLabel, () => releaseDeposit(leaseId, "early_move_out", me));
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
        onSubmit: (values, button) => act(button, confirmLabel, async () => {
          await releaseDeposit(leaseId, "damage_claim", me);
          openForm = null;
          return proposeSplit(leaseId, values.tenant, values.landlord, values.reason);
        }),
      });
    } else if (openForm === "propose" && relevant.includes("propose") && s.settlement?.balance) {
      form = splitForm({
        total: s.settlement.balance, code: s.currency, submitLabel: "Propose this split",
        intro: `The settlement account holds ${money(s.settlement.balance, s.currency)}. A new proposal replaces the open one${me.can_sign ? " and counts as your approval" : ""}.`,
        onCancel: () => toggleForm(null),
        onSubmit: (values, button) => act(button, "Preparing the payout…", async () => {
          const next = await proposeSplit(leaseId, values.tenant, values.landlord, values.reason);
          openForm = null;
          return next;
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
      proposalCard(proposal, s, (button) => act(button, SUBMITTING, () => approveSplit(proposal.id)))));
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
        el("button", { type: "button", class: "btn btn--ghost btn--sm", text: "Verify an altered copy", title: "Demo: changes one byte of the photo in memory, then checks it", on: { click: check(true) } })),
      !item.anchor_tx_hash && me && item.uploaded_by_id === me.party_id && el("button", {
        type: "button", class: "btn btn--sm", text: "Anchor on the ledger",
        on: { click: (event) => act(event.currentTarget, SUBMITTING, async () => { await anchorEvidence(item, me); }) },
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
  return act(form.querySelector("button[type=submit]"), "Uploading and anchoring (about 5 s)", async () => {
    const item = await uploadEvidence(leaseId, data, state.me);
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
    fill(label, "Return date passed. The bot returns it about 10 s later. ",
      config.demo_mode && el("button", { type: "button", class: "btn btn--ghost btn--sm", text: "Run bot now", on: { click: (event) => runBot(event.currentTarget) } }));
  }
}

setInterval(tick, 1000);
onPersona(() => poller?.now());
document.addEventListener("bot:ran", () => poller?.now());
poller = poll(refresh, 3000);
