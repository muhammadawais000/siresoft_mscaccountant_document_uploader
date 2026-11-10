// API client: same-origin session auth, CSRF header, one error format.

/**
 * @typedef {{ code: string, message: string, details: Record<string, unknown> }} ErrorBody
 */

export class ApiError extends Error {
  /**
   * @param {number} status
   * @param {ErrorBody} body
   */
  constructor(status, body) {
    super(body.message || `Request failed (${status})`);
    this.name = "ApiError";
    this.status = status;
    this.code = body.code;
    this.details = body.details ?? {};
  }

  /** Human-readable text, including the first field error if there is one. */
  get detailText() {
    const first = Object.entries(this.details)[0];
    if (!first) return this.message;
    const [field, messages] = first;
    const text = Array.isArray(messages) ? messages.join(" ") : String(messages);
    return `${field}: ${text}`;
  }
}

/** @returns {string} */
function csrfToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta?.getAttribute("content") ?? "";
}

/** @returns {string} */
export function apiBase() {
  return document.body.dataset.apiBase ?? "/api/v1/intake/";
}

/**
 * @param {Response | XMLHttpRequest} res
 * @param {string} text
 * @returns {ApiError}
 */
function toError(res, text) {
  let body = { code: "error", message: `Request failed (${res.status})`, details: {} };
  try {
    const parsed = JSON.parse(text);
    if (parsed && parsed.error) body = parsed.error;
  } catch {
    if (res.status === 0) body.message = "Network error. Check your connection.";
  }
  if (res.status === 403 && body.code === "not_authenticated") {
    body.message = "Your session has ended. Sign in again.";
  }
  return new ApiError(res.status, body);
}

/**
 * JSON request to the intake API.
 * @param {string} path  relative to the API base, e.g. "documents/"
 * @param {{ method?: string, json?: unknown, params?: URLSearchParams, signal?: AbortSignal }} [opts]
 * @returns {Promise<any>}
 */
export async function api(path, opts = {}) {
  const method = opts.method ?? "GET";
  const url = new URL(path, new URL(apiBase(), window.location.origin));
  if (opts.params) url.search = opts.params.toString();
  /** @type {Record<string, string>} */
  const headers = { Accept: "application/json" };
  if (method !== "GET") headers["X-CSRFToken"] = csrfToken();
  if (opts.json !== undefined) headers["Content-Type"] = "application/json";

  let res;
  try {
    res = await fetch(url, {
      method,
      headers,
      credentials: "same-origin",
      body: opts.json === undefined ? undefined : JSON.stringify(opts.json),
      signal: opts.signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError(0, { code: "network", message: "Network error. Check your connection.", details: {} });
  }
  const text = await res.text();
  if (!res.ok) throw toError(res, text);
  return text ? JSON.parse(text) : null;
}

/**
 * Upload one file with progress (fetch has no upload progress, so this uses XHR).
 * @param {File} file
 * @param {Record<string, string>} fields
 * @param {(fraction: number) => void} onProgress
 * @returns {Promise<any>}
 */
export function uploadFile(file, fields, onProgress) {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append("file", file);
    for (const [key, value] of Object.entries(fields)) form.append(key, value);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", new URL("documents/", new URL(apiBase(), window.location.origin)));
    xhr.setRequestHeader("X-CSRFToken", csrfToken());
    xhr.setRequestHeader("Accept", "application/json");
    xhr.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable) onProgress(event.loaded / event.total);
    });
    xhr.addEventListener("load", () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve(JSON.parse(xhr.responseText));
      else reject(toError(xhr, xhr.responseText));
    });
    xhr.addEventListener("error", () => reject(toError(xhr, "")));
    xhr.send(form);
  });
}
