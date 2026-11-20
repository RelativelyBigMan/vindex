/** My leases: the deposits where the current persona is tenant, landlord or arbiter. */
import { get } from "../api.js";
import { poll } from "../poll.js";
import { onPersona } from "../session.js";
import { ROLE_LABEL, el, fmtTime, money, renderIfChanged, statusBadge } from "../ui.js";

const $ = (selector) => document.querySelector(selector);
let poller = null;

function show(name) {
  for (const key of ["loading", "leases", "empty", "load-error"]) $(`[data-${key}]`).hidden = key !== name;
}

async function refresh() {
  try {
    const { leases } = await get("/api/leases/", { quiet: true });
    if (leases.length === 0) return show("empty");
    renderIfChanged($("[data-lease-rows]"), leases, (body) => {
      body.replaceChildren(...leases.map((lease) => el("tr", {},
        el("td", {}, el("a", { href: `/leases/${lease.id}/`, text: lease.title || `Deposit ${lease.id.slice(0, 8)}` }),
          el("div", { class: "small muted", text: `${lease.parties[0].name} → ${lease.parties[1].name}` })),
        el("td", { text: ROLE_LABEL[lease.role] || lease.role }),
        el("td", {}, el("time", { dateTime: lease.cancel_after, text: fmtTime(lease.cancel_after) })),
        el("td", { class: "num", text: money(lease.amount, lease.currency) }),
        el("td", {}, statusBadge(lease.status)))));
    });
    return show("leases");
  } catch (error) {
    $("[data-load-error-text]").textContent = error.message;
    return show("load-error");
  }
}

onPersona((me) => {
  $("[data-me-name]").textContent = me ? me.name : "you";
  $("[data-new-lease]").hidden = Boolean(me) && me.role !== "tenant";
  poller?.now();
});
$("[data-retry]").addEventListener("click", () => { show("loading"); poller.now(); });
poller = poll(refresh, 5000);
