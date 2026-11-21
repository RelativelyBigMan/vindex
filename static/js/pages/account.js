/** My account: who I am, my wallet and its balances, and a top-up of test funds. */
import { post } from "../api.js";
import { verifiedBadge } from "../parts.js";
import { config, loadMe } from "../session.js";
import { ROLE_LABEL, busy, copyButton, el, extLink, fill, money, statusLine, toast } from "../ui.js";

const WALLET_KIND = {
  demo: "Testnet wallet held by safeDeposit (it signs for you)",
  gemwallet: "Your own GemWallet (you sign every transaction)",
};

function render(me) {
  const rows = [
    ["Name", me.name],
    ["Role", el("span", { class: `role-${me.role}` }, el("span", { class: "role-dot", "aria-hidden": "true" }), ` ${ROLE_LABEL[me.role]} `, verifiedBadge(me))],
    me.username && ["Username", el("span", { class: "mono", text: me.username }), " ", copyButton(me.username, "Copy")],
    ["Sign-in", me.username ? "Username and password" : "With your wallet"],
  ].filter(Boolean);
  fill(document.querySelector("[data-account]"), rows.map(([term, ...value]) => [el("dt", { text: term }), el("dd", {}, ...value)]));

  const balances = me.balances;
  fill(document.querySelector("[data-wallet-card]"),
    me.funding && statusLine("Your Testnet wallet is being funded: XRP first, then trust lines and test tokens (up to a minute)…"),
    el("p", { class: "mono small", text: me.address }),
    el("div", { class: "row mt-4" }, copyButton(me.address, "Copy address"), extLink(me.explorer_url, "Explorer", { class: "small" })),
    el("p", { class: "small muted mt-4", text: WALLET_KIND[me.wallet_type] || "" }),
    balances
      ? el("dl", { class: "balances" }, Object.entries(balances).map(([code, value]) => el("div", {}, el("dt", { text: code }), el("dd", { text: money(value) }))))
      : !me.funding && el("p", { class: "alert alert--warning mt-4", text: "This wallet isn't on the ledger yet. Use the top-up below." }),
    config.demo_mode && !me.funding && el("div", { class: "actions mt-4" },
      el("button", { type: "button", class: "btn btn--ghost", text: "Top up test funds", on: { click: (event) => topUp(event.currentTarget) } })),
    el("p", { class: "small muted mt-4", text: "Share your username or this address with the other party so they can name you on a deposit." }));
}

async function topUp(button) {
  await busy(button, "Sending test funds (about 15 s)", async () => {
    try {
      const result = await post("/api/demo/fund/");
      toast(result.skipped.length ? `Topped up, except ${result.skipped.join(", ")} (add the trust line in your wallet first).` : "Topped up with 5,000 of each test token.", "success");
      render(await loadMe(true));
    } catch { /* shown by api.js */ }
  });
}

/** A new Testnet wallet is funded in the background: keep reading until the funds have arrived. */
async function watchFunding(tries = 40) {
  for (let i = 0; i < tries; i += 1) {
    let me;
    try { me = await loadMe(true); } catch { return; }
    render(me);
    if (!me.funding) return;
    await new Promise((resolve) => setTimeout(resolve, 3000));
  }
}

watchFunding();
