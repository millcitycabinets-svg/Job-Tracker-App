export const colors=[['#203e39','Dark green'],['#1d4ed8','Blue'],['#dc2626','Red'],['#9333ea','Purple'],['#c2410c','Orange'],['#111827','Black']];
export const newPage=(title='New page')=>({id:crypto.randomUUID(),title,text:'',strokes:[],bubbles:[]});
export function upgradeNotebook(note){
 const c=note.content||{};
 return {...note,content:{...c,version:2,text:'',strokes:[],pages:c.pages?.length?c.pages:[{id:'original-page',title:'Measurements',text:c.text||'',strokes:c.strokes||[],bubbles:[]}],measurements:{color:'',door_style:'',assembly:'To confirm',delivery:'To confirm',install:'To confirm',ceiling_height:'',upper_finish:'',scope_notes:'',...c.measurements},review_notes:c.review_notes||''}};
}
export function replacePage(note,id,patch){return {...note,content:{...note.content,pages:note.content.pages.map(p=>p.id===id?{...p,...patch}:p)}};}
export function pageMatches(page,query){return [page.title,page.text,...page.bubbles.map(b=>b.text)].join(' ').toLowerCase().includes(query.toLowerCase());}
