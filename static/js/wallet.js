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
  constructor() { super("demo", "Testnet wallet held by safeDeposit"); }
  async isAvailable() { return Boolean(config.demo_mode); }
  async connect() { return (await loadMe()).address; }
}

export class GemWallet extends WalletAdapter {
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

  async connect() {
    const sdk = await this.sdk();
    return GemWallet.result(await sdk.getAddress(), "Sharing the address").address;
  }

  async getNetwork() {
    const sdk = await this.sdk();
    return String(GemWallet.result(await sdk.getNetwork(), "Reading the network").network || "");
  }

  async signAndSubmit(tx) {
    const sdk = await this.sdk();
    // The wallet adds its own signature fields; ours are placeholders from the server's autofill.
    const { SigningPubKey: _key, TxnSignature: _signature, ...transaction } = tx;
    const result = GemWallet.result(await sdk.submitTransaction({ transaction }), "The transaction");
    if (!result.hash) throw new WalletError("GemWallet didn't return a transaction hash.");
    return { hash: result.hash };
  }

  async signMessage(message) {
    const sdk = await this.sdk();
    const key = GemWallet.result(await sdk.getPublicKey(), "Sharing the public key");
    const signed = GemWallet.result(await sdk.signMessage(message), "Signing in");
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
 * Ask GemWallet for its address and one signature over the server's one-time challenge.
 * @returns {Promise<{address: string, public_key: string, signature: string, registered: boolean}>}
 */
export async function walletProof({ mustBeRegistered = null } = {}) {
  const wallet = adapters.get("gemwallet");
  if (!(await wallet.isAvailable())) {
    throw new WalletError("GemWallet wasn't found in this browser. Install the extension and set it to " + config.network.name + ".");
  }
  const address = await wallet.connect();
  assertNetwork(await wallet.getNetwork());
  const challenge = await get(`/api/wallet/challenge/?address=${encodeURIComponent(address)}`);
  // Checked before the signature popup, so nobody signs for nothing.
  if (mustBeRegistered === true && !challenge.registered) throw new WalletError("No account uses this wallet yet. Create an account first.");
  if (mustBeRegistered === false && challenge.registered) throw new WalletError("This wallet already has an account. Sign in instead.");
  const proof = await wallet.signMessage(challenge.message);
  if (proof.address !== address) throw new WalletError("The wallet switched accounts during sign-in. Try again.");
  return { address, public_key: proof.publicKey, signature: proof.signature, registered: challenge.registered };
}

/** Sign in to an existing account with the wallet it was registered with. */
export async function signInWithWallet() {
  const { address, public_key, signature } = await walletProof({ mustBeRegistered: true });
  await post("/api/wallet/link/", { address, public_key, signature });
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
  assertNetwork(await wallet.getNetwork());
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
