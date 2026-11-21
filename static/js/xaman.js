/**
 * Xaman sign requests. The server creates the request and renders its QR code; this module shows
 * it, polls GET /api/xaman/requests/<id>/ until it is done, and reports the outcome.
 * Push notifications reach the phone too once the account has signed in before.
 */
import { get, post } from "./api.js";

const POLL_MS = 2000;
const IN_PROGRESS = ["open", "signed", "finishing"];
const dialog = () => document.querySelector("[data-xaman-dialog]");
const STATUS_TEXT = {
  open: "Waiting for you to sign in Xaman…",
  signed: "Signed. Waiting for the ledger to confirm…",
  finishing: "Signed. Waiting for the ledger to confirm…",
  done: "Done.",
};

export class XamanCancelled extends Error {}

function fillDialog(node, request, title) {
  node.querySelector("[data-xaman-title]").textContent = title;
  const qr = node.querySelector("[data-xaman-qr]");
  qr.hidden = !request.qr;
  if (request.qr) qr.src = request.qr;
  const link = node.querySelector("[data-xaman-link]");
  link.hidden = !request.deeplink;
  if (request.deeplink) link.href = request.deeplink;
  node.querySelector("[data-xaman-text]").textContent = request.pushed
    ? "A sign request was sent to your Xaman app. You can also scan the code."
    : "Scan the code with the Xaman app, or open it on this phone.";
  node.querySelector("[data-xaman-status]").textContent = STATUS_TEXT[request.status] || "";
}

/**
 * @param {() => Promise<object>} start  calls the API endpoint that creates the request
 * @param {{title?: string}} [options]
 */
export async function signWithXaman(start, { title = "Sign in Xaman" } = {}) {
  const node = dialog();
  let request = await start();
  let cancelled = false;
  const onCancel = async () => {
    cancelled = true;
    try { await post(`/api/xaman/requests/${request.request_id}/cancel/`, {}, { quiet: true }); } catch { /* already resolved */ }
  };
  const cancelButton = node.querySelector("[data-xaman-cancel]");
  cancelButton.addEventListener("click", onCancel);
  node.addEventListener("cancel", onCancel);
  fillDialog(node, request, title);
  if (!node.open) node.showModal();
  try {
    while (!cancelled && IN_PROGRESS.includes(request.status)) {
      await new Promise((resolve) => setTimeout(resolve, POLL_MS));
      if (cancelled) break;
      request = await get(`/api/xaman/requests/${request.request_id}/`, { quiet: true });
      fillDialog(node, request, title);
    }
  } finally {
    cancelButton.removeEventListener("click", onCancel);
    node.removeEventListener("cancel", onCancel);
    if (node.open) node.close();
  }
  if (cancelled || request.status === "rejected") throw new XamanCancelled("Cancelled in Xaman. Nothing was signed.");
  if (request.status === "expired") throw new XamanCancelled("The sign request expired. Try again.");
  if (request.status === "failed") throw new Error(request.error || "That didn't work. Try again.");
  return request;
}

/** POST `path` repeatedly, signing each Xaman request it returns, until the API answers {done: true}. */
export async function signSteps(path, { title } = {}) {
  for (let step = 0; step < 10; step += 1) {
    const answer = await post(path);
    if (answer.done) return answer;
    await signWithXaman(async () => answer, { title });
  }
  throw new Error("Too many signing steps.");
}
