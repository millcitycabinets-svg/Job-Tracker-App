const ACCOUNT_CHANGED = "Your signed-in account changed. Sign in again before continuing.";
const AUTH_EVENT_KEY = "mill-city-auth-change";
const sessionListeners = new Set();
const seenAuthSignals = new Set();
let principal = null, sessionEpoch = 0, sessionBlocked = false, authChannel = null;

function sessionError(message = ACCOUNT_CHANGED) {
  const error = new Error(message); error.sessionChanged = true; return error;
}
function invalidateSession(message) {
  principal = null; sessionBlocked = true; sessionEpoch += 1;
  for (const listener of sessionListeners) listener(message);
}
export function onSessionInvalidated(listener) {
  sessionListeners.add(listener);
  return () => sessionListeners.delete(listener);
}
function receiveAuthChange(signal) {
  if (signal?.type !== "auth-changed" || typeof signal.nonce !== "string" || seenAuthSignals.has(signal.nonce)) return;
  seenAuthSignals.add(signal.nonce);
  if (seenAuthSignals.size > 50) seenAuthSignals.delete(seenAuthSignals.values().next().value);
  invalidateSession(ACCOUNT_CHANGED);
}
function broadcastAuthChange() {
  // This signal contains no account details, credentials, or customer data.
  const signal = { type: "auth-changed", nonce: crypto.randomUUID() };
  try { authChannel?.postMessage(signal); } catch {}
  try { window.localStorage.setItem(AUTH_EVENT_KEY, JSON.stringify(signal)); window.localStorage.removeItem(AUTH_EVENT_KEY); } catch {}
}
if (typeof window !== "undefined") {
  try {
    authChannel = new BroadcastChannel(AUTH_EVENT_KEY);
    authChannel.onmessage = event => receiveAuthChange(event.data);
  } catch {}
  window.addEventListener("storage", event => {
    if (event.key === AUTH_EVENT_KEY && event.newValue) {
      try { receiveAuthChange(JSON.parse(event.newValue)); } catch {}
    }
  });
}

export async function api(path, { method = "GET", body, responseType = "json" } = {}) {
  if (sessionBlocked && path !== "/login") throw sessionError();
  const epoch = sessionEpoch;
  const form = body instanceof FormData;
  const headers = {};
  if (body && !form) headers["Content-Type"] = "application/json";
  // Bind requests to the account shown in this tab. The server rejects a stale
  // tab if another tab replaced the shared session cookie with another account.
  if (principal !== null && path !== "/login") headers["X-Workspace-User"] = principal;
  const res = await fetch(`/api${path}`, {
    method,
    headers,
    body: body && !form ? JSON.stringify(body) : body,
  });
  const data = res.ok && responseType === "blob" ? await res.blob() : await res.json().catch(() => ({}));
  if (epoch !== sessionEpoch) throw sessionError();
  if (!res.ok) {
    const d = data.detail;
    const msg = Array.isArray(d) ? d.map((e) => `${e.loc?.at(-1)}: ${e.msg}`).join("; ") : d;
    if (res.status === 401 && path !== "/login") invalidateSession(msg || "Your session expired. Sign in again to continue.");
    // HTTP/2 has no status text, so a non-JSON error page would otherwise show nothing at all.
    throw new Error(msg || `Something went wrong (error ${res.status}). Try again or tell Skyler.`);
  }
  if (path === "/login" || path === "/me") {
    principal = String(data.id); sessionBlocked = false;
    if (path === "/login") { sessionEpoch += 1; broadcastAuthChange(); }
  } else if (path === "/logout") {
    invalidateSession(""); broadcastAuthChange();
  }
  return data;
}

// Phone photos are 3–10 MB; shrink to 1600px JPEG before upload. PDFs and small files go as-is.
export async function shrinkPhoto(file) {
  const epoch = sessionEpoch;
  const checkSession = () => { if (sessionBlocked || epoch !== sessionEpoch) throw sessionError(); };
  checkSession();
  if (!file.type.startsWith("image/") || file.size < 600_000) return file;
  try {
    const img = await createImageBitmap(file);
    checkSession();
    const scale = Math.min(1, 1600 / Math.max(img.width, img.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(img.width * scale);
    canvas.height = Math.round(img.height * scale);
    canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise((ok) => canvas.toBlob(ok, "image/jpeg", 0.82));
    checkSession();
    return blob ? new File([blob], "receipt.jpg", { type: "image/jpeg" }) : file;
  } catch {
    checkSession();
    return file; // browser can't decode it (e.g. some HEIC); send the original
  }
}

const fmt = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
export const usd = (v) => (v == null ? "—" : fmt.format(Number(v)));
const whole = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
export const usd0 = (v) => (v == null ? "—" : whole.format(Number(v)));
export const today = () => new Date().toLocaleDateString("en-CA"); // YYYY-MM-DD in local time
