/**
 * Create an account: name, surname(s), role and a wallet.
 * - "testnet": the server creates and funds a test wallet; sign-in is by username and password.
 * - "gemwallet": the user's own wallet, proved with one signature. No key is ever typed or sent.
 */
import { post } from "../api.js";
import { walletProof } from "../wallet.js";
import { busy, toast } from "../ui.js";

const form = document.querySelector("[data-register-form]");
const $ = (selector) => form.querySelector(selector);

function walletKind() {
  return form.elements.wallet.value || form.elements.wallet[0]?.value;
}

function onWalletChoice() {
  const testnet = walletKind() === "testnet";
  $("[data-testnet-fields]").hidden = !testnet;
  $("[data-gemwallet-note]").hidden = testnet;
  form.elements.username.required = testnet;
  form.elements.password.required = testnet;
  $("[data-submit]").textContent = testnet ? "Create account" : "Connect GemWallet and create account";
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!form.reportValidity()) return;
  const testnet = walletKind() === "testnet";
  const body = {
    first_name: form.elements.first_name.value,
    last_name: form.elements.last_name.value,
    role: form.elements.role.value,
    accept_terms: form.elements.accept_terms.checked,
    wallet: walletKind(),
  };
  await busy($("[data-submit]"), testnet ? "Creating your account…" : "Waiting for the wallet…", async () => {
    try {
      if (testnet) {
        body.username = form.elements.username.value.trim().toLowerCase();
        body.password = form.elements.password.value;
      } else {
        Object.assign(body, await walletProof({ mustBeRegistered: false }));
        delete body.registered;
      }
      await post("/api/register/", body);
      form.elements.password.value = "";
      // A new Testnet wallet is funded in the background; the account page shows it arriving.
      location.assign(!testnet && form.dataset.next && form.dataset.next !== "/leases/" ? form.dataset.next : "/account/");
    } catch (error) {
      if (!error.code) toast(error instanceof Error ? error.message : "Could not create the account.", "warning");
    }
  });
});

form.elements.wallet.forEach?.((radio) => radio.addEventListener("change", onWalletChoice));
form.elements.username.addEventListener("input", (event) => {
  event.target.value = event.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "");
});
onWalletChoice();
