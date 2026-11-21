/** Sign in: username and password (Testnet wallet held by the server), or the user's own GemWallet. */
import { post } from "../api.js";
import { signInWithWallet } from "../wallet.js";
import { busy, toast } from "../ui.js";

const root = document.querySelector("[data-login]");
const done = () => location.assign(root.dataset.next || "/leases/");

const form = document.querySelector("[data-login-form]");
form?.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!form.reportValidity()) return;
  await busy(form.querySelector("button[type=submit]"), "Signing in…", async () => {
    try {
      await post("/api/login/", { username: form.elements.username.value.trim().toLowerCase(), password: form.elements.password.value });
      done();
    } catch {
      form.elements.password.value = ""; // the error toast is already up
      form.elements.password.focus();
    }
  });
});

document.querySelector("[data-wallet-login]").addEventListener("click", async (event) => {
  await busy(event.currentTarget, "Waiting for the wallet…", async () => {
    try {
      await signInWithWallet();
      done();
    } catch (error) {
      if (!error.code) toast(error instanceof Error ? error.message : "Could not sign in with the wallet.", "warning");
    }
  });
});
