export const colors=[['#203e39','Dark green'],['#1d4ed8','Blue'],['#dc2626','Red'],['#9333ea','Purple'],['#c2410c','Orange'],['#111827','Black']];
export const newPage=(title='New page')=>({id:crypto.randomUUID(),title,text:'',strokes:[],bubbles:[]});
export function upgradeNotebook(note){
 const c=note.content||{};
 return {...note,content:{...c,version:2,text:'',strokes:[],pages:c.pages?.length?c.pages:[{id:'original-page',title:'Measurements',text:c.text||'',strokes:c.strokes||[],bubbles:[]}],measurements:{color:'',door_style:'',assembly:'To confirm',delivery:'To confirm',install:'To confirm',ceiling_height:'',upper_finish:'',scope_notes:'',...c.measurements},review_notes:c.review_notes||''}};
}
export function replacePage(note,id,patch){return {...note,content:{...note.content,pages:note.content.pages.map(p=>p.id===id?{...p,...patch}:p)}};}
export function pageMatches(page,query){return [page.title,page.text,...page.bubbles.map(b=>b.text)].join(' ').toLowerCase().includes(query.toLowerCase());}
// Highland finish names from the estimator's saved catalog; accessories excluded.
export const cabinetOptions=['White Shaker','Gray Shaker','Modern Slate','Onyx Black','Signature Blue','White Nano','Green Nano','Luxe Nano','True Core — Value White'];
export const serviceOptions=['Assembled, delivered & installed','Assembled & delivered','Customer pickup'];
export function measurementSelection(value){
 if(value.cabinet_selection!=null)return value.cabinet_selection;
 const color=value.color&&value.color!=='To confirm'?value.color:'';
 const style=value.door_style&&value.door_style!=='To confirm'?value.door_style:'';
 return style&&!color.toLowerCase().includes(style.toLowerCase())?[color,style].filter(Boolean).join(' · '):color;
}
export function strokeNear(stroke,point,radius=18){
 const distance=(a,b)=>{const dx=b.x-a.x,dy=b.y-a.y;const t=Math.max(0,Math.min(1,((point.x-a.x)*dx+(point.y-a.y)*dy)/(dx*dx+dy*dy||1)));return Math.hypot(point.x-a.x-t*dx,point.y-a.y-t*dy);};
 return stroke.points.some((p,i)=>distance(p,stroke.points[Math.max(0,i-1)])<=radius+stroke.width/2);
}
