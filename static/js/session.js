/**
 * Who is signed in (GET /api/me/). Fires "me:changed" on document whenever it is loaded.
 */
import { get, post } from "./api.js";

let me = null;
let pending = null;

export function readConfig() {
  const fallback = { network: { id: "testnet", name: "Testnet", is_testnet: true, explorer: "", ws: "" }, xaman_configured: false, signed_in: false, currencies: [] };
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
      me = data.signed_in ? data : null;
      document.dispatchEvent(new CustomEvent("me:changed", { detail: me }));
      return me;
    }).catch((error) => {
      pending = null;
      throw error;
    });
  }
  return pending;
}

export const currentMe = () => me;

export function onMe(callback) {
  document.addEventListener("me:changed", (event) => callback(event.detail));
}

export async function signOut() {
  await post("/api/auth/signout/");
  window.location.assign("/");
}
