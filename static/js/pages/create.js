/** New agreement: send the terms, then go to the lease page, which shows the invite links. */
import { post } from "../api.js";
import { busy, toast } from "../ui.js";

const form = document.querySelector("[data-lease-form]");
const endDate = form.elements.end_date;
endDate.min = new Date(Date.now() + 86400000).toISOString().slice(0, 10);

form.querySelectorAll("input[name=end_mode]").forEach((radio) => radio.addEventListener("change", () => {
  const byDate = form.elements.end_mode.value === "date";
  form.querySelector("[data-end-date]").hidden = !byDate;
  form.querySelector("[data-end-minutes]").hidden = byDate;
  endDate.required = byDate;
}));

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!form.elements.accept_terms.checked) return toast("Please accept the terms first.", "warning");
  const byDate = form.elements.end_mode.value === "date";
  if (byDate && !endDate.value) return toast("Choose the lease end date.", "warning");
  const body = {
    role: form.elements.role.value,
    title: form.elements.title.value.trim(),
    amount: form.elements.amount.value.trim(),
    currency: form.elements.currency.value,
    grace_minutes: form.elements.grace_minutes.value,
    arbiter_address: form.elements.arbiter_address.value.trim(),
    ...(byDate ? { end_date: endDate.value } : { end_in_minutes: form.elements.end_in_minutes.value }),
  };
  await busy(form.querySelector("[data-submit]"), "Creating…", async () => {
    try {
      const { lease_id: leaseId } = await post("/api/leases/", body);
      window.location.assign(`/leases/${leaseId}/`);
    } catch { /* shown by api.js */ }
  });
  return undefined;
});
