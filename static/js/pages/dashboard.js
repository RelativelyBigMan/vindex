/** My deposits: agreements grouped by stage, plus the Testnet faucet card. */
import { addTrustLine } from "../actions.js";
import { get, post } from "../api.js";
import { poll } from "../poll.js";
import { config, onMe } from "../session.js";
import { ROLE_LABEL, busy, el, fill, fmtTime, money, renderIfChanged, statusBadge, toast } from "../ui.js";

const $ = (selector) => document.querySelector(selector);
const GROUPS = [
  ["Waiting for acceptance", ["PENDING_ACCEPTANCE", "DRAFT", "SETTLEMENT_READY"]],
  ["Active", ["ACTIVE"]],
  ["In settlement", ["IN_SETTLEMENT"]],
  ["Closed", ["RETURNED", "SETTLED", "FAILED", "CANCELLED"]],
];
let poller = null;

function show(name) {
  for (const key of ["loading", "groups", "empty", "load-error"]) $(`[data-${key}]`).hidden = key !== name;
}

function row(lease) {
  const others = lease.parties.filter((p) => p.role !== lease.role).map((p) => `${p.name} (${p.role})`);
  if (lease.open_roles.length) others.push(`waiting for ${lease.open_roles.join(" and ")}`);
  return el("tr", {},
    el("td", {}, el("a", { href: `/leases/${lease.id}/`, text: lease.title || `Deposit ${lease.id.slice(0, 8)}` }),
      el("div", { class: "small muted", text: others.join(" · ") })),
    el("td", { text: ROLE_LABEL[lease.role] || lease.role }),
    el("td", {}, el("time", { dateTime: lease.cancel_after, text: fmtTime(lease.cancel_after) })),
    el("td", { class: "num", text: money(lease.amount, lease.currency) }),
    el("td", {}, statusBadge(lease.status)));
}

function table(title, leases) {
  return el("section", {},
    el("h2", { class: "h3", text: `${title} (${leases.length})` }),
    el("div", { class: "table-wrap mt-4" }, el("table", { class: "table" },
      el("thead", {}, el("tr", {},
        el("th", { scope: "col", text: "Property" }), el("th", { scope: "col", text: "Your role" }),
        el("th", { scope: "col", text: "Automatic return" }), el("th", { scope: "col", class: "num", text: "Deposit" }),
        el("th", { scope: "col", text: "Status" }))),
      el("tbody", {}, leases.map(row)))));
}

async function refresh() {
  try {
    const { leases } = await get("/api/leases/", { quiet: true });
    if (leases.length === 0) return show("empty");
    renderIfChanged($("[data-groups]"), leases, (node) => {
      node.replaceChildren(...GROUPS.map(([title, statuses]) => [title, leases.filter((l) => statuses.includes(l.status))])
        .filter(([, list]) => list.length).map(([title, list]) => table(title, list)));
    });
    return show("groups");
  } catch (error) {
    $("[data-load-error-text]").textContent = error.message;
    return show("load-error");
  }
}

/* ---------- faucet ---------- */
async function faucetStep(button, label, fn, success) {
  await busy(button, label, async () => {
    try {
      await fn();
      toast(success, "success");
    } catch (error) {
      if (!error.code) toast(error.message, "warning");
    }
    await renderFaucet();
  });
}

async function renderFaucet() {
  const body = $("[data-faucet-body]");
  let data;
  try {
    data = await get("/api/faucet/", { quiet: true });
  } catch (error) {
    fill(body, el("p", { class: "alert alert--warning", text: error.message }));
    return;
  }
  const steps = [];
  if (!data.activated) {
    steps.push(el("p", { text: "Your account doesn't exist on the ledger yet. Activate it with 50 test XRP." }),
      el("button", { type: "button", class: "btn btn--primary", disabled: !data.available.activate,
        on: { click: (e) => faucetStep(e.currentTarget, "Sending test XRP…", () => post("/api/faucet/activate/"), "Account activated with 50 test XRP.") } },
      data.available.activate ? "Activate account" : "Activated in the last 24 hours"));
  } else {
    steps.push(el("p", {}, "Balances: ", Object.entries(data.balances).map(([code, value]) => money(value, code)).join(" · ") || "none"));
    for (const currency of config.currencies) {
      const hasLine = data.trust_lines[currency.code];
      const canGet = data.available[`tokens:${currency.code}`];
      steps.push(el("div", { class: "actions" },
        hasLine
          ? el("span", { class: "badge badge--success", text: `${currency.code} trust line set` })
          : el("button", { type: "button", class: "btn", on: { click: (e) => faucetStep(e.currentTarget, "Confirm in Xaman…", () => addTrustLine(currency.code), `Your account can now hold ${currency.label}.`) } }, `Add ${currency.code} trust line`),
        hasLine && el("button", { type: "button", class: "btn btn--primary", disabled: !canGet,
          on: { click: (e) => faucetStep(e.currentTarget, "Sending test tokens…", () => post("/api/faucet/tokens/", { currency: currency.code }), `5,000 ${currency.label} sent.`) } },
        canGet ? `Get 5,000 ${currency.label}` : `${currency.code} received in the last 24 hours`)));
    }
  }
  fill(body, ...steps);
}

onMe((me) => { if (me) $("[data-me-greeting]").textContent = me.name; });
$("[data-retry]").addEventListener("click", () => { show("loading"); poller.now(); });
poller = poll(refresh, 5000);
renderFaucet();
