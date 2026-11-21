/**
 * Public verification page. Everything shown here is read by THIS BROWSER from a public XRP Ledger
 * server over WebSocket (xrpl.js). Our own server only says where to look:
 * GET /api/leases/<id>/public/ returns addresses and the escrow's sequence number, no secrets.
 */
import { get } from "../api.js";
import { hexToText, optional, rippleTime, withLedger } from "../ledger.js";
import { config } from "../session.js";
import { el, extLink, fill, fmtTime, money, shortAddress } from "../ui.js";

const root = document.querySelector("[data-verify-page]");
const leaseId = root.dataset.leaseId;
const $ = (selector) => root.querySelector(selector);
const LSF_DISABLE_MASTER = 0x00100000;
const LSF_ACCEPTED = 0x00010000;

function setStatus(text, spinning = false) {
  fill($("[data-verify-status]"), spinning && el("span", { class: "spinner", "aria-hidden": "true" }), text);
}

function check(state, text, detail) {
  // state: true = holds, false = does not hold, null = not applicable right now
  const cls = state === true ? "is-done" : state === false ? "is-failed" : "is-warn";
  return el("li", { class: cls }, el("span", {}, text, detail && el("span", { class: "small muted", text: ` ${detail}` })));
}

function details(node, rows) {
  node.replaceChildren(...rows.filter(Boolean).flatMap(([term, ...value]) => [el("dt", { text: term }), el("dd", {}, ...value)]));
}

const account = (address, where) => [
  el("span", { class: "mono small", title: address, text: shortAddress(address) }), " ",
  extLink(`${where.explorer}/accounts/${address}`, "Explorer", { class: "small" }),
];

async function readLedger(where) {
  return withLedger(async (request) => {
    const validated = { ledger_index: "validated" };
    const out = { read_at_ledger: null };
    out.ledger = await request({ command: "ledger", ...validated });
    out.read_at_ledger = out.ledger.ledger_index;
    out.escrow = where.escrow_sequence === null ? null
      : await optional(request({ command: "ledger_entry", escrow: { owner: where.escrow_owner, seq: where.escrow_sequence }, ...validated }));
    if (where.settlement_address) {
      out.settlement = await optional(request({ command: "account_info", account: where.settlement_address, signer_lists: true, ...validated }));
      out.settlement_lines = out.settlement && await request({ command: "account_lines", account: where.settlement_address, peer: where.issuer_address, ...validated });
    }
    out.tenant_lines = await optional(request({ command: "account_lines", account: where.tenant_address, peer: where.issuer_address, ...validated }));
    out.arbiter_credentials = await optional(request({ command: "account_objects", account: where.arbiter_address, type: "credential", ...validated }));
    return out;
  });
}

const balanceOf = (lines, code) => lines?.lines?.find((line) => line.currency === code)?.balance ?? "0";

function render(where, data) {
  const escrow = data.escrow?.node || null;
  const root_ = data.settlement?.account_data || null;
  const signerList = (data.settlement?.signer_lists || root_?.signer_lists || [])[0] || null;
  const signers = (signerList?.SignerEntries || []).map((entry) => entry.SignerEntry);
  const expected = [where.tenant_address, where.landlord_address, where.arbiter_address];
  const settlementBalance = balanceOf(data.settlement_lines, where.currency);
  const credential = (data.arbiter_credentials?.account_objects || []).find((object) =>
    object.Subject === where.arbiter_address && object.Issuer === where.credential_registry
    && hexToText(object.CredentialType) === where.arbiter_credential);

  const checks = [];
  if (escrow) {
    const amount = escrow.Amount;
    const token = typeof amount === "object";
    checks.push(check(true, `${token ? money(amount.value, amount.currency) : "XRP"} is locked in an escrow on the ledger.`));
    checks.push(check(escrow.Account === where.tenant_address, "The escrow belongs to the tenant, so an automatic return goes back to the tenant."));
    checks.push(check(escrow.Destination === where.settlement_address, "If released early, it can only go to the settlement account."));
    checks.push(check(token && amount.issuer === where.issuer_address && amount.currency === where.currency, "It is held in the agreed token from the agreed issuer."));
    checks.push(check(Boolean(escrow.Condition) && escrow.FinishAfter === undefined, "Releasing it needs a secret (crypto-condition); no time-based release exists."));
    checks.push(check(Number.isInteger(escrow.CancelAfter), "It returns automatically after the lease end plus the grace period.",
      Number.isInteger(escrow.CancelAfter) ? `(${fmtTime(rippleTime(escrow.CancelAfter).toISOString())})` : ""));
  } else if (where.escrow_sequence === null) {
    checks.push(check(null, "No deposit has been locked for this lease yet."));
  } else if (Number(settlementBalance) > 0) {
    checks.push(check(null, `The escrow was released: ${money(settlementBalance, where.currency)} is now in the settlement account, waiting for 2 of 3 signatures.`));
  } else {
    checks.push(check(null, "The escrow no longer exists: the deposit was returned to the tenant or paid out by a 2-of-3 decision."));
  }
  if (root_) {
    checks.push(check(Boolean(root_.Flags & LSF_DISABLE_MASTER), "The settlement account's own key is disabled: nobody, including safeDeposit, can spend from it alone."));
    checks.push(check(signerList?.SignerQuorum === 2 && signers.length === 3 && signers.every((s) => s.SignerWeight === 1),
      "Spending from it needs 2 of 3 signatures."));
    checks.push(check(signers.length === 3 && expected.every((address) => signers.some((s) => s.Account === address)),
      "The three signers are exactly the tenant, the owner and the arbiter."));
  } else {
    checks.push(check(where.settlement_address ? false : null, where.settlement_address ? "The settlement account wasn't found on the ledger." : "The settlement account is still being set up."));
  }
  checks.push(check(credential ? Boolean(credential.Flags & LSF_ACCEPTED) : false,
    "The arbiter holds an accepted verified-arbiter credential from the mediation registry."));
  if (where.evidence.length) {
    checks.push(check(true, `${where.evidence.length} photo fingerprint${where.evidence.length === 1 ? " is" : "s are"} anchored on the ledger. The photos themselves stay private to the parties.`));
  }
  $("[data-checks]").replaceChildren(...checks);

  details($("[data-v-escrow]"), escrow ? [
    ["Amount", typeof escrow.Amount === "object" ? money(escrow.Amount.value, escrow.Amount.currency) : `${escrow.Amount} drops`],
    ["Owner", ...account(escrow.Account, where)],
    ["Destination", ...account(escrow.Destination, where)],
    ["Returns after", Number.isInteger(escrow.CancelAfter) ? fmtTime(rippleTime(escrow.CancelAfter).toISOString()) : "never"],
    ["Condition", el("span", { class: "mono small", text: `${(escrow.Condition || "none").slice(0, 24)}…` })],
  ] : [
    ["Escrow", "None on the ledger right now"],
    ["Tenant", ...account(where.tenant_address, where)],
    ["Tenant holds", money(balanceOf(data.tenant_lines, where.currency), where.currency)],
  ]);
  details($("[data-v-settlement]"), root_ ? [
    ["Account", ...account(where.settlement_address, where)],
    ["Own key", root_.Flags & LSF_DISABLE_MASTER ? "Disabled" : "ACTIVE"],
    ["Quorum", `${signerList?.SignerQuorum ?? "?"} of ${signers.length}`],
    ...signers.map((s) => [expected.indexOf(s.Account) >= 0 ? ["Tenant", "Owner", "Arbiter"][expected.indexOf(s.Account)] : "Unknown signer", ...account(s.Account, where)]),
    ["Holds", money(settlementBalance, where.currency)],
  ] : [["Account", "Not on the ledger yet"]]);

  $("[data-raw]").textContent = JSON.stringify({ asked_for: where, ledger_answers: data }, null, 2);
  const failed = checks.filter((item) => item.classList.contains("is-failed")).length;
  setStatus(failed
    ? `${failed} check${failed === 1 ? "" : "s"} did not hold. Read from ledger ${data.read_at_ledger} on ${config.network.name}.`
    : `All checks hold. Read from ledger ${data.read_at_ledger} on ${config.network.name} at ${new Date().toLocaleTimeString()}.`);
}

async function run() {
  const again = $("[data-recheck]");
  again.hidden = true;
  setStatus("Connecting to the ledger…", true);
  try {
    const where = await get(`/api/leases/${leaseId}/public/`, { quiet: true });
    render(where, await readLedger(where));
  } catch (error) {
    setStatus(`The ledger couldn't be read: ${error.message || "connection failed"}.`);
    $("[data-checks]").replaceChildren(check(null, "Nothing could be verified. Try again in a moment."));
  }
  again.hidden = false;
}

$("[data-recheck]").addEventListener("click", run);
run();
