/**
 * Wallet adapter layer.
 *
 * The UI only talks to this interface, never to a specific wallet. Once the
 * escrow mechanism is chosen (e.g. XRPL multisig or an EVM contract), add an
 * adapter per wallet (GemWallet, Crossmark, Xaman, ...) and register it.
 * The rest of the frontend doesn't need to change.
 *
 * Security rules every adapter MUST follow:
 *  - Never request, accept, store or transmit a seed, secret or private key.
 *    Signing happens inside the user's wallet only.
 *  - Never persist anything except the public address.
 *  - Reject when the wallet's network doesn't match config.network.id.
 */

/** @typedef {{ address: string, network: string }} WalletSession */

export class WalletAdapter {
  /** @param {string} id  @param {string} name */
  constructor(id, name) {
    this.id = id;
    this.name = name;
  }

  /** @returns {Promise<boolean>} true if the wallet is installed or reachable. */
  async isAvailable() { return false; }

  /** @returns {Promise<WalletSession>} */
  async connect() { throw new Error(`${this.name}: connect() not implemented`); }

  async disconnect() {}

  /**
   * Ask the wallet to sign (and optionally submit) a transaction.
   * The payload shape is decided by the escrow implementation later.
   * @param {object} _tx
   * @returns {Promise<{ hash?: string, signed?: string }>}
   */
  async sign(_tx) { throw new Error(`${this.name}: sign() not implemented`); }
}

/** @type {Map<string, WalletAdapter>} */
const adapters = new Map();

/** @param {WalletAdapter} adapter */
export function registerAdapter(adapter) {
  if (!(adapter instanceof WalletAdapter)) throw new TypeError("Adapter must extend WalletAdapter");
  adapters.set(adapter.id, adapter);
}

/** @returns {Promise<WalletAdapter[]>} adapters that are actually usable in this browser. */
export async function availableAdapters() {
  const list = [...adapters.values()];
  const checks = await Promise.all(list.map((a) => a.isAvailable().catch(() => false)));
  return list.filter((_, i) => checks[i]);
}

/* ---- Session state (in memory only, cleared on reload) ---- */

/** @type {{ adapter: WalletAdapter | null, session: WalletSession | null }} */
const state = { adapter: null, session: null };

export function currentSession() { return state.session; }
export function currentAdapter() { return state.adapter; }

/**
 * @param {WalletAdapter} adapter
 * @param {string} expectedNetwork
 */
export async function connectWith(adapter, expectedNetwork) {
  const session = await adapter.connect();
  if (expectedNetwork && session.network !== expectedNetwork) {
    await adapter.disconnect().catch(() => {});
    throw new Error(`Wrong network: wallet is on "${session.network}", this site needs "${expectedNetwork}".`);
  }
  state.adapter = adapter;
  state.session = session;
  document.dispatchEvent(new CustomEvent("wallet:connected", { detail: session }));
  return session;
}

export async function disconnect() {
  if (state.adapter) await state.adapter.disconnect().catch(() => {});
  state.adapter = null;
  state.session = null;
  document.dispatchEvent(new CustomEvent("wallet:disconnected"));
}
