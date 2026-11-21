/**
 * Wallet adapter layer. The UI talks to this interface, never to a specific wallet.
 *
 *   isAvailable() -> boolean         connect() -> address          getNetwork() -> "Testnet" | …
 *   signAndSubmit(txJson) -> {hash}  signMessage(text) -> {signature, publicKey, address}
 *
 * Adapters: DemoWallet (the server signs with the persona's Testnet key through the demo
 * endpoints) and GemWallet (browser extension, via the self-hosted @gemwallet/api build).
 *
 * Security rules every adapter follows:
 *  - Never request, accept, store or transmit a seed, secret or private key.
 *    Signing happens inside the user's wallet only.
 *  - Nothing is persisted in the browser; the server session remembers the linked address.
 *  - Refuse when the wallet's network isn't the one this site runs on.
 */
import { get, post } from "./api.js";
import { config, currentMe, loadMe } from "./session.js";

const VENDOR = {
  gemwallet: "vendor/gemwallet-api-3.8.0.js",
  xrpl: "vendor/xrpl-5.3.0.min.js",
};

const loading = new Map();

/** Load a self-hosted classic script once (CSP: script-src 'self'). */
export function loadVendor(name) {
  if (!loading.has(name)) {
    loading.set(name, new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = (document.body.dataset.static || "/static/") + VENDOR[name];
      script.onload = () => resolve();
      script.onerror = () => {
        loading.delete(name);
        reject(new Error(`Couldn't load ${VENDOR[name]}. Run scripts/fetch_vendor.py on the server.`));
      };
      document.head.append(script);
    }));
  }
  return loading.get(name);
}

export class WalletError extends Error {}

export class WalletAdapter {
  constructor(id, name) {
    this.id = id;
    this.name = name;
  }
  async isAvailable() { return false; }
  /** @returns {Promise<string>} the wallet's classic address */
  async connect() { throw new WalletError(`${this.name} can't connect.`); }
  async getNetwork() { return config.network.name; }
  /** @returns {Promise<{hash: string}>} */
  async signAndSubmit(_tx) { throw new WalletError(`${this.name} can't sign transactions in the browser.`); }
  async signMessage(_message) { throw new WalletError(`${this.name} can't sign messages.`); }
}

/** Demo personas: keys live on the server (Testnet only), so pages call the demo endpoints instead of signing here. */
export class DemoWallet extends WalletAdapter {
  constructor() { super("demo", "Demo persona"); }
  async isAvailable() { return Boolean(config.demo_mode); }
  async connect() { return (await loadMe()).address; }
}

export class GemWallet extends WalletAdapter {
  /** Time for the previous popup window to close before the next request opens one. */
  static GAP_MS = 700;
  static lastClosed = 0;

  constructor() { super("gemwallet", "GemWallet"); }

  async sdk() {
    await loadVendor("gemwallet");
    if (!window.GemWalletApi) throw new WalletError("The GemWallet API didn't load.");
    return window.GemWalletApi;
  }

  async isAvailable() {
    try {
      const response = await (await this.sdk()).isInstalled();
      return Boolean(response?.result?.isInstalled);
    } catch {
      return false;
    }
  }

  static result(response, what) {
    if (!response || response.type === "reject" || !response.result) {
      throw new WalletError(`${what} was declined in GemWallet.`);
    }
    return response.result;
  }

  /**
   * Every GemWallet request opens its own popup window. When a popup closes, the extension
   * rejects whichever request is current at that moment (chrome.windows.onRemoved ->
   * buildRejectMessage), so a request sent while the previous popup is still closing comes
   * back as a "reject" the user never chose. Space requests out, and retry read-only ones.
   */
  async request(call, what, { retry = false } = {}) {
    const sdk = await this.sdk();
    const attempts = retry ? 3 : 1;
    for (let attempt = 1; ; attempt += 1) {
      const wait = GemWallet.lastClosed + GemWallet.GAP_MS - Date.now();
      if (wait > 0) await new Promise((resolve) => setTimeout(resolve, wait));
      let response;
      try {
        response = await call(sdk);
      } finally {
        GemWallet.lastClosed = Date.now();
      }
      if (response?.type !== "reject" && response?.result) return response.result;
      if (attempt >= attempts) return GemWallet.result(response, what);
    }
  }

  async connect() {
    return (await this.request((sdk) => sdk.getAddress(), "Sharing the address", { retry: true })).address;
  }

  async getNetwork() {
    const result = await this.request((sdk) => sdk.getNetwork(), "Reading the network", { retry: true });
    return String(result.network || "");
  }

  async signAndSubmit(tx) {
    // The wallet adds its own signature fields; ours are placeholders from the server's autofill.
    const { SigningPubKey: _key, TxnSignature: _signature, ...transaction } = tx;
    const result = await this.request((sdk) => sdk.submitTransaction({ transaction }), "The transaction");
    if (!result.hash) throw new WalletError("GemWallet didn't return a transaction hash.");
    return { hash: result.hash };
  }

  async signMessage(message) {
    const key = await this.request((sdk) => sdk.getPublicKey(), "Sharing the public key", { retry: true });
    const signed = await this.request((sdk) => sdk.signMessage(message), "Signing in");
    return { signature: signed.signedMessage, publicKey: key.publicKey, address: key.address };
  }
}

const adapters = new Map([["demo", new DemoWallet()], ["gemwallet", new GemWallet()]]);
export const adapter = (id) => adapters.get(id);

function assertNetwork(walletNetwork) {
  if (String(walletNetwork).toLowerCase() !== String(config.network.name).toLowerCase()) {
    throw new WalletError(`Your wallet is on "${walletNetwork || "another network"}". Switch it to ${config.network.name} and try again.`);
  }
}

/**
 * Check the wallet is on this site's network. GemWallet answers getNetwork from a hidden 1x1
 * popup; in some browsers that window is closed before it replies and the API returns a bare
 * "reject". Then fall back to the ledger itself: the address must exist on this network.
 * This is a convenience check only: the server verifies every wallet transaction on its own network.
 */
async function checkNetwork(wallet, address) {
  let network;
  try {
    network = await wallet.getNetwork();
  } catch (error) {
    if (!(error instanceof WalletError)) throw error;
    const { withLedger, optional } = await import("./ledger.js");
    const account = await withLedger((request) => optional(request({ command: "account_info", account: address, ledger_index: "validated" })));
    if (!account) {
      throw new WalletError(`GemWallet didn't tell us its network, and ${address} has no account on ${config.network.name}. Switch GemWallet to ${config.network.name} (and fund the account from the faucet), then try again.`);
    }
    return;
  }
  assertNetwork(network);
}

/**
 * Connect GemWallet and prove ownership of its address to the server with one signed challenge.
 * @param {(known: string|null) => Promise<string|null>} askRole  called for a wallet the server hasn't seen
 */
export async function connectBrowserWallet(askRole) {
  const wallet = adapters.get("gemwallet");
  if (!(await wallet.isAvailable())) {
    throw new WalletError("GemWallet wasn't found in this browser. Install the extension, or keep using the demo personas.");
  }
  const address = await wallet.connect();
  await checkNetwork(wallet, address);
  const challenge = await get(`/api/wallet/challenge/?address=${encodeURIComponent(address)}`);
  let role = challenge.known_role;
  if (!role) {
    role = await askRole(null);
    if (!role) throw new WalletError("No role chosen; the wallet wasn't connected.");
  }
  const proof = await wallet.signMessage(challenge.message);
  if (proof.address !== address) throw new WalletError("The wallet switched accounts during sign-in. Try again.");
  await post("/api/wallet/link/", { address, role, public_key: proof.publicKey, signature: proof.signature });
  return loadMe(true);
}

export async function disconnectBrowserWallet() {
  await post("/api/wallet/unlink/");
  return loadMe(true);
}

/** The adapter for whoever this session acts as, checked against the live wallet for browser wallets. */
export async function walletFor(me = currentMe()) {
  if (!me || me.wallet_type === "demo") return adapters.get("demo");
  const wallet = adapters.get("gemwallet");
  if (!(await wallet.isAvailable())) throw new WalletError("GemWallet isn't available in this browser.");
  const address = await wallet.connect();
  if (address !== me.address) {
    throw new WalletError("GemWallet is on a different account than the one you connected. Switch accounts in the wallet, or reconnect.");
  }
  await checkNetwork(wallet, address);
  return wallet;
}

/** After the wallet submitted a transaction, the server needs a few seconds until it is validated. */
export async function registerWhenValidated(send, tries = 8) {
  let last;
  for (let attempt = 0; attempt < tries; attempt += 1) {
    try {
      return await send();
    } catch (error) {
      last = error;
      if (error.status !== 400) break;
      await new Promise((resolve) => setTimeout(resolve, 2500));
    }
  }
  throw last;
}
