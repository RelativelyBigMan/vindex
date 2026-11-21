/**
 * Lease actions. Every signature happens in the user's Xaman app; the server builds the
 * transaction, Xaman signs it, and the server checks the result on the ledger.
 */
import { api, post } from "./api.js";
import { readMemo, sha256Hex, withLedger, optional } from "./ledger.js";
import { loadMe } from "./session.js";
import { signSteps, signWithXaman } from "./xaman.js";

export const EVIDENCE_MEMO = "vindex/evidence";

export async function signIn() {
  const done = await signWithXaman(() => post("/api/auth/signin/"), { title: "Sign in with Xaman" });
  await loadMe(true);
  return done;
}

export async function lockDeposit(leaseId) {
  await signWithXaman(() => post(`/api/leases/${leaseId}/escrow/`), { title: "Lock the deposit" });
}

/** @param {"early_move_out"|"damage_claim"} reason  The bot submits the release; no signature needed. */
export function releaseDeposit(leaseId, reason) {
  return post(`/api/leases/${leaseId}/release/`, { reason });
}

export async function proposeSplit(leaseId, tenantAmount, landlordAmount, reason) {
  const result = await post(`/api/leases/${leaseId}/proposals/`, { tenant_amount: tenantAmount, landlord_amount: landlordAmount, reason });
  return result;
}

export async function approveSplit(proposalId) {
  return (await signSteps(`/api/proposals/${proposalId}/sign/`, { title: "Approve the split" })).lease;
}

export async function retryPayout(proposalId) {
  return (await post(`/api/proposals/${proposalId}/retry/`)).lease;
}

export async function addTrustLine(currency) {
  await signWithXaman(() => post("/api/faucet/trustline/", { currency }), { title: `Add the ${currency} trust line` });
}

export async function uploadEvidence(leaseId, form) {
  const item = await api(`/api/leases/${leaseId}/evidence/`, { method: "POST", form });
  return anchorEvidence(item, { quiet: true }).then(() => item, (error) => ({ ...item, anchor_error: error.message }));
}

export async function anchorEvidence(item, { quiet = false } = {}) {
  await signWithXaman(() => post(`/api/evidence/${item.id}/anchor/`, {}, { quiet }), { title: "Anchor the photo" });
}

/**
 * Check a photo against the ledger without trusting our database: hash the image bytes in this
 * browser and compare with the fingerprint in the anchoring transaction, read straight from the ledger.
 * `tamper` flips one byte first, to show what a swapped photo looks like.
 * @returns {Promise<{match: boolean, computed: string, anchored: string|null, validated: boolean, date: Date|null}>}
 */
export async function verifyEvidence(item, leaseId, { tamper = false } = {}) {
  const response = await fetch(item.url, { credentials: "same-origin" });
  if (!response.ok) throw new Error("The photo couldn't be loaded.");
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (tamper && bytes.length) bytes[bytes.length - 1] ^= 0x01;
  const computed = await sha256Hex(bytes);
  const tx = await withLedger((request) => optional(request({ command: "tx", transaction: item.anchor_tx_hash })));
  const memo = tx ? readMemo(tx.tx_json || tx, EVIDENCE_MEMO) : null;
  const validated = Boolean(tx?.validated) && (tx?.meta?.TransactionResult || "tesSUCCESS") === "tesSUCCESS";
  const anchored = memo?.sha256 || null;
  return {
    match: validated && anchored === computed && memo?.lease === leaseId,
    computed, anchored, validated,
    date: tx?.close_time_iso ? new Date(tx.close_time_iso) : null,
  };
}
