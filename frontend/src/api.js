export async function api(path, { method = "GET", body } = {}) {
  const form = body instanceof FormData;
  const res = await fetch(`/api${path}`, {
    method,
    headers: body && !form ? { "Content-Type": "application/json" } : undefined,
    body: body && !form ? JSON.stringify(body) : body,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    const msg = Array.isArray(d) ? d.map((e) => `${e.loc?.at(-1)}: ${e.msg}`).join("; ") : d;
    // HTTP/2 has no status text, so a non-JSON error page would otherwise show nothing at all.
    throw new Error(msg || `Something went wrong (error ${res.status}). Try again or tell Skyler.`);
  }
  return data;
}

// Phone photos are 3–10 MB; shrink to 1600px JPEG before upload. PDFs and small files go as-is.
export async function shrinkPhoto(file) {
  if (!file.type.startsWith("image/") || file.size < 600_000) return file;
  try {
    const img = await createImageBitmap(file);
    const scale = Math.min(1, 1600 / Math.max(img.width, img.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(img.width * scale);
    canvas.height = Math.round(img.height * scale);
    canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise((ok) => canvas.toBlob(ok, "image/jpeg", 0.82));
    return blob ? new File([blob], "receipt.jpg", { type: "image/jpeg" }) : file;
  } catch {
    return file; // browser can't decode it (e.g. some HEIC); send the original
  }
}

const fmt = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
export const usd = (v) => (v == null ? "—" : fmt.format(Number(v)));
export const today = () => new Date().toLocaleDateString("en-CA"); // YYYY-MM-DD in local time
