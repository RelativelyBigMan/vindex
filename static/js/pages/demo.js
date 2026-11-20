/** Persona switcher: who am I in this demo, and what does each wallet hold. */
import { get, post } from "../api.js";
import { verifiedBadge } from "../parts.js";
import { actAs, onPersona } from "../session.js";
import { ROLE_LABEL, busy, copyButton, el, extLink, money, shortAddress, toast } from "../ui.js";

const grid = document.querySelector("[data-personas]");
let personas = [];
let current = null;

function card(persona) {
  const active = persona.party_id === current;
  const balances = persona.balances;
  return el("article", { class: `card party party--${persona.role}` },
    el("p", { class: "party__role" }, ROLE_LABEL[persona.role], active && el("span", { class: "badge badge--info party__you", text: "You" })),
    el("p", { class: "party__name", text: persona.name }),
    el("p", { class: "mono small", title: persona.address, text: shortAddress(persona.address) }),
    el("div", { class: "row" }, verifiedBadge(persona), copyButton(persona.address, "Copy address"), extLink(persona.explorer_url, "Explorer", { class: "small" })),
    balances
      ? el("dl", { class: "balances" }, Object.entries(balances).map(([code, value]) => el("div", {}, el("dt", { text: code }), el("dd", { text: money(value) }))))
      : el("p", { class: "small muted mt-4", text: persona.balances_error || "Balances unavailable right now." }),
    el("div", { class: "actions mt-4" },
      active
        ? el("button", { type: "button", class: "btn btn--ghost", text: "Top up test funds", on: { click: (event) => topUp(event.currentTarget) } })
        : el("button", { type: "button", class: `btn btn--tint-${persona.role === "landlord" ? "owner" : persona.role}`, text: `Act as ${persona.name.split(" ")[0]}`, on: { click: (event) => become(event.currentTarget, persona) } })));
}

function render() {
  document.querySelector("[data-no-personas]").hidden = personas.length > 0;
  grid.hidden = personas.length === 0;
  grid.replaceChildren(...personas.map(card));
}

async function load() {
  try {
    const data = await get("/api/demo/personas/?balances=1");
    personas = data.personas;
    current = data.current;
  } catch {
    personas = [];
  }
  render();
}

async function become(button, persona) {
  await busy(button, "Switching…", async () => {
    try { await actAs(persona.party_id); } catch { /* shown by api.js */ }
  });
}

async function topUp(button) {
  await busy(button, "Sending test funds (about 10 s)", async () => {
    try {
      const result = await post("/api/demo/fund/");
      toast(result.skipped.length ? `Topped up, except ${result.skipped.join(", ")} (no trust line).` : "Topped up with 5,000 of each test token.", "success");
      const mine = personas.find((p) => p.party_id === current);
      if (mine) mine.balances = result.balances;
      render();
    } catch { /* shown by api.js */ }
  });
}

onPersona((me) => {
  if (!me || me.party_id === current) return;
  current = me.party_id;
  render();
});
load();
