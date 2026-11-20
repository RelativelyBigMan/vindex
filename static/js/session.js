/**
 * Who this browser is acting as: a demo persona or a linked wallet (GET /api/me/).
 * Fires "persona:changed" on document whenever it is loaded or switched.
 */
import { get, post } from "./api.js";

let me = null;
let pending = null;

export function readConfig() {
  const fallback = { network: { id: "testnet", name: "Testnet", is_testnet: true, explorer: "", ws: "" }, demo_mode: false, currencies: [] };
  try {
    const parsed = JSON.parse(document.getElementById("escrow-config")?.textContent || "null");
    return parsed && typeof parsed === "object" ? { ...fallback, ...parsed } : fallback;
  } catch {
    return fallback;
  }
}
export const config = readConfig();

export function loadMe(force = false) {
  if (!pending || force) {
    pending = get("/api/me/", { quiet: true }).then((data) => {
      me = data;
      document.dispatchEvent(new CustomEvent("persona:changed", { detail: data }));
      return data;
    }).catch((error) => {
      pending = null;
      me = null;
      document.dispatchEvent(new CustomEvent("persona:changed", { detail: null }));
      throw error;
    });
  }
  return pending;
}

export const currentMe = () => me;

export async function actAs(partyId) {
  await post("/api/demo/persona/", { party_id: partyId });
  return loadMe(true);
}

export function onPersona(callback) {
  document.addEventListener("persona:changed", (event) => callback(event.detail));
}
