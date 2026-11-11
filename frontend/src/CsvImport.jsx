import { useEffect, useRef, useState } from "react";
import { commandId, ErrorNotice, Field, Modal, wsApi } from "./workspace-ui.jsx";
import "./csv-import.css";

const MAX_BYTES = 5 * 1024 * 1024;
const FIELDS = {
  name: "Opportunity / contact name",
  customer: "Customer / company",
  email: "Email",
  phone: "Phone",
  address: "Address",
  notes: "Sales notes",
  source_url: "Source URL",
};
const count = value => Number.isFinite(Number(value)) ? Number(value).toLocaleString("en-US") : "—";

export default function CsvImport({ initialFile, me, context, onClose, onImported }) {
  const [file, setFile] = useState(null);
  const [mapping, setMapping] = useState({});
  const [manualMapping, setManualMapping] = useState(false);
  const [visibility, setVisibility] = useState("private");
  const [ownerId, setOwnerId] = useState(String(me.id));
  const [preview, setPreview] = useState(null);
  const [dirty, setDirty] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [phase, setPhase] = useState("");
  const [commitFailed, setCommitFailed] = useState(false);
  const operation = useRef(0);
  const commitIdentity = useRef(null);
  const resultHeading = useRef(null);
  const users = (context.users || []).filter(user => ["owner", "rep"].includes(user.role));
  const destinationOwner = users.find(user => String(user.id) === ownerId)?.name || me.name;
  const busy = Boolean(phase);

  useEffect(() => () => { operation.current += 1; }, []);
  useEffect(() => { if (result) resultHeading.current?.focus(); }, [result]);

  const requestPreview = async (sourceFile = file, selectedMapping = manualMapping ? mapping : {}) => {
    if (!sourceFile) return;
    if (sourceFile.size > MAX_BYTES) { setError("This file is larger than 5 MiB. Split it into smaller CSV files and try again."); return; }
    if (!sourceFile.size) { setError("This file is empty. Choose a CSV file containing your contacts."); return; }
    const requestNumber = ++operation.current;
    setPhase("preview"); setError(""); setDirty(true); setCommitFailed(false);
    const body = new FormData();
    body.append("file", sourceFile);
    body.append("mapping", JSON.stringify(selectedMapping));
    body.append("visibility", visibility);
    if (me.role === "owner") body.append("owner_id", ownerId);
    try {
      const data = await wsApi("/imports/preview", { method: "POST", body });
      if (requestNumber !== operation.current) return;
      setPreview(data); setMapping(data.mapping || selectedMapping); setDirty(false);
      commitIdentity.current = { importId: data.id, commandId: commandId() };
    } catch (err) { if (requestNumber === operation.current) setError(err.message); }
    finally { if (requestNumber === operation.current) setPhase(""); }
  };

  useEffect(() => { if (initialFile) { setFile(initialFile); requestPreview(initialFile, {}); } }, []);

  const chooseFile = event => {
    const selected = event.target.files?.[0];
    if (!selected) return;
    setFile(selected); setPreview(null); setMapping({}); setManualMapping(false); setResult(null); setError(""); setDirty(true);
    commitIdentity.current = null;
    requestPreview(selected, {});
  };
  const updateSetting = setter => event => {
    setter(event.target.value); setDirty(Boolean(preview)); setError(""); setCommitFailed(false);
  };
  const updateMapping = (field, header) => {
    setMapping(previous => ({ ...previous, [field]: header }));
    setManualMapping(true);
    setDirty(true); setError(""); setCommitFailed(false);
  };

  const importContacts = async () => {
    if (!preview || dirty || busy || result) return;
    if (!commitIdentity.current || commitIdentity.current.importId !== preview.id) {
      commitIdentity.current = { importId: preview.id, commandId: commandId() };
    }
    const requestNumber = ++operation.current;
    setPhase("commit"); setError("");
    try {
      const data = await wsApi(`/imports/${encodeURIComponent(preview.id)}/commit`, {
        method: "POST", body: { command_id: commitIdentity.current.commandId },
      });
      if (requestNumber !== operation.current) return;
      setResult(data); setCommitFailed(false); onImported(data);
    } catch (err) {
      if (requestNumber === operation.current) { setError(err.message); setCommitFailed(true); }
    } finally { if (requestNumber === operation.current) setPhase(""); }
  };

  const downloadTemplate = async () => {
    const requestNumber = ++operation.current;
    setPhase("template"); setError("");
    try {
      const blob = await wsApi("/imports/template", { responseType: "blob" });
      if (requestNumber !== operation.current) return;
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a"); link.href = url; link.download = "mill-city-contacts-template.csv";
      document.body.appendChild(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) { if (requestNumber === operation.current) setError(err.message); }
    finally { if (requestNumber === operation.current) setPhase(""); }
  };

  const rows = (preview?.rows || []).slice(0, 100);
  const safeClose = () => { if (phase !== "commit") onClose(); };
  return <Modal title="Upload contacts from CSV" onClose={safeClose}>
    <div className="csv-import" aria-busy={busy}>
      {result ? <>
        <div className="csv-result" role="status">
          <span className="csv-result-mark" aria-hidden="true">✓</span>
          <h3 ref={resultHeading} tabIndex={-1}>Import complete</h3>
          <strong>{count(result.created_count)} {Number(result.created_count) === 1 ? "contact" : "contacts"} added</strong>
          <p>{visibility === "shared" ? "Added to the shared lead pool." : `Added to ${destinationOwner}’s personal leads.`}</p>
        </div>
        <div className="csv-summary" aria-label="Import results">
          <div><strong>{count(result.created_count)}</strong><span>Created</span></div>
          <div><strong>{count(result.duplicate_count)}</strong><span>Duplicates skipped</span></div>
          <div><strong>{count(result.invalid_count)}</strong><span>Invalid rows skipped</span></div>
        </div>
        <p className="csv-note">Imported contacts start as New Lead. No messages were sent and no marketing subscription was added.</p>
        <div className="ws-form-actions"><button onClick={onClose}>Done</button></div>
      </> : <>
        <div className="csv-intro"><p>Bring your contacts into Sales, check the matches, then import the valid rows.</p><button type="button" className="secondary" disabled={busy} onClick={downloadTemplate}>{phase === "template" ? "Downloading…" : "Download template"}</button></div>
        <div className="ws-form-grid csv-destination">
          <Field label="Add contacts to"><select value={visibility} disabled={busy} onChange={updateSetting(setVisibility)}><option value="private">Personal leads</option><option value="shared">Shared lead pool</option></select></Field>
          {me.role === "owner" && <Field label="Sales owner"><select value={ownerId} disabled={busy} onChange={updateSetting(setOwnerId)}>{users.map(user => <option key={user.id} value={user.id}>{user.name}</option>)}</select></Field>}
        </div>
        <div className="csv-file-section" onDragOver={e => e.preventDefault()} onDrop={e => { e.preventDefault(); e.stopPropagation(); if (!busy) chooseFile({ target: { files: e.dataTransfer.files } }); }}>
          <label className="ws-field"><span>CSV file</span><input type="file" accept=".csv,text/csv" disabled={busy} onChange={chooseFile} aria-describedby="csv-file-help" /></label>
          <span id="csv-file-help" className="csv-file-help">Drop a CSV here or choose a file. Up to 5 MiB and 5,000 rows. Headers are matched automatically.</span>
          {file && <div className="csv-file-label"><span>{file.name}</span><span>{Math.max(1, Math.round(file.size / 1024)).toLocaleString("en-US")} KiB</span></div>}
        </div>
        <ErrorNotice error={error} />
        {phase === "preview" && <div className="csv-progress" role="status"><span />Checking headers, contacts, and duplicates…</div>}
        {preview && <>
          {!!preview.errors?.length && <div className="csv-preview-errors" role="status"><strong>Review these details</strong><ul>{preview.errors.map((message, index) => <li key={index}>{message}</li>)}</ul></div>}
          <details className="csv-mapping" open={dirty || Boolean(preview.errors?.length) || undefined}><summary>Match CSV columns <span>{Object.values(mapping).filter(Boolean).length} matched</span></summary><div className="csv-mapping-grid">{Object.entries(FIELDS).map(([field, label]) => <Field key={field} label={label}><select disabled={busy} value={mapping[field] || ""} onChange={event => updateMapping(field, event.target.value)}><option value="">Not mapped</option>{(preview.headers || []).map((header, index) => <option key={`${index}-${header}`} value={header}>{header}</option>)}</select></Field>)}</div></details>
          {dirty ? <div className="csv-review-needed"><p>Your file or settings changed. Update the preview before importing.</p><button disabled={busy} onClick={() => requestPreview()}>{phase === "preview" ? "Checking…" : "Update preview"}</button></div> : <>
            <div className="csv-review-heading"><h3>Review before importing</h3><span>{count(preview.total_rows)} {Number(preview.total_rows) === 1 ? "row" : "rows"} checked</span></div>
            <div className="csv-summary" aria-label="CSV preview summary"><div className="csv-valid"><strong>{count(preview.valid_count)}</strong><span>Ready to import</span></div><div><strong>{count(preview.duplicate_count)}</strong><span>Duplicates to skip</span></div><div><strong>{count(preview.invalid_count)}</strong><span>Invalid rows</span></div></div>
            <div className="csv-sample-label">Showing the first {rows.length} reviewed {rows.length === 1 ? "row" : "rows"}{Number(preview.total_rows) > rows.length ? ` of ${count(preview.total_rows)}` : ""}.</div>
            {!!rows.length && <div className="csv-preview-table"><table><thead><tr><th>Row</th><th>Review</th><th>Contact</th><th>Email / phone</th><th>Details</th></tr></thead><tbody>{rows.map((row, index) => <tr key={`${row.row_number}-${index}`}><td>{row.row_number}</td><td><span className={`csv-row-status csv-row-${row.status}`}>{row.status === "valid" ? "Ready" : row.status === "duplicate" ? "Duplicate" : "Invalid"}</span></td><td><strong>{row.data?.name || row.data?.customer || "—"}</strong>{row.data?.customer && row.data.customer !== row.data.name && <span>{row.data.customer}</span>}</td><td><span>{row.data?.email || "—"}</span>{row.data?.phone && <span>{row.data.phone}</span>}</td><td>{row.errors?.length ? row.errors.map((message, errorIndex) => <span className="csv-row-error" key={errorIndex}>{message}</span>) : row.duplicate_reason ? <span>{row.duplicate_reason}</span> : <span>{row.status === "valid" ? "New Lead" : "Will be skipped"}</span>}{row.data?.source_url && <span className="csv-source">Source: {row.data.source_url}</span>}</td></tr>)}</tbody></table></div>}
          </>}
        </>}
        <p className="csv-note">Contacts stay prospects in New Lead. Importing does not send messages or mark anyone as subscribed to marketing.</p>
        {phase === "commit" && <div className="csv-progress" role="status"><span />Importing contacts… Keep this window open.</div>}
        <div className="ws-form-actions"><button type="button" className="secondary" disabled={phase === "commit"} onClick={safeClose}>Cancel</button>{preview && !dirty ? <button disabled={busy || !Number(preview.valid_count)} onClick={importContacts}>{phase === "commit" ? "Importing…" : commitFailed ? "Retry import" : `Import ${count(preview.valid_count)} valid ${Number(preview.valid_count) === 1 ? "contact" : "contacts"}`}</button> : file && !preview && <button disabled={busy || file.size > MAX_BYTES} onClick={() => requestPreview(file, {})}>{phase === "preview" ? "Checking…" : "Check file"}</button>}</div>
      </>}
    </div>
  </Modal>;
}
