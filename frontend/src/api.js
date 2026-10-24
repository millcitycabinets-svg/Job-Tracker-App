export async function api(path, { method = "GET", body } = {}) {
  const res = await fetch(`/api${path}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    throw new Error(Array.isArray(d) ? d.map((e) => `${e.loc?.at(-1)}: ${e.msg}`).join("; ") : d || res.statusText);
  }
  return data;
}

const fmt = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
export const usd = (v) => (v == null ? "—" : fmt.format(Number(v)));
export const today = () => new Date().toLocaleDateString("en-CA"); // YYYY-MM-DD in local time
