/**
 * Presenter view: scenarios A, B and C side by side, polled every 2 s, one primary button per column.
 * The leases come from `manage.py demo_scenarios`; the server acts as the right persona for each button.
 */
import { get, post } from "../api.js";
import { runBot } from "../app.js";
import { poll } from "../poll.js";
import { busy, el, extLink, fmtCountdown, money, statusBadge, toast } from "../ui.js";

const stage = document.querySelector("[data-stage]");
const statusLine = document.querySelector("[data-stage-status]");
const BOT_GRACE = 20; // seconds past the return date before suggesting a manual bot run
let scenarios = { A: null, B: null, C: null };
let deadlines = {};
let poller = null;

async function act(button, leaseId, step) {
  await busy(button, "Submitting to the XRPL (about 4 s)", async () => {
    try {
      apply(await post("/api/demo/stage/act/", { lease_id: leaseId, step }));
    } catch {
      await poller.now(); // the error toast is already up; show the true state and keep the button for a retry
    }
  });
}

function primary(letter, s) {
  const open = s.proposals.find((p) => p.status === "OPEN");
  const button = (label, step) => el("button", { type: "button", class: "btn btn--primary btn--block", text: label, on: { click: (event) => act(event.currentTarget, s.id, step) } });
  if (letter === "B" && s.status === "ACTIVE") return button("Tenant moves out", "move_out");
  if (letter === "B" && s.status === "IN_SETTLEMENT" && open) return button("Owner approves the refund", "landlord_approve");
  if (letter === "C" && s.status === "IN_SETTLEMENT" && open) return button("Arbiter approves", "arbiter_approve");
  return null;
}

function column(letter, s) {
  if (!s) return [el("p", { class: "muted", text: "Not staged yet. Run: python manage.py demo_scenarios" })];
  if (s.error) return [el("p", { class: "alert alert--warning", text: s.error })];
  const open = s.proposals.find((p) => p.status === "OPEN");
  const paid = s.proposals.find((p) => p.status === "EXECUTED");
  const shown = open || paid;
  const parts = [
    el("div", { class: "row row--between" }, el("p", { class: "stage__status", text: money(s.amount, s.currency) }), statusBadge(s.status)),
  ];
  if (s.status === "ACTIVE") {
    parts.push(el("div", {},
      el("p", { class: "countdown__label", text: "Automatic return in" }),
      el("p", { class: "countdown", data: { countdown: letter }, text: "--:--" }),
      el("div", { data: { botHint: letter } })));
  }
  if (s.status === "RETURNED") parts.push(el("p", { class: "outcome__mark", text: "✓ returned by the ledger" }));
  if (s.status === "SETTLED") parts.push(el("p", { class: "outcome__mark", text: "✓ settled 2 of 3" }));
  if (shown) {
    parts.push(el("div", {},
      el("p", { text: `${money(shown.tenant_amount, s.currency)} tenant · ${money(shown.landlord_amount, s.currency)} owner` }),
      el("p", { class: "small muted", text: `Approvals ${Math.min(shown.signatures_count, 2)} of 2: ${shown.signed_by.map((p) => p.name).join(", ") || "none yet"}` }),
      el("div", { class: "progress", "aria-hidden": "true" },
        el("span", { class: `progress__step${shown.signatures_count >= 1 ? " is-filled" : ""}` }),
        el("span", { class: `progress__step${shown.signatures_count >= 2 ? " is-filled" : ""}` }))));
  }
  const latest = s.events[0];
  if (latest) parts.push(el("p", { class: "stage__event" }, latest.summary, " ", latest.explorer_url && extLink(latest.explorer_url, "explorer", { class: "small" })));
  parts.push(primary(letter, s));
  parts.push(el("p", { class: "small" }, el("a", { href: `/leases/${s.id}/`, text: "Open the deposit" }), " · ", el("a", { href: `/verify/${s.id}/`, text: "Public check" })));
  return parts;
}

function apply(data) {
  const previous = scenarios;
  scenarios = data.scenarios;
  for (const letter of "ABC") {
    const s = scenarios[letter];
    const body = stage.querySelector(`[data-col="${letter}"] [data-body]`);
    // The return date never moves, so a slow (stale) answer can only make it look later: keep the earliest.
    if (s && !s.error) {
      const key = `${letter}:${s.id}`;
      deadlines[key] = Math.min(deadlines[key] ?? Infinity, Date.now() + s.seconds_to_cancel_after * 1000);
      deadlines[letter] = deadlines[key];
    }
    if (s && previous[letter] && previous[letter].status && previous[letter].status !== s.status && s.events?.[0]) {
      toast(`${letter}: ${s.events[0].summary}`, ["RETURNED", "SETTLED"].includes(s.status) ? "success" : "info");
    }
    // Rebuilt only when something visible changed, so a busy button is never replaced mid-request.
    const signature = JSON.stringify(s && [s.status, s.error, s.events?.[0]?.id, s.proposals?.map((p) => [p.id, p.status, p.signatures_count])]);
    if (body.dataset.sig !== signature) {
      body.dataset.sig = signature;
      body.replaceChildren(...column(letter, s).filter(Boolean));
    }
  }
  statusLine.replaceChildren(el("span", { class: "live-tag", text: `live · ${data.network}` }));
  tick();
}

function tick() {
  for (const letter of "ABC") {
    const node = stage.querySelector(`[data-countdown="${letter}"]`);
    if (!node) continue;
    const left = Math.round((deadlines[letter] - Date.now()) / 1000);
    node.textContent = left > 0 ? fmtCountdown(left) : "due";
    node.classList.toggle("countdown--due", left <= 0);
    const hint = stage.querySelector(`[data-bot-hint="${letter}"]`);
    if (hint && left <= -BOT_GRACE && !hint.childElementCount) {
      // The run_bot command normally does this by itself; this is the fallback when it isn't running.
      hint.replaceChildren(
        el("p", { class: "small muted", text: "Automatic return pending. If the bot isn't running, run it once:" }),
        el("button", { type: "button", class: "btn btn--block", text: "Run bot now", on: { click: async (event) => { await runBot(event.currentTarget); poller.now(); } } }));
    }
  }
}

async function refresh() {
  try {
    apply(await get("/api/demo/stage/", { quiet: true }));
  } catch (error) {
    statusLine.replaceChildren(el("span", { class: "verdict--mismatch", text: `Connection lost, retrying… (${error.message})` }));
  }
}

setInterval(tick, 1000);
document.addEventListener("bot:ran", () => poller?.now());
poller = poll(refresh, 2000);
