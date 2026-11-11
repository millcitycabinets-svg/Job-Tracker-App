import { useEffect, useRef, useState } from 'react';
import { wsApi, Modal, ErrorNotice } from './workspace-ui.jsx';
import { cachedNotes, cacheNote } from './notebook-cache.js';

const empty = (id,opportunityId,jobId)=>({id,title:'Measurement notes',opportunity_id:opportunityId||null,job_id:jobId||null,revision:0,editable:true,content:{text:'',strokes:[]}});
export default function FieldNotebooks({me,opportunityId,jobId,readOnly=false}) {
  const [rows,setRows]=useState([]),[local,setLocal]=useState([]),[selected,setSelected]=useState(null),[error,setError]=useState(''),[version,setVersion]=useState(0);
  useEffect(()=>{let active=true; const query=opportunityId?`?opportunity_id=${opportunityId}`:jobId?`?job_id=${jobId}`:'';
    wsApi('/notebooks'+query).then(v=>{if(active){setRows(v);setError('');}}).catch(e=>{if(active)setError(e.message);});
    cachedNotes(me.id).then(v=>{if(active)setLocal(v.filter(x=>x.dirty&&(!opportunityId||x.note.opportunity_id===opportunityId)&&(!jobId||x.note.job_id===jobId)).map(x=>({...x.note,local:true})));}).catch(()=>{});
    return()=>{active=false;};
  },[me.id,opportunityId,jobId,version]);
  const all=[...local,...rows.filter(r=>!local.some(x=>x.id===r.id))];
  return <section className="ws-notebooks"><div className="ws-section-heading"><h3>{opportunityId||jobId?'Job notes & drawings':'My notes & drawings'}</h3>{!readOnly&&<button type="button" className="secondary" onClick={()=>setSelected(empty(crypto.randomUUID(),opportunityId,jobId))}>+ New notebook</button>}</div><p className="ws-caption">{opportunityId||jobId?'New pages are automatically linked to this record.':'Start a private draft, then assign it to a customer or job.'}</p><ErrorNotice error={error}/>{!all.length&&<p className="ws-caption">No saved notebooks yet.</p>}<div className="ws-notebook-list">{all.map(row=><button type="button" className="secondary" key={row.id} onClick={()=>setSelected(row)}><strong>{row.title}</strong><small>{row.local?'Device draft — needs sync':row.opportunity_id?`Customer record #${row.opportunity_id}`:row.job_id?`Job #${row.job_id}`:'Unassigned draft'}</small></button>)}</div>{selected&&<NotebookEditor me={me} initial={selected} onClose={()=>{setSelected(null);setVersion(v=>v+1);}}/>}</section>;
}

function NotebookEditor({me,initial,onClose}) {
  const [doc,setDoc]=useState(null),[status,setStatus]=useState('Loading saved notebook…'),[error,setError]=useState(''),[targets,setTargets]=useState([]),[penOnly,setPenOnly]=useState(true),[color,setColor]=useState('#203e39'),[width,setWidth]=useState(3);
  const latest=useRef(null),dirty=useRef(false),busy=useRef(false),generation=useRef(0),blocked=useRef(false),mounted=useRef(true),canvas=useRef(null),stroke=useRef(null),cacheQueue=useRef(Promise.resolve());
  const persist=(value,isDirty)=>{cacheQueue.current=cacheQueue.current.catch(()=>{}).then(()=>cacheNote(me.id,value,isDirty));cacheQueue.current.catch(()=>{if(mounted.current)setError('Device storage is unavailable or full. Keep this page open until saved, or export a backup.');});return cacheQueue.current;};
  const apply=(value)=>{latest.current=value;dirty.current=true;generation.current++;setDoc(value);setStatus('Saving…');persist(value,true);};
  const save=async()=>{
    if(busy.current||!dirty.current||!latest.current||blocked.current)return;
    busy.current=true;const snapshot=latest.current,seq=generation.current;
    try {
      const saved=await wsApi(`/notebooks/${snapshot.id}`,{method:'PUT',body:{title:snapshot.title||'Untitled notes',opportunity_id:snapshot.opportunity_id,job_id:snapshot.job_id,content:snapshot.content,revision:snapshot.revision}});
      const current={...latest.current,revision:saved.revision,updated_at:saved.updated_at};latest.current=current;
      if(seq===generation.current){dirty.current=false;await persist(current,false);if(mounted.current&&seq===generation.current)setStatus(`Saved to workspace · ${new Date().toLocaleTimeString([], {hour:'numeric',minute:'2-digit'})}`);}
      else await persist(current,true);
      if(mounted.current){setDoc(latest.current);setError('');}
    } catch(e) {
      if(/newer version|another device|changed|unavailable/i.test(e.message))blocked.current=true;
      if(mounted.current){setStatus('Not synced — device draft retained');setError(e.message);}
    } finally {busy.current=false;}
  };
  useEffect(()=>{
    mounted.current=true;let active=true;
    (async()=>{
      const local=await cachedNotes(me.id).catch(()=>[]);const cached=local.find(x=>x.note.id===initial.id);let value;
      if(cached?.dirty){value=cached.note;dirty.current=true;setStatus('Recovered device draft · syncing…');}
      else if(initial.revision===0){value=initial;dirty.current=true;persist(value,true);setStatus('Saving new notebook…');}
      else {try{value=await wsApi(`/notebooks/${initial.id}`);setStatus('Saved to workspace');}catch(e){if(!cached)throw e;value=cached.note;setStatus('Offline copy · changes will sync when connected');}}
      if(active){latest.current=value;setDoc(value);}
    })().catch(e=>{if(active)setError(e.message);});
    const timer=setInterval(save,1500);
    const leave=e=>{if(dirty.current){e.preventDefault();e.returnValue='';}};
    window.addEventListener('beforeunload',leave);window.addEventListener('online',save);
    return()=>{active=false;mounted.current=false;clearInterval(timer);window.removeEventListener('beforeunload',leave);window.removeEventListener('online',save);};
  },[initial.id]);
  useEffect(()=>{let active=true;Promise.all([['owner','rep'].includes(me.role)?wsApi('/opportunities?scope=mine'):Promise.resolve([]),wsApi('/tracker-jobs')]).then(([opps,jobs])=>{if(active)setTargets([...opps.map(o=>({value:`o:${o.id}`,label:`Customer: ${o.name}`})),...jobs.map(j=>({value:`j:${j.id}`,label:`Job #${j.id}: ${j.name}`}))]);}).catch(()=>{});return()=>{active=false;};},[me.role]);
  const paint=()=>{const c=canvas.current;if(!c||!latest.current)return;const ctx=c.getContext('2d');ctx.fillStyle='#ffffff';ctx.fillRect(0,0,1200,900);ctx.strokeStyle='#edf0ec';ctx.lineWidth=1;for(let x=0;x<=1200;x+=30){ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,900);ctx.stroke();}for(let y=0;y<=900;y+=30){ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(1200,y);ctx.stroke();}for(const s of [...latest.current.content.strokes,...(stroke.current?[stroke.current]:[])]){ctx.strokeStyle=s.color;ctx.fillStyle=s.color;ctx.lineWidth=s.width;ctx.lineCap='round';ctx.lineJoin='round';ctx.beginPath();s.points.forEach((p,i)=>i?ctx.lineTo(p.x,p.y):ctx.moveTo(p.x,p.y));ctx.stroke();if(s.points.length===1){ctx.beginPath();ctx.arc(s.points[0].x,s.points[0].y,s.width/2,0,Math.PI*2);ctx.fill();}}};
  useEffect(paint,[doc]);
  const point=e=>{const r=canvas.current.getBoundingClientRect();return{x:Math.max(0,Math.min(1200,(e.clientX-r.left)*1200/r.width)),y:Math.max(0,Math.min(900,(e.clientY-r.top)*900/r.height))};};
  const start=e=>{if(!doc?.editable||stroke.current||(penOnly&&e.pointerType==='touch'))return;e.preventDefault();canvas.current.setPointerCapture(e.pointerId);stroke.current={color,width,points:[point(e)]};paint();};
  const move=e=>{if(!stroke.current)return;if(stroke.current.points.length<20000)stroke.current.points.push(point(e));paint();};
  const end=()=>{if(!stroke.current)return;const s=stroke.current;stroke.current=null;apply({...latest.current,content:{...latest.current.content,strokes:[...latest.current.content.strokes,s]}});};
  const download=(data,type,name)=>{const url=URL.createObjectURL(new Blob([data],{type}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
  const exportBackup=()=>download(JSON.stringify(latest.current,null,2),'application/json',`${doc.title||'measurement-notes'}.json`);
  const close=async()=>{end();await save();await cacheQueue.current.catch(()=>{});if(dirty.current&&!window.confirm('This notebook has not synced yet. A device draft is retained on this browser. Close anyway?'))return;onClose();};
  const assigned=doc?.opportunity_id?`o:${doc.opportunity_id}`:doc?.job_id?`j:${doc.job_id}`:'';
  return <Modal title="Job notebook" onClose={close}><div className="ws-notebook-editor"><div className="ws-notebook-status" role="status">{status}</div><ErrorNotice error={error}/>{doc&&<><label>Notebook title<input maxLength={200} disabled={!doc.editable} value={doc.title} onChange={e=>apply({...latest.current,title:e.target.value})}/></label><label>Saved with<select disabled={!doc.editable} value={assigned} onChange={e=>{const [type,id]=e.target.value.split(':');apply({...latest.current,opportunity_id:type==='o'?Number(id):null,job_id:type==='j'?Number(id):null});}}><option value="">Unassigned · private draft</option>{assigned&&!targets.some(t=>t.value===assigned)&&<option value={assigned}>{doc.opportunity_id?`Customer record #${doc.opportunity_id}`:`Job #${doc.job_id}`}</option>}{targets.map(t=><option value={t.value} key={t.value}>{t.label}</option>)}</select></label><label>Measurement notes<textarea rows={5} disabled={!doc.editable} value={doc.content.text} onChange={e=>apply({...latest.current,content:{...latest.current.content,text:e.target.value}})} placeholder="Dimensions, ceiling height, plumbing, appliances, and details to remember…"/></label><div className="ws-sketch-tools"><label>Ink<input type="color" value={color} onChange={e=>setColor(e.target.value)}/></label><label>Width<select value={width} onChange={e=>setWidth(Number(e.target.value))}><option value={2}>Fine</option><option value={3}>Normal</option><option value={6}>Bold</option></select></label><label><input type="checkbox" checked={penOnly} onChange={e=>setPenOnly(e.target.checked)}/> Pencil / mouse only</label><button type="button" className="secondary" disabled={!doc.editable||!doc.content.strokes.length} onClick={()=>apply({...latest.current,content:{...latest.current.content,strokes:latest.current.content.strokes.slice(0,-1)}})}>Undo stroke</button></div><canvas ref={canvas} width={1200} height={900} className="ws-sketch-canvas" aria-label="Freehand job drawing" onPointerDown={start} onPointerMove={move} onPointerUp={end} onPointerCancel={end}/><p className="ws-caption">Draw with Apple Pencil or mouse. Uncheck Pencil / mouse only to draw with your finger. Each completed stroke is saved automatically. Device drafts depend on this browser’s storage; wait for “Saved to workspace” before leaving or clearing browser data.</p><div className="ws-form-actions"><button type="button" disabled={!doc.editable} onClick={()=>{blocked.current=false;save();}}>Save now / retry</button><button type="button" className="secondary" onClick={exportBackup}>Export notes backup</button><button type="button" className="secondary" onClick={()=>canvas.current.toBlob(blob=>{if(blob)download(blob,'image/png',`${doc.title||'drawing'}.png`);})}>Export drawing PNG</button><button type="button" className="secondary" onClick={close}>Close notebook</button></div></>}</div></Modal>;
}
