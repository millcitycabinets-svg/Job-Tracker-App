import { useEffect, useState } from "react";
import { api, today, usd } from "./api.js";

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
    e?.preventDefault();
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
  const nav = owner
    ? [["/", "Jobs"], ["/hours", "Log hours"], ["/team", "Team"]]
    : me.role === "crew" ? [["/", "My hours"]] : [];

  let page;
  const jobMatch = hash.match(/^\/jobs\/(\d+)$/);
  if (!owner && me.role !== "crew") page = <p className="card">Your screens are coming soon.</p>;
  else if (!owner) page = <LogHours me={me} />;
  else if (jobMatch) page = <JobDetail id={jobMatch[1]} />;
  else if (hash === "/new") page = <NewJob />;
  else if (hash === "/hours") page = <LogHours me={me} />;
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
  const [job, error, , setJob] = useLoad(`/jobs/${id}`, [id]);
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
                      <button className="link" onClick={() => remove(e)} aria-label="Delete entry">✕</button>
                    </td>
                  </tr>
                )),
              ];
            })}
          </tbody>
        </table>
        <ErrorText error={removeError} />
        {w.margin_pct != null && <p className="muted">Net margin {w.margin_pct}% of adjusted revenue</p>}
      </section>

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
