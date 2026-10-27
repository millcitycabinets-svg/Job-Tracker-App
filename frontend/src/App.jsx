import { useEffect, useState } from "react";
import { api, shrinkPhoto, today, usd } from "./api.js";

const STATUSES = ["Ordered", "Assembled", "Delivered", "Installed", "Punch", "Closed"];
const ENTRY_LINES = {
  2: "Change order",
  4: "Materials",
  5: "Assembly",
  6: "Install labor",
  7: "Countertops",
  8: "Design",
  9: "Subcontractor",
  10: "Card processing fee",
  11: "Unexpected",
};
const SUBTOTALS = new Set([3, 13, 15]);
const counted = (e) => e.status === "approved" || e.status === "paid";
const STATUS_LABEL = { submitted: "Waiting", approved: "Approved", paid: "Paid", rejected: "Rejected" };

function StatusPill({ entry }) {
  return (
    <span className={`pill status-${entry.status}`} title={entry.status_note || undefined}>
      {STATUS_LABEL[entry.status]}{entry.status === "rejected" && entry.status_note ? `: ${entry.status_note}` : ""}
    </span>
  );
}

function useHash() {
  const [hash, setHash] = useState(location.hash.slice(1) || "/");
  useEffect(() => {
    const on = () => setHash(location.hash.slice(1) || "/");
    addEventListener("hashchange", on);
    return () => removeEventListener("hashchange", on);
  }, []);
  return hash;
}

function useLoad(path, deps = []) {
  const [state, setState] = useState({ data: null, error: null });
  const reload = () => api(path).then((data) => setState({ data, error: null }), (e) => setState({ data: null, error: e.message }));
  useEffect(() => { reload(); }, deps); // eslint-disable-line react-hooks/exhaustive-deps
  return [state.data, state.error, reload, (data) => setState({ data, error: null })];
}

function Field({ label, children }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
    </label>
  );
}

function ErrorText({ error }) {
  return error ? <p className="error" role="alert">{error}</p> : null;
}

// Wraps a submit handler with busy + error state.
function useSubmit(fn) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const submit = async (e) => {
    e?.preventDefault?.();
    setBusy(true);
    setError(null);
    try {
      await fn(e);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };
  return [submit, busy, error];
}

export default function App() {
  const [me, setMe] = useState(undefined);
  const hash = useHash();
  useEffect(() => { api("/me").then(setMe, () => setMe(null)); }, []);

  if (me === undefined) return <p className="center muted">Loading…</p>;
  if (!me) return <Login onLogin={setMe} />;

  const owner = me.role === "owner";
  const logout = () => api("/logout", { method: "POST" }).then(() => setMe(null));
  const nav = {
    owner: [["/", "Jobs"], ["/inbox", "Quick log"], ["/hours", "Hours"], ["/assembly", "Assembly"], ["/collections", "Collections"], ["/designs", "Designs"], ["/approvals", "Approvals"], ["/team", "Team"]],
    crew: [["/", "Hours"], ["/assembly", "Assembly"], ["/reimbursements", "Reimbursements"]],
    designer: [["/", "My designs"], ["/reimbursements", "Reimbursements"]],
  }[me.role] || [];

  let page;
  const jobMatch = hash.match(/^\/jobs\/(\d+)$/);
  if (me.role === "designer") page = hash === "/reimbursements" ? <Reimbursements /> : <MyDesigns />;
  else if (me.role !== "owner" && me.role !== "crew") page = <p className="card">Your screens are coming soon.</p>;
  else if (hash === "/assembly") page = <Assembly me={me} />;
  else if (hash === "/reimbursements" && !owner) page = <Reimbursements />;
  else if (!owner) page = <LogHours me={me} />;
  else if (jobMatch) page = <JobDetail id={jobMatch[1]} />;
  else if (hash === "/new") page = <NewJob />;
  else if (hash === "/hours") page = <LogHours me={me} />;
  else if (hash === "/approvals") page = <Approvals />;
  else if (hash === "/designs") page = <DesignPipeline />;
  else if (hash === "/collections") page = <Collections />;
  else if (hash === "/inbox") page = <Inbox />;
  else if (hash === "/team") page = <Team />;
  else page = <JobsList />;

  return (
    <div className="app">
      <header className="top">
        <strong>Mill City Jobs</strong>
        <nav>
          {nav.map(([to, label]) => (
            <a key={to} href={`#${to}`} className={hash === to ? "active" : ""}>{label}</a>
          ))}
        </nav>
        <button className="link" onClick={logout}>Log out</button>
      </header>
      <main>{page}</main>
    </div>
  );
}

function Login({ onLogin }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submit, busy, error] = useSubmit(async () => onLogin(await api("/login", { method: "POST", body: { email, password } })));
  return (
    <form className="card login" onSubmit={submit}>
      <h1>Mill City Jobs</h1>
      <Field label="Email"><input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required /></Field>
      <Field label="Password"><input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></Field>
      <ErrorText error={error} />
      <button disabled={busy}>{busy ? "Logging in…" : "Log in"}</button>
    </form>
  );
}

function JobsList() {
  const [status, setStatus] = useState("");
  const [jobs, error] = useLoad(`/jobs${status ? `?status=${status}` : ""}`, [status]);
  return (
    <>
      <div className="row between">
        <h1>Jobs</h1>
        <a className="button" href="#/new">New job</a>
      </div>
      <IntakeQueue />
      <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Filter by status">
        <option value="">All statuses</option>
        {STATUSES.map((s) => <option key={s}>{s}</option>)}
      </select>
      <ErrorText error={error} />
      {jobs?.length === 0 && <p className="muted">No jobs yet.</p>}
      <ul className="list">
        {jobs?.map((j) => (
          <li key={j.id}>
            <a className="card job" href={`#/jobs/${j.id}`}>
              <div className="row between">
                <strong>{j.name}</strong>
                <span className="pill">{j.status}{j.frozen ? " · Frozen" : ""}</span>
              </div>
              <div className="row between muted">
                <span>{j.rep_name || "No rep"}</span>
                <span>{usd(j.adjusted_revenue)}</span>
              </div>
              <div className="row between">
                <span>Net {usd(j.net)}{j.margin_pct != null && ` · ${j.margin_pct}%`}</span>
                {Number(j.balance_due) > 0 && <span className="muted">Due {usd(j.balance_due)}</span>}
                {j.material_pending && <span className="warn">Material total pending</span>}
              </div>
            </a>
          </li>
        ))}
      </ul>
    </>
  );
}

function JobFields({ job, reps, onChange }) {
  const set = (k) => (e) => onChange({ ...job, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });
  return (
    <div className="grid">
      <Field label="Job name"><input value={job.name ?? ""} onChange={set("name")} required /></Field>
      <Field label="Customer"><input value={job.customer ?? ""} onChange={set("customer")} /></Field>
      <Field label="Sales rep">
        <select value={job.rep_id ?? ""} onChange={set("rep_id")}>
          <option value="">None</option>
          {reps.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
        </select>
      </Field>
      <Field label="Manufacturer"><input value={job.manufacturer ?? ""} onChange={set("manufacturer")} /></Field>
      <Field label="Invoice total ($)"><input type="number" step="0.01" min="0" value={job.invoice_total ?? ""} onChange={set("invoice_total")} /></Field>
      <Field label="Material total ($)"><input type="number" step="0.01" min="0" value={job.material_total ?? ""} onChange={set("material_total")} placeholder="Blank if not known yet" /></Field>
      <Field label="Countertop invoice ($)"><input type="number" step="0.01" min="0" value={job.countertop_invoice ?? ""} onChange={set("countertop_invoice")} /></Field>
      <Field label="Referral payout ($)"><input type="number" step="0.01" min="0" value={job.referral_amount ?? ""} onChange={set("referral_amount")} /></Field>
      <Field label="Boxes"><input type="number" min="0" value={job.boxes ?? ""} onChange={set("boxes")} /></Field>
      <Field label="Modifications"><input type="number" min="0" value={job.modifications ?? ""} onChange={set("modifications")} /></Field>
      <Field label="Install date"><input type="date" value={job.install_date ?? ""} onChange={set("install_date")} /></Field>
      <Field label="Status">
        <select value={job.status ?? "Ordered"} onChange={set("status")}>
          {STATUSES.map((s) => <option key={s}>{s}</option>)}
        </select>
      </Field>
      <label className="check"><input type="checkbox" checked={!!job.frozen} onChange={set("frozen")} /> Frozen (commission on hold)</label>
    </div>
  );
}

// Form values are strings; blank optional fields go to the API as null, counts as numbers.
function jobBody(job) {
  const out = {};
  for (const k of ["name", "customer", "manufacturer", "status", "frozen"]) if (k in job) out[k] = job[k];
  for (const k of ["rep_id", "material_total", "install_date"]) if (k in job) out[k] = job[k] === "" ? null : job[k];
  for (const k of ["invoice_total", "countertop_invoice", "referral_amount", "boxes", "modifications"])
    if (k in job) out[k] = job[k] === "" || job[k] == null ? 0 : job[k];
  return out;
}

function useReps() {
  const [users] = useLoad("/users");
  return (users || []).filter((u) => u.role === "rep" && u.active);
}

function NewJob() {
  const reps = useReps();
  const [job, setJob] = useState({ name: "", status: "Ordered" });
  const [submit, busy, error] = useSubmit(async () => {
    const created = await api("/jobs", { method: "POST", body: jobBody(job) });
    location.hash = `/jobs/${created.id}`;
  });
  return (
    <form className="card" onSubmit={submit}>
      <h1>New job</h1>
      <JobFields job={job} reps={reps} onChange={setJob} />
      <ErrorText error={error} />
      <button disabled={busy}>Create job</button>
    </form>
  );
}

function JobDetail({ id }) {
  const [job, error, reloadJob, setJob] = useLoad(`/jobs/${id}`, [id]);
  const [editing, setEditing] = useState(null);
  const [open, setOpen] = useState(null);
  const reps = useReps();
  const [save, busy, saveError] = useSubmit(async () => {
    setJob(await api(`/jobs/${id}`, { method: "PATCH", body: jobBody(editing) }));
    setEditing(null);
  });
  const [remove, , removeError] = useSubmit(async (entry) => {
    if (!confirm("Delete this entry?")) return;
    if (entry.kind === "ledger") setJob(await api(`/ledger/${entry.id}`, { method: "DELETE" }));
    else {
      await api(`/labor/${entry.laborId}`, { method: "DELETE" });
      setJob(await api(`/jobs/${id}`));
    }
  });

  if (error) return <ErrorText error={error} />;
  if (!job) return <p className="muted">Loading…</p>;
  const w = job.waterfall;
  const entriesFor = (n) => [
    ...job.ledger.filter((e) => e.line === n).map((e) => ({ ...e, kind: "ledger" })),
    ...(n === 6 ? job.labor.map((e) => ({ id: `l${e.id}`, laborId: e.id, amount: e.total, payee: e.worker, note: `${e.hours} h × ${usd(e.rate)}`, entry_date: e.work_date, kind: "labor" })) : []),
    ...(n === 5 ? job.assembly.filter(counted).map((e) => ({ id: `a${e.id}`, amount: e.total, payee: e.worker, note: `${e.boxes} boxes, ${e.modifications} mods${e.note ? ` · ${e.note}` : ""}`, entry_date: e.work_date })) : []),
    ...(n === 8 ? job.designs.map((d) => ({ id: `d${d.id}`, amount: d.charge, payee: d.designer, note: `${d.design_name}${d.revisions ? ` · ${d.revisions} revision${d.revisions === 1 ? "" : "s"}` : ""}`, entry_date: d.design_date })) : []),
    ...(n === 4 ? job.reimbursements.filter(counted).map((r) => ({ id: `r${r.id}`, amount: r.amount, payee: r.user, note: `Reimbursement · ${r.purpose}`, entry_date: r.spent_on })) : []),
  ];

  return (
    <>
      <a href="#/" className="back">← Jobs</a>
      <div className="row between">
        <h1>{job.name}</h1>
        <span className="pill">{job.status}{job.frozen ? " · Frozen" : ""}</span>
      </div>
      <p className="muted">{[job.customer, job.rep_name, job.manufacturer, job.install_date && `Install ${job.install_date}`].filter(Boolean).join(" · ")}</p>

      {editing ? (
        <form className="card" onSubmit={save}>
          <JobFields job={editing} reps={reps} onChange={setEditing} />
          <ErrorText error={saveError} />
          <div className="row">
            <button disabled={busy}>Save</button>
            <button type="button" className="secondary" onClick={() => setEditing(null)}>Cancel</button>
          </div>
        </form>
      ) : (
        <button className="secondary" onClick={() => setEditing({ ...job })}>Edit job</button>
      )}

      <section className="card">
        <h2>Profit waterfall</h2>
        {w.material_pending && <p className="warn">Manufacturer material total not in yet.</p>}
        <table className="waterfall">
          <tbody>
            {w.lines.map((l) => {
              const entries = entriesFor(l.line);
              return [
                <tr key={l.line} className={SUBTOTALS.has(l.line) ? "subtotal" : ""} onClick={() => entries.length && setOpen(open === l.line ? null : l.line)}>
                  <td className="num">{l.line}</td>
                  <td>{l.label}{entries.length > 0 && <span className="muted"> ({entries.length})</span>}</td>
                  <td className="amount">{l.line >= 4 && l.line <= 12 || l.line === 14 ? `− ${usd(l.amount)}` : usd(l.amount)}</td>
                </tr>,
                open === l.line && entries.map((e) => (
                  <tr key={e.id} className="entry">
                    <td />
                    <td>{e.entry_date} · {e.payee || ""} {e.note && <span className="muted">{e.note}</span>}</td>
                    <td className="amount">
                      {usd(e.amount)}{" "}
                      {e.kind && <button className="link" onClick={() => remove(e)} aria-label="Delete entry">✕</button>}
                    </td>
                  </tr>
                )),
              ];
            })}
          </tbody>
        </table>
        <ErrorText error={removeError} />
        {w.margin_pct != null && <p className="muted">Net margin {w.margin_pct}% of adjusted revenue</p>}
        <p>Collected {usd(w.collected)} · <strong>Balance due {usd(w.balance_due)}</strong></p>
        {job.status === "Closed" && Number(w.commission) > 0 && (
          <p className="row between">
            <span>{job.commission_paid_at ? `Commission paid ${job.commission_paid_at.slice(0, 10)}` : job.frozen ? "Commission frozen" : `Commission due: ${usd(w.commission)}`}</span>
            <button className="link" onClick={() => api(`/jobs/${id}/commission-paid`, { method: "POST", body: { paid: !job.commission_paid_at } }).then(setJob)}>
              {job.commission_paid_at ? "Undo" : "Mark commission paid"}
            </button>
          </p>
        )}
      </section>

      <JobPayments job={job} onSaved={setJob} />
      <JobDesigns job={job} reload={reloadJob} />
      <AddEntry jobId={id} onSaved={setJob} />
    </>
  );
}

function AddEntry({ jobId, onSaved }) {
  const blank = { line: "4", amount: "", payee: "", note: "", entry_date: today() };
  const [entry, setEntry] = useState(blank);
  const set = (k) => (e) => setEntry({ ...entry, [k]: e.target.value });
  const [submit, busy, error] = useSubmit(async () => {
    onSaved(await api(`/jobs/${jobId}/ledger`, { method: "POST", body: { ...entry, line: Number(entry.line) } }));
    setEntry(blank);
  });
  return (
    <form className="card" onSubmit={submit}>
      <h2>Add a cost or change order</h2>
      <div className="grid">
        <Field label="Line">
          <select value={entry.line} onChange={set("line")}>
            {Object.entries(ENTRY_LINES).map(([n, label]) => <option key={n} value={n}>{label}</option>)}
          </select>
        </Field>
        <Field label="Amount ($)"><input type="number" step="0.01" value={entry.amount} onChange={set("amount")} required /></Field>
        <Field label="Paid to"><input value={entry.payee} onChange={set("payee")} /></Field>
        <Field label="Date"><input type="date" value={entry.entry_date} onChange={set("entry_date")} /></Field>
        <Field label={entry.line === "11" ? "Reason (required)" : "Note"}><input value={entry.note} onChange={set("note")} required={entry.line === "11"} /></Field>
      </div>
      <ErrorText error={error} />
      <button disabled={busy}>Add</button>
    </form>
  );
}

function LogHours({ me }) {
  const owner = me.role === "owner";
  const [jobs] = useLoad("/job-names");
  const [users] = useLoad(owner ? "/users" : "/me");
  const [mine, , reloadMine] = useLoad("/labor/mine");
  const crew = owner ? (users || []).filter((u) => u.role === "crew" && u.active) : [];
  const blank = { job_id: "", hours: "", work_date: today(), worker_id: "" };
  const [form, setForm] = useState(blank);
  const [done, setDone] = useState(null);
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  const [submit, busy, error] = useSubmit(async () => {
    const body = { job_id: Number(form.job_id), hours: form.hours, work_date: form.work_date };
    if (owner) body.worker_id = Number(form.worker_id);
    const saved = await api("/labor", { method: "POST", body });
    setDone(`Logged ${saved.hours} h on ${saved.job}`);
    setForm({ ...blank, worker_id: form.worker_id });
    reloadMine();
  });

  return (
    <>
      <form className="card" onSubmit={submit}>
        <h1>Log hours</h1>
        <div className="grid">
          {owner && (
            <Field label="Who worked">
              <select value={form.worker_id} onChange={set("worker_id")} required>
                <option value="">Pick a person</option>
                {crew.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
              </select>
            </Field>
          )}
          <Field label="Job">
            <select value={form.job_id} onChange={set("job_id")} required>
              <option value="">Pick a job</option>
              {jobs?.map((j) => <option key={j.id} value={j.id}>{j.name}</option>)}
            </select>
          </Field>
          <Field label="Date"><input type="date" value={form.work_date} max={today()} onChange={set("work_date")} required /></Field>
          <Field label="Hours"><input type="number" inputMode="decimal" step="0.25" min="0.25" max="24" value={form.hours} onChange={set("hours")} required /></Field>
        </div>
        <ErrorText error={error} />
        {done && <p className="ok" role="status">{done}</p>}
        <button disabled={busy}>Save hours</button>
      </form>
      {!owner && <MyHours entries={mine} jobs={jobs} reload={reloadMine} />}
    </>
  );
}

function MyHours({ entries, jobs, reload }) {
  const [editing, setEditing] = useState(null);
  const [save, busy, error] = useSubmit(async () => {
    await api(`/labor/${editing.id}`, { method: "PATCH", body: { job_id: Number(editing.job_id), hours: editing.hours, work_date: editing.work_date } });
    setEditing(null);
    reload();
  });
  const [remove, , removeError] = useSubmit(async (entryId) => {
    if (confirm("Delete this entry?")) {
      await api(`/labor/${entryId}`, { method: "DELETE" });
      reload();
    }
  });
  if (!entries) return null;
  const total = entries.reduce((s, e) => s + Number(e.hours), 0);
  return (
    <section className="card">
      <div className="row between"><h2>My hours</h2><span className="muted">{total} h total</span></div>
      <ErrorText error={error || removeError} />
      {entries.length === 0 && <p className="muted">Nothing logged yet.</p>}
      <ul className="list">
        {entries.map((e) => (
          <li key={e.id} className="row between entry-row">
            {editing?.id === e.id ? (
              <form className="grid" onSubmit={save}>
                <select value={editing.job_id} onChange={(ev) => setEditing({ ...editing, job_id: ev.target.value })}>
                  {jobs?.map((j) => <option key={j.id} value={j.id}>{j.name}</option>)}
                </select>
                <input type="date" value={editing.work_date} onChange={(ev) => setEditing({ ...editing, work_date: ev.target.value })} />
                <input type="number" step="0.25" min="0.25" max="24" value={editing.hours} onChange={(ev) => setEditing({ ...editing, hours: ev.target.value })} />
                <div className="row">
                  <button disabled={busy}>Save</button>
                  <button type="button" className="secondary" onClick={() => setEditing(null)}>Cancel</button>
                </div>
              </form>
            ) : (
              <>
                <span>{e.work_date} · {e.job}</span>
                <span>
                  {e.hours} h{" "}
                  {e.editable ? (
                    <>
                      <button className="link" onClick={() => setEditing({ ...e })}>Edit</button>
                      <button className="link" onClick={() => remove(e.id)}>Delete</button>
                    </>
                  ) : <span className="muted">Locked</span>}
                </span>
              </>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

function Team() {
  const [users, error, reload] = useLoad("/users");
  const blank = { name: "", email: "", password: "", role: "crew", hourly_rate: "", commission_rate: "" };
  const [form, setForm] = useState(blank);
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  const [submit, busy, addError] = useSubmit(async () => {
    const body = { ...form, hourly_rate: form.hourly_rate || null, commission_rate: form.commission_rate ? Number(form.commission_rate) / 100 : null };
    await api("/users", { method: "POST", body });
    setForm(blank);
    reload();
  });
  const [patch, , patchError] = useSubmit(async ([u, change]) => {
    await api(`/users/${u.id}`, { method: "PATCH", body: change });
    reload();
  });
  const editRate = (u) => {
    const field = u.role === "rep" ? "commission_rate" : "hourly_rate";
    const current = u.role === "rep" ? (u.commission_rate ? Number(u.commission_rate) * 100 : "") : u.hourly_rate ?? "";
    const value = prompt(u.role === "rep" ? `${u.name} commission %` : `${u.name} hourly rate ($)`, current);
    if (value === null || value === "") return;
    patch([u, { [field]: u.role === "rep" ? Number(value) / 100 : value }]);
  };
  const resetPassword = (u) => {
    const value = prompt(`New password for ${u.name} (8+ characters)`);
    if (value) patch([u, { password: value }]);
  };

  return (
    <>
      <h1>Team</h1>
      <ErrorText error={error || patchError} />
      <ul className="list">
        {users?.map((u) => (
          <li key={u.id} className={`card ${u.active ? "" : "inactive"}`}>
            <div className="row between">
              <strong>{u.name}</strong>
              <span className="pill">{u.role}</span>
            </div>
            <div className="muted">{u.email}</div>
            <div className="row between">
              <span>
                {u.role === "crew" && (u.hourly_rate ? `${usd(u.hourly_rate)}/h` : "No rate set")}
                {u.role === "rep" && (u.commission_rate ? `${Number(u.commission_rate) * 100}% commission` : "No commission set")}
              </span>
              <span>
                {(u.role === "crew" || u.role === "rep") && <button className="link" onClick={() => editRate(u)}>Rate</button>}
                <button className="link" onClick={() => resetPassword(u)}>Password</button>
                {u.role !== "owner" && (
                  <button className="link" onClick={() => patch([u, { active: !u.active }])}>{u.active ? "Deactivate" : "Reactivate"}</button>
                )}
              </span>
            </div>
          </li>
        ))}
      </ul>
      <form className="card" onSubmit={submit}>
        <h2>Add a login</h2>
        <div className="grid">
          <Field label="Name"><input value={form.name} onChange={set("name")} required /></Field>
          <Field label="Email"><input type="email" value={form.email} onChange={set("email")} required /></Field>
          <Field label="Temporary password"><input value={form.password} onChange={set("password")} minLength={8} required /></Field>
          <Field label="Role">
            <select value={form.role} onChange={set("role")}>
              <option value="crew">Crew (install, assembly, delivery)</option>
              <option value="designer">Designer</option>
              <option value="rep">Sales rep</option>
              <option value="owner">Owner</option>
            </select>
          </Field>
          {form.role === "crew" && <Field label="Hourly rate ($)"><input type="number" step="0.01" min="0" value={form.hourly_rate} onChange={set("hourly_rate")} /></Field>}
          {form.role === "rep" && <Field label="Commission (%)"><input type="number" step="1" min="0" max="100" value={form.commission_rate} onChange={set("commission_rate")} /></Field>}
        </div>
        <ErrorText error={addError} />
        <button disabled={busy}>Add login</button>
      </form>
    </>
  );
}

function Assembly({ me }) {
  const owner = me.role === "owner";
  const [jobs] = useLoad("/job-names");
  const [users] = useLoad(owner ? "/users" : "/me");
  const [mine, , reloadMine] = useLoad("/assembly/mine");
  const crew = owner ? (users || []).filter((u) => u.role === "crew" && u.active) : [];
  const blank = { job_id: "", boxes: "", modifications: "", extra_cost: "", note: "", work_date: today(), worker_id: "" };
  const [form, setForm] = useState(blank);
  const [done, setDone] = useState(null);
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  const job = jobs?.find((j) => String(j.id) === form.job_id);
  const [submit, busy, error] = useSubmit(async () => {
    const body = {
      job_id: Number(form.job_id), boxes: Number(form.boxes || 0), modifications: Number(form.modifications || 0),
      extra_cost: form.extra_cost || 0, note: form.note || null, work_date: form.work_date,
    };
    if (owner) body.worker_id = Number(form.worker_id);
    const saved = await api("/assembly", { method: "POST", body });
    setDone(owner ? `Logged ${usd(saved.total)} on ${saved.job}` : `Sent for approval: ${saved.boxes} boxes on ${saved.job}`);
    setForm({ ...blank, worker_id: form.worker_id });
    reloadMine();
  });
  const [remove, , removeError] = useSubmit(async (id) => {
    if (confirm("Delete this entry?")) {
      await api(`/assembly/${id}`, { method: "DELETE" });
      reloadMine();
    }
  });

  return (
    <>
      <form className="card" onSubmit={submit}>
        <h1>Log assembly</h1>
        <div className="grid">
          {owner && (
            <Field label="Who built it">
              <select value={form.worker_id} onChange={set("worker_id")} required>
                <option value="">Pick a person</option>
                {crew.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
              </select>
            </Field>
          )}
          <Field label="Job">
            <select value={form.job_id} onChange={set("job_id")} required>
              <option value="">Pick a job</option>
              {jobs?.map((j) => <option key={j.id} value={j.id}>{j.name}</option>)}
            </select>
          </Field>
          <Field label="Date"><input type="date" value={form.work_date} max={today()} onChange={set("work_date")} required /></Field>
          <Field label="Boxes built"><input type="number" inputMode="numeric" min="0" value={form.boxes} onChange={set("boxes")} /></Field>
          <Field label="Modifications"><input type="number" inputMode="numeric" min="0" value={form.modifications} onChange={set("modifications")} /></Field>
          <Field label="Extra modification cost ($)"><input type="number" inputMode="decimal" step="0.01" min="0" value={form.extra_cost} onChange={set("extra_cost")} /></Field>
          <Field label={form.extra_cost ? "What was the extra cost for? (required)" : "Note"}>
            <input value={form.note} onChange={set("note")} required={!!Number(form.extra_cost)} />
          </Field>
        </div>
        {job && (job.boxes > 0 || job.modifications > 0) && (
          <p className="muted">Order calls for {job.boxes} boxes and {job.modifications} modifications.</p>
        )}
        <ErrorText error={error} />
        {done && <p className="ok" role="status">{done}</p>}
        <button disabled={busy}>{owner ? "Save" : "Send for approval"}</button>
      </form>
      {mine?.length > 0 && (
        <section className="card">
          <h2>{owner ? "Assembly you logged for yourself" : "My assembly"}</h2>
          <ErrorText error={removeError} />
          <ul className="list">
            {mine.map((e) => (
              <li key={e.id} className="entry-row">
                <div className="row between">
                  <span>{e.work_date} · {e.job}</span>
                  <StatusPill entry={e} />
                </div>
                <div className="row between muted">
                  <span>{e.boxes} boxes · {e.modifications} mods{Number(e.extra_cost) ? ` · +${usd(e.extra_cost)}` : ""}</span>
                  <span>
                    {usd(e.total)}
                    {e.status === "submitted" && <button className="link" onClick={() => remove(e.id)}>Delete</button>}
                  </span>
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}

function Reimbursements() {
  const [jobs] = useLoad("/job-names");
  const [mine, , reload] = useLoad("/reimbursements/mine");
  const blank = { amount: "", purpose: "", job_id: "", spent_on: today() };
  const [form, setForm] = useState(blank);
  const [file, setFile] = useState(null);
  const [fileKey, setFileKey] = useState(0);
  const [done, setDone] = useState(null);
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  const [submit, busy, error] = useSubmit(async () => {
    const data = new FormData();
    for (const [k, v] of Object.entries(form)) if (v !== "") data.append(k, v);
    data.append("receipt", await shrinkPhoto(file));
    const saved = await api("/reimbursements", { method: "POST", body: data });
    setDone(`Sent for approval: ${usd(saved.amount)} for ${saved.purpose}`);
    setForm(blank);
    setFile(null);
    setFileKey((k) => k + 1); // clears the file input
    reload();
  });
  const [remove, , removeError] = useSubmit(async (id) => {
    if (confirm("Delete this reimbursement?")) {
      await api(`/reimbursements/${id}`, { method: "DELETE" });
      reload();
    }
  });
  const sum = (status) => (mine || []).filter((r) => r.status === status).reduce((s, r) => s + Number(r.amount), 0);

  return (
    <>
      <form className="card" onSubmit={submit}>
        <h1>Get reimbursed</h1>
        <div className="grid">
          <Field label="Amount ($)"><input type="number" inputMode="decimal" step="0.01" min="0.01" value={form.amount} onChange={set("amount")} required /></Field>
          <Field label="What was it for?"><input value={form.purpose} onChange={set("purpose")} placeholder="Fuel, Home Depot fillers…" required /></Field>
          <Field label="Job">
            <select value={form.job_id} onChange={set("job_id")}>
              <option value="">No job (fuel, shop supplies)</option>
              {jobs?.map((j) => <option key={j.id} value={j.id}>{j.name}</option>)}
            </select>
          </Field>
          <Field label="Date"><input type="date" value={form.spent_on} max={today()} onChange={set("spent_on")} required /></Field>
          <Field label="Receipt photo">
            <input key={fileKey} type="file" accept="image/*,application/pdf" onChange={(e) => setFile(e.target.files[0] || null)} required />
          </Field>
        </div>
        <ErrorText error={error} />
        {done && <p className="ok" role="status">{done}</p>}
        <button disabled={busy}>{busy ? "Sending…" : "Send for approval"}</button>
      </form>
      <section className="card">
        <div className="row between">
          <h2>My reimbursements</h2>
          <span className="muted">Waiting {usd(sum("submitted"))} · Owed {usd(sum("approved"))}</span>
        </div>
        <ErrorText error={removeError} />
        {mine?.length === 0 && <p className="muted">None yet.</p>}
        <ul className="list">
          {mine?.map((r) => (
            <li key={r.id} className="entry-row">
              <div className="row between">
                <span>{r.spent_on} · {r.purpose}</span>
                <StatusPill entry={r} />
              </div>
              <div className="row between muted">
                <span>{r.job || "No job"} · <a href={r.receipt_url} target="_blank" rel="noreferrer">Receipt</a></span>
                <span>
                  {usd(r.amount)}
                  {r.status === "submitted" && <button className="link" onClick={() => remove(r.id)}>Delete</button>}
                </span>
              </div>
            </li>
          ))}
        </ul>
      </section>
    </>
  );
}

function Approvals() {
  const [data, error, reload] = useLoad("/approvals");
  const [act, busy, actError] = useSubmit(async ([kind, item, status]) => {
    let note = null;
    if (status === "rejected") {
      note = prompt("Reason (they'll see this)");
      if (note === null) return;
    }
    await api(`/${kind}/${item.id}/status`, { method: "POST", body: { status, note } });
    reload();
  });
  if (error) return <ErrorText error={error} />;
  if (!data) return <p className="muted">Loading…</p>;

  const items = [
    ...data.assembly.map((e) => ({ ...e, kind: "assembly", who: e.worker, amount: e.total, date: e.work_date, what: `Assembly · ${e.boxes} boxes, ${e.modifications} mods${e.note ? ` · ${e.note}` : ""}` })),
    ...data.reimbursements.map((r) => ({ ...r, kind: "reimbursements", who: r.user, date: r.spent_on, what: `Reimbursement · ${r.purpose}`, job: r.job || "No job" })),
  ];
  const waiting = items.filter((i) => i.status === "submitted");
  const owedBy = {}; // not Object.groupBy: crew phones may run older Safari
  for (const i of items.filter((i) => i.status === "approved")) (owedBy[i.who] ||= []).push(i);
  const owed = Object.entries(owedBy);

  const row = (i, buttons) => (
    <li key={`${i.kind}${i.id}`} className="entry-row">
      <div className="row between">
        <strong>{i.who}</strong>
        <span>{usd(i.amount)}</span>
      </div>
      <div className="muted">{i.date} · {i.job} · {i.what}</div>
      <div className="row">
        {i.receipt_url && <a href={i.receipt_url} target="_blank" rel="noreferrer">View receipt</a>}
        {buttons}
      </div>
    </li>
  );

  return (
    <>
      <h1>Approvals</h1>
      <ErrorText error={actError} />
      <section className="card">
        <h2>Waiting for you ({waiting.length})</h2>
        {waiting.length === 0 && <p className="muted">Nothing waiting.</p>}
        <ul className="list">
          {waiting.map((i) => row(i, (
            <>
              <button disabled={busy} onClick={() => act([i.kind, i, "approved"])}>Approve</button>
              <button disabled={busy} className="secondary" onClick={() => act([i.kind, i, "rejected"])}>Reject</button>
            </>
          )))}
        </ul>
      </section>
      <section className="card">
        <h2>Approved, not paid yet</h2>
        {owed.length === 0 && <p className="muted">Everyone's paid up.</p>}
        {owed.map(([who, list]) => (
          <div key={who}>
            <div className="row between owed-head">
              <strong>{who}</strong>
              <span>{usd(list.reduce((s, i) => s + Number(i.amount), 0))}</span>
            </div>
            <ul className="list">
              {list.map((i) => row(i, (
                <>
                  <button disabled={busy} onClick={() => act([i.kind, i, "paid"])}>Mark paid</button>
                  <button disabled={busy} className="link" onClick={() => act([i.kind, i, "submitted"])}>Undo approval</button>
                </>
              )))}
            </ul>
          </div>
        ))}
      </section>
    </>
  );
}

function JobDesigns({ job, reload }) {
  const [suggestions, , reloadSuggestions] = useLoad(`/jobs/${job.id}/design-suggestions`, [job.id]);
  const [link, busy, error] = useSubmit(async ([designId, jobId]) => {
    await api(`/designs/${designId}/match`, { method: "POST", body: { job_id: jobId } });
    reload();
    reloadSuggestions();
  });
  if (!job.designs.length && !suggestions?.length) return null;
  return (
    <section className="card">
      <h2>Designs</h2>
      <ErrorText error={error} />
      <ul className="list">
        {job.designs.map((d) => (
          <li key={d.id} className="row between entry-row">
            <span>{d.design_name} <span className="muted">· {d.designer} · {d.design_date}</span></span>
            <span>{usd(d.charge)} <button className="link" disabled={busy} onClick={() => link([d.id, null])}>Unlink</button></span>
          </li>
        ))}
      </ul>
      {suggestions?.length > 0 && (
        <>
          <p className="muted">Designs that look like this job:</p>
          <ul className="list">
            {suggestions.map((d) => (
              <li key={d.id} className="row between entry-row">
                <span>{d.design_name} <span className="muted">· {d.designer} · {d.design_date}</span></span>
                <span>{usd(d.charge)} <button disabled={busy} onClick={() => link([d.id, job.id])}>Link</button></span>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

function MyDesigns() {
  const [mine, , reload] = useLoad("/designs/mine");
  const blank = { design_name: "", design_date: today(), revisions: "", charge: "", note: "" };
  const [form, setForm] = useState(blank);
  const [done, setDone] = useState(null);
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  const [submit, busy, error] = useSubmit(async () => {
    const saved = await api("/designs", { method: "POST", body: { ...form, revisions: Number(form.revisions || 0), note: form.note || null } });
    setDone(`Logged ${saved.design_name}`);
    setForm(blank);
    reload();
  });
  const [remove, , removeError] = useSubmit(async (id) => {
    if (confirm("Delete this design?")) {
      await api(`/designs/${id}`, { method: "DELETE" });
      reload();
    }
  });
  const matched = (mine || []).filter((d) => d.job).length;
  return (
    <>
      <form className="card" onSubmit={submit}>
        <h1>Log a design</h1>
        <div className="grid">
          <Field label="Design name"><input value={form.design_name} onChange={set("design_name")} placeholder="Customer or project name" required /></Field>
          <Field label="Date"><input type="date" value={form.design_date} max={today()} onChange={set("design_date")} required /></Field>
          <Field label="Revisions"><input type="number" inputMode="numeric" min="0" value={form.revisions} onChange={set("revisions")} /></Field>
          <Field label="Charge ($)"><input type="number" inputMode="decimal" step="0.01" min="0" value={form.charge} onChange={set("charge")} required /></Field>
          <Field label="Note"><input value={form.note} onChange={set("note")} /></Field>
        </div>
        <ErrorText error={error} />
        {done && <p className="ok" role="status">{done}</p>}
        <button disabled={busy}>Save design</button>
      </form>
      <section className="card">
        <div className="row between">
          <h2>My designs</h2>
          <span className="muted">{mine?.length || 0} logged · {matched} turned into jobs</span>
        </div>
        <ErrorText error={removeError} />
        {mine?.length === 0 && <p className="muted">None yet.</p>}
        <ul className="list">
          {mine?.map((d) => (
            <li key={d.id} className="entry-row">
              <div className="row between">
                <span>{d.design_date} · {d.design_name}</span>
                <span>{usd(d.charge)}</span>
              </div>
              <div className="row between muted">
                <span>{d.revisions ? `${d.revisions} revision${d.revisions === 1 ? "" : "s"} · ` : ""}{d.job ? `Job: ${d.job}` : "Not matched to a job yet"}</span>
                {!d.job && <button className="link" onClick={() => remove(d.id)}>Delete</button>}
              </div>
            </li>
          ))}
        </ul>
      </section>
    </>
  );
}

function DesignPipeline() {
  const [designs, error, reload] = useLoad("/designs");
  const [jobs] = useLoad("/job-names");
  const [link, busy, linkError] = useSubmit(async ([designId, jobId]) => {
    await api(`/designs/${designId}/match`, { method: "POST", body: { job_id: jobId } });
    reload();
  });
  if (error) return <ErrorText error={error} />;
  if (!designs) return <p className="muted">Loading…</p>;
  const byDesigner = {};
  for (const d of designs) (byDesigner[d.designer] ||= []).push(d);
  const unmatched = designs.filter((d) => !d.job);
  return (
    <>
      <h1>Designs</h1>
      <section className="card">
        <h2>Close rate</h2>
        {designs.length === 0 && <p className="muted">No designs logged yet.</p>}
        <table className="waterfall">
          <tbody>
            {Object.entries(byDesigner).map(([who, list]) => {
              const won = list.filter((d) => d.job);
              return (
                <tr key={who}>
                  <td>{who}</td>
                  <td>{won.length} of {list.length} became jobs ({Math.round((won.length / list.length) * 100)}%)</td>
                  <td className="amount">{usd(list.reduce((s, d) => s + Number(d.charge), 0))} charged</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>
      <section className="card">
        <h2>Not matched to a job ({unmatched.length})</h2>
        <ErrorText error={linkError} />
        <ul className="list">
          {unmatched.map((d) => (
            <li key={d.id} className="entry-row">
              <div className="row between">
                <span>{d.design_name} <span className="muted">· {d.designer} · {d.design_date}</span></span>
                <span>{usd(d.charge)}</span>
              </div>
              <select disabled={busy} value="" onChange={(e) => e.target.value && link([d.id, Number(e.target.value)])} aria-label={`Link ${d.design_name} to a job`}>
                <option value="">Link to a job…</option>
                {jobs?.map((j) => <option key={j.id} value={j.id}>{j.name}</option>)}
              </select>
            </li>
          ))}
        </ul>
      </section>
    </>
  );
}

const METHODS = { card: "Card", check: "Check", ach: "ACH / transfer", cash: "Cash", other: "Other" };
const PAY_TYPES = { deposit: "Deposit", progress: "Progress", final: "Final", change_order: "Change order" };

function JobPayments({ job, onSaved }) {
  const blank = { amount: "", method: "card", payment_type: "deposit", status: "received", date: today(), processing_fee: "", note: "" };
  const [form, setForm] = useState(blank);
  const [file, setFile] = useState(null);
  const [fileKey, setFileKey] = useState(0);
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  const [submit, busy, error] = useSubmit(async () => {
    const data = new FormData();
    const { date, ...rest } = form;
    for (const [k, v] of Object.entries(rest)) if (v !== "") data.append(k, v);
    data.append(form.status === "received" ? "received_date" : "expected_date", date);
    if (file) data.append("screenshot", await shrinkPhoto(file));
    onSaved(await api(`/jobs/${job.id}/payments`, { method: "POST", body: data }));
    setForm(blank);
    setFile(null);
    setFileKey((k) => k + 1);
  });
  const [act, actBusy, actError] = useSubmit(async ([p, change]) => {
    if (change === "delete") {
      if (confirm("Delete this payment?")) onSaved(await api(`/payments/${p.id}`, { method: "DELETE" }));
    } else onSaved(await api(`/payments/${p.id}`, { method: "PATCH", body: change }));
  });

  return (
    <section className="card">
      <h2>Payments</h2>
      <ErrorText error={actError} />
      {job.payments.length === 0 && <p className="muted">No payments yet.</p>}
      <ul className="list">
        {job.payments.map((p) => (
          <li key={p.id} className="entry-row">
            <div className="row between">
              <span>{p.status === "pending" ? `Expected ${p.expected_date || "soon"}` : p.received_date} · {METHODS[p.method]} · {PAY_TYPES[p.payment_type]}</span>
              <strong>{usd(p.amount)}</strong>
            </div>
            <div className="row between muted">
              <span>
                {p.status === "pending" ? <span className="pill status-submitted">Pending</span> : "Received"}
                {Number(p.processing_fee) > 0 && ` · fee ${usd(p.processing_fee)}`}
                {p.note && ` · ${p.note}`}
              </span>
              <span>
                {p.screenshot_url && <a href={p.screenshot_url} target="_blank" rel="noreferrer">Screenshot</a>}
                {p.status === "pending" && <button className="link" disabled={actBusy} onClick={() => act([p, { status: "received", received_date: today() }])}>Mark received</button>}
                <button className="link" disabled={actBusy} onClick={() => act([p, "delete"])}>Delete</button>
              </span>
            </div>
          </li>
        ))}
      </ul>
      <form onSubmit={submit}>
        <h2>Add a payment</h2>
        <div className="grid">
          <Field label="Amount ($)"><input type="number" inputMode="decimal" step="0.01" min="0.01" value={form.amount} onChange={set("amount")} required /></Field>
          <Field label="Method">
            <select value={form.method} onChange={set("method")}>
              {Object.entries(METHODS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </Field>
          <Field label="Type">
            <select value={form.payment_type} onChange={set("payment_type")}>
              {Object.entries(PAY_TYPES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </Field>
          <Field label="Status">
            <select value={form.status} onChange={set("status")}>
              <option value="received">Received</option>
              <option value="pending">Pending / on its way</option>
            </select>
          </Field>
          <Field label={form.status === "received" ? "Date received" : "Expected date"}><input type="date" value={form.date} onChange={set("date")} required /></Field>
          {form.method === "card" && <Field label="Card fee ($)"><input type="number" inputMode="decimal" step="0.01" min="0" value={form.processing_fee} onChange={set("processing_fee")} /></Field>}
          <Field label="Note"><input value={form.note} onChange={set("note")} /></Field>
          <Field label="Screenshot (optional)"><input key={fileKey} type="file" accept="image/*,application/pdf" onChange={(e) => setFile(e.target.files[0] || null)} /></Field>
        </div>
        <ErrorText error={error} />
        <button disabled={busy}>Add payment</button>
      </form>
    </section>
  );
}

function Collections() {
  const [period, setPeriod] = useState("week");
  const [data, error, reload] = useLoad(`/collections?period=${period}&count=${period === "day" ? 14 : period === "year" ? 3 : 8}`, [period]);
  const [paid, busy, paidError] = useSubmit(async (jobId) => {
    await api(`/jobs/${jobId}/commission-paid`, { method: "POST", body: { paid: true } });
    reload();
  });
  const newestFirst = (list) => [...list].reverse(); // current period shows first on a phone
  const table = (rows, totalRow) => (
    <div className="scroll">
      <table className="grid-table">
        <thead>
          <tr><th />{newestFirst(data.buckets).map((b) => <th key={b.start}>{b.label}</th>)}<th>Total</th></tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.rep}><th>{METHODS[r.rep] || r.rep}</th>{newestFirst(r.amounts).map((a, i) => <td key={i}>{Number(a) ? usd(a) : "—"}</td>)}<td><strong>{usd(r.total)}</strong></td></tr>
          ))}
          {totalRow && (
            <tr className="subtotal"><th>All reps</th>{newestFirst(totalRow).map((a, i) => <td key={i}>{usd(a)}</td>)}<td>{usd(totalRow.reduce((s, a) => s + Number(a), 0))}</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );

  return (
    <>
      <h1>Collections</h1>
      <div className="row segmented" role="group" aria-label="Period">
        {["day", "week", "month", "quarter", "year"].map((p) => (
          <button key={p} className={p === period ? "" : "secondary"} onClick={() => setPeriod(p)}>{p[0].toUpperCase() + p.slice(1)}</button>
        ))}
      </div>
      <ErrorText error={error || paidError} />
      {!data ? <p className="muted">Loading…</p> : (
        <>
          <section className="card">
            <h2>Collected by rep</h2>
            {data.collected.length === 0 ? <p className="muted">No payments received in this range.</p> : table(data.collected, data.collected_total)}
          </section>
          <section className="card">
            <h2>Pending and on its way</h2>
            {data.pending.length === 0 && <p className="muted">Nothing pending.</p>}
            <ul className="list">
              {data.pending.map((p) => (
                <li key={p.id} className="row between entry-row">
                  <a href={`#/jobs/${p.job_id}`}>{p.job}</a>
                  <span className="muted">{p.rep} · {METHODS[p.method]} · {p.expected_date || "no date"}</span>
                  <strong>{usd(p.amount)}</strong>
                </li>
              ))}
            </ul>
          </section>
          <section className="card">
            <h2>Commission due</h2>
            {data.commission_due.length === 0 && <p className="muted">No closed, paid-in-full jobs waiting on commission.</p>}
            <ul className="list">
              {data.commission_due.map((c) => (
                <li key={c.job_id} className="row between entry-row">
                  <span><a href={`#/jobs/${c.job_id}`}>{c.job}</a> <span className="muted">· {c.rep}</span></span>
                  <span><strong>{usd(c.commission)}</strong> <button className="link" disabled={busy} onClick={() => paid(c.job_id)}>Mark paid</button></span>
                </li>
              ))}
            </ul>
          </section>
          <section className="card">
            <h2>Still owed by customers</h2>
            {data.outstanding.length === 0 && <p className="muted">Every open job is paid up.</p>}
            <ul className="list">
              {data.outstanding.map((o) => (
                <li key={o.job_id} className="row between entry-row">
                  <span><a href={`#/jobs/${o.job_id}`}>{o.job}</a> <span className="muted">· {o.rep}</span></span>
                  <span className="muted">{o.days_since_payment == null ? "No payments yet" : o.days_since_payment === 0 ? "Paid today" : `Last paid ${o.days_since_payment}d ago`}</span>
                  <strong>{usd(o.balance_due)}</strong>
                </li>
              ))}
            </ul>
          </section>
          <section className="card">
            <h2>Sold by rep</h2>
            {data.sold.length === 0 ? <p className="muted">No jobs started in this range.</p> : table(data.sold)}
          </section>
          <section className="card">
            <h2>How customers paid</h2>
            {data.methods.length === 0 ? <p className="muted">No payments received in this range.</p> : table(data.methods)}
          </section>
        </>
      )}
    </>
  );
}

const KIND_LABEL = { payment: "Payment in", hours: "Crew hours", cost: "Job cost" };

function Inbox() {
  const [text, setText] = useState("");
  const [file, setFile] = useState(null);
  const [fileKey, setFileKey] = useState(0);
  const [draft, setDraft] = useState(null);
  const [saved, setSaved] = useState(null);
  const [jobs] = useLoad("/job-names");
  const [users] = useLoad("/users");
  const crew = (users || []).filter((u) => u.role === "crew" && u.active);

  const [read, reading, readError] = useSubmit(async () => {
    setSaved(null);
    const data = new FormData();
    data.append("text", text);
    if (file) data.append("image", await shrinkPhoto(file));
    const d = await api("/inbox/parse", { method: "POST", body: data });
    setDraft({
      kind: d.kind === "unknown" ? "cost" : d.kind, job_id: d.job_id ?? "", worker_id: d.worker_id ?? "",
      amount: d.amount ?? "", hours: d.hours ?? "", date: d.date || today(), method: d.method || "check",
      payment_type: d.payment_type || "progress", status: d.status || "received", processing_fee: d.processing_fee ?? "",
      line: String(d.line || 4), payee: d.payee || "", note: d.note || "",
      confidence: d.confidence, question: d.question, unknown: d.kind === "unknown", cost: d.cost_usd,
    });
  });

  const [save, saving, saveError] = useSubmit(async () => {
    const d = draft;
    const job = jobs?.find((j) => String(j.id) === String(d.job_id));
    if (d.kind === "payment") {
      const data = new FormData();
      for (const [k, v] of Object.entries({ amount: d.amount, method: d.method, payment_type: d.payment_type, status: d.status, processing_fee: d.processing_fee || 0, note: d.note }))
        if (v !== "") data.append(k, v);
      data.append(d.status === "received" ? "received_date" : "expected_date", d.date);
      if (file) data.append("screenshot", await shrinkPhoto(file));
      await api(`/jobs/${d.job_id}/payments`, { method: "POST", body: data });
    } else if (d.kind === "hours") {
      await api("/labor", { method: "POST", body: { job_id: Number(d.job_id), worker_id: Number(d.worker_id), hours: d.hours, work_date: d.date } });
    } else {
      await api(`/jobs/${d.job_id}/ledger`, { method: "POST", body: { line: Number(d.line), amount: d.amount, payee: d.payee || null, note: d.note || null, entry_date: d.date } });
    }
    setSaved({ text: `Saved ${KIND_LABEL[d.kind].toLowerCase()} on ${job?.name}`, jobId: d.job_id });
    setDraft(null);
    setText("");
    setFile(null);
    setFileKey((k) => k + 1);
  });

  const set = (k) => (e) => setDraft({ ...draft, [k]: e.target.value });

  return (
    <>
      <form className="card" onSubmit={read}>
        <h1>Quick log</h1>
        <p className="muted">Type or dictate one line, or add a screenshot. Examples: "Omar 13 hours at Dustin Selden", "Got a $4,200 check from Garcia", "Abraham $2,800 countertops Selden".</p>
        <textarea rows="3" value={text} onChange={(e) => setText(e.target.value)} placeholder="What happened?" aria-label="What happened?" />
        <Field label="Screenshot or receipt (optional)">
          <input key={fileKey} type="file" accept="image/*,application/pdf" onChange={(e) => setFile(e.target.files[0] || null)} />
        </Field>
        <ErrorText error={readError} />
        {saved && <p className="ok" role="status">{saved.text} · <a href={`#/jobs/${saved.jobId}`}>Open job</a></p>}
        <button disabled={reading || (!text.trim() && !file)}>{reading ? "Reading…" : "Read it"}</button>
      </form>

      {draft && (
        <form className="card draft" onSubmit={save}>
          <div className="row between">
            <h2>Check this, then save</h2>
            {draft.confidence !== "high" && <span className="pill status-submitted">Double-check</span>}
          </div>
          {draft.unknown && <p className="warn">Couldn't tell what this is. Pick the type below.</p>}
          {draft.cost != null && <p className="muted">This read cost about ${draft.cost < 0.01 ? draft.cost.toFixed(4) : draft.cost.toFixed(2)}</p>}
          {draft.question && <p className="warn">{draft.question}</p>}
          <div className="grid">
            <Field label="What is it">
              <select value={draft.kind} onChange={set("kind")}>
                {Object.entries(KIND_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </Field>
            <Field label="Job">
              <select value={draft.job_id} onChange={set("job_id")} required>
                <option value="">Pick a job</option>
                {jobs?.map((j) => <option key={j.id} value={j.id}>{j.name}</option>)}
              </select>
            </Field>
            {draft.kind === "hours" ? (
              <>
                <Field label="Who">
                  <select value={draft.worker_id} onChange={set("worker_id")} required>
                    <option value="">Pick a person</option>
                    {crew.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
                  </select>
                </Field>
                <Field label="Hours"><input type="number" step="0.25" min="0.25" max="24" value={draft.hours} onChange={set("hours")} required /></Field>
              </>
            ) : (
              <Field label="Amount ($)"><input type="number" step="0.01" min="0.01" value={draft.amount} onChange={set("amount")} required /></Field>
            )}
            {draft.kind === "payment" && (
              <>
                <Field label="Method">
                  <select value={draft.method} onChange={set("method")}>
                    {Object.entries(METHODS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                  </select>
                </Field>
                <Field label="Status">
                  <select value={draft.status} onChange={set("status")}>
                    <option value="received">Received</option>
                    <option value="pending">Pending / on its way</option>
                  </select>
                </Field>
                <Field label="Payment type">
                  <select value={draft.payment_type} onChange={set("payment_type")}>
                    {Object.entries(PAY_TYPES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                  </select>
                </Field>
                {draft.method === "card" && <Field label="Card fee ($)"><input type="number" step="0.01" min="0" value={draft.processing_fee} onChange={set("processing_fee")} /></Field>}
              </>
            )}
            {draft.kind === "cost" && (
              <>
                <Field label="Line">
                  <select value={draft.line} onChange={set("line")}>
                    {Object.entries(ENTRY_LINES).filter(([n]) => n !== "6" && n !== "10").map(([n, label]) => <option key={n} value={n}>{label}</option>)}
                  </select>
                </Field>
                <Field label="Paid to"><input value={draft.payee} onChange={set("payee")} /></Field>
              </>
            )}
            <Field label={draft.kind === "payment" && draft.status === "pending" ? "Expected date" : "Date"}><input type="date" value={draft.date} onChange={set("date")} required /></Field>
            {draft.kind !== "hours" && (
              <Field label={draft.kind === "cost" && draft.line === "11" ? "Reason (required)" : "Note"}>
                <input value={draft.note} onChange={set("note")} required={draft.kind === "cost" && draft.line === "11"} />
              </Field>
            )}
          </div>
          <ErrorText error={saveError} />
          <div className="row">
            <button disabled={saving}>Save</button>
            <button type="button" className="secondary" onClick={() => setDraft(null)}>Discard</button>
          </div>
        </form>
      )}
    </>
  );
}

function IntakeQueue() {
  const [items, , reload] = useLoad("/intake?outcome=unmatched");
  const [jobs] = useLoad("/job-names");
  const [resolve, busy, error] = useSubmit(async ([id, jobId]) => {
    await api(`/intake/${id}/resolve`, { method: "POST", body: jobId ? { job_id: jobId } : {} });
    reload();
  });
  if (!items?.length) return null;
  return (
    <section className="card draft">
      <h2>Emails that need you ({items.length})</h2>
      <p className="muted">These ready-to-order emails looked close to an existing job, so nothing was changed yet.</p>
      <ErrorText error={error} />
      <ul className="list">
        {items.map((e) => (
          <li key={e.id} className="entry-row">
            <div><strong>{e.subject}</strong></div>
            <div className="muted">
              {[e.parsed.invoice_total && `Invoice ${usd(e.parsed.invoice_total)}`, e.parsed.material_total && `Material ${usd(e.parsed.material_total)}`, e.parsed.rep_name].filter(Boolean).join(" · ")}
            </div>
            <div className="row">
              <select disabled={busy} value="" onChange={(ev) => ev.target.value && resolve([e.id, Number(ev.target.value)])} aria-label="Add to an existing job">
                <option value="">Add to existing job…</option>
                {jobs?.map((j) => <option key={j.id} value={j.id}>{j.name}</option>)}
              </select>
              {!e.parsed.is_vendor_reply && <button disabled={busy} className="secondary" onClick={() => resolve([e.id, null])}>Create new job</button>}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
