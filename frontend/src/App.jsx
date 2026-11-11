import { useEffect, useRef, useState } from "react";
import { api, onSessionInvalidated } from "./api.js";
import WebsiteLeadAlerts from "./WebsiteLeadAlerts.jsx";
import logo from "./logo.png";
import LegacyJobTracker from "./JobTracker.jsx";
import { HomePage, TrackerPage, CommunicationsPage, ToolsPage } from "./WorkspacePages.jsx";
import { SalesPage } from "./SalesWorkspace.jsx";
import { OperationsPage } from "./OperationsWorkspace.jsx";
import { useHash, wsApi, initials, Loading, ErrorNotice, Field } from "./workspace-ui.jsx";

export default function App() {
  const [me, setMe] = useState(undefined), [context, setContext] = useState(null), [preferences, setPreferences] = useState({});
  const [error, setError] = useState(""), [notice, setNotice] = useState(""), [authNotice, setAuthNotice] = useState(""), [version, setVersion] = useState(0), [mobileNav, setMobileNav] = useState(false);
  const path = useHash(), prefRef = useRef({}), prefQueue = useRef(Promise.resolve()), sessionGeneration = useRef(0), loggingOut = useRef(false);
  useEffect(() => {
    let active = true;
    const unsubscribe = onSessionInvalidated(message => {
      ++sessionGeneration.current; prefRef.current = {}; prefQueue.current = Promise.resolve();
      setMe(null); setContext(null); setPreferences({}); setNotice(""); setError(""); setAuthNotice(message); setMobileNav(false);
    });
    api("/me").then(user => { if (active) setMe(user); }, () => { if (active) setMe(null); });
    return () => { active = false; unsubscribe(); };
  }, []);
  useEffect(() => {
    const generation = ++sessionGeneration.current;
    if (!me) return;
    let active = true;
    Promise.all([wsApi("/context"), me.role === "crew" ? Promise.resolve({}) : wsApi("/preferences")]).then(([ctx, prefs]) => {
      if (active && generation === sessionGeneration.current) { setContext(ctx); setPreferences(prefs); prefRef.current = prefs; setError(""); }
    }).catch(e => { if (active && generation === sessionGeneration.current) setError(e.message); });
    return () => { active = false; };
  }, [me]);
  useEffect(() => { setMobileNav(false); }, [path]);
  useEffect(() => { if (!notice) return; const timer = setTimeout(() => setNotice(""), 5000); return () => clearTimeout(timer); }, [notice]);
  const savePreferences = patch => {
    const generation = sessionGeneration.current;
    const sessionChanged = () => { const error = new Error("Your session changed. Preferences were not saved."); error.sessionChanged = true; return error; };
    if (loggingOut.current) return Promise.reject(sessionChanged());
    const next = { ...prefRef.current, ...patch }; prefRef.current = next;
    const save = prefQueue.current.catch(() => {}).then(() => {
      if (generation !== sessionGeneration.current || loggingOut.current) throw sessionChanged();
      return wsApi("/preferences", { method: "PUT", body: next });
    });
    prefQueue.current = save;
    return save.then(saved => { if (generation !== sessionGeneration.current || loggingOut.current) throw sessionChanged(); setPreferences(saved); return saved; }).catch(e => { if (!e.sessionChanged && generation === sessionGeneration.current) setNotice(`Preferences could not be saved: ${e.message}`); throw e; });
  };
  const logout = async () => {
    if (loggingOut.current) return;
    loggingOut.current = true; ++sessionGeneration.current;
    try {
      // Invalidate queued work, then let any dispatched write settle before cookies change.
      await prefQueue.current.catch(() => {});
      await api("/logout", { method: "POST" });
      prefRef.current = {}; prefQueue.current = Promise.resolve();
      setMe(null); setContext(null); setPreferences({}); setNotice("");
    } catch (e) { setNotice(e.message); } finally { loggingOut.current = false; }
  };
  if (me === undefined) return <Loading />;
  if (!me) return <Login message={authNotice} onLogin={user => { setAuthNotice(""); setMe(user); }} />;
  if (me.role === "crew") return <>{context?.environment === "local-pilot" && <div className="ws-pilot-banner">Local pilot · Example records · Separate from your live jobs</div>}<LegacyJobTracker onLogout={() => { setMe(null); setContext(null); }} /></>;
  if (!context) return <div className="ws-starting"><img src={logo} alt="Mill City Cabinetry" />{error ? <><ErrorNotice error={error} /><button onClick={() => location.reload()}>Try again</button><button className="secondary" onClick={logout}>Log out</button></> : <Loading />}</div>;
  const caps = context.capabilities || {}, current = path.split("?")[0].split("/")[1] || "home";
  const nav = [["home", "Home", "◫"], ...(caps.sales ? [["sales", "Sales", "↗"]] : []), ...(caps.operations || me.role === "owner" ? [["operations", "Operations", "▤"]] : []), ["tracker", "Job Tracker", "▦"], ...(caps.sales ? [["communications", "Communications", "◌"]] : []), ["tools", "Tools & resources", "◇"]];
  const props = { me, context, preferences, savePreferences, version, refresh: () => setVersion(v => v + 1), notify: setNotice };
  let page;
  if (current === "sales" && caps.sales) page = <SalesPage {...props} />;
  else if (current === "operations" && (caps.operations || me.role === "owner")) page = <OperationsPage {...props} />;
  else if (current === "tracker") page = <TrackerPage {...props} path={path.replace(/^\/tracker/, "") || "/"} />;
  else if (current === "communications" && caps.sales) page = <CommunicationsPage context={context} me={me} />;
  else if (current === "tools") page = <ToolsPage />;
  else page = <HomePage {...props} />;
  return <div className="workspace-shell"><aside className={`ws-sidebar ${mobileNav ? "is-open" : ""}`}><a className="ws-brand" href="#/"><img src={logo} alt="Mill City Cabinetry" /><span>Mill City<span>TEAM WORKSPACE</span></span></a><div className="ws-sidebar-label">WORKSPACE</div><nav aria-label="Main navigation">{nav.map(([key, label, icon]) => <a key={key} onClick={() => setMobileNav(false)} className={current === key ? "active" : ""} href={key === "home" ? "#/" : `#/${key}`}><span aria-hidden="true">{icon}</span>{label}{key === "communications" && context.channels?.some(c => c.status !== "connected") && <i className="ws-nav-dot" />}</a>)}</nav><div className="ws-sidebar-foot"><div className="ws-avatar">{initials(me.name)}</div><div><strong>{me.name}</strong><span>{({ owner: "Administrator", rep: "Sales", designer: "Design team", operations: "Operations" })[me.role] || me.role}</span></div><button className="ws-signout" onClick={logout} aria-label="Log out" title="Log out">↪</button></div></aside><div className="ws-content"><header className="ws-topbar"><button className="ws-menu-button" onClick={() => setMobileNav(!mobileNav)} aria-label="Toggle navigation">☰</button><div className="ws-breadcrumb">Mill City <span>/</span><strong>{nav.find(n => n[0] === current)?.[1] || "Home"}</strong></div><span className="ws-date">{new Date().toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" })}</span><span className="ws-user-badge">{initials(me.name)}</span></header>{context.environment === "local-pilot" && <div className="ws-pilot-banner"><span className="ws-live-dot" />Local pilot · Example records · Separate from your live jobs</div>}{caps.sales && <WebsiteLeadAlerts key={me.id} {...props} />}<main className="workspace-main" key={current}>{page}</main></div>{notice && <div className="ws-toast" role="status">{notice}<button aria-label="Dismiss notification" onClick={() => setNotice("")}>×</button></div>}</div>;
}

function Login({ onLogin, message }) {
  const [email, setEmail] = useState(""), [password, setPassword] = useState(""), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const submit = async e => { e.preventDefault(); setBusy(true); setError(""); try { onLogin(await api("/login", { method: "POST", body: { email, password } })); } catch (err) { setError(err.message); } finally { setBusy(false); } };
  return <div className="ws-login-page"><div className="ws-login-story"><img src={logo} alt="Mill City Cabinetry" /><span className="ws-eyebrow">THE MILL CITY WORKSPACE</span><h1>Good work.<br />All in one place.</h1><p>From the first conversation<br />to the finished cabinet.</p><div className="ws-login-line" /></div><form className="ws-login-form" onSubmit={submit}><span className="ws-eyebrow">WELCOME BACK</span><h2>Sign in to your workspace</h2><p>Your sales, your team, your next step.</p><Field label="Work email"><input autoComplete="username" type="email" required value={email} onChange={e => setEmail(e.target.value)} /></Field><Field label="Password"><input autoComplete="current-password" type="password" required value={password} onChange={e => setPassword(e.target.value)} /></Field><ErrorNotice error={error || message} /><button disabled={busy}>{busy ? "Signing in…" : "Sign in →"}</button><small>Use your Mill City team account.</small></form></div>;
}
