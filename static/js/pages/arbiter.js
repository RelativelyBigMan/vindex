/** Arbiter desk: deposits in settlement, each with its open split, its evidence and a quick Approve. */
import { approveSplit } from "../actions.js";
import { get } from "../api.js";
import { proposalCard } from "../parts.js";
import { poll } from "../poll.js";
import { currentMe, onPersona } from "../session.js";
import { busy, el, extLink, money, renderIfChanged, shortHash, statusBadge } from "../ui.js";

const $ = (selector) => document.querySelector(selector);
let poller = null;

function caseCard(state) {
  const open = state.proposals.find((p) => p.status === "OPEN");
  return el("section", { class: "stack" },
    el("div", { class: "row row--between" },
      el("h2", {}, el("a", { href: `/leases/${state.id}/`, text: state.title || `Deposit ${state.id.slice(0, 8)}` })),
      statusBadge(state.status)),
    el("p", { class: "muted", text: `${money(state.amount, state.currency)} · ${state.parties[0].name} (tenant) and ${state.parties[1].name} (owner) · ${state.release_reason === "damage_claim" ? "damage claim" : "early move-out"}` }),
    open
      ? proposalCard(open, state, (button) => busy(button, "Submitting to the XRPL (about 4 s)", async () => {
        try { await approveSplit(open.id); } catch { /* shown by api.js */ }
        await poller.now();
      }))
      : el("p", { class: "note", text: "No split has been proposed yet. Open the deposit to propose one." }),
    state.evidence.length > 0 && el("div", {},
      el("h3", { class: "small sc", text: `Evidence (${state.evidence.length})` }),
      el("div", { class: "gallery mt-4" }, state.evidence.map((item) => el("article", { class: "shot" },
        el("a", { href: `/leases/${state.id}/#evidence-heading`, title: "Open the deposit to verify this photo against the ledger" },
          el("img", { src: item.url, alt: `${item.kind_label} photo uploaded by ${item.uploaded_by}`, loading: "lazy" })),
        el("div", { class: "shot__body" },
          el("p", {}, el("span", { class: "badge", text: item.kind_label }), ` ${item.uploaded_by}`),
          el("p", { class: "mono", title: item.sha256, text: `sha256 ${shortHash(item.sha256)}` }),
          item.anchor_tx_hash ? el("p", {}, extLink(item.explorer_url, "Anchored on XRPL")) : el("p", { class: "muted", text: "Not anchored" })))))));
}

async function refresh() {
  const me = currentMe();
  try {
    const { leases } = await get("/api/leases/?status=IN_SETTLEMENT", { quiet: true });
    const states = await Promise.all(leases.slice(0, 12).map((lease) => get(`/api/leases/${lease.id}/`, { quiet: true })));
    $("[data-loading]").hidden = true;
    $("[data-empty]").hidden = states.length > 0;
    renderIfChanged($("[data-cases]"), [states, me?.party_id], (node) => node.replaceChildren(...states.map(caseCard)));
  } catch {
    $("[data-loading]").hidden = true;
  }
}

onPersona((me) => {
  const notArbiter = Boolean(me) && me.role !== "arbiter";
  $("[data-not-arbiter]").hidden = !notArbiter;
  poller?.now();
});
poller = poll(refresh, 4000);
