/**
 * Direct reads from the XRP Ledger in the browser (xrpl.js, self-hosted), used where the point is
 * NOT to trust our server: the public verify page and the evidence check.
 */
import { loadVendor } from "./wallet.js";
import { config } from "./session.js";

/** Run fn(request) against a public server of the configured test network, then disconnect. */
export async function withLedger(fn) {
  await loadVendor("xrpl");
  if (!config.network.ws.startsWith("wss://")) throw new Error("No ledger server is configured.");
  const client = new window.xrpl.Client(config.network.ws, { connectionTimeout: 10000 });
  await client.connect();
  try {
    return await fn(async (request) => (await client.request(request)).result);
  } finally {
    client.disconnect().catch(() => {});
  }
}

/** A ledger_entry / account request that answers null instead of throwing when the object doesn't exist. */
export async function optional(promise) {
  try {
    return await promise;
  } catch (error) {
    const code = error?.data?.error;
    if (code === "entryNotFound" || code === "actNotFound" || code === "txnNotFound") return null;
    throw error;
  }
}

export function hexToText(hex) {
  const bytes = new Uint8Array((hex || "").match(/.{2}/g)?.map((pair) => parseInt(pair, 16)) || []);
  return new TextDecoder().decode(bytes);
}

export function textToHex(text) {
  return [...new TextEncoder().encode(text)].map((b) => b.toString(16).padStart(2, "0")).join("").toUpperCase();
}

export async function sha256Hex(buffer) {
  // Browsers only offer this on https:// addresses and on localhost.
  if (!globalThis.crypto?.subtle) throw new Error("this check needs the site to be opened over https");
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** Seconds since 2000-01-01 UTC -> Date */
export const rippleTime = (seconds) => new Date((seconds + 946684800) * 1000);

/** The JSON of the first memo with this type on a transaction, or null. */
export function readMemo(tx, type) {
  const wanted = textToHex(type);
  for (const entry of tx?.Memos || []) {
    if ((entry.Memo?.MemoType || "").toUpperCase() !== wanted) continue;
    try { return JSON.parse(hexToText(entry.Memo.MemoData)); } catch { return null; }
  }
  return null;
}
