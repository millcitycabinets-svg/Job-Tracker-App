import test from "node:test";
import assert from "node:assert/strict";

let moduleNumber = 0;
async function setup() {
  const calls = [], channels = [], storageListeners = [], stored = [];
  globalThis.window = {
    localStorage: { setItem: (key, value) => stored.push({ key, value }), removeItem() {} },
    addEventListener: (name, listener) => { if (name === "storage") storageListeners.push(listener); },
  };
  globalThis.BroadcastChannel = class {
    constructor() { channels.push(this); this.sent = []; }
    postMessage(signal) { this.sent.push(signal); }
  };
  let reply = () => ({ id: 7 });
  globalThis.fetch = async (url, options) => {
    calls.push({ url, ...options });
    const value = await reply(url, options);
    return value instanceof Response ? value : Response.json(value);
  };
  const client = await import(`../src/api.js?case=${++moduleNumber}`);
  return {
    ...client, calls, channels, stored,
    reply: fn => { reply = fn; },
    otherTab: nonce => channels[0].onmessage({ data: { type: "auth-changed", nonce } }),
    storage: nonce => storageListeners.forEach(listener => listener({ key: "mill-city-auth-change", newValue: JSON.stringify({ type: "auth-changed", nonce }) })),
  };
}

test("authenticated workspace and legacy finance calls bind the displayed user", async () => {
  const client = await setup();
  await client.api("/me");
  await client.api("/workspace/opportunities", { method: "POST", body: { name: "Private draft" } });
  await client.api("/jobs/12", { method: "PATCH", body: { name: "Test job" } });
  const upload = new FormData(); upload.append("note", "Test receipt");
  await client.api("/reimbursements", { method: "POST", body: upload });
  for (const call of client.calls.slice(1)) assert.equal(call.headers["X-Workspace-User"], "7");
  assert.equal(client.calls.at(-1).headers["Content-Type"], undefined);
  await client.api("/login", { method: "POST", body: { email: "test@example.com", password: "fictional" } });
  assert.equal(client.calls.at(-1).headers["X-Workspace-User"], undefined);
});

test("a cross-tab account change clears the session and blocks stale writes until explicit login", async () => {
  const client = await setup(), notices = [];
  client.onSessionInvalidated(message => notices.push(message));
  await client.api("/me");
  client.otherTab("new-login");
  const before = client.calls.length;
  await assert.rejects(client.api("/workspace/opportunities", { method: "POST", body: { notes: "Old private draft" } }), /account changed/);
  await assert.rejects(client.api("/logout", { method: "POST" }), /account changed/);
  await assert.rejects(client.api("/me"), /account changed/);
  assert.equal(client.calls.length, before);
  assert.equal(notices.length, 1);
  client.reply(() => ({ id: 9 }));
  await client.api("/login", { method: "POST", body: {} });
  await client.api("/workspace/opportunities");
  assert.equal(client.calls.at(-1).headers["X-Workspace-User"], "9");
});

test("401 invalidates local UI but does not broadcast a logout to the valid account in another tab", async () => {
  const client = await setup(), notices = [];
  await client.api("/me");
  client.onSessionInvalidated(message => notices.push(message));
  client.reply(() => Response.json({ detail: "Your signed-in account changed." }, { status: 401 }));
  await assert.rejects(client.api("/jobs/12", { method: "PATCH", body: {} }), /account changed/);
  assert.deepEqual(notices, ["Your signed-in account changed."]);
  assert.equal(client.channels[0].sent.length, 0);
  assert.equal(client.stored.length, 0);
  const before = client.calls.length;
  await assert.rejects(client.api("/jobs"), /account changed/);
  assert.equal(client.calls.length, before);
});

test("an old response cannot clear or populate a newly signed-in session", async () => {
  const client = await setup(), notices = [];
  await client.api("/me");
  client.onSessionInvalidated(message => notices.push(message));
  let finishOld;
  client.reply(url => url.endsWith("/login") ? { id: 9 } : new Promise(resolve => { finishOld = resolve; }));
  const oldRequest = client.api("/workspace/opportunities");
  client.otherTab("account-switch");
  await client.api("/login", { method: "POST", body: {} });
  finishOld(Response.json({ detail: "Old session expired" }, { status: 401 }));
  await assert.rejects(oldRequest, /account changed/);
  assert.equal(notices.length, 1);
  client.reply(() => []);
  await client.api("/workspace/opportunities");
  assert.equal(client.calls.at(-1).headers["X-Workspace-User"], "9");
});

test("storage fallback works and duplicate broadcast/storage signals do not invalidate a fresh login", async () => {
  const client = await setup(), notices = [];
  await client.api("/me");
  client.onSessionInvalidated(message => notices.push(message));
  client.otherTab("same-event");
  client.reply(() => ({ id: 9 }));
  await client.api("/login", { method: "POST", body: {} });
  client.storage("same-event");
  await client.api("/jobs");
  assert.equal(notices.length, 1);
  client.storage("storage-only-event");
  await assert.rejects(client.api("/jobs"), /account changed/);
  assert.equal(notices.length, 2);
});

test("logout broadcasts no private data; failed login does not invalidate another tab", async () => {
  const client = await setup();
  await client.api("/me");
  await client.api("/logout", { method: "POST" });
  const signal = client.channels[0].sent[0];
  assert.deepEqual(Object.keys(signal).sort(), ["nonce", "type"]);
  await assert.rejects(client.api("/jobs"), /account changed/);
  client.reply(() => Response.json({ detail: "Invalid login" }, { status: 401 }));
  await assert.rejects(client.api("/login", { method: "POST", body: {} }), /Invalid login/);
  assert.equal(client.channels[0].sent.length, 1);
});

test("template blobs use the same account binding and 401 behavior", async () => {
  const client = await setup();
  await client.api("/me");
  client.reply(() => new Response("name,email\n", { headers: { "Content-Type": "text/csv" } }));
  const blob = await client.api("/workspace/imports/template", { responseType: "blob" });
  assert.equal(await blob.text(), "name,email\n");
  assert.equal(client.calls.at(-1).headers["X-Workspace-User"], "7");
  client.reply(() => Response.json({ detail: "Sign in again" }, { status: 401 }));
  await assert.rejects(client.api("/workspace/imports/template", { responseType: "blob" }), /Sign in again/);
  await assert.rejects(client.api("/jobs"), /account changed/);
});

test("photo preparation cannot resume a legacy upload under another account", async () => {
  const client = await setup();
  await client.api("/me");
  let finishDecode;
  globalThis.createImageBitmap = () => new Promise(resolve => { finishDecode = resolve; });
  const shrinking = client.shrinkPhoto({ type: "image/jpeg", size: 900000 });
  client.otherTab("during-upload");
  client.reply(() => ({ id: 9 }));
  await client.api("/login", { method: "POST", body: {} });
  finishDecode({ width: 2000, height: 1000 });
  await assert.rejects(shrinking, /account changed/);
});
