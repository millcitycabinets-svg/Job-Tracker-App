import { useEffect, useRef, useState } from "react";
import { api } from "./api.js";

export const STAGES = ["New Lead", "Follow up", "Design", "Estimate", "Ready to Send", "Awaiting Customer", "Won", "Lost"];
export const WORK_STATUSES = ["Queued", "In Progress", "Needs Information", "Complete"];
export const COLUMNS = { email: "Email", phone: "Phone", customer: "Customer", stage: "Stage", owner: "Owner", amount: "Value", next_follow_up: "Follow-up", visibility: "Access" };
export const WIDGETS = { pipeline: "Sales pipeline", followups: "Follow-ups", collections: "Collected", balance: "Customer balances", commission: "My commissions", work: "Operations work", goal: "Monthly goal", team: "Team sales" };
export const commandId = () => crypto.randomUUID();
export const initials = (name = "") => name.split(" ").map(v => v[0]).slice(0, 2).join("").toUpperCase();
export const displayDate = value => value ? new Date(`${value.slice(0, 10)}T12:00:00`).toLocaleDateString("en-US", { month: "short", day: "numeric" }) : "Not set";
export const wsApi = (path, options) => api(`/workspace${path}`, options);
export function useHash() {
  const [path, setPath] = useState(location.hash.slice(1) || "/");
  useEffect(() => { const update = () => setPath(location.hash.slice(1) || "/"); addEventListener("hashchange", update); return () => removeEventListener("hashchange", update); }, []);
  return path;
}
export function useResource(path, version = 0) {
  const [state, setState] = useState({ data: null, error: "", loading: true });
  useEffect(() => {
    let active = true; setState(previous => ({ ...previous, loading: true, error: "" }));
    wsApi(path).then(data => { if (active) setState({ data, error: "", loading: false }); }, error => { if (active) setState({ data: null, error: error.message, loading: false }); });
    return () => { active = false; };
  }, [path, version]);
  return state;
}
export function ErrorNotice({ error }) { return error ? <div className="ws-error" role="alert">{error}</div> : null; }
export function Empty({ title, children }) { return <div className="ws-empty"><span className="ws-empty-mark" aria-hidden="true">◇</span><h3>{title}</h3>{children && <p>{children}</p>}</div>; }
export function StagePill({ value }) { return <span className={`ws-stage stage-${String(value || "").toLowerCase().replaceAll(" ", "-")}`}><i aria-hidden="true" />{value}</span>; }
export function Field({ label, children, wide = false }) { return <label className={`ws-field ${wide ? "span-two" : ""}`}><span>{label}</span>{children}</label>; }
export function PageHeading({ eyebrow, title, description, children }) { return <div className="ws-page-heading"><div><span className="ws-eyebrow">{eyebrow}</span><h1>{title}</h1>{description && <p>{description}</p>}</div><div className="ws-heading-actions">{children}</div></div>; }
export function Loading() { return <div className="ws-loading" role="status"><span />Loading your workspace…</div>; }
export function Modal({ title, onClose, children, drawer = false }) {
  const panel = useRef(null); const closeRef = useRef(onClose); closeRef.current = onClose;
  useEffect(() => {
    const previous = document.activeElement, node = panel.current;
    node.querySelector("input,select,textarea,button,a[href]")?.focus();
    const key = event => {
      if (event.key === "Escape") closeRef.current();
      if (event.key === "Tab") {
        const items = [...node.querySelectorAll('button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),a[href]')].filter(el => el.offsetParent !== null);
        if (event.shiftKey && document.activeElement === items[0]) { event.preventDefault(); items.at(-1)?.focus(); }
        else if (!event.shiftKey && document.activeElement === items.at(-1)) { event.preventDefault(); items[0]?.focus(); }
      }
    };
    document.addEventListener("keydown", key); const overflow = document.body.style.overflow; document.body.style.overflow = "hidden";
    return () => { document.removeEventListener("keydown", key); document.body.style.overflow = overflow; previous?.focus(); };
  }, []);
  return <div className={`ws-modal-backdrop ${drawer ? "drawer-backdrop" : ""}`} onClick={event => { if (event.target === event.currentTarget) onClose(); }}><section ref={panel} className={`ws-modal ${drawer ? "ws-drawer" : ""}`} role="dialog" aria-modal="true" aria-label={title}><header className="ws-modal-heading"><h2>{title}</h2><button className="ws-icon-button" onClick={onClose} aria-label="Close">×</button></header>{children}</section></div>;
}
