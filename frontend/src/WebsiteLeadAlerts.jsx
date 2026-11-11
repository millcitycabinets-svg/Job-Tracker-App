import { useEffect, useState } from "react";
import { wsApi } from "./workspace-ui.jsx";
import { OpportunityDrawer } from "./SalesWorkspace.jsx";

export default function WebsiteLeadAlerts(props) {
  const [data, setData] = useState({ count: 0, items: [] });
  const [selected, setSelected] = useState(null);
  const [expanded, setExpanded] = useState(false);
  useEffect(() => {
    let active = true, busy = false;
    const poll = async () => {
      if (busy) return;
      busy = true;
      try { const result = await wsApi("/website-lead-alerts"); if (active) setData(result); }
      catch { if (active) setData({ count: 0, items: [] }); }
      finally { busy = false; }
    };
    poll();
    const timer = setInterval(poll, 30000);
    window.addEventListener("focus", poll);
    return () => { active = false; clearInterval(timer); window.removeEventListener("focus", poll); };
  }, [props.version]);
  const openPool = async () => {
    try { await props.savePreferences({ sales_scope: "shared" }); location.hash = "/sales"; }
    catch { /* Preference errors are displayed by the workspace. */ }
  };
  return <>
    {data.count > 0 && <section className="ws-lead-alert" aria-label="Website inquiries">
      <div className="ws-lead-alert-row"><span role="status" aria-live="polite"><strong>{data.count} website {data.count === 1 ? "inquiry needs" : "inquiries need"} an owner</strong><span>New in the shared lead pool · Claim one to follow up.</span></span><button className="secondary" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>{expanded ? "Hide inquiries" : "View inquiries"}</button></div>
      {expanded && <div className="ws-lead-alert-list">{data.items.map(item => <button key={item.id} className="secondary" onClick={() => setSelected(item.id)}>{item.name}<span>Open & claim →</span></button>)}{data.count > data.items.length && <button className="secondary" onClick={openPool}>View all {data.count} in shared pool →</button>}</div>}
    </section>}
    {selected && <OpportunityDrawer {...props} key={selected} id={selected} onClose={() => setSelected(null)} />}
  </>;
}
