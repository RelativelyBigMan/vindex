/**
 * Lease actions. Each one works for both kinds of identity:
 *  - a demo persona: the server signs with the persona's Testnet key ({mode: "demo"});
 *  - a browser wallet: the server builds the unsigned transaction, the wallet signs and submits it,
 *    and the server then checks the validated transaction on the ledger ({mode: "wallet", tx_hash}).
 */
import { api, get, post } from "./api.js";
import { readMemo, sha256Hex, withLedger, optional } from "./ledger.js";
import { config, currentMe } from "./session.js";
import { registerWhenValidated, walletFor } from "./wallet.js";

const isDemo = (me) => !me || me.wallet_type === "demo";
export const EVIDENCE_MEMO = "safedeposit/evidence";

export async function lockDeposit(leaseId, me = currentMe()) {
  if (isDemo(me)) return post(`/api/leases/${leaseId}/escrow/`, { mode: "demo" });
  const wallet = await walletFor(me);
  const { tx } = await get(`/api/leases/${leaseId}/escrow-tx/`);
  const { hash } = await wallet.signAndSubmit(tx);
  return registerWhenValidated(() => post(`/api/leases/${leaseId}/escrow/`, { mode: "wallet", tx_hash: hash }, { quiet: true }));
}

/** @param {"early_move_out"|"damage_claim"} reason */
export async function releaseDeposit(leaseId, reason, me = currentMe()) {
  if (isDemo(me)) return post(`/api/leases/${leaseId}/release/`, { reason, mode: "demo" });
  const wallet = await walletFor(me);
  // This transaction carries the escrow's release secret. It goes from our server straight into the wallet.
  const { tx } = await get(`/api/leases/${leaseId}/release-tx/`);
  const { hash } = await wallet.signAndSubmit(tx);
  return registerWhenValidated(() => post(`/api/leases/${leaseId}/release/`, { reason, mode: "wallet", tx_hash: hash }, { quiet: true }));
}

export async function proposeSplit(leaseId, tenantAmount, landlordAmount, reason) {
  const result = await post(`/api/leases/${leaseId}/proposals/`, { tenant_amount: tenantAmount, landlord_amount: landlordAmount, reason });
  return result.lease;
}

export async function approveSplit(proposalId) {
  return (await post(`/api/proposals/${proposalId}/sign/`)).lease;
}

/** The wallet adds a trust line for the deposit token (needed before it can hold or lock it). */
export async function addTrustLine(currency, me = currentMe()) {
  const wallet = await walletFor(me);
  const issuer = config.currencies.find((c) => c.code === currency)?.issuer;
  if (!issuer) throw new Error("Unknown token.");
  return wallet.signAndSubmit({
    TransactionType: "TrustSet", Account: me.address,
    LimitAmount: { currency, issuer, value: "1000000000" },
  });
}

export async function uploadEvidence(leaseId, form, me = currentMe()) {
  let item = await api(`/api/leases/${leaseId}/evidence/`, { method: "POST", form });
  if (!item.anchor_tx_hash && !isDemo(me)) item = await anchorEvidence(item, me);
  return item;
}

export async function anchorEvidence(item, me = currentMe()) {
  if (isDemo(me)) return post(`/api/evidence/${item.id}/anchor/`, { mode: "demo" });
  const wallet = await walletFor(me);
  const { tx } = await get(`/api/evidence/${item.id}/anchor-tx/`);
  const { hash } = await wallet.signAndSubmit(tx);
  return registerWhenValidated(() => post(`/api/evidence/${item.id}/anchor/`, { mode: "wallet", tx_hash: hash }, { quiet: true }));
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
