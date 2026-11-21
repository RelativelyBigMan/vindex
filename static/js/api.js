/**
 * Fetch wrapper for the JSON API: sends Django's CSRF token on every write and turns
 * {error, code} answers into ApiError, with a toast unless the caller asks for quiet.
 */
import { toast } from "./ui.js";

export class ApiError extends Error {
  constructor(message, code, status) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

function csrfToken() {
  const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : "";
}

/**
 * @param {string} path  same-origin path, e.g. "/api/me/"
 * @param {{method?: string, json?: unknown, form?: FormData, quiet?: boolean}} [options]
 */
export async function api(path, { method = "GET", json, form, quiet = false } = {}) {
  const headers = { Accept: "application/json" };
  let body;
  if (json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(json);
  } else if (form) {
    body = form;
  }
  if (method !== "GET") headers["X-CSRFToken"] = csrfToken();

  let response;
  try {
    response = await fetch(path, { method, headers, body, credentials: "same-origin" });
  } catch {
    const error = new ApiError("Can't reach the Vindex server. Check the connection and try again.", "offline", 0);
    if (!quiet) toast(error.message, "error");
    throw error;
  }
  let data = null;
  try { data = await response.json(); } catch { /* not JSON: handled below */ }
  if (!response.ok || data === null) {
    const error = new ApiError(
      (data && data.error) || `The server answered with an error (HTTP ${response.status}).`,
      (data && data.code) || "http_error",
      response.status,
    );
    if (!quiet) toast(error.message, "error");
    throw error;
  }
  return data;
}

export const get = (path, options) => api(path, options);
export const post = (path, json = {}, options = {}) => api(path, { ...options, method: "POST", json });
