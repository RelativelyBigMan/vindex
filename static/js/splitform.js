/**
 * The split form: how much of the deposit goes to the tenant and how much to the landlord.
 * The two amounts always add up to the total exactly (BigInt micro-units, no floats).
 */
import { fromMicro, toMicro } from "./dec.js";
import { splitBar } from "./parts.js";
import { el, money } from "./ui.js";

/**
 * @param {{total: string, code: string, submitLabel: string, tenant?: string, intro?: string,
 *          onSubmit: (values: {tenant: string, landlord: string, reason: string}, button: HTMLButtonElement) => void,
 *          onCancel?: () => void}} options
 */
export function splitForm({ total, code, submitLabel, tenant, intro, onSubmit, onCancel }) {
  const totalMicro = toMicro(total) ?? 0n;
  const input = (name, label, value) => {
    const field = el("input", { id: `split_${name}`, name, type: "text", inputMode: "decimal", autocomplete: "off", required: true, value, data: { amount: "" } });
    return [el("div", { class: "field" }, el("label", { for: `split_${name}`, text: label }), field), field];
  };
  const startTenant = tenant ?? total;
  const [tenantField, tenantInput] = input("tenant", `To the tenant (${code})`, startTenant);
  const [landlordField, landlordInput] = input("landlord", `To the owner (${code})`, fromMicro(totalMicro - (toMicro(startTenant) ?? totalMicro)));
  const slider = el("input", { id: "split_share", type: "range", min: "0", max: "100", step: "1", "aria-label": "Owner's share in percent" });
  const reason = el("textarea", { id: "split_reason", name: "reason", maxLength: 500, required: true, placeholder: "What is the deduction for?" });
  const preview = el("div", {});
  const problem = el("p", { class: "small", role: "alert" });
  const submit = el("button", { type: "submit", class: "btn btn--primary", text: submitLabel });

  function refresh() {
    const t = toMicro(tenantInput.value), l = toMicro(landlordInput.value);
    const ok = t !== null && l !== null && t + l === totalMicro;
    problem.textContent = ok ? "" : `The two amounts must add up to exactly ${money(total, code)}.`;
    problem.className = ok ? "small" : "small verdict--mismatch";
    submit.disabled = !ok;
    if (ok) {
      preview.replaceChildren(splitBar(tenantInput.value, landlordInput.value, code, total));
      slider.value = totalMicro === 0n ? "0" : String(Number((l * 100n) / totalMicro));
    }
  }

  // Typing in one box fills the other with the remainder.
  const follow = (source, target) => source.addEventListener("input", () => {
    const value = toMicro(source.value);
    if (value !== null && value <= totalMicro) target.value = fromMicro(totalMicro - value);
    refresh();
  });
  follow(tenantInput, landlordInput);
  follow(landlordInput, tenantInput);
  slider.addEventListener("input", () => {
    const landlord = (totalMicro * BigInt(slider.value)) / 100n;
    landlordInput.value = fromMicro(landlord);
    tenantInput.value = fromMicro(totalMicro - landlord);
    refresh();
  });

  const form = el("form", { class: "form", novalidate: true },
    intro && el("p", { class: "muted", text: intro }),
    el("div", { class: "grid grid--2" }, tenantField, landlordField),
    el("div", { class: "field" }, el("label", { for: "split_share", text: "Owner's share" }), slider),
    preview,
    el("div", { class: "field" }, el("label", { for: "split_reason", text: "Reason" }), reason,
      el("small", { text: "Shown to the other parties. Not written to the ledger." })),
    problem,
    el("div", { class: "actions" }, submit,
      onCancel && el("button", { type: "button", class: "btn btn--ghost", text: "Cancel", on: { click: onCancel } })));
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (submit.disabled) return;
    if (!reason.value.trim()) {
      reason.setAttribute("aria-invalid", "true");
      reason.focus();
      return;
    }
    onSubmit({ tenant: tenantInput.value.trim(), landlord: landlordInput.value.trim(), reason: reason.value.trim() }, submit);
  });
  refresh();
  return form;
}
